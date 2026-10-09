"""Modern reference imagery: SWISSIMAGE (swisstopo) via WMS.

* `mosaic()`   – coarse canton-wide mosaic (default 5 m/px) used for retrieval
* `fetch(bb, px)` – arbitrary bbox at arbitrary resolution (cached) for fine matching
"""
import hashlib
from io import BytesIO

import numpy as np
from PIL import Image

from common import CACHE, S, WMS_CH, canton_zh

Image.MAX_IMAGE_PIXELS = None
MARGIN = 3000  # m around the canton bbox
LAYER = "ch.swisstopo.swissimage"


def wms(bb, w, h, layer=LAYER):
    r = S.get(WMS_CH, timeout=120, params=dict(
        SERVICE="WMS", VERSION="1.3.0", REQUEST="GetMap", LAYERS=layer, STYLES="",
        CRS="EPSG:2056", BBOX=",".join(f"{v:.2f}" for v in bb), WIDTH=w, HEIGHT=h,
        FORMAT="image/jpeg"))
    r.raise_for_status()
    return np.array(Image.open(BytesIO(r.content)).convert("RGB"))


def fetch(bb, w, h):
    key = hashlib.md5(f"{bb}{w}{h}".encode()).hexdigest()
    f = CACHE / "ref" / f"{key}.jpg"
    f.parent.mkdir(exist_ok=True)
    if f.exists():
        return np.array(Image.open(f))
    a = wms(bb, w, h)
    Image.fromarray(a).save(f, quality=92)
    return a


def mosaic_bounds():
    x0, y0, x1, y1 = canton_zh().bounds
    return (x0 - MARGIN) // 1000 * 1000, (y0 - MARGIN) // 1000 * 1000, \
        -(-(x1 + MARGIN) // 1000) * 1000, -(-(y1 + MARGIN) // 1000) * 1000


def mosaic(res=5.0, tile=2000):
    """Returns (rgb array, (minE, maxN), res). Row 0 = north."""
    f = CACHE / f"mosaic_{int(res)}m.png"
    x0, y0, x1, y1 = mosaic_bounds()
    if f.exists():
        return np.array(Image.open(f)), (x0, y1), res
    W, H = int((x1 - x0) / res), int((y1 - y0) / res)
    out = np.zeros((H, W, 3), np.uint8)
    step = tile * res
    for ty in range(0, H, tile):
        for tx in range(0, W, tile):
            w, h = min(tile, W - tx), min(tile, H - ty)
            bb = (x0 + tx * res, y1 - (ty + h) * res, x0 + (tx + w) * res, y1 - ty * res)
            out[ty:ty + h, tx:tx + w] = wms(bb, w, h)
            print(f"  tile {tx},{ty}", flush=True)
    Image.fromarray(out).save(f)
    return out, (x0, y1), res


if __name__ == "__main__":
    m, o, r = mosaic()
    print(m.shape, o, r)
