# Notebooks

Run in order. Each notebook reads `config.yaml` and writes only to the folder listed.

| Notebook | Does | Writes to |
|---|---|---|
| `01_collect.ipynb` | Download NParks habitat maps and boundary; export annual MNDWI and observation-count rasters from Earth Engine; fill `data_manifest.csv` | `data/raw/` |
| `02_classify.ipynb` | Threshold annual MNDWI into water / land masks; threshold table | `data/interim/masks/`, `outputs/tables/` |
| `03_detect.ipynb` | Persistent water-to-land detection, cleaning, vectorising with `year` attribute | `data/processed/` |
| `04_overlay.ipynb` | Intersect reclamation polygons with the pre-reclamation habitat map | `data/processed/`, `outputs/tables/` |
| `05_stats.ipynb` | Annual hectares, habitat composition, selection ratio (RQ3), hotspots, figures | `outputs/` |
| `06a_sar_crosscheck.ipynb` | Sentinel-1 cross-check: radar transition year per reclamation polygon, and radar confirmation of pixels removed by cleaning | `outputs/tables/` |
| `06_validate.ipynb` | Stratified sample, confusion matrix, area confidence intervals | `validation/`, `outputs/tables/` |

Reusable logic goes in `src/reclaim/`, not in notebook cells.
