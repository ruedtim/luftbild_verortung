"""Step 1: collect historical nadir aerial photos of Kanton Zürich.

Sources
  * swisstopo LUBIS (black/white + colour, 1930-2010), COGs with overviews
  * GIS-ZH "Historische Luftbilder" WMS (1981-2000), full-res TIFF scans

The query images are written as plain PNGs with random names and *no* metadata
(no georef, no EXIF, no date). Everything that identifies the location goes to
data/gt/ground_truth.json, which the localizer never reads.
"""
import argparse
import json
import random
import re
import tempfile
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import rasterio
from PIL import Image
from rasterio.enums import Resampling
from shapely.geometry import Point, box, shape
from tqdm import tqdm

from common import API, GT, IMAGES, S, canton_zh

MAX_SIDE = 2048       # stored query image size (longest side)
CROP = 0.05           # crop film border / fiducials on each side

LUBIS_LAYERS = ["ch.swisstopo.lubis-luftbilder_schwarzweiss",
                "ch.swisstopo.lubis-luftbilder_farbe"]
ZH_WMS = "https://wms.zh.ch/HistLuftZHWMS"
ZH_YEARS = [1981, 1985, 1986, 1987, 1988, 1989, 1991, 1992, 1993, 1994,
            1995, 1996, 1997, 1998, 1999, 2000]


# ---------------------------------------------------------------- LUBIS
def lubis_cell(layer, cell, pages=4):
    out = []
    for p in range(pages):
        r = S.get(f"{API}/identify", timeout=60, params=dict(
            geometryType="esriGeometryEnvelope", geometry=",".join(map(str, cell)),
            sr=2056, layers=f"all:{layer}", tolerance=0, returnGeometry="true",
            geometryFormat="geojson", limit=50, offset=50 * p))
        if r.status_code != 200:
            break
        res = r.json().get("results", [])
        out += res
        if len(res) < 50:
            break
    return out


def lubis_candidates(zh, step=5000):
    minx, miny, maxx, maxy = zh.bounds
    cells = [(x, y, x + step, y + step)
             for x in range(int(minx), int(maxx), step)
             for y in range(int(miny), int(maxy), step)
             if zh.intersects(box(x, y, x + step, y + step))]
    jobs = [(l, c) for l in LUBIS_LAYERS for c in cells]
    feats = {}
    with ThreadPoolExecutor(8) as ex:
        for res in tqdm(ex.map(lambda j: lubis_cell(*j), jobs), total=len(jobs), desc="LUBIS query"):
            for f in res:
                a = f.get("properties") or f.get("attributes")
                if not a or not a.get("filename"):
                    continue
                feats[f["id"]] = f
    cands = []
    for fid, f in feats.items():
        a = f.get("properties") or f.get("attributes")
        c = Point(a["e"], a["n"])
        if not zh.contains(c):
            continue
        fp = None
        g = f.get("geometry")
        geoms = g.get("geometries", [g]) if g else []
        for gg in geoms:
            if gg["type"] in ("Polygon", "MultiPolygon"):
                fp = shape(gg)
        cands.append(dict(source="lubis", id=fid, url=a["filename"],
                          year=int(a["bgdi_flugjahr"]), date=a.get("flugdatum"),
                          film=a.get("film_type"), altitude=a.get("flughoehe"),
                          center=[a["e"], a["n"]],
                          footprint=fp.__geo_interface__ if fp else None))
    return cands


def fetch_lubis(c):
    with rasterio.open("/vsicurl/" + c["url"]) as d:
        f = max(1, max(d.width, d.height) // MAX_SIDE)
        f = 1 << (f.bit_length() - 1)  # power of two -> uses an overview level
        h, w = d.height // f, d.width // f
        arr = d.read(out_shape=(d.count, h, w), resampling=Resampling.average)
        T = d.transform * d.transform.scale(d.width / w, d.height / h)
    arr = np.moveaxis(arr, 0, -1)
    if arr.shape[2] == 1:
        arr = arr[..., 0]
    elif arr.shape[2] > 3:
        arr = arr[..., :3]
    y0, x0 = int(h * CROP), int(w * CROP)
    arr = arr[y0:h - y0, x0:w - x0]
    T = T * T.translation(x0, y0)
    hh, ww = arr.shape[:2]
    # nominal georef of the 4 image corners (TL, TR, BR, BL) in LV95
    corners = [list(T * (0, 0)), list(T * (ww, 0)), list(T * (ww, hh)), list(T * (0, hh))]
    return arr, corners


# ---------------------------------------------------------------- GIS-ZH
FEAT_RE = re.compile(r"<gml:coordinates>([\d.]+),([\d.]+) ([\d.]+),([\d.]+)</gml:coordinates>(.*?)</\w+_feature>", re.S)


def zh_year(year, zh, n_probe=40, seed=0):
    rnd = random.Random(seed + year)
    minx, miny, maxx, maxy = zh.bounds
    found = {}
    for _ in range(n_probe):
        while True:
            x, y = rnd.uniform(minx, maxx), rnd.uniform(miny, maxy)
            if zh.contains(Point(x, y)):
                break
        bb = (x - 1000, y - 1000, x + 1000, y + 1000)
        r = S.get(ZH_WMS, timeout=60, params=dict(
            SERVICE="WMS", VERSION="1.3.0", REQUEST="GetFeatureInfo",
            LAYERS=f"flugjahr_{year}", QUERY_LAYERS=f"flugjahr_{year}", STYLES="",
            CRS="EPSG:2056", BBOX=",".join(map(str, bb)), WIDTH=101, HEIGHT=101,
            I=50, J=50, INFO_FORMAT="application/vnd.ogc.gml", FEATURE_COUNT=5))
        for m in FEAT_RE.finditer(r.text):
            x0, y0, x1, y1 = map(float, m.groups()[:4])
            body = m.group(5)
            get = lambda k: (re.search(fr"<{k}>(.*?)</{k}>", body) or [None, None])[1]
            url = get("Datei")
            if not url or not url.endswith(".tif"):
                continue
            cx, cy = float(get("Center_X")), float(get("Center_Y"))
            if cx < 2e6:
                cx, cy = cx + 2e6, cy + 1e6
            found[url] = dict(source="gis-zh", id=url.rsplit("/", 1)[1][:-4], url=url,
                              year=year, date=get("Flugdatum"), film=None,
                              altitude=float(get("Flughöhe") or 0),
                              kappa=get("Kappa"), focal=get("focallen"),
                              center=[cx, cy],
                              footprint=box(x0, y0, x1, y1).__geo_interface__)
    return list(found.values())


def fetch_zh(c):
    with tempfile.NamedTemporaryFile(suffix=".tif") as tmp:
        with S.get(c["url"], stream=True, timeout=600) as r:
            r.raise_for_status()
            for chunk in r.iter_content(1 << 20):
                tmp.write(chunk)
        tmp.flush()
        with rasterio.open(tmp.name) as d:
            f = max(1, max(d.width, d.height) // MAX_SIDE)
            h, w = d.height // f, d.width // f
            arr = d.read(out_shape=(d.count, h, w), resampling=Resampling.average)
    arr = np.moveaxis(arr, 0, -1)
    arr = arr[..., 0] if arr.shape[2] == 1 else arr[..., :3]
    y0, x0 = int(h * CROP), int(w * CROP)
    return arr[y0:h - y0, x0:w - x0], None


# ---------------------------------------------------------------- main
def stratified(cands, n, key=lambda c: c["year"] // 10 * 10, seed=42):
    rnd = random.Random(seed)
    groups = defaultdict(list)
    for c in cands:
        groups[key(c)].append(c)
    for g in groups.values():
        rnd.shuffle(g)
    picked, keys = [], sorted(groups)
    while len(picked) < n and any(groups.values()):
        for k in keys:
            if groups[k] and len(picked) < n:
                picked.append(groups[k].pop())
    return picked


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-lubis", type=int, default=200)
    ap.add_argument("--n-zh", type=int, default=40)
    args = ap.parse_args()
    zh = canton_zh()

    gt_file = GT / "ground_truth.json"
    gt = json.loads(gt_file.read_text()) if gt_file.exists() else {}
    have = {v["id"] for v in gt.values()}

    lub = lubis_candidates(zh)
    print(f"LUBIS candidates in canton: {len(lub)}")
    by_dec = defaultdict(int)
    for c in lub:
        by_dec[c["year"] // 10 * 10] += 1
    print("  per decade:", dict(sorted(by_dec.items())))
    picks = [("lubis", c) for c in stratified([c for c in lub if c["id"] not in have], args.n_lubis)]

    if args.n_zh:
        zhc = []
        for y in tqdm(ZH_YEARS, desc="GIS-ZH query"):
            zhc += zh_year(y, zh, n_probe=8)
        zhc = [c for c in zhc if zh.contains(Point(c["center"]))]
        print(f"GIS-ZH candidates: {len(zhc)}")
        picks += [("zh", c) for c in stratified([c for c in zhc if c["id"] not in have],
                                                args.n_zh, key=lambda c: c["year"])]

    def work(item):
        kind, c = item
        try:
            arr, corners = (fetch_lubis if kind == "lubis" else fetch_zh)(c)
        except Exception as e:  # noqa: BLE001
            return None, f"{c['id']}: {e}"
        name = uuid.uuid4().hex[:12]
        # PIL from a bare numpy array -> PNG without any metadata chunks
        Image.fromarray(arr).save(IMAGES / f"{name}.png", optimize=True)
        rec = dict(c, image=f"{name}.png", size=[arr.shape[1], arr.shape[0]],
                   corners=corners)
        return (name, rec), None

    with ThreadPoolExecutor(6) as ex:
        for res, err in tqdm(ex.map(work, picks), total=len(picks), desc="download"):
            if err:
                print("  FAIL", err)
                continue
            gt[res[0]] = res[1]
            gt_file.write_text(json.dumps(gt, indent=1))
    print(f"ground truth records: {len(gt)}")


if __name__ == "__main__":
    main()
