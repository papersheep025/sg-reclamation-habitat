"""Keep data/data_manifest.csv in sync with what has been downloaded."""
import csv
import getpass
from datetime import date

from .config import DATA, ROOT

PATH = DATA / "data_manifest.csv"
FIELDS = ["dataset", "source", "dataset_id", "year", "file", "n_images",
          "downloaded_on", "downloaded_by", "licence", "notes"]


def record(file, **row):
    """Add or replace the manifest row for `file` (path inside the project)."""
    rel = str(file.relative_to(ROOT)) if hasattr(file, "relative_to") else str(file)
    rows = []
    if PATH.exists():
        with open(PATH, newline="", encoding="utf-8") as f:
            rows = [r for r in csv.DictReader(f) if r.get("file") != rel]
    new = {k: "" for k in FIELDS}
    new.update(row, file=rel, downloaded_on=date.today().isoformat(),
               downloaded_by=getpass.getuser())
    rows.append(new)
    with open(PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
