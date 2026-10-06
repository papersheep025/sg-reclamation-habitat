"""Load config.yaml and resolve project paths."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
RAW = DATA / "raw"
INTERIM = DATA / "interim"
PROCESSED = DATA / "processed"
OUTPUTS = ROOT / "outputs"


def load(path=None):
    """Return config.yaml as a dict."""
    with open(path or ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def year_range(cfg, key="collect"):
    """Inclusive list of years for cfg['years'][key]."""
    start, end = cfg["years"][key]
    return list(range(start, end + 1))
