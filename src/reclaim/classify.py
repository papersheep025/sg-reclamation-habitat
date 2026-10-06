"""Stage 2, annual water / land masks from the MNDWI composites.

Sentinel-2 years get their own Otsu threshold, computed from pixels inside the Singapore
boundary with enough observations. Landsat 8 only fills the years before Sentinel-2; its
threshold gives the same water fraction as the Sentinel-2 masks in the overlap years.
Maximising pixel agreement instead leaves Landsat biased towards land at coastal edges.

Each mask is a two-band uint8 GeoTIFF on the original grid:
  band 1  water     1 water, 0 land, 255 nodata
  band 2  low_obs   1 where n_obs < min_valid_obs
"""
import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import geometry_mask
from skimage.filters import threshold_otsu

from .config import INTERIM, OUTPUTS, RAW, ROOT, year_range
from .gee import SOURCES

MASKS = INTERIM / "masks"
NODATA = 255


def inside_boundary(cfg, shape, transform):
    """True for pixels inside the Singapore boundary (territorial waters included)."""
    b = gpd.read_file(ROOT / cfg["aoi"]["boundary_file"]).to_crs(cfg["project"]["crs"])
    return geometry_mask(b.geometry, shape, transform, invert=True)


def read(key, year):
    """MNDWI, n_obs and the raster profile for source `key` ("s2" or "landsat")."""
    folder, prefix = SOURCES[key][2:4]
    with rasterio.open(RAW / folder / f"{prefix}_{year}.tif") as src:
        return src.read(1), src.read(2), src.profile


def threshold(values, cfg):
    c = cfg["classification"]
    return float(threshold_otsu(values)) if c["method"] == "otsu" else float(c["fixed_threshold"])


def calibrate_landsat(l8_values, s2_water, step=0.005):
    """Landsat threshold whose water fraction matches the Sentinel-2 masks on the same pixels.
    Returns the threshold and, for reference, the agreement curve over candidate thresholds."""
    t = float(np.quantile(l8_values, 1 - s2_water.mean()))
    ts = np.round(np.arange(-0.5, 0.8 + step, step), 3)
    water, land = np.sort(l8_values[s2_water]), np.sort(l8_values[~s2_water])
    # agreement = (S2 water with L8 > t) + (S2 land with L8 <= t)
    hits = (len(water) - np.searchsorted(water, ts, "right")) + np.searchsorted(land, ts, "right")
    curve = pd.DataFrame({"threshold": ts, "agreement": hits / l8_values.size})
    return t, curve


def write_mask(path, water, low_obs, profile):
    profile = dict(profile, dtype="uint8", count=2, nodata=NODATA, predictor=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(np.stack([water, low_obs]).astype("uint8"))
        dst.set_band_description(1, "water")
        dst.set_band_description(2, "low_obs")


def run(cfg):
    """Write one mask per year to data/interim/masks/ and the threshold table to outputs/tables/.
    Returns (table, Landsat calibration curve)."""
    s2_years = year_range(cfg, "collect")
    l8_all = year_range(cfg, "landsat")
    l8_years = [y for y in l8_all if y < s2_years[0]]
    cal_years = [y for y in l8_all if y in s2_years]
    nodata, min_obs = cfg["imagery"]["nodata"], cfg["imagery"]["min_valid_obs"]
    px_km2 = cfg["project"]["pixel_size_m"] ** 2 / 1e6

    inside = None
    rows, s2_cal = [], {}

    def classify(key, year, t=None, save=True):
        nonlocal inside
        m, n, profile = read(key, year)
        if inside is None:
            inside = inside_boundary(cfg, m.shape, profile["transform"])
        valid, low = m != nodata, n < min_obs
        good = inside & valid & ~low
        t = threshold(m[good], cfg) if t is None else t
        water = np.where(valid, m > t, NODATA).astype("uint8")
        if save:
            write_mask(MASKS / f"mask_{year}.tif", water, low, profile)
            print(f"{year} {key}: threshold {t:.3f} -> mask_{year}.tif")
        rows.append(dict(year=year, sensor=key, mask=save, threshold=round(t, 4),
                         land_km2=round((inside & (water == 0)).sum() * px_km2, 2),
                         water_pct=round(water[inside & valid].mean() * 100, 2),
                         low_obs_pct=round(low[inside].mean() * 100, 2)))
        return m, water, good

    for year in s2_years:
        _, water, good = classify("s2", year)
        if year in cal_years:
            s2_cal[year] = water == 1, good

    # Landsat: calibrate on the overlap years, then classify the years before Sentinel-2
    l8 = {y: read("landsat", y) for y in cal_years}
    sel = {y: s2_cal[y][1] & (l8[y][0] != nodata) & (l8[y][1] >= min_obs) for y in cal_years}
    t_l8, curve = calibrate_landsat(np.concatenate([l8[y][0][sel[y]] for y in cal_years]),
                                    np.concatenate([s2_cal[y][0][sel[y]] for y in cal_years]))
    agreement = {y: ((l8[y][0][sel[y]] > t_l8) == s2_cal[y][0][sel[y]]).mean() for y in cal_years}
    del l8
    for year in cal_years:
        classify("landsat", year, t_l8, save=False)
    for year in l8_years:
        classify("landsat", year, t_l8)

    table = pd.DataFrame(rows)
    table["agreement_with_s2"] = [round(agreement[r.year] * 100, 2)
                                  if r.sensor == "landsat" and r.year in agreement else np.nan
                                  for r in table.itertuples()]
    sla = pd.read_csv(RAW / "reference" / "sg_total_land_area.csv")
    table = (table.merge(sla.rename(columns={"total_land_area": "sla_km2"}), on="year", how="left")
             .sort_values(["year", "sensor"]).reset_index(drop=True))
    dest = OUTPUTS / "tables" / "02_thresholds.csv"
    table.to_csv(dest, index=False)
    print(f"wrote {dest.relative_to(ROOT)}")
    return table, curve
