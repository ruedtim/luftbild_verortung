"""Place names inside a (predicted) footprint: swissNAMES3D + municipalities (swissBOUNDARIES3D)."""
import csv
import json
from functools import lru_cache

from shapely.geometry import Point, Polygon
from shapely.strtree import STRtree

from common import API, CACHE, S, canton_zh

DIR = CACHE / "names"
# (file, objektart) -> (category, priority: lower = more important)
KEEP = {
    "Ort": ("Siedlung", 0), "Ortsteil": ("Siedlung", 1), "Quartier": ("Quartier", 1),
    "Quartierteil": ("Quartier", 2),
    "See": ("Gewässer", 1), "Seeinsel": ("Gewässer", 2), "Fluss": ("Gewässer", 1),
    "Fliessgewaesser": ("Gewässer", 2),
    "Haupthuegel": ("Erhebung", 2), "Huegel": ("Erhebung", 3), "Gipfel": ("Erhebung", 2),
    "Hauptgipfel": ("Erhebung", 2), "Huegelzug": ("Erhebung", 2),
    "Tal": ("Gelände", 3), "Gebiet": ("Gebiet", 3), "Landschaftsname": ("Gebiet", 2),
    "Historisches Areal": ("Objekt", 2), "Klosterareal": ("Objekt", 2),
    "Kraftwerkareal": ("Objekt", 3), "Zooareal": ("Objekt", 2), "Spitalareal": ("Objekt", 3),
    "Flugplatzareal": ("Objekt", 2), "Truppenuebungsplatz": ("Objekt", 3),
    "Staumauer": ("Objekt", 3), "Wehr": ("Objekt", 3),
}
# inhabitants category -> bonus (bigger places first)
POP = {"> 100'000": -5, "50'000 bis 100'000": -4, "10'000 bis 49'999": -3,
       "2'000 bis 9'999": -2, "1'000 bis 1'999": -1, "100 bis 999": 0,
       "50 bis 99": 1, "20 bis 49": 1.5, "< 20": 2}


@lru_cache(1)
def gazetteer():
    area = canton_zh().buffer(5000)
    x0, y0, x1, y1 = area.bounds
    recs, seen = [], set()
    for fn in ("swissNAMES3D_PLY.csv", "swissNAMES3D_PKT.csv", "swissNAMES3D_LIN.csv"):
        with open(DIR / fn, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f, delimiter=";"):
                art = r["OBJEKTART"]
                if art not in KEEP or r["STATUS"] not in ("offiziell", "k_W", ""):
                    continue
                try:
                    e, n = float(r["E"]), float(r["N"])
                except ValueError:
                    continue
                if not (x0 < e < x1 and y0 < n < y1):
                    continue
                key = (r["NAME"], art, round(e, -2), round(n, -2))
                if key in seen:
                    continue
                seen.add(key)
                cat, prio = KEEP[art]
                prio += POP.get(r.get("EINWOHNERKATEGORIE", ""), 0) * 0.5
                recs.append(dict(name=r["NAME"], type=art, category=cat, prio=prio, e=e, n=n))
    tree = STRtree([Point(r["e"], r["n"]) for r in recs])
    return recs, tree


def names_in(poly: Polygon, max_n=40):
    recs, tree = gazetteer()
    idx = tree.query(poly, predicate="contains")
    hits = sorted((recs[i] for i in idx), key=lambda r: (r["prio"], r["name"]))
    # one entry per name (rivers/streams appear as many segments)
    out, seen = [], set()
    for r in hits:
        if r["name"] in seen:
            continue
        seen.add(r["name"])
        out.append({k: r[k] for k in ("name", "type", "category", "e", "n")})
    return out[:max_n]


def municipalities(poly: Polygon):
    ring = [list(map(lambda v: round(v, 1), c)) for c in poly.exterior.coords]
    r = S.get(f"{API}/identify", timeout=60, params=dict(
        geometryType="esriGeometryPolygon", geometry=json.dumps({"rings": [ring]}),
        sr=2056, layers="all:ch.swisstopo.swissboundaries3d-gemeinde-flaeche.fill",
        tolerance=0, returnGeometry="false", timeInstant=2025, limit=50))
    r.raise_for_status()
    res = r.json().get("results", [])
    return sorted({(a["attributes"]["gemname"], a["attributes"].get("kanton")) for a in res})


if __name__ == "__main__":
    from shapely.geometry import box
    b = box(2682000, 1247000, 2685000, 1250000)
    for x in names_in(b, 15):
        print(x)
    print(municipalities(b))
