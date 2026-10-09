# Luftbild-Verortung Zürich (PoC)

Historische Senkrecht-Luftbilder des Kantons Zürich werden allein aus dem Bildinhalt verortet:
Bildumriss und Zentrum (LV95/WGS84), Konfidenz, Gemeinden und abgebildete Ortsnamen mit Koordinaten.
Alle Modelle laufen lokal (Hugging Face, Apple-GPU/MPS); Referenzdaten kommen aus offenen swisstopo-Diensten.

## Einzelnes Bild verorten

```bash
.venv/bin/python src/locate.py pfad/zum/bild.tif
```

Akzeptiert JPG/PNG/TIFF, ignoriert Metadaten, schneidet den Filmrand automatisch ab.
Schreibt `bild.json` (Koordinaten, Umriss, Orte) und `bild.html` (Karte mit Overlay) neben das Bild
(oder nach `--out ordner/`). Dauer: 30–60 s pro Bild.

## Ganze Pipeline (Testdatensatz mit Auswertung)

```bash
uv venv -p 3.12 .venv && uv pip install -p .venv/bin/python -r requirements.txt
cd src
../.venv/bin/python fetch_images.py --n-lubis 200 --n-zh 40   # Bilder holen, Metadaten -> data/gt/
../.venv/bin/python reference.py                              # SWISSIMAGE-Mosaik 5 m/px
../.venv/bin/python retrieval.py                              # DINOv2-Suchindex (~15 min)
../.venv/bin/python localize.py                               # Verortung (~16 s/Bild)
../.venv/bin/python localize.py --retry 50                    # zweiter Durchgang für unsichere Bilder
../.venv/bin/python evaluate.py                               # output/results.csv, results.geojson
../.venv/bin/python make_map.py                               # output/map/index.html (vorschau.html lokal)
```

swissNAMES3D muss einmalig nach `data/cache/names/` entpackt werden:
`https://data.geo.admin.ch/ch.swisstopo.swissnames3d/swissnames3d_2025/swissnames3d_2025_2056.csv.zip`

## Methode

1. **Grobsuche:** DINOv2 (`facebook/dinov2-base`) vergleicht das Bild in 4 Drehungen mit 35'860 Fenstern
   (0,8–9 km) eines SWISSIMAGE-Mosaiks → Top-50-Kandidaten.
2. **Feinabgleich:** LightGlue + SuperPoint (`ETH-CVG/lightglue_superpoint`) je Kandidat und Drehung,
   Homographie mit MAGSAC; bester Kandidat nach Inlier-Zahl.
3. **Verfeinern:** Referenz genau um den gefundenen Umriss neu holen und erneut abgleichen.
4. **Ortsnamen:** swissNAMES3D-Punkte im Umriss (Orte, Quartiere, Gewässer, Erhebungen, Areale) +
   Gemeinden über die geo.admin-API.

Konfidenz: ≥ 40 Inlier = hoch, ≥ 20 = mittel, darunter tief (unter 10: nicht verortet).

## Dateien

| Pfad | Inhalt |
| --- | --- |
| `data/images/` | Abfragebilder ohne Metadaten (Zufallsnamen) |
| `data/gt/ground_truth.json` | Archiv-Metadaten (nur für die Auswertung) |
| `output/predictions.json` | Rohvorhersagen inkl. Homographie Bild→LV95 |
| `output/results.csv`, `results.geojson` | Ergebnisse mit Fehlern, Gemeinden, Orten |
| `output/map/` | interaktive Karte |
| `beispiele/` | Beispielbild für `locate.py` |

## Quellen und Lizenzen

Luftbilder (LUBIS), SWISSIMAGE, swissNAMES3D, swissBOUNDARIES3D © swisstopo, freie Nutzung mit Quellenangabe.
Historische Luftbilder 1981–2000 © GIS-ZH, Kanton Zürich (`wms.zh.ch/HistLuftZHWMS`).
