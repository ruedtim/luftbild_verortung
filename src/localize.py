"""Step 2: blind localisation of the stripped aerial photos.

Reads ONLY data/images/*.png (no metadata). For each image:
  1. DINOv2 retrieval over multi-scale SWISSIMAGE windows -> top-K candidates (+ rotation)
  2. LightGlue (SuperPoint) matching against each candidate, MAGSAC homography
  3. refinement: re-fetch the reference exactly around the predicted footprint, re-match
Output: output/predictions.json with image->LV95 homography, footprint corners, centre.
"""
import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np
from PIL import Image
from tqdm import tqdm

from common import IMAGES, OUT
from matching import Matcher, homography, plausible
from reference import fetch
from retrieval import Embedder, build_index, query

K = 50            # candidates to verify
EXPAND = 1.6      # reference context around a candidate window
REF_PX = 1024
MIN_INLIERS = 10  # below this nothing is reported (confidence is graded later)
GOOD_ENOUGH = 80   # stop verifying candidates early
POOL = ThreadPoolExecutor(8)
SCREEN_PX = 1024   # matching resolution while screening candidates
FINE_PX = 1536     # second, finer screening pass (only if the first finds < FINE_BELOW inliers)
FINE_K = 30
FINE_BELOW = 20


def rot_mat(r, w, h):
    """Pixel transform original -> np.rot90(img, r) coordinates."""
    return {0: np.eye(3),
            1: np.array([[0, 1, 0], [-1, 0, w], [0, 0, 1.]]),
            2: np.array([[-1, 0, w], [0, -1, h], [0, 0, 1.]]),
            3: np.array([[0, -1, h], [1, 0, 0], [0, 0, 1.]])}[r]


def ref_affine(bb, w, h):
    x0, y0, x1, y1 = bb
    return np.array([[(x1 - x0) / w, 0, x0], [0, -(y1 - y0) / h, y1], [0, 0, 1.]])


def apply(T, pts):
    p = np.c_[pts, np.ones(len(pts))] @ T.T
    return p[:, :2] / p[:, 2:]


def try_match(M, q, r, bb, px=REF_PX):
    qr = np.ascontiguousarray(np.rot90(q, r))
    ref = fetch(bb, px, px)
    k0, k1, _ = M.match(qr, ref)
    H, n, _ = homography(k0, k1)
    if H is None or not plausible(H, qr.shape[1], qr.shape[0], px, px):
        return None, 0
    h, w = q.shape[:2]
    T = ref_affine(bb, px, px) @ H @ rot_mat(r, w, h)   # original px -> LV95
    return T, n


def screen(M, q, bb, px=REF_PX, size=SCREEN_PX, qsize=None):
    """Match all 4 rotations against the reference bbox; best (T, inliers, rot).

    The matcher only copes with ~1.5x scale difference. The reference context is EXPAND x
    the retrieval window, so a query of about window size should be matched at
    qsize ~ size / EXPAND to get a similar pixel scale (see the fine pass in localize()).
    """
    h, w = q.shape[:2]
    ref = fetch(bb, px, px)
    best = (None, 0, 0)
    for r, (k0, k1) in enumerate(M.match_rotations(q, ref, size=size, qsize=qsize)):
        H, n, _ = homography(k0, k1)
        rw, rh = (w, h) if r % 2 == 0 else (h, w)
        if H is not None and n > best[1] and plausible(H, rw, rh, px, px):
            best = (ref_affine(bb, px, px) @ H @ rot_mat(r, w, h), n, r)
    return best


def localize(q, M, emb, win, desc, k=K):
    h, w = q.shape[:2]
    cands = query(q, win, desc, emb, k=k)
    bbs = []
    for _, i, _ in cands:
        x0, y0, x1, y1 = win[i, 3:7]
        cx, cy, s = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) * EXPAND / 2
        bbs.append((cx - s, cy - s, cx + s, cy + s))
    list(POOL.map(lambda bb: fetch(bb, REF_PX, REF_PX), bbs))  # prefetch references in parallel
    best = dict(n=0)
    for rank, ((sc, i, _), bb) in enumerate(zip(cands, bbs)):
        T, n, r = screen(M, q, bb)
        if n > best["n"]:
            best = dict(n=n, T=T, rot=r, rank=rank, retrieval_score=sc)
        if best["n"] >= GOOD_ENOUGH:
            break
    if best["n"] < FINE_BELOW:
        # fine pass: reference at 1536 px, query scaled to the window size (similar pixel scale)
        list(POOL.map(lambda bb: fetch(bb, FINE_PX, FINE_PX), bbs[:FINE_K]))
        for rank, ((sc, _, _), bb) in enumerate(zip(cands[:FINE_K], bbs)):
            T, n, r = screen(M, q, bb, px=FINE_PX, size=FINE_PX, qsize=round(FINE_PX / EXPAND))
            if n > best["n"]:
                best = dict(n=n, T=T, rot=r, rank=rank, retrieval_score=sc)
            if best["n"] >= GOOD_ENOUGH:
                break
    if best["n"] < MIN_INLIERS:
        return dict(status="not_localized", best_inliers=best["n"])
    # refinement around predicted footprint (2 iterations)
    for _ in range(2):
        c = apply(best["T"], np.array([[0, 0], [w, 0], [w, h], [0, h]], float))
        cx, cy = c.mean(0)
        s = (c.max(0) - c.min(0)).max() * 0.6
        T, n = try_match(M, q, best["rot"], (cx - s, cy - s, cx + s, cy + s), px=1280)
        if T is not None and n >= 0.8 * best["n"]:
            best.update(T=T, n=n)
    T = best["T"]
    corners = apply(T, np.array([[0, 0], [w, 0], [w, h], [0, h]], float))
    centre = apply(T, np.array([[w / 2, h / 2]]))[0]
    return dict(status="ok", inliers=best["n"], rot=best["rot"], cand_rank=best["rank"],
                retrieval_score=best["retrieval_score"], T=T.tolist(),
                corners=corners.tolist(), center=centre.tolist(), size=[w, h])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--redo", action="store_true")
    ap.add_argument("--retry", type=int, default=0,
                    help="re-run images without a confident result using this many candidates")
    args = ap.parse_args()
    out_f = OUT / "predictions.json"
    preds = {} if args.redo or not out_f.exists() else json.loads(out_f.read_text())
    win, desc = build_index()
    emb, M = Embedder(), Matcher("lightglue", size=1024)
    files = sorted(IMAGES.glob("*.png"))
    if args.limit:
        files = files[:args.limit]
    for f in tqdm(files, desc="localize"):
        old = preds.get(f.stem)
        if old and not (args.retry and (old["status"] != "ok" or old["inliers"] < 40)):
            continue
        q = np.array(Image.open(f))
        t = time.time()
        p = localize(q, M, emb, win, desc, k=args.retry or K)
        if old and old.get("inliers", old.get("best_inliers", 0)) >= p.get("inliers", p.get("best_inliers", 0)):
            continue
        p["seconds"] = round(time.time() - t, 1)
        preds[f.stem] = p
        out_f.write_text(json.dumps(preds, indent=1))
    ok = sum(p["status"] == "ok" for p in preds.values())
    print(f"localized {ok}/{len(preds)}")


if __name__ == "__main__":
    main()
