"""Step 3: attach place names to predictions and compare against ground truth.

Writes output/results.csv, output/results.geojson (WGS84) and prints a summary.
"""
import csv
import json
from collections import defaultdict

import numpy as np
from pyproj import Transformer
from shapely.geometry import Point, Polygon, mapping, shape
from shapely.ops import transform

from common import GT, OUT
from names import municipalities, names_in

TO_WGS = Transformer.from_crs(2056, 4326, always_xy=True).transform
THRESH = [250, 500, 1000, 2000]
# inlier thresholds for confidence classes (calibrated against ground truth, see summary)
CONF = [(40, "hoch"), (20, "mittel"), (0, "tief")]


def confidence(n):
    return next(c for t, c in CONF if n >= t)


def main():
    gt = json.loads((GT / "ground_truth.json").read_text())
    preds = json.loads((OUT / "predictions.json").read_text())
    rows, feats = [], []
    for k, p in sorted(preds.items()):
        g = gt[k]
        gt_poly = shape(g["footprint"]) if g.get("footprint") else None
        gt_c = np.array(g["center"])
        row = dict(image=g["image"], source=g["source"], year=g["year"], status=p["status"],
                   gt_e=gt_c[0], gt_n=gt_c[1], gt_side_m=round(np.sqrt(gt_poly.area)) if gt_poly else None)
        if p["status"] == "ok":
            poly = Polygon(p["corners"])
            c = np.array(p["center"])
            err = float(np.linalg.norm(c - gt_c))
            iou = poly.intersection(gt_poly).area / poly.union(gt_poly).area if gt_poly else None
            nm = names_in(poly)
            gde = municipalities(poly)
            gt_set = {n["name"] for n in names_in(gt_poly) if n["category"] in ("Siedlung", "Quartier")}
            pr_set = {n["name"] for n in nm if n["category"] in ("Siedlung", "Quartier")}
            name_j = len(gt_set & pr_set) / len(gt_set | pr_set) if gt_set | pr_set else None
            lon, lat = TO_WGS(*c)
            row.update(pred_e=round(c[0], 1), pred_n=round(c[1], 1), lon=round(lon, 6), lat=round(lat, 6),
                       error_m=round(err), iou=round(iou, 3) if iou is not None else None,
                       inliers=p["inliers"], confidence=confidence(p["inliers"]), side_m=round(np.sqrt(poly.area)),
                       municipalities="; ".join(n for n, _ in gde),
                       places="; ".join(n["name"] for n in nm[:12]),
                       name_jaccard=round(name_j, 3) if name_j is not None else None)
            feats.append(dict(type="Feature", geometry=mapping(transform(TO_WGS, poly)), properties=dict(
                row, names=nm, gt_footprint=mapping(transform(TO_WGS, gt_poly)) if gt_poly else None)))
        rows.append(row)

    cols = ["image", "source", "year", "status", "error_m", "iou", "inliers", "confidence", "pred_e", "pred_n", "lon", "lat",
            "gt_e", "gt_n", "side_m", "gt_side_m", "municipalities", "places", "name_jaccard"]
    with open(OUT / "results.csv", "w", newline="") as f:
        w = csv.DictWriter(f, cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    (OUT / "results.geojson").write_text(json.dumps(dict(type="FeatureCollection", features=feats)))

    # ---- summary
    n = len(rows)
    errs = np.array([r["error_m"] if r["status"] == "ok" else np.inf for r in rows])
    print(f"\n{n} images, localised {np.isfinite(errs).sum()} ({np.isfinite(errs).mean():.0%})")
    print("centre error <", ", ".join(f"{t} m: {(errs < t).mean():.0%}" for t in THRESH))
    ok = errs[np.isfinite(errs)]
    if len(ok):
        print(f"median error of localised: {np.median(ok):.0f} m")
    by = defaultdict(list)
    for r, e in zip(rows, errs):
        by[(r["source"], r["year"] // 10 * 10)].append(e)
    print("\nsource  decade   n  loc  <500m  <1km  median")
    for (s, d), es in sorted(by.items()):
        es = np.array(es)
        fin = es[np.isfinite(es)]
        print(f"{s:7s} {d}s {len(es):4d} {np.isfinite(es).mean():4.0%} {(es < 500).mean():5.0%} "
              f"{(es < 1000).mean():5.0%} {np.median(fin) if len(fin) else float('nan'):7.0f}")
    print("\nconfidence  n   correct(<1km)  median error   (inlier range)")
    for _, c in CONF:
        rs = [r for r in rows if r.get("confidence") == c]
        if rs:
            e = np.array([r["error_m"] for r in rs])
            print(f"{c:9s} {len(rs):4d}   {(e < 1000).mean():6.0%}   {np.median(e):8.0f} m"
                  f"    ({min(r['inliers'] for r in rs)}-{max(r['inliers'] for r in rs)})")
    bins = [10, 15, 20, 25, 30, 40, 60, 100, 10**6]
    inl = np.array([r["inliers"] for r in rows if r["status"] == "ok"])
    cor = np.array([r["error_m"] < 1000 for r in rows if r["status"] == "ok"])
    print("inliers bin -> precision:", ", ".join(
        f"{a}-{b}: {cor[(inl >= a) & (inl < b)].mean():.0%} (n={((inl >= a) & (inl < b)).sum()})"
        for a, b in zip(bins, bins[1:]) if ((inl >= a) & (inl < b)).any()))
    js = [r["name_jaccard"] for r in rows if r.get("name_jaccard") is not None and r["error_m"] < 1000]
    if js:
        print(f"\nplace-name overlap (Jaccard, settlements) for correct localisations: {np.mean(js):.2f}")


if __name__ == "__main__":
    main()
