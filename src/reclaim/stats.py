"""Stage 5, answers to the three research questions.

RQ1  hectares detected per year against SLA's annual increase
RQ2  reclaimed area by habitat, split into mapped habitat, reclamation works and unmapped
RQ3  selection ratio per habitat = share of reclaimed mapped habitat / share of the map

RQ3 only uses the area a baseline map covers, and leaves the reclamation works out of both
the numerator and the denominator (`habitat.works` in config.yaml). Its 95% intervals come from
resampling the reclamation polygons of each period with replacement; pieces cut from one
polygon stay together, and the map areas in the denominator are a fixed census.
"""
import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import box

from . import overlay
from .config import OUTPUTS, RAW, ROOT

TABLES = OUTPUTS / "tables"


def group(g, cfg):
    """'habitat', 'works' or 'unmapped' for each row of pieces or of a habitat map.
    `g` needs the type field and a `baseline` column."""
    h, w = cfg["habitat"], cfg["habitat"]["works"]
    area = gpd.GeoSeries([box(*w["bbox"])], crs="EPSG:4326").to_crs(g.crs).iloc[0]
    is_works = g.representative_point().within(area) & pd.Series(
        [t in w["classes"].get(b, []) for t, b in zip(g[h["type_field"]], g.baseline)], index=g.index)
    return pd.Series("habitat", index=g.index).mask(is_works, "works").mask(
        g[h["type_field"]] == h["unmapped_label"], "unmapped")


def rq1(pieces):
    sla = pd.read_csv(RAW / "reference" / "sg_total_land_area.csv").set_index("year").total_land_area
    t = pieces.groupby("year").area_ha.sum().rename("detected_ha").to_frame()
    t["sla_increase_ha"] = (sla.diff() * 100).reindex(t.index)
    return t.round(1).reset_index()


def rq2(pieces, cfg):
    typ = cfg["habitat"]["type_field"]
    t = pieces.groupby(["baseline", "group", typ]).area_ha.sum().reset_index()
    t["share_of_all_pct"] = t.area_ha / t.area_ha.sum() * 100
    hab = t.group == "habitat"
    t.loc[hab, "share_of_mapped_habitat_pct"] = (
        t.area_ha[hab] / t[hab].groupby("baseline").area_ha.transform("sum") * 100)
    return t.sort_values(["baseline", "group", "area_ha"], ascending=[True, True, False]).round(2)


def rq3(pieces, cfg):
    """Selection ratio per baseline map with a 95% bootstrap interval.
    Above 1: reclaimed more than its share of the map."""
    typ = cfg["habitat"]["type_field"]
    rng = np.random.default_rng(cfg["stats"]["random_seed"])
    out = []
    for key in cfg["habitat"]["baseline_for_year"]:
        m = overlay.habitat_map(cfg, key).assign(baseline=key)
        m = m[group(m, cfg) == "habitat"]
        avail = (m.area / 1e4).groupby(m[typ]).sum().rename("map_ha")
        p = pieces[(pieces.baseline == key) & (pieces.group == "habitat")]
        used = p.groupby(typ).area_ha.sum().rename("reclaimed_ha")
        t = pd.concat([avail, used], axis=1).fillna(0).assign(baseline=key)
        t["map_share_pct"] = t.map_ha / t.map_ha.sum() * 100
        t["reclaimed_share_pct"] = t.reclaimed_ha / t.reclaimed_ha.sum() * 100
        t["selection_ratio"] = t.reclaimed_share_pct / t.map_share_pct
        # polygon x habitat area matrix, every polygon of the period included (most have no habitat)
        polys = pieces.loc[pieces.baseline == key, "rec_id"].unique()
        area = (p.pivot_table(index="rec_id", columns=typ, values="area_ha", aggfunc="sum")
                .reindex(index=polys, columns=t.index).fillna(0).to_numpy())
        draws = rng.integers(0, len(polys), (cfg["stats"]["bootstrap_n"], len(polys)))
        sums = np.stack([np.bincount(d, minlength=len(polys)) for d in draws]) @ area
        with np.errstate(invalid="ignore", divide="ignore"):
            ratios = sums / sums.sum(axis=1, keepdims=True) * 100 / t.map_share_pct.to_numpy()
        t["ci_low"], t["ci_high"] = np.nanpercentile(ratios, [2.5, 97.5], axis=0)
        out.append(t.rename_axis(typ).reset_index())
    cols = ["baseline", typ, "map_ha", "map_share_pct", "reclaimed_ha", "reclaimed_share_pct",
            "selection_ratio", "ci_low", "ci_high"]
    return pd.concat(out)[cols].sort_values(["baseline", "reclaimed_ha"], ascending=[True, False]).round(3)


def run(cfg):
    """Write the three tables to outputs/tables/ and return them with the grouped pieces."""
    pieces = gpd.read_file(overlay.OUT)
    pieces["group"] = group(pieces, cfg)
    tables = {"05_rq1_annual": rq1(pieces), "05_rq2_habitat": rq2(pieces, cfg),
              "05_rq3_selection": rq3(pieces, cfg)}
    for name, t in tables.items():
        t.to_csv(TABLES / f"{name}.csv", index=False)
        print(f"wrote {(TABLES / name).relative_to(ROOT)}.csv")
    return pieces, tables
