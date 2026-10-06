"""Stage 4, intersect reclamation polygons with the habitat map that was current before them.

Detections in each year range of `habitat.baseline_for_year` are cut by that map. Parts no
map covers are labelled `habitat.unmapped_label`. Writes the pieces to
data/processed/reclamation_habitat.gpkg and a year x habitat table to outputs/tables/.
"""
import geopandas as gpd
import pandas as pd

from . import detect
from .config import OUTPUTS, PROCESSED, RAW, ROOT

OUT = PROCESSED / "reclamation_habitat.gpkg"
TABLE = OUTPUTS / "tables" / "04_year_habitat.csv"


def habitat_map(cfg, key):
    """One habitat map in the working CRS with invalid geometries repaired and overlaps removed:
    where two polygons overlap, the one with the lower OBJECTID keeps the area."""
    h = cfg["habitat"]
    g = gpd.read_file(RAW / "habitat" / f"habitat_{key.removeprefix('map_')}.geojson")
    g = g.to_crs(cfg["project"]["crs"])[["OBJECTID", h["code_field"], h["type_field"], "geometry"]]
    g["geometry"] = g.make_valid()
    pairs = gpd.sjoin(g, g, predicate="overlaps")
    pairs = pairs[pairs.OBJECTID_left > pairs.OBJECTID_right]
    for i, earlier in pairs.groupby(level=0).index_right:
        g.loc[i, "geometry"] = g.geometry[i].difference(g.geometry[earlier.values].union_all())
    return g[~g.is_empty].drop(columns="OBJECTID")


def run(cfg):
    """Returns the pieces and the year x habitat table (hectares and share of the year)."""
    h = cfg["habitat"]
    rec = gpd.read_file(detect.OUT).reset_index(names="rec_id")
    parts = []
    for key, (start, end) in h["baseline_for_year"].items():
        r = rec[rec.year.between(start, end)].drop(columns="area_ha")
        hab = habitat_map(cfg, key)
        mapped = gpd.overlay(r, hab, how="intersection", keep_geom_type=True)
        unmapped = gpd.overlay(r, hab, how="difference", keep_geom_type=True)
        unmapped[h["type_field"]] = h["unmapped_label"]
        parts.append(pd.concat([mapped, unmapped]).assign(baseline=key))
        print(f"{key}: {len(r)} polygons ({start}-{end}) -> {len(mapped)} mapped, {len(unmapped)} unmapped pieces")
    pieces = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs=rec.crs)
    pieces["area_ha"] = pieces.area / 1e4
    pieces = pieces[pieces.area_ha > 0]
    OUT.unlink(missing_ok=True)
    pieces.to_file(OUT, layer="reclamation_habitat")

    table = (pieces.groupby(["year", "baseline", h["code_field"], h["type_field"]], dropna=False)
             .area_ha.sum().reset_index())
    table["share_pct"] = table.area_ha / table.groupby("year").area_ha.transform("sum") * 100
    table = table.round({"area_ha": 3, "share_pct": 2}).sort_values(["year", "area_ha"], ascending=[True, False])
    table.to_csv(TABLE, index=False)
    print(f"wrote {OUT.relative_to(ROOT)} ({len(pieces)} pieces) and {TABLE.relative_to(ROOT)}")
    return pieces, table
