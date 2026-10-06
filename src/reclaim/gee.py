"""Annual composites from Google Earth Engine, downloaded straight into data/raw/.

For every year one two-band GeoTIFF is written on a fixed 10 m SVY21 grid:
  band 1  the water index (or backscatter) composite
  band 2  n_obs, the number of cloud-free observations behind each pixel

The image is requested tile by tile with ee.data.computePixels and written into one file,
so nothing goes through Google Drive.
"""
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import ee
import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.io import MemoryFile
from rasterio.transform import from_origin
from rasterio.windows import Window

from . import manifest
from .config import RAW

HIGH_VOLUME = "https://earthengine-highvolume.googleapis.com"


def init(cfg):
    """Initialise Earth Engine with the project from EE_PROJECT or config.yaml."""
    project = os.environ.get("EE_PROJECT") or cfg["project"].get("ee_project")
    if not project:
        raise SystemExit("No Earth Engine project set. Put your Google Cloud project ID in "
                         "config.yaml (project.ee_project) or run: export EE_PROJECT=your-project-id")
    try:
        ee.Initialize(project=project, opt_url=HIGH_VOLUME)
    except Exception:
        ee.Authenticate()
        ee.Initialize(project=project, opt_url=HIGH_VOLUME)
    print(f"Earth Engine ready (project {project})")


# ---------------------------------------------------------------- grid

@dataclass(frozen=True)
class Grid:
    crs: str
    px: float
    x0: float      # west edge
    y1: float      # north edge
    width: int
    height: int

    @property
    def transform(self):
        return from_origin(self.x0, self.y1, self.px, self.px)

    def tiles(self, size):
        """(col, row, width, height) windows covering the grid."""
        return [(c, r, min(size, self.width - c), min(size, self.height - r))
                for r in range(0, self.height, size) for c in range(0, self.width, size)]


def make_grid(cfg):
    """Project the lon/lat bbox to the working CRS and snap it outward to whole pixels,
    so every year lands on exactly the same pixels."""
    lon0, lat0, lon1, lat1 = cfg["aoi"]["bbox"]
    crs, px = cfg["project"]["crs"], cfg["project"]["pixel_size_m"]
    xs, ys = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform(
        [lon0, lon0, lon1, lon1], [lat0, lat1, lat0, lat1])
    x0, y1 = math.floor(min(xs) / px) * px, math.ceil(max(ys) / px) * px
    return Grid(crs, px, x0, y1, math.ceil((max(xs) - x0) / px), math.ceil((y1 - min(ys)) / px))


# ---------------------------------------------------------------- composites

def _region(cfg):
    return ee.Geometry.Rectangle(cfg["aoi"]["bbox"])


def _year(col, year):
    return col.filterDate(f"{year}-01-01", f"{year + 1}-01-01")


def _percentile_and_count(index, cfg):
    """High percentile of a water index = the high-tide state, so tidal flats stay water."""
    im = cfg["imagery"]
    p = index.reduce(ee.Reducer.percentile([im["mndwi_percentile"]])).unmask(im["nodata"])
    return p.addBands(index.count().unmask(0)).rename(["mndwi", "n_obs"]).toFloat()


def s2_annual(year, cfg):
    """Sentinel-2 L1C MNDWI = (B3 - B11) / (B3 + B11), cloud-masked with Cloud Score+."""
    im = cfg["imagery"]
    col = _year(ee.ImageCollection(im["s2_collection"]).filterBounds(_region(cfg)), year)
    linked = col.linkCollection(ee.ImageCollection(im["cloud_collection"]), [im["cloud_band"]])

    def mndwi(img):
        clear = img.select(im["cloud_band"]).gte(im["cloud_threshold"])
        return img.normalizedDifference(["B3", "B11"]).rename("mndwi").updateMask(clear)

    return _percentile_and_count(linked.map(mndwi), cfg), col


def landsat_annual(year, cfg):
    """Landsat 8 TOA MNDWI = (B3 - B6) / (B3 + B6); QA_PIXEL bits 1-4 flag dilated cloud,
    cirrus, cloud and cloud shadow."""
    col = _year(ee.ImageCollection(cfg["imagery"]["landsat_collection"]).filterBounds(_region(cfg)), year)

    def mndwi(img):
        clear = img.select("QA_PIXEL").bitwiseAnd(0b11110).eq(0)
        return img.normalizedDifference(["B3", "B6"]).rename("mndwi").updateMask(clear)

    return _percentile_and_count(col.map(mndwi), cfg), col


def s1_annual(year, cfg):
    """Sentinel-1 annual median VV backscatter in dB (open water is dark)."""
    col = (_year(ee.ImageCollection(cfg["imagery"]["s1_collection"]).filterBounds(_region(cfg)), year)
           .filter(ee.Filter.eq("instrumentMode", "IW"))
           .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
           .select("VV"))
    img = col.median().unmask(cfg["imagery"]["nodata"]).addBands(col.count().unmask(0))
    return img.rename(["vv_median", "n_obs"]).toFloat(), col


SOURCES = {
    # key: (builder, config key for year range, folder, file prefix, band names, dataset, licence)
    "s2": (s2_annual, "collect", "s2_mndwi", "s2_mndwi", ("mndwi", "n_obs"),
           "Sentinel-2 L1C annual MNDWI percentile", "Copernicus Sentinel data, free and open"),
    "landsat": (landsat_annual, "landsat", "landsat_mndwi", "l8_mndwi", ("mndwi", "n_obs"),
                "Landsat 8 C2 TOA annual MNDWI percentile", "USGS public domain"),
    "s1": (s1_annual, "s1", "s1_vv", "s1_vv_median", ("vv_median", "n_obs"),
           "Sentinel-1 GRD annual median VV", "Copernicus Sentinel data, free and open"),
}


# ---------------------------------------------------------------- download

def _fetch_tile(image, grid, tile, tries=6):
    col, row, w, h = tile
    request = {
        "expression": image,
        "fileFormat": "GEO_TIFF",
        "grid": {
            "dimensions": {"width": w, "height": h},
            "affineTransform": {"scaleX": grid.px, "shearX": 0, "translateX": grid.x0 + col * grid.px,
                                "shearY": 0, "scaleY": -grid.px, "translateY": grid.y1 - row * grid.px},
            "crsCode": grid.crs,
        },
    }
    for i in range(tries):
        try:
            return tile, ee.data.computePixels(request)
        except Exception:  # quota bursts and timeouts are transient
            if i == tries - 1:
                raise
            time.sleep(min(2 ** i * 2, 60))


def download_image(image, grid, dest, bands, nodata, tile_px=1024, workers=6, tiles=None):
    """Fetch `image` tile by tile and assemble one compressed GeoTIFF at `dest`."""
    tiles = tiles if tiles is not None else grid.tiles(tile_px)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.stem + ".part.tif")
    profile = dict(driver="GTiff", dtype="float32", count=len(bands), crs=grid.crs,
                   transform=grid.transform, width=grid.width, height=grid.height, nodata=nodata,
                   compress="deflate", predictor=3, tiled=True, blockxsize=512, blockysize=512,
                   BIGTIFF="IF_SAFER")
    with rasterio.open(part, "w", **profile) as dst:
        for i, name in enumerate(bands, 1):
            dst.set_band_description(i, name)
        with ThreadPoolExecutor(workers) as pool:
            done = 0
            for (col, row, w, h), blob in pool.map(lambda t: _fetch_tile(image, grid, t), tiles):
                with MemoryFile(blob) as mem, mem.open() as src:
                    data = src.read().astype("float32")
                if data.shape != (len(bands), h, w):
                    raise RuntimeError(f"tile {col},{row}: got shape {data.shape}, expected {(len(bands), h, w)}")
                dst.write(np.nan_to_num(data, nan=nodata), window=Window(col, row, w, h))
                done += 1
                print(f"\r    tiles {done}/{len(tiles)}", end="", flush=True)
    print()
    part.replace(dest)
    return dest


def years_of(cfg, key):
    start, end = cfg["years"][key]
    return list(range(start, end + 1))


def run(cfg, sources=("s2", "landsat"), years=None, test=False, workers=6, overwrite=False):
    """Download annual composites. `test` fetches one tile for one year into data/raw/_test/."""
    init(cfg)
    grid = make_grid(cfg)
    im = cfg["imagery"]
    print(f"grid: {grid.width} x {grid.height} px at {grid.px:g} m, {grid.crs}, "
          f"origin ({grid.x0:.0f}, {grid.y1:.0f})")
    all_tiles = grid.tiles(im["tile_px"])
    for key in sources:
        build, year_key, folder, prefix, bands, dataset, licence = SOURCES[key]
        todo = [y for y in years_of(cfg, year_key) if years is None or y in years]
        out_grid, tiles = grid, all_tiles
        if test:  # one tile from the middle of the grid, written as its own small raster
            c, r, w, h = all_tiles[len(all_tiles) // 2]
            out_grid = Grid(grid.crs, grid.px, grid.x0 + c * grid.px, grid.y1 - r * grid.px, w, h)
            todo, tiles, folder = todo[-1:], [(0, 0, w, h)], "_test"
        for year in todo:
            dest = RAW / folder / f"{prefix}_{year}.tif"
            if dest.exists() and not overwrite:
                print(f"{key} {year}: exists, skipped")
                continue
            image, col = build(year, cfg)
            n = col.size().getInfo()
            if n == 0:
                print(f"{key} {year}: no images in the collection, skipped")
                continue
            print(f"{key} {year}: {n} images -> {dest.name}")
            download_image(image, out_grid, dest, bands, im["nodata"], im["tile_px"], workers, tiles)
            if not test:
                manifest.record(dest, dataset=dataset, source="Google Earth Engine",
                                dataset_id=cfg["imagery"][{"s2": "s2_collection", "landsat": "landsat_collection",
                                                           "s1": "s1_collection"}[key]],
                                year=year, n_images=n, licence=licence,
                                notes=f"bands: {', '.join(bands)}; {grid.crs} {grid.px:g} m")
