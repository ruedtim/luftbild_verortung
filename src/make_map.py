"""Step 4: interactive Leaflet map (output/map/index.html), self-contained.

Each localised photo is warped with its estimated image->LV95 homography into a
north-up Web-Mercator WebP and laid over a SWISSIMAGE base image. For every photo a
modern SWISSIMAGE crop on the same grid is written too, so the opacity slider
cross-fades between then and now. Data, thumbnails and the Leaflet CSS are inlined;
the only other files are the base image and the per-photo WebPs.

    python make_map.py                            # full map -> output/map (index.html, vorschau.html)
    python make_map.py --demo 40 --out ../docs    # small public demo for GitHub Pages

--demo N keeps only about N random photos (stratified by source and decade, fixed seed) to keep
the page small; the header statistics still describe all photos. It writes an index.html with a
complete page skeleton (what Pages serves), leaves out the Google web fonts and adds .nojekyll.
"""
import argparse
import base64
import io
import json
import random
import re
import shutil
import statistics
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from pyproj import Transformer

from common import CACHE, GT, IMAGES, OUT
from reference import fetch, mosaic, mosaic_bounds

MAP = OUT / "map"  # default output folder
LV_TO_WGS = Transformer.from_crs(2056, 4326, always_xy=True)
LV_TO_MERC = Transformer.from_crs(2056, 3857, always_xy=True)
MERC_TO_LV = Transformer.from_crs(3857, 2056, always_xy=True)
MERC_TO_WGS = Transformer.from_crs(3857, 4326, always_xy=True)
OV_PX = 900
BASE_RES = 10.0  # m per pixel of the base image (Web Mercator metres)
DEMO_SEED = 7
REPO_URL = "https://github.com/ruedtim/luftbild_verortung"
SKELETON = ("<!doctype html><html lang='de'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'></head><body>")


def merc_grid(corners, px):
    mx, my = LV_TO_MERC.transform(*np.array(corners).T)
    x0, x1, y0, y1 = mx.min(), mx.max(), my.min(), my.max()
    s = max(x1 - x0, y1 - y0) / px
    W, H = int((x1 - x0) / s), int((y1 - y0) / s)
    gx, gy = np.meshgrid(x0 + (np.arange(W) + .5) * s, y1 - (np.arange(H) + .5) * s)
    e, n = MERC_TO_LV.transform(gx.ravel(), gy.ravel())
    (lo0, la0), (lo1, la1) = MERC_TO_WGS.transform(x0, y0), MERC_TO_WGS.transform(x1, y1)
    return e.reshape(H, W), n.reshape(H, W), [[la0, lo0], [la1, lo1]]


def remap(img, u, v):
    f = max(1, int(img.shape[1] / (u.shape[1] * 1.5)))
    small = cv2.resize(img, (img.shape[1] // f, img.shape[0] // f), interpolation=cv2.INTER_AREA)
    rgb = small if small.ndim == 3 else np.stack([small] * 3, -1)
    return cv2.remap(rgb, (u / f).astype(np.float32), (v / f).astype(np.float32), cv2.INTER_LINEAR,
                     borderMode=cv2.BORDER_CONSTANT)


def warp(img, T, corners, px=OV_PX):
    """Historical photo -> RGBA on a north-up Web-Mercator grid + Leaflet bounds."""
    e, n, bounds = merc_grid(corners, px)
    p = np.stack([e, n, np.ones_like(e)], -1) @ np.linalg.inv(np.array(T)).T
    u, v = p[..., 0] / p[..., 2], p[..., 1] / p[..., 2]
    inside = (u >= 0) & (v >= 0) & (u < img.shape[1]) & (v < img.shape[0])
    return np.dstack([remap(img, u, v), (inside * 255).astype(np.uint8)]), bounds


def warp_today(corners, alpha, px=OV_PX):
    """Modern SWISSIMAGE on exactly the same grid/mask as the warped photo."""
    e, n, _ = merc_grid(corners, px)
    c = np.array(corners)
    pad = (c.max(0) - c.min(0)).max() * 0.05
    bb = (c[:, 0].min() - pad, c[:, 1].min() - pad, c[:, 0].max() + pad, c[:, 1].max() + pad)
    ref = fetch(bb, 1024, 1024)
    u = (e - bb[0]) / (bb[2] - bb[0]) * 1024
    v = (bb[3] - n) / (bb[3] - bb[1]) * 1024
    return np.dstack([remap(ref, u, v), alpha])


def webp(a, f, q=72):
    Image.fromarray(a).save(f, "WEBP", quality=q, method=6)


def base_image(out):
    """Canton-wide SWISSIMAGE mosaic re-projected to Web Mercator at BASE_RES."""
    m, (E0, N1), res = mosaic()
    x0, y0, x1, y1 = mosaic_bounds()
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    mx, _ = LV_TO_MERC.transform(*np.array(corners).T)
    W = int((mx.max() - mx.min()) / BASE_RES)
    e, n, bounds = merc_grid(corners, W)
    u, v = (e - E0) / res, (N1 - n) / res
    a = cv2.remap(m, u.astype(np.float32), v.astype(np.float32), cv2.INTER_AREA)
    Image.fromarray(a).save(out / "base.jpg", quality=66, optimize=True, progressive=True)
    return bounds


def data_uri(im, q=70):
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=q)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def demo_sample(ids, gt, n):
    """About n random ids, stratified by (source, decade) so that every group is represented."""
    groups = defaultdict(list)
    for k in sorted(ids):
        groups[(gt[k]["source"], gt[k]["year"] // 10)].append(k)
    rnd = random.Random(DEMO_SEED)
    return {k for v in groups.values() for k in rnd.sample(v, min(len(v), max(1, round(n * len(v) / len(ids)))))}


def run_stats(shown, preds, gt, res):
    """Headline numbers over all shown photos (what the map header displays)."""
    errs = [res[gt[k]["image"]]["properties"]["error_m"] for k in shown if preds[k]["status"] == "ok"]
    return dict(n=len(shown), ok=len(errs), median=round(statistics.median(errs)) if errs else None,
                wrong=sum(e >= 1000 for e in errs))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--demo", type=int, default=0, metavar="N", help="public demo with about N random photos")
    ap.add_argument("--out", default=str(MAP), help="output folder (default: output/map)")
    args = ap.parse_args()
    out = Path(args.out).resolve()
    ov = out / "overlays"

    gt = json.loads((GT / "ground_truth.json").read_text())
    preds = json.loads((OUT / "predictions.json").read_text())
    res = {f["properties"]["image"]: f for f in json.loads((OUT / "results.geojson").read_text())["features"]}
    # a prediction newer than results.geojson (run still in progress) is skipped
    shown = [k for k, p in preds.items() if p["status"] != "ok" or gt[k]["image"] in res]
    stats = run_stats(shown, preds, gt, res)
    keep = demo_sample(shown, gt, args.demo) if args.demo else set(shown)
    shutil.rmtree(ov, ignore_errors=True)
    ov.mkdir(parents=True)
    base_bounds = base_image(out)
    items = []
    for k in sorted((k for k in shown if k in keep), key=lambda k: gt[k]["year"]):
        p, g = preds[k], gt[k]
        img = np.array(Image.open(IMAGES / g["image"]))
        th = Image.fromarray(img).convert("L")
        th.thumbnail((160, 160))
        it = dict(id=k, year=g["year"], date=g["date"], source=g["source"], src_id=g["id"],
                  status=p["status"], thumb=data_uri(th))
        if g.get("footprint"):
            fp = g["footprint"]
            ring = fp["coordinates"][0] if fp["type"] == "Polygon" else fp["coordinates"][0][0]
            it["gt_poly"] = [list(reversed(LV_TO_WGS.transform(*c))) for c in ring]
        if p["status"] == "ok":
            rgba, bounds = warp(img, p["T"], p["corners"])
            webp(rgba, ov / f"{k}.webp")
            webp(warp_today(p["corners"], rgba[..., 3]), ov / f"{k}_heute.webp")
            props = res[g["image"]]["properties"]
            it.update(bounds=bounds, poly=[list(reversed(LV_TO_WGS.transform(*c))) for c in p["corners"]],
                      center=[props["lat"], props["lon"]], error_m=props["error_m"], iou=props["iou"],
                      conf=props["confidence"], inliers=p["inliers"], side_m=props["side_m"],
                      lv95=[props["pred_e"], props["pred_n"]], municipalities=props["municipalities"],
                      names=[dict(n=x["name"], t=x["type"], c=x["category"],
                                  ll=list(reversed(LV_TO_WGS.transform(x["e"], x["n"]))),
                                  lv=[round(x["e"]), round(x["n"])]) for x in props["names"]])
        items.append(it)
    html = (OUT.parent / "src" / "map_template.html").read_text()
    html = html.replace("/*LEAFLET_CSS*/", (CACHE / "leaflet.min.css").read_text())
    data = ("window.DATA = " + json.dumps(items) + ";\nwindow.BASE = "
            + json.dumps(dict(url="base.jpg", bounds=base_bounds)) + ";")
    if args.demo:
        note = (f"Online-Demo: zufällige Auswahl von {len(items)} der {stats['n']} Testbilder; die Kennzahlen oben "
                f"gelten für alle {stats['n']}. <a href='{REPO_URL}'>Code und Dokumentation</a>")
        data += f"\nwindow.STATS = {json.dumps(stats)};\nwindow.NOTE = {json.dumps(note)};"
        html = re.sub(r"<link[^>]*fonts\.g[^>]*>\n?", "", html)  # public page: no third-party web fonts
        html = html.replace("/*DATA*/", data)
        (out / "index.html").write_text(SKELETON + html + "</body></html>")  # what GitHub Pages serves
        (out / ".nojekyll").touch()
    else:
        html = html.replace("/*DATA*/", data)
        (out / "index.html").write_text(html)  # published: the artifact host adds the document skeleton
        (out / "vorschau.html").write_text(SKELETON + html + "</body></html>")  # local preview with its own skeleton
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"map: {out / 'index.html'}  ({sum(i['status'] == 'ok' for i in items)} overlays, "
          f"{len(items)} photos, {size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
