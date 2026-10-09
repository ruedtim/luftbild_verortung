"""Shared paths, constants and small helpers."""
import json
from pathlib import Path

import requests
from shapely.geometry import shape

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
IMAGES = DATA / "images"          # stripped query images (no metadata, random names)
GT = DATA / "gt"                  # ground truth, never read by the localizer
CACHE = DATA / "cache"
OUT = ROOT / "output"
for p in (IMAGES, GT, CACHE, OUT):
    p.mkdir(parents=True, exist_ok=True)

API = "https://api3.geo.admin.ch/rest/services/api/MapServer"
WMS_CH = "https://wms.geo.admin.ch/"
S = requests.Session()
S.headers["User-Agent"] = "histluft-poc/0.1 (research prototype)"


def canton_zh():
    """Kanton Zürich boundary polygon (LV95)."""
    f = CACHE / "kanton_zh.geojson"
    if not f.exists():
        r = S.get(f"{API}/ch.swisstopo.swissboundaries3d-kanton-flaeche.fill/1",
                  params={"sr": 2056, "geometryFormat": "geojson"}, timeout=60)
        r.raise_for_status()
        f.write_text(json.dumps(r.json()["feature"]["geometry"]))
    return shape(json.loads(f.read_text()))
