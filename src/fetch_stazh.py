"""Extra test set: 1981 aerial photos from the StAZH / GIS-ZH list (xlsx).

Picks one random image per flight line (excluding images already in
data/gt/ground_truth.json), downsizes like fetch_images.fetch_zh and writes
metadata-free PNGs to data/images_stazh/. Ground truth -> data/gt/stazh_1981.json.
"""
import argparse
import json
import random
import uuid
from concurrent.futures import ThreadPoolExecutor

import openpyxl
from PIL import Image
from shapely.geometry import box
from tqdm import tqdm

from common import DATA, GT
from fetch_images import fetch_zh

BASE = "https://maps.zh.ch/download/luftbilder/1981_Kanton_Zuerich/"
OUT = DATA / "images_stazh"
OUT.mkdir(exist_ok=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx")
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    ws = openpyxl.load_workbook(args.xlsx, read_only=True)["luftbilder1981"]
    rows = list(ws.iter_rows(values_only=True))
    hdr = rows[0]
    rows = [dict(zip(hdr, r)) for r in rows[1:] if r[0] is not None]

    known = {v["id"] for v in json.loads((GT / "ground_truth.json").read_text()).values()}
    lines = {}
    for r in rows:
        if r["dateiname"][:-4] not in known:
            lines.setdefault(r["streifennr"], []).append(r)
    rnd = random.Random(args.seed)
    picks = [rnd.choice(v) for _, v in sorted(lines.items())]
    rnd.shuffle(picks)
    picks = picks[:args.n]

    def work(r):
        url = BASE + r["dateiname"]
        try:
            arr, _ = fetch_zh(dict(url=url))
        except Exception as e:  # noqa: BLE001
            return None, f"{r['dateiname']}: {e}"
        name = uuid.uuid4().hex[:12]
        Image.fromarray(arr).save(OUT / f"{name}.png", optimize=True)
        rec = dict(source="stazh-1981", id=r["dateiname"][:-4], url=url, year=1981,
                   date=r["flugdatum"], altitude=r["flghoehe_m"], line=r["streifennr"],
                   center=[r["center_x"] + 2e6, r["center_y"] + 1e6],
                   footprint=box(r["st_xmin"], r["st_ymin"], r["st_xmax"], r["st_ymax"]).__geo_interface__,
                   image=f"{name}.png", size=[arr.shape[1], arr.shape[0]])
        return (name, rec), None

    gt_file = GT / "stazh_1981.json"
    gt = json.loads(gt_file.read_text()) if gt_file.exists() else {}
    with ThreadPoolExecutor(3) as ex:
        for res, err in tqdm(ex.map(work, picks), total=len(picks), desc="download"):
            if err:
                print("  FAIL", err)
                continue
            gt[res[0]] = res[1]
            gt_file.write_text(json.dumps(gt, indent=1))
    print(f"{len(gt)} images in {OUT}")


if __name__ == "__main__":
    main()
