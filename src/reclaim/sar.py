"""Sentinel-1 cross-check of the stage 3 detections.

Annual median VV backscatter is split into water and land. Open water is dark, but fresh sand
fill is also fairly dark (about -15 dB against -20 dB for water and -7 dB for built land), so a
two-class Otsu split puts new fill on the water side for years. The question here is "is it still
open water?", so the threshold is the `sar.water_quantile` of VV over pixels that are optical water
in every year (stable water both sensors agree on); changing pixels are judged by radar alone.
The stage 3 persistence rule is then
applied to the radar masks: per polygon on the share of water pixels, per pixel for the area
that stage 3 cleaning removed. Radar agreement shows a detection is not an optical artefact;
it is not an accuracy measure. Floating solar also looks like land to radar and is already
masked out in stage 3. Sentinel-1 starts in 2015, so for 2016 only 2015 is checked as water.
"""
import geopandas as gpd
import numpy as np
import pandas as pd
from rasterio.features import rasterize

from . import classify, detect
from .config import OUTPUTS, ROOT, year_range

TABLES = OUTPUTS / "tables"
STATUS = ["same year", "1 year apart", "more than 1 year apart", "no transition seen", "no radar data"]


def stable_water(cfg):
    """Pixels inside the boundary that the optical masks call water in every year."""
    years = detect.mask_years(cfg)
    water, land, profile = detect.stack(years)
    return water.all(0) & classify.inside_boundary(cfg, water.shape[1:], profile["transform"])


def masks(cfg):
    """Radar water and land stacks (year, row, col); low-observation pixels are neither."""
    years = year_range(cfg, "s1")
    nodata, min_obs = cfg["imagery"]["nodata"], cfg["imagery"]["min_valid_obs"]
    ref = stable_water(cfg)
    water, land, rows, inside = [], [], [], None
    for y in years:
        vv, n, profile = classify.read("s1", y)
        if inside is None:
            inside = classify.inside_boundary(cfg, vv.shape, profile["transform"])
        valid = (vv != nodata) & (n >= min_obs)
        t = float(np.quantile(vv[ref & valid], cfg["sar"]["water_quantile"]))
        water.append(valid & (vv < t))
        land.append(valid & (vv >= t))
        rows.append(dict(year=y, threshold_db=round(t, 2), median_obs=float(np.median(n[inside])),
                         low_obs_pct=round((~valid[inside]).mean() * 100, 2),
                         land_km2=round((land[-1] & inside).sum() / 1e4, 2)))
    return years, np.stack(water), np.stack(land), pd.DataFrame(rows), profile, inside


def first_land_year(water, land, years, cfg):
    """The stage 3 rule on any (year, ...) stack. Unlike detect.first_land_year it uses only the
    'before' years that exist, so 2016 is checked against 2015 alone."""
    d = cfg["detection"]
    before, after = d["water_years_before"], d["land_years_after"]
    out = np.zeros(water.shape[1:], "int16")
    for y in year_range(cfg, "study"):
        i = years.index(y)
        hit = water[max(0, i - before):i].all(0) & land[i:i + after].all(0)
        if d["require_no_reversion"]:
            hit &= land[i:].all(0)
        out[hit & (out == 0)] = y
    return out


def status(optical, radar, has_data):
    if not has_data:
        return "no radar data"
    if radar == 0:
        return "no transition seen"
    gap = abs(int(radar) - int(optical))
    return STATUS[min(gap, 2)]


def polygon_check(cfg, water, land, years, transform):
    """Per polygon: share of radar-water pixels each year, the radar transition year and a status."""
    rec = gpd.read_file(detect.OUT)
    ids = rasterize(((g, i + 1) for i, g in enumerate(rec.geometry)), out_shape=water.shape[1:],
                    transform=transform, dtype="int32").ravel()
    k = len(rec) + 1
    w = np.stack([np.bincount(ids, weights=water[t].ravel(), minlength=k) for t in range(len(years))])[:, 1:]
    lnd = np.stack([np.bincount(ids, weights=land[t].ravel(), minlength=k) for t in range(len(years))])[:, 1:]
    with np.errstate(invalid="ignore"):
        frac = w / (w + lnd)                               # (year, polygon), NaN without radar data
    q = cfg["sar"]["min_frac"]
    poly_water, poly_land = frac >= q, (1 - frac) >= q      # NaN compares False: neither
    radar_year = first_land_year(poly_water, poly_land, years, cfg)
    d = cfg["detection"]
    out = rec.drop(columns="geometry").reset_index(names="rec_id")
    out["radar_year"] = radar_year
    out["status"] = [
        status(y, r, not np.isnan(frac[max(0, years.index(y) - d["water_years_before"]):
                                        years.index(y) + d["land_years_after"], j]).any())
        for j, (y, r) in enumerate(zip(out.year, radar_year))]
    for t, y in enumerate(years):
        out[f"water_{y}"] = frac[t].round(3)
    return out


def removed_check(cfg, radar):
    """Radar confirmation (year within 1) of pixels kept vs removed by stage 3 cleaning."""
    years = detect.mask_years(cfg)
    water, land, profile = detect.stack(years)
    pre = detect.first_land_year(water, land, years, cfg)
    del water, land
    pre[~classify.inside_boundary(cfg, pre.shape, profile["transform"])] = 0
    pre[rasterize([(g, 1) for g in detect.exclusions(cfg)], out_shape=pre.shape,
                  transform=profile["transform"], dtype="uint8").astype(bool)] = 0
    kept = detect.clean(pre, cfg) > 0
    removed = (pre > 0) & ~kept
    ok = (radar > 0) & (np.abs(radar.astype(int) - pre) <= 1)
    rows = []
    for y in year_range(cfg, "study"):
        for name, m in (("kept", kept), ("removed", removed)):
            sel = m & (pre == y)
            rows.append(dict(year=y, pixels=name, area_ha=sel.sum() / 100,
                             radar_confirmed_pct=round(ok[sel].mean() * 100, 1) if sel.any() else np.nan))
    for name, m in (("kept", kept), ("removed", removed)):
        rows.append(dict(year="all", pixels=name, area_ha=m.sum() / 100,
                         radar_confirmed_pct=round(ok[m].mean() * 100, 1)))
    return pd.DataFrame(rows)


def summary(poly):
    """Area by status per detection year, with the share confirmed (same year or 1 year apart)."""
    t = poly.pivot_table(index="year", columns="status", values="area_ha", aggfunc="sum", fill_value=0)
    t = t.reindex(columns=STATUS, fill_value=0)
    t.loc["all"] = t.sum()
    t["confirmed_pct"] = (t["same year"] + t["1 year apart"]) / t.sum(axis=1) * 100
    return t.round(1).reset_index()


def run(cfg):
    years, water, land, thresholds, profile, _ = masks(cfg)
    radar = first_land_year(water, land, years, cfg)
    poly = polygon_check(cfg, water, land, years, profile["transform"])
    del water, land
    tables = {"06_sar_thresholds": thresholds, "06_sar_crosscheck": poly, "06_sar_summary": summary(poly),
              "06_sar_removed": removed_check(cfg, radar)}
    for name, t in tables.items():
        t.to_csv(TABLES / f"{name}.csv", index=False)
        print(f"wrote {(TABLES / name).relative_to(ROOT)}.csv")
    return tables, radar
