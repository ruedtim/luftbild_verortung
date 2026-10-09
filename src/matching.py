"""Local feature matching (Hugging Face transformers) + robust homography."""
import cv2
import numpy as np
import torch
from PIL import Image
from transformers import AutoImageProcessor, AutoModelForKeypointMatching

DEV = "mps" if torch.backends.mps.is_available() else "cpu"
MODELS = {
    "lightglue": "ETH-CVG/lightglue_superpoint",
    "matchanything": "zju-community/matchanything_eloftr",
    "eloftr": "zju-community/efficientloftr",
}


def to_gray_rgb(a):
    """Grayscale + CLAHE, replicated to 3 channels: removes colour/era differences."""
    g = a if a.ndim == 2 else cv2.cvtColor(a, cv2.COLOR_RGB2GRAY)
    g = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(g)
    return np.stack([g] * 3, -1)


def pad_to(img, w, h):
    """Zero-pad a PIL image on the right/bottom (keeps pixel coordinates)."""
    if img.size == (w, h):
        return img
    out = Image.new(img.mode, (w, h))
    out.paste(img, (0, 0))
    return out


class Matcher:
    def __init__(self, name="matchanything", size=832):
        self.name, self.size = name, size
        self.proc = AutoImageProcessor.from_pretrained(MODELS[name])
        self.model = AutoModelForKeypointMatching.from_pretrained(MODELS[name]).to(DEV).eval()

    def _prep(self, a):
        """Resize longest side to self.size (multiple of 32). Returns PIL img, scale."""
        h, w = a.shape[:2]
        s = self.size / max(h, w)
        nw, nh = int(round(w * s / 32) * 32), int(round(h * s / 32) * 32)
        return Image.fromarray(cv2.resize(to_gray_rgb(a), (nw, nh), interpolation=cv2.INTER_AREA)), (w / nw, h / nh)

    @torch.no_grad()
    def match(self, a, b):
        """Returns matched keypoints (N,2) in a- and b-pixel coords + scores."""
        ia, sa = self._prep(a)
        ib, sb = self._prep(b)
        W, H = max(ia.size[0], ib.size[0]), max(ia.size[1], ib.size[1])
        inp = self.proc([[pad_to(ia, W, H), pad_to(ib, W, H)]], return_tensors="pt", do_resize=False).to(DEV)
        out = self.model(**inp)
        res = self.proc.post_process_keypoint_matching(
            out, [[(H, W), (H, W)]], threshold=0.2)[0]
        k0 = res["keypoints0"].cpu().numpy().astype(np.float64) * sa
        k1 = res["keypoints1"].cpu().numpy().astype(np.float64) * sb
        sc = res["matching_scores"].cpu().numpy()
        return k0, k1, sc


    @torch.no_grad()
    def match_rotations(self, q, ref, size=None, qsize=None):
        """Match all 4 90°-rotations of q against ref in one batch.

        q is squashed to a square first (aerial frames are ~square) so the rotations
        share a shape. Returns [(k0 in rot90(q, r) pixel coords, k1 in ref coords)] per r.
        """
        size = size or self.size
        qsize = qsize or size
        h, w = q.shape[:2]
        qs = cv2.resize(to_gray_rgb(q), (qsize, qsize), interpolation=cv2.INTER_AREA)
        rs = cv2.resize(to_gray_rgb(ref), (size, size), interpolation=cv2.INTER_AREA)
        ib = Image.fromarray(rs)
        S = max(size, qsize)
        ib = pad_to(ib, S, S)
        pairs = [[pad_to(Image.fromarray(np.ascontiguousarray(np.rot90(qs, r))), S, S), ib] for r in range(4)]
        inp = self.proc(pairs, return_tensors="pt", do_resize=False).to(DEV)
        out = self.model(**inp)
        res = self.proc.post_process_keypoint_matching(out, [[(S, S), (S, S)]] * 4, threshold=0.2)
        sref = (ref.shape[1] / size, ref.shape[0] / size)
        result = []
        for r, x in enumerate(res):
            # rotated-square coords -> rotated-original coords
            rw, rh = (w, h) if r % 2 == 0 else (h, w)
            k0 = x["keypoints0"].cpu().numpy().astype(np.float64) * (rw / qsize, rh / qsize)
            k1 = x["keypoints1"].cpu().numpy().astype(np.float64) * sref
            result.append((k0, k1))
        return result


def homography(k0, k1, thr=8.0):
    if len(k0) < 8:
        return None, 0, None
    H, mask = cv2.findHomography(k0, k1, cv2.USAC_MAGSAC, thr, maxIters=10000, confidence=0.999)
    if H is None:
        return None, 0, None
    return H, int(mask.sum()), mask.ravel().astype(bool)


def plausible(H, w, h, out_w, out_h):
    """Reject degenerate homographies (folded, extreme scale/shear)."""
    c = np.float32([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2)
    p = cv2.perspectiveTransform(c, H).reshape(-1, 2)
    if not cv2.isContourConvex(p.astype(np.float32)):
        return False
    sides = np.linalg.norm(p - np.roll(p, 1, 0), axis=1)
    if sides.min() < 1e-3 or sides.max() / sides.min() > 2.0:
        return False
    d1, d2 = np.linalg.norm(p[0] - p[2]), np.linalg.norm(p[1] - p[3])
    return 0.75 < d1 / d2 < 1.33
