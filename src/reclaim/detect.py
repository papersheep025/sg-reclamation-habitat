"""Stage 3, persistent water-to-land detection.

A pixel is reclaimed in year y when it is water in the `water_years_before` years before y,
land in y and the `land_years_after` - 1 years after, and (with `require_no_reversion`)
never water again. Results are clipped to the Singapore boundary, reservoirs and solar
plants from OSM are masked out (floating solar looks like new land), and each year is
cleaned with a morphological opening and a minimum patch size before vectorising.
"""
import json

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import rasterize, shapes
from scipy import ndimage as ndi
from shapely.geometry import LineString, Polygon, shape
from shapely.ops import polygonize, unary_union

from .classify import MASKS, inside_boundary
from .config import PROCESSED, ROOT, year_range

OUT = PROCESSED / "reclamation.gpkg"


def mask_years(cfg):
    """Every year with a mask: the Landsat-only years plus the Sentinel-2 years."""
    return list(range(min(cfg["years"]["landsat"][0], cfg["years"]["collect"][0]),
                      cfg["years"]["collect"][1] + 1))


def stack(years):
    """Water and land arrays of shape (year, row, col), plus the raster profile.
    Nodata pixels are neither water nor land."""
    masks = []
    for y in years:
        with rasterio.open(MASKS / f"mask_{y}.tif") as src:
            masks.append(src.read(1))
            profile = src.profile
    masks = np.stack(masks)
    return masks == 1, masks == 0, profile


def exclusions(cfg):
    """OSM reservoir and solar-plant polygons in the working CRS."""
    elements = json.loads((ROOT / cfg["reference"]["osm_exclusions"]).read_text())["elements"]

    def lines(e, role):
        return unary_union([LineString([(p["lon"], p["lat"]) for p in m["geometry"]])
                            for m in e["members"] if m.get("role") == role and m.get("geometry")])

    geoms = []
    for e in elements:
        if e["type"] == "way" and len(e.get("geometry", [])) >= 4:
            geoms.append(Polygon([(p["lon"], p["lat"]) for p in e["geometry"]]))
        elif e["type"] == "relation":
            outer = unary_union(list(polygonize(lines(e, "outer"))))
            geoms.append(outer.difference(unary_union(list(polygonize(lines(e, "inner"))))))
    return gpd.GeoSeries(geoms, crs="EPSG:4326").to_crs(cfg["project"]["crs"])


def first_land_year(water, land, years, cfg):
    """int16 raster of the reclamation year, 0 where the rule never holds."""
    d = cfg["detection"]
    before, after = d["water_years_before"], d["land_years_after"]
    out = np.zeros(water.shape[1:], "int16")
    for y in year_range(cfg, "study"):
        i = years.index(y)
        hit = water[i - before:i].all(0) & land[i:i + after].all(0)
        if d["require_no_reversion"]:
            hit &= land[i:].all(0)
        out[hit & (out == 0)] = y
    return out


def clean(year_raster, cfg):
    """Per year: opening with a (2r+1) square, then drop patches below min_patch_ha."""
    d = cfg["detection"]
    r = d["morph_open_px"]
    min_px = d["min_patch_ha"] * 1e4 / cfg["project"]["pixel_size_m"] ** 2
    out = np.zeros_like(year_raster)
    for y in np.unique(year_raster[year_raster > 0]):
        m = ndi.binary_opening(year_raster == y, np.ones((2 * r + 1, 2 * r + 1), bool))
        lab, n = ndi.label(m)
        keep = np.flatnonzero(ndi.sum(m, lab, range(1, n + 1)) >= min_px) + 1
        out[np.isin(lab, keep)] = y
    return out


def run(cfg):
    """Write the reclamation polygons to data/processed/reclamation.gpkg.
    Returns (polygons, hectares per year after each filtering step)."""
    years = mask_years(cfg)
    water, land, profile = stack(years)
    shape_, transform = water.shape[1:], profile["transform"]
    ha = cfg["project"]["pixel_size_m"] ** 2 / 1e4

    steps = {}
    yr = first_land_year(water, land, years, cfg)
    del water, land
    steps["raw"] = yr.copy()
    yr[~inside_boundary(cfg, shape_, transform)] = 0
    steps["in_boundary"] = yr.copy()
    excluded = rasterize([(g, 1) for g in exclusions(cfg)], out_shape=shape_, transform=transform,
                         dtype="uint8").astype(bool)
    yr[excluded] = 0
    steps["no_reservoir_solar"] = yr.copy()
    yr = clean(yr, cfg)
    steps["cleaned"] = yr

    summary = pd.DataFrame({k: {y: (v == y).sum() * ha for y in year_range(cfg, "study")}
                            for k, v in steps.items()}).rename_axis("year").round(1)

    polys = gpd.GeoDataFrame(
        [{"year": int(v), "geometry": shape(g)} for g, v in shapes(yr, mask=yr > 0, transform=transform)],
        crs=cfg["project"]["crs"])
    polys["area_ha"] = (polys.area / 1e4).round(3)
    polys = polys.sort_values(["year", "area_ha"], ascending=[True, False]).reset_index(drop=True)
    OUT.unlink(missing_ok=True)
    polys.to_file(OUT, layer="reclamation")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(polys)} polygons, {polys.area_ha.sum():,.1f} ha")
    return polys, summary
