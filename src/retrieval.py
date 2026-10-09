"""Coarse localisation: DINOv2 global descriptors of multi-scale reference windows.

The canton is covered with square windows of several sizes (scale is unknown for a
blind query). Each window and each query (in 4 rotations) is embedded with DINOv2
(Hugging Face, local). Nearest windows = candidate locations + rotation.
"""
import cv2
import numpy as np
import torch
import torch.nn.functional as F
from shapely.geometry import box
from tqdm import tqdm
from transformers import AutoModel

from common import CACHE, canton_zh
from matching import DEV, to_gray_rgb
from reference import mosaic

SCALES_KM = [0.8, 1.2, 1.8, 2.7, 4.0, 6.0, 9.0]
STRIDE = 0.4          # fraction of window size
INPUT = 322           # DINOv2 input (multiple of 14)
MODEL = "facebook/dinov2-base"
MEAN = torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1)
STD = torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1)


class Embedder:
    def __init__(self):
        self.m = AutoModel.from_pretrained(MODEL).to(DEV).eval()

    @torch.no_grad()
    def __call__(self, imgs):
        x = torch.from_numpy(np.stack(imgs)).permute(0, 3, 1, 2).float() / 255
        x = ((x - MEAN) / STD).to(DEV)
        h = self.m(pixel_values=x).last_hidden_state
        cls, patches = h[:, 0], h[:, 1:]
        gem = patches.clamp(min=1e-6).pow(3).mean(1).pow(1 / 3)  # GeM pooling
        d = torch.cat([F.normalize(cls, dim=-1), F.normalize(gem, dim=-1)], -1)
        return F.normalize(d, dim=-1).cpu().numpy().astype(np.float32)


def prep(a):
    return cv2.resize(to_gray_rgb(a), (INPUT, INPUT), interpolation=cv2.INTER_AREA)


def windows():
    zh = canton_zh().buffer(500)
    m, (E0, N1), res = mosaic()
    H, W = m.shape[:2]
    out = []
    for s in SCALES_KM:
        px = int(s * 1000 / res)
        st = max(1, int(px * STRIDE))
        for r in range(0, H - px + 1, st):
            for c in range(0, W - px + 1, st):
                bb = (E0 + c * res, N1 - (r + px) * res, E0 + (c + px) * res, N1 - r * res)
                if zh.intersects(box(*bb)):
                    out.append((r, c, px, *bb))
    return np.array(out)


def build_index(bs=64):
    f = CACHE / "index_dinov2.npz"
    if f.exists():
        z = np.load(f)
        return z["win"], z["desc"]
    m, _, _ = mosaic()
    win = windows()
    emb = Embedder()
    descs = []
    for i in tqdm(range(0, len(win), bs), desc="index"):
        batch = [prep(m[int(r):int(r + p), int(c):int(c + p)]) for r, c, p, *_ in win[i:i + bs]]
        descs.append(emb(batch))
    desc = np.concatenate(descs)
    np.savez(f, win=win, desc=desc)
    return win, desc


def query(q, win, desc, emb, k=40, rots=range(4)):
    """Returns list of (score, window_idx, rot) sorted by score, de-duplicated."""
    qd = emb([prep(np.ascontiguousarray(np.rot90(q, r))) for r in rots])
    sims = desc @ qd.T                       # (n_win, n_rot)
    best_rot = sims.argmax(1)
    best = sims.max(1)
    order = np.argsort(-best)
    picked = []
    for i in order:
        cx, cy = (win[i, 3] + win[i, 5]) / 2, (win[i, 4] + win[i, 6]) / 2
        s = win[i, 5] - win[i, 3]
        # non-max suppression: skip windows overlapping an already-picked one of similar scale
        if any(abs(cx - pcx) < 0.5 * max(s, ps) and abs(cy - pcy) < 0.5 * max(s, ps) and 0.6 < s / ps < 1.7
               for _, _, _, pcx, pcy, ps in picked):
            continue
        picked.append((float(best[i]), int(i), int(rots[best_rot[i]]), cx, cy, s))
        if len(picked) >= k:
            break
    return [(sc, i, r) for sc, i, r, *_ in picked]


if __name__ == "__main__":
    w, d = build_index()
    print(w.shape, d.shape)
