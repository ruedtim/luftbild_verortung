"""Locate a single (new) aerial photo.

    python src/locate.py path/to/bild.tif [--out ergebnisse/] [--candidates 80] [--crop 0.05]

Prints centre coordinates (LV95 + WGS84), confidence, municipalities and place names,
and writes <name>.json plus a self-contained <name>.html map next to it (or into --out).
Only the pixels are used; any metadata in the file is ignored.
"""
import argparse
import base64
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image
from pyproj import Transformer
from shapely.geometry import Polygon

from evaluate import confidence
from localize import K, localize
from make_map import warp
from matching import Matcher
from names import municipalities, names_in
from retrieval import Embedder, build_index

Image.MAX_IMAGE_PIXELS = None
TO_WGS = Transformer.from_crs(2056, 4326, always_xy=True).transform
MAX_SIDE = 2048

HTML = """<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<style>html,body{{margin:0;height:100%;font:14px system-ui,sans-serif}}#m{{height:100%}}
.box{{position:absolute;z-index:999;top:10px;left:50px;background:#fff;padding:8px 12px;border-radius:6px;
box-shadow:0 1px 4px rgba(0,0,0,.3);max-width:360px}}</style></head><body><div id="m"></div>
<div class="box"><b>{title}</b><br>{summary}<br><label>Deckkraft <input type="range" id="op" min="0" max="100" value="85"></label></div>
<script>const R={data};
const m=L.map('m');const w=l=>`https://wmts.geo.admin.ch/1.0.0/${{l}}/default/current/3857/{{z}}/{{x}}/{{y}}.jpeg`;
const b={{'SWISSIMAGE':L.tileLayer(w('ch.swisstopo.swissimage'),{{maxZoom:19,attribution:'© swisstopo'}}).addTo(m),
'Landeskarte':L.tileLayer(w('ch.swisstopo.pixelkarte-farbe'),{{maxZoom:18,attribution:'© swisstopo'}})}};
L.control.layers(b).addTo(m);
const ov=L.imageOverlay(R.overlay,R.bounds,{{opacity:.85}}).addTo(m);
L.polygon(R.poly,{{color:'#ef4444',weight:2,fill:false}}).addTo(m);
R.names.forEach((n,i)=>L.circleMarker(n.ll,{{radius:4,color:'#f59e0b',weight:2}}).bindTooltip(n.n+' ('+n.t+')',{{permanent:i<8,direction:'top'}}).addTo(m));
m.fitBounds(R.bounds);document.getElementById('op').oninput=e=>ov.setOpacity(e.target.value/100);</script></body></html>"""


def load(path, crop):
    im = Image.open(path)
    if im.mode not in ("L", "RGB"):
        a = np.array(im).astype(np.float32)
        if a.ndim == 3:
            a = a[..., :3]
        lo, hi = np.percentile(a, [0.5, 99.5])
        im = Image.fromarray(np.clip((a - lo) / (hi - lo + 1e-6) * 255, 0, 255).astype(np.uint8))
    im.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    a = auto_crop(np.array(im))
    h, w = a.shape[:2]
    y0, x0 = int(h * crop), int(w * crop)
    return np.ascontiguousarray(a[y0:h - y0, x0:w - x0])


def auto_crop(a, dark=40, frac=0.25):
    """Cut a dark scan/film border: drop outer rows/columns that are mostly black."""
    g = a if a.ndim == 2 else a.mean(-1)
    d = g < dark
    cols, rows = d.mean(0), d.mean(1)

    def span(v):
        lim = len(v) // 4  # never cut more than a quarter per side
        lo = next((i for i in range(lim) if v[i] < frac), lim)
        hi = next((i for i in range(lim) if v[-1 - i] < frac), lim)
        return lo, len(v) - hi

    x0, x1 = span(cols)
    y0, y1 = span(rows)
    return a[y0:y1, x0:x1]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image", nargs="+")
    ap.add_argument("--out", default=None, help="output folder (default: next to the image)")
    ap.add_argument("--candidates", type=int, default=max(K, 80))
    ap.add_argument("--crop", type=float, default=0.05, help="border fraction to cut (film frame)")
    args = ap.parse_args()

    win, desc = build_index()
    emb, M = Embedder(), Matcher("lightglue", size=1024)
    for path in map(Path, args.image):
        out = Path(args.out) if args.out else path.parent
        out.mkdir(parents=True, exist_ok=True)
        q = load(path, args.crop)
        p = localize(q, M, emb, win, desc, k=args.candidates)
        if p["status"] != "ok":
            print(f"{path.name}: nicht verortet (beste Kandidaten nur {p['best_inliers']} Matches)")
            (out / f"{path.stem}.json").write_text(json.dumps(dict(image=path.name, status="not_localized")))
            continue
        poly = Polygon(p["corners"])
        lon, lat = TO_WGS(*p["center"])
        nm = names_in(poly)
        gde = [g for g, _ in municipalities(poly)]
        res = dict(image=path.name, status="ok", confidence=confidence(p["inliers"]), inliers=p["inliers"],
                   center_lv95=[round(v, 1) for v in p["center"]], center_wgs84=[round(lat, 6), round(lon, 6)],
                   footprint_lv95=[[round(x, 1), round(y, 1)] for x, y in p["corners"]],
                   side_length_m=round(np.sqrt(poly.area)), rotation_deg=90 * p["rot"],
                   municipalities=gde, places=nm, image_to_lv95_homography=p["T"])
        (out / f"{path.stem}.json").write_text(json.dumps(res, indent=1, ensure_ascii=False))

        rgba, bounds = warp(q, p["T"], p["corners"])
        buf = io.BytesIO()
        Image.fromarray(rgba).save(buf, "PNG", optimize=True)
        data = dict(overlay="data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(), bounds=bounds,
                    poly=[list(reversed(TO_WGS(*c))) for c in p["corners"]],
                    names=[dict(n=x["name"], t=x["type"], ll=list(reversed(TO_WGS(x["e"], x["n"])))) for x in nm])
        summary = (f"Zentrum LV95 {res['center_lv95'][0]:.0f} / {res['center_lv95'][1]:.0f} · "
                   f"Konfidenz {res['confidence']} ({p['inliers']} Matches)<br>Gemeinden: {', '.join(gde)}")
        (out / f"{path.stem}.html").write_text(HTML.format(title=path.name, summary=summary, data=json.dumps(data)))

        print(f"\n{path.name}")
        print(f"  Konfidenz:  {res['confidence']} ({p['inliers']} Matches)")
        print(f"  Zentrum:    LV95 {res['center_lv95'][0]:.0f} / {res['center_lv95'][1]:.0f}"
              f"   WGS84 {lat:.5f}, {lon:.5f}")
        print(f"  Grösse:     ≈ {res['side_length_m']} m Seitenlänge, gedreht {res['rotation_deg']}°")
        print(f"  Gemeinden:  {', '.join(gde)}")
        print(f"  Orte:       {', '.join(n['name'] for n in nm[:15])}")
        print(f"  -> {out / (path.stem + '.json')}, {out / (path.stem + '.html')}")


if __name__ == "__main__":
    main()
