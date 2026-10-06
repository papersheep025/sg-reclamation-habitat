# A Decade of Reclamation

What coastal habitats did Singapore's new land replace? Mapping land reclamation and
historical habitat conversion, 2016-2025. GE5219 Spatial Programming final project.

## Research questions

1. Where and how much new land was created along Singapore's coast each year from 2016 to 2025?
2. Which historical coastal habitats overlapped with the new land?
3. Did some habitats account for a larger share of reclaimed area than their share of the coastal zone?

## Workflow

Sentinel-2 annual composites -> water / land masks -> persistent water-to-land detection ->
reclamation polygons -> overlay with NParks habitat maps -> year x habitat x location table.

See `notebooks/README.md` for the step-by-step order.

## Setup

```bash
conda env create -f environment.yml      # or: python -m venv .venv && source .venv/bin/activate && pip install -e .
conda activate sg-reclaim
```

Put your own Google Cloud project ID in `config.yaml` under `project.ee_project`
(or `export EE_PROJECT=your-project-id`). The project must be registered for Earth Engine.

## Collecting the data

```bash
python -m reclaim.collect static        # habitat maps, land area, boundary; no account needed
python -m reclaim.collect gee --test    # one tile, one year; opens a browser to sign in the first time
python -m reclaim.collect gee           # Sentinel-2 2015-2026 and Landsat 8 2014-2016
```

Each command skips files that already exist, so it is safe to interrupt and rerun.
`notebooks/01_collect.ipynb` runs the same steps with a preview of the test tile.

## Layout

```
config.yaml            parameters: AOI, years, thresholds, dataset IDs
environment.yml        conda environment
data/
  raw/                 original downloads, read-only, not in git
    habitat/           NParks habitat maps 2010/11 and 2018
    boundary/          Singapore boundary (OSM)
    reference/         SLA total land area by year
    s2_mndwi/          annual Sentinel-2 MNDWI + observation counts (from Earth Engine)
    landsat_mndwi/     Landsat 8, 2014-2016 (pre-Sentinel-2 baseline)
    s1_vv/             optional Sentinel-1 cross-check
  interim/masks/       annual water / land masks, not in git
  processed/           reclamation polygons, overlay results (small, committed)
  data_manifest.csv    what was downloaded, when, by whom
notebooks/             01_collect ... 06_validate
src/reclaim/           shared functions
outputs/figures/       maps and charts for slides and report
outputs/tables/        result tables
validation/            sample points and interpretation labels
docs/                  method notes
```

## Data

| Data | Source | Licence |
|---|---|---|
| Sentinel-2 L1C, 2015-2026 | Earth Engine `COPERNICUS/S2_HARMONIZED` | Copernicus open data |
| Cloud Score+ | Earth Engine `GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED` | CC-BY 4.0 |
| Landsat 8 Collection 2 | Earth Engine `LANDSAT/LC08/C02/T1_TOA` | Public domain |
| Coastal and Marine Habitat Map 2010/2011, 2018 | data.gov.sg (NParks) | Singapore Open Data Licence |
| Total Land Area of Singapore | data.gov.sg (SLA) | Singapore Open Data Licence |
| National boundary | OpenStreetMap via Nominatim | ODbL |
| High-resolution historical imagery | Google Earth Pro, Esri Wayback | Viewing only, for validation; never stored here |

L1C is used instead of L2A because the Earth Engine L2A collection starts in March 2017 and
would leave 2015-2016 uncovered.

## Rules

- `data/raw/` is read-only. Never edit a downloaded file; regenerate it from `01_collect`.
- Rasters are not committed. Vectors and tables in `data/processed/` are.
- All areas are computed in EPSG:3414 (SVY21).
- Change parameters in `config.yaml`, not inside notebooks.
- No credentials or validation imagery in the repository.
