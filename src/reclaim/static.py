"""Download the datasets that need no account: NParks habitat maps, the SLA land-area
table and the Singapore boundary. Raw files are stored exactly as served."""
import os
import time

import requests

from . import manifest
from .config import PROCESSED, RAW, ROOT

UA = {"User-Agent": "sg-reclamation-habitat (GE5219 coursework, NUS)"}
OPEN_DATA = "Singapore Open Data Licence"


def presigned_url(dataset_id, api, tries=8):
    """Ask data.gov.sg for a temporary download link. Retries while the file is being prepared
    or when the rate limit (HTTP 429) is hit. Set DATA_GOV_SG_API_KEY for higher limits."""
    headers = dict(UA)
    if os.environ.get("DATA_GOV_SG_API_KEY"):
        headers["x-api-key"] = os.environ["DATA_GOV_SG_API_KEY"]
    for i in range(tries):
        r = requests.get(api.format(dataset_id=dataset_id), headers=headers, timeout=60)
        if r.status_code == 429:
            time.sleep(min(2 ** i, 60))
            continue
        r.raise_for_status()
        body = r.json()
        if body.get("code") != 0:
            raise RuntimeError(f"{dataset_id}: {body.get('errorMsg') or body.get('errMsg') or body}")
        url = (body.get("data") or {}).get("url")
        if url:
            return url
        time.sleep(3)
    raise RuntimeError(f"{dataset_id}: no download link after {tries} attempts")


def download(url, dest, overwrite=False, **kw):
    """Stream `url` to `dest`. Writes to a .part file first so a broken download is never
    mistaken for a finished one."""
    if dest.exists() and not overwrite:
        print(f"  exists, skipped: {dest.name}")
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    with requests.get(url, stream=True, timeout=300, headers=UA, **kw) as r:
        r.raise_for_status()
        with open(part, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    part.replace(dest)
    print(f"  saved {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")
    return True


def habitat_maps(cfg, overwrite=False):
    """NParks Coastal and Marine Habitat Maps 2010/11 and 2018."""
    out = {}
    for key, dataset_id in cfg["habitat"]["datasets"].items():
        dest = RAW / "habitat" / f"habitat_{key.removeprefix('map_')}.geojson"
        if download_if_needed(dest, dataset_id, cfg, overwrite):
            manifest.record(dest, dataset=f"NParks Coastal and Marine Habitat Map ({key})",
                            source="data.gov.sg", dataset_id=dataset_id,
                            year=key.removeprefix("map_"), licence=OPEN_DATA)
        out[key] = dest
    return out


def download_if_needed(dest, dataset_id, cfg, overwrite):
    if dest.exists() and not overwrite:
        print(f"  exists, skipped: {dest.name}")
        return False
    return download(presigned_url(dataset_id, cfg["habitat"]["api"]), dest, overwrite=True)


def land_area(cfg, overwrite=False):
    """SLA official total land area by year, used to sanity-check our cumulative totals."""
    dataset_id = cfg["reference"]["land_area_dataset"]
    dest = RAW / "reference" / "sg_total_land_area.csv"
    if download_if_needed(dest, dataset_id, cfg, overwrite):
        manifest.record(dest, dataset="Total Land Area of Singapore (SLA)", source="data.gov.sg",
                        dataset_id=dataset_id, year="1960-2025", licence=OPEN_DATA)
    return dest


def boundary(cfg, overwrite=False):
    """Singapore national boundary from OpenStreetMap (admin_level=2) via Nominatim."""
    dest = ROOT / cfg["aoi"]["boundary_file"]
    params = {"country": "Singapore", "format": "geojson", "polygon_geojson": 1, "limit": 1}
    if dest.exists() and not overwrite:
        print(f"  exists, skipped: {dest.name}")
    else:
        download("https://nominatim.openstreetmap.org/search", dest, overwrite=True, params=params)
        manifest.record(dest, dataset="Singapore national boundary (OSM admin_level=2)",
                        source="OpenStreetMap via Nominatim", licence="ODbL 1.0",
                        notes="(c) OpenStreetMap contributors; check that it includes territorial waters")
    return dest


OVERPASS = "https://overpass-api.de/api/interpreter"
EXCLUSION_QUERY = """[out:json][timeout:120];
area["ISO3166-1"="SG"][admin_level=2]->.sg;
(nwr["water"="reservoir"](area.sg); nwr["landuse"="reservoir"](area.sg);
 nwr["power"="plant"]["plant:source"="solar"](area.sg););
out geom;"""


def osm_exclusions(cfg, overwrite=False):
    """Reservoirs and solar plants from OpenStreetMap via Overpass, stored as the raw JSON.
    Stage 3 masks them out: floating solar (e.g. Tengeh) turns water into apparent land."""
    dest = ROOT / cfg["reference"]["osm_exclusions"]
    if dest.exists() and not overwrite:
        print(f"  exists, skipped: {dest.name}")
    else:
        download(OVERPASS, dest, overwrite=True, params={"data": EXCLUSION_QUERY})
        manifest.record(dest, dataset="Singapore reservoirs and solar plants (OSM)",
                        source="OpenStreetMap via Overpass", licence="ODbL 1.0",
                        notes="(c) OpenStreetMap contributors; water/landuse=reservoir, power=plant solar")
    return dest


def check_boundary(cfg):
    """Report the boundary area. Singapore's land area is ~735 km2, so a value near that means
    the polygon is land-only and cannot be used to clip reclamation at sea."""
    import geopandas as gpd
    g = gpd.read_file(ROOT / cfg["aoi"]["boundary_file"]).to_crs(cfg["project"]["crs"])
    km2 = g.area.sum() / 1e6
    props = g.drop(columns="geometry").iloc[0].to_dict()
    print(f"  boundary: {props.get('display_name')} | osm {props.get('osm_type')} "
          f"{props.get('osm_id')} | {g.geom_type.iloc[0]} | {km2:,.0f} km2")
    if km2 < 900:
        print("  WARNING: area is close to land-only (~735 km2). This polygon does not include "
              "territorial waters; get a maritime boundary before clipping.")
    return km2


def habitat_code_table(cfg):
    """Build the habitat class table from the maps themselves (the separate code table is no
    longer published) with feature counts and area per class for both years, so the two
    class schemes can be compared before the overlay."""
    import geopandas as gpd
    import pandas as pd
    code, typ = cfg["habitat"]["code_field"], cfg["habitat"]["type_field"]
    parts = []
    for key in cfg["habitat"]["datasets"]:
        tag = key.removeprefix("map_")
        g = gpd.read_file(RAW / "habitat" / f"habitat_{tag}.geojson")
        print(f"  {tag}: {len(g):,} features, CRS {g.crs.to_string() if g.crs else 'none'}, "
              f"invalid geometries: {(~g.is_valid).sum()}")
        g = g.to_crs(cfg["project"]["crs"])
        t = (g.assign(area_ha=g.area / 1e4).groupby([code, typ], dropna=False)
               .agg(n=("area_ha", "size"), area_ha=("area_ha", "sum")).round(2))
        parts.append(t.rename(columns={"n": f"n_{tag}", "area_ha": f"area_ha_{tag}"}))
    table = pd.concat(parts, axis=1).reset_index().sort_values(code)
    dest = PROCESSED / "habitat_code_table.csv"
    table.to_csv(dest, index=False)
    print(f"  wrote {dest.name}: {len(table)} classes")
    return table


def run(cfg, overwrite=False):
    print("NParks habitat maps"); habitat_maps(cfg, overwrite)
    print("SLA land area"); land_area(cfg, overwrite)
    print("Boundary"); boundary(cfg, overwrite); check_boundary(cfg)
    print("OSM reservoirs and solar plants"); osm_exclusions(cfg, overwrite)
    print("Habitat class table"); return habitat_code_table(cfg)
