"""Step 4: interactive Leaflet map (output/map/index.html), self-contained.

Each localised photo is warped with its estimated image->LV95 homography into a
north-up Web-Mercator WebP and laid over a SWISSIMAGE base image. For every photo a
modern SWISSIMAGE crop on the same grid is written too, so the opacity slider
cross-fades between then and now. Data, thumbnails and the Leaflet CSS are inlined;
the only other files are the base image and the per-photo WebPs.
"""
import base64
import io
import json
import shutil

import cv2
import numpy as np
from PIL import Image
from pyproj import Transformer

from common import CACHE, GT, IMAGES, OUT
from reference import fetch, mosaic, mosaic_bounds

MAP = OUT / "map"
OV = MAP / "overlays"
LV_TO_WGS = Transformer.from_crs(2056, 4326, always_xy=True)
LV_TO_MERC = Transformer.from_crs(2056, 3857, always_xy=True)
MERC_TO_LV = Transformer.from_crs(3857, 2056, always_xy=True)
MERC_TO_WGS = Transformer.from_crs(3857, 4326, always_xy=True)
OV_PX = 900
BASE_RES = 10.0  # m per pixel of the base image (Web Mercator metres)


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


def base_image():
    """Canton-wide SWISSIMAGE mosaic re-projected to Web Mercator at BASE_RES."""
    m, (E0, N1), res = mosaic()
    x0, y0, x1, y1 = mosaic_bounds()
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    mx, _ = LV_TO_MERC.transform(*np.array(corners).T)
    W = int((mx.max() - mx.min()) / BASE_RES)
    e, n, bounds = merc_grid(corners, W)
    u, v = (e - E0) / res, (N1 - n) / res
    a = cv2.remap(m, u.astype(np.float32), v.astype(np.float32), cv2.INTER_AREA)
    Image.fromarray(a).save(MAP / "base.jpg", quality=66, optimize=True, progressive=True)
    return bounds


def data_uri(im, q=70):
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=q)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def main():
    gt = json.loads((GT / "ground_truth.json").read_text())
    preds = json.loads((OUT / "predictions.json").read_text())
    res = {f["properties"]["image"]: f for f in json.loads((OUT / "results.geojson").read_text())["features"]}
    shutil.rmtree(MAP, ignore_errors=True)
    OV.mkdir(parents=True)
    base_bounds = base_image()
    items = []
    for k, p in sorted(preds.items(), key=lambda kv: gt[kv[0]]["year"]):
        g = gt[k]
        if p["status"] == "ok" and g["image"] not in res:
            continue  # prediction newer than results.geojson (run still in progress)
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
            webp(rgba, OV / f"{k}.webp")
            webp(warp_today(p["corners"], rgba[..., 3]), OV / f"{k}_heute.webp")
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
    html = html.replace("/*DATA*/", "window.DATA = " + json.dumps(items) + ";\nwindow.BASE = "
                        + json.dumps(dict(url="base.jpg", bounds=base_bounds)) + ";")
    (MAP / "index.html").write_text(html)  # published: the artifact host adds the document skeleton
    (MAP / "vorschau.html").write_text(  # local preview with its own skeleton
        "<!doctype html><html lang='de'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'></head><body>" + html + "</body></html>")
    print(f"map: {MAP / 'index.html'}  ({sum(i['status'] == 'ok' for i in items)} overlays)")


if __name__ == "__main__":
    main()
