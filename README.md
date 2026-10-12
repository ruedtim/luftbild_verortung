# Luftbild-Verortung Zürich (PoC)

Historische Senkrecht-Luftbilder des Kantons Zürich werden allein aus dem Bildinhalt verortet:
Bildumriss und Zentrum (LV95/WGS84), Konfidenz, Gemeinden und abgebildete Ortsnamen mit Koordinaten.
Alle Modelle laufen lokal (Hugging Face; Apple-GPU/MPS oder CPU); Referenzdaten kommen aus offenen swisstopo-Diensten.

**Online-Beispiel:** [Testbilder, Vorhersage und Überblendung «damals/heute»](https://ruedtim.github.io/luftbild_verortung/) (Auswahl aus dem Testlauf)

## So funktioniert's

### Die Frage

Wo wurde dieses alte Luftbild aufgenommen? Das Programm beantwortet sie, indem es nur das Bild selbst anschaut
(ohne Dateiangaben und Archiv-Metadaten) und die passende Stelle im heutigen Luftbild des Kantons Zürich sucht.
Am Ende stehen:

- Lage und Umriss des Bildes auf der Karte
- eine Einstufung, wie sicher das Ergebnis ist (Konfidenz)
- die betroffenen Gemeinden
- die Orte, Quartiere und Gewässer, die im Bild liegen

Beispiel (`beispiele/testbild_1984`): Sicherheit «hoch» (1'324 übereinstimmende Punktpaare), Bildmitte im Raum Winterthur
(LV95 2698801 / 1260114), Gemeinden Elsau, Illnau-Effretikon, Winterthur und Zell (ZH), Orte u. a. Winterthur, Iberg,
Mattenbach und Seen.

### Die Idee: grob suchen, dann genau prüfen

Eine ortskundige Person würde in zwei Stufen vorgehen. Zuerst ein grober Eindruck («Das sieht nach dem Stadtrand von
Winterthur aus»), dann die Kontrolle am Detail («Diese Kreuzung und diese Bahnlinie finde ich auf der heutigen Karte
genau so wieder»). Das Programm macht dasselbe automatisch, für den ganzen Kanton. Als Vergleich dient das heutige
Luftbild von swisstopo (SWISSIMAGE).

```
 altes Luftbild  (nur die Bildpunkte)
        │
        ▼
 ① Grobsuche      rund 36'000 Ausschnitte des heutigen Kantons  →  die 50 ähnlichsten
        │
        ▼
 ② Feinprüfung    welcher dieser 50 passt im Detail wirklich?
        │
        ▼
 ③ Ergebnis       Lage · Umriss · Sicherheit · Gemeinden · Ortsnamen
```

**① Grobsuche: «Was sieht ähnlich aus?»**
Der Kanton wird in rund 36'000 sich überlappende Ausschnitte zerlegt, kleine und grosse, denn niemand weiss, aus welcher
Höhe das alte Bild aufgenommen wurde. Ein KI-Modell (DINOv2) rechnet jeden Ausschnitt in eine Zahlenreihe um, eine Art
«Fingerabdruck» des Bildinhalts: Ähnliche Landschaften ergeben ähnliche Zahlen. Dasselbe passiert mit dem alten Bild, in
allen vier Drehungen, denn auch die Ausrichtung ist unbekannt. Die 50 Ausschnitte mit den ähnlichsten Fingerabdrücken
kommen in die engere Wahl. Das geht schnell, ist aber nur ein Eindruck: «könnte passen».

**② Feinprüfung: «Passen die Einzelheiten?»**
Bei jedem Kandidaten sucht ein zweites KI-Modell (SuperPoint und LightGlue) markante Punkte in beiden Bildern, etwa
Gebäudeecken, Kreuzungen oder Feldränder, und ordnet sie einander zu: «Diese Kreuzung links oben im alten Bild ist
dieselbe wie die hier im heutigen.» Zeigen beide Bilder wirklich dieselbe Gegend, lassen sich fast alle Paare mit
**einer einzigen** Verschiebung, Drehung und Skalierung ineinander überführen. Zufällige, falsche Paare passen dazu nicht
und fallen heraus. Je mehr Paare zusammenpassen, desto sicherer ist die Stelle. Damit schwarz-weisse und farbige Bilder
vergleichbar sind, werden beide vorher in Graustufen umgewandelt und im Kontrast angeglichen.

**③ Ergebnis**
Aus den zusammenpassenden Paaren folgt, wie das alte Bild auf die Karte gelegt werden muss. Zur Kontrolle holt das
Programm das heutige Luftbild noch einmal exakt für das gefundene Gebiet und gleicht erneut ab. Daraus entstehen Umriss
und Mittelpunkt; in den Datenbanken von swisstopo werden die Gemeinden und Ortsnamen nachgeschlagen, die in diesem
Gebiet liegen.

### Wie sicher ist ein Ergebnis?

Das Mass ist die Zahl der zusammenpassenden Punktpaare:

| Zusammenpassende Paare | Einstufung |
| --- | --- |
| 40 und mehr | hoch |
| 20 bis 39 | mittel |
| 10 bis 19 | tief |
| unter 10 | nicht verortet: Das Programm meldet lieber nichts, als zu raten |

### Wie gut funktioniert es?

Im Testlauf mit 240 Luftbildern aus den Jahren 1930 bis 2000 (das Programm kannte deren wahren Ort nicht):

- **140 Bilder (58 %) wurden verortet**, 100 blieben «nicht verortet».
- Die verorteten Bilder lagen im Median **75 m** neben der Lage laut Archiv, **kein einziges mehr als 500 m**.
- Neuere Scans werden häufiger gefunden als sehr alte: GIS-ZH-Luftbilder aus den 1980er/90er-Jahren zu 83–90 %,
  swisstopo-Bilder aus den 1930er-Jahren zu 24 %.

Details siehe [Ergebnisse](#ergebnisse).

### Was läuft wo?

Alle KI-Modelle laufen auf dem eigenen Rechner. Das Luftbild wird nirgends hochgeladen; aus dem Internet werden nur
Karten- und Ortsdaten von swisstopo abgerufen (siehe [Daten: lokal oder live?](#daten-lokal-oder-live)).

### Grenzen

- Ausgelegt auf **Senkrechtaufnahmen** (Blick von oben) im **Kanton Zürich** (plus 500 m Puffer); Schrägaufnahmen und
  andere Regionen sind nicht vorgesehen.
- Gefunden wird nur, was genug wiedererkennbare Strukturen zeigt. Warum im Testlauf 42 % unverortet blieben, wurde noch
  nicht untersucht.
- Prototyp (PoC), kein fertiges Produkt.

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
../.venv/bin/python make_map.py --demo 40 --out ../docs       # kleine Online-Demo für GitHub Pages (~15 MB)
```

swissNAMES3D muss einmalig nach `data/cache/names/` entpackt werden:
`https://data.geo.admin.ch/ch.swisstopo.swissnames3d/swissnames3d_2025/swissnames3d_2025_2056.csv.zip`

## Technische Details

### Ablauf im Detail

**Vorverarbeitung.** Abfragebild und Referenz werden in Graustufen umgewandelt und mit CLAHE (lokaler
Kontrastausgleich) angeglichen; das neutralisiert Film-, Farb- und Epochenunterschiede. `locate.py` skaliert das
Abfragebild auf höchstens 2048 px, schneidet einen dunklen Film-/Scanrand automatisch ab (`auto_crop`) und zusätzlich
5 % pro Seite.

**Grobsuche** ([src/retrieval.py](src/retrieval.py), [src/reference.py](src/reference.py)). Referenz ist ein
SWISSIMAGE-Mosaik des Kantons (plus 3 km Rand) mit 5 m/px. Es wird mit quadratischen Fenstern in 7 Grössen
(0,8 / 1,2 / 1,8 / 2,7 / 4 / 6 / 9 km; Schrittweite 40 % der Fenstergrösse; nur Fenster im Kanton plus 500 m)
abgedeckt: 35'860 Fenster. Jedes Fenster (auf 322 × 322 px skaliert) wird mit DINOv2 zu einem Vektor mit 1536 Zahlen
(CLS-Token und GeM-Pooling der Patch-Tokens, normalisiert). Das Abfragebild wird in 4 Drehungen eingebettet; per
Kosinus-Ähnlichkeit ergibt sich je Fenster die beste Drehung. Überlappende Fenster ähnlicher Grösse werden unterdrückt
(Non-Maximum-Suppression), die besten K = 50 (`locate.py`: 80) sind die Kandidaten. Der Index entsteht einmalig
(~15 min) und liegt in `data/cache/index_dinov2.npz`.

**Feinabgleich** ([src/localize.py](src/localize.py), [src/matching.py](src/matching.py)). Pro Kandidat wird der
SWISSIMAGE-Ausschnitt (Fenster × 1,6, damit Kontext dabei ist) mit 1024 px geholt. SuperPoint findet Keypoints,
LightGlue ordnet sie zu, alle 4 Drehungen in einem Batch. OpenCV schätzt aus den Zuordnungen mit MAGSAC (Toleranz 8 px)
eine Homographie; die Zahl der Inlier ist das Gütemass. Unplausible Lösungen (nicht konvex, Seitenverhältnis > 2, stark
ungleiche Diagonalen) werden verworfen. Der Kandidat mit den meisten Inliern gewinnt; ab 80 Inliern wird die Suche früh
abgebrochen, unter 10 gilt das Bild als nicht verortet.

**Verfeinern.** Zweimal: Referenz exakt um den vorhergesagten Umriss neu holen (1,2 × längste Ausdehnung, 1280 px) und
erneut abgleichen. Das Ergebnis wird nur übernommen, wenn mindestens 80 % der bisherigen Inlier erreicht werden.

**Ergebnis.** Die 3×3-Matrix bildet Bildpixel auf LV95 (EPSG:2056) ab: Bildecken ergeben den Umriss, die Bildmitte das
Zentrum (zusätzlich in WGS84). Konfidenz nach Inliern: ≥ 40 hoch, ≥ 20 mittel, darunter tief (unter 10: nicht
verortet); die Schwellen wurden am Testlauf kalibriert ([src/evaluate.py](src/evaluate.py)).

**Ortsnamen** ([src/names.py](src/names.py)). swissNAMES3D (Polygone, Punkte, Linien) mit ausgewählten Objektarten
(Orte, Ortsteile, Quartiere, Gewässer, Erhebungen, Areale u. a.) im Kanton plus 5 km liegt als räumlicher Index im
Speicher. `names_in()` liefert alle Punkte im Umriss, nach Bedeutung sortiert (Objektart, Einwohnerkategorie), jeden
Namen einmal, höchstens 40. Die Gemeinden kommen aus der geo.admin-API (swissBOUNDARIES3D, Stand 2025).

**Zweiter Durchgang.** `localize.py --retry N` wiederholt Bilder ohne Ergebnis oder mit weniger als 40 Inliern mit
N Kandidaten und übernimmt das neue Ergebnis nur, wenn es mehr Inlier hat.

### Begriffe

| Begriff | Bedeutung |
| --- | --- |
| Orthofoto, SWISSIMAGE | entzerrtes, massstabsgetreues Luftbild von heute (swisstopo); dient als Referenz |
| LV95 | Schweizer Landeskoordinaten (EPSG:2056) in Metern: E (Ost) / N (Nord) |
| Embedding | Zahlenvektor, der den Inhalt eines Bildes zusammenfasst («Fingerabdruck»); ähnliche Bilder ergeben ähnliche Vektoren |
| Keypoint | markanter, wiedererkennbarer Punkt im Bild (Ecke, Kreuzung) |
| Homographie | 3×3-Matrix, die ein Bild auf ein anderes abbildet (Verschiebung, Drehung, Massstab, perspektivische Verzerrung); für Senkrechtaufnahmen über weitgehend ebenem Gelände eine gute Näherung |
| Inlier | Zuordnung, die zur geschätzten Homographie passt (Gegenteil: Ausreisser); ihre Anzahl ist das Gütemass |
| MAGSAC | robustes Schätzverfahren, das die Homographie auch findet, wenn viele Zuordnungen falsch sind |
| CLAHE | lokaler Kontrastausgleich |
| IoU | Überlappung zweier Flächen (Schnittfläche geteilt durch Vereinigungsfläche) |
| Ground Truth | Soll-Wert: Lage laut Archiv-Metadaten, nur für die Auswertung genutzt |

### Modelle und Hardware

| Modell | Hugging Face | Grösse | Aufgabe |
| --- | --- | --- | --- |
| DINOv2 (base) | `facebook/dinov2-base` | ca. 350 MB | Grobsuche (Embeddings) |
| SuperPoint + LightGlue | `ETH-CVG/lightglue_superpoint` | ca. 55 MB | Feinabgleich |

Beide werden beim ersten Start vom Hugging Face Hub geladen und danach lokal in `~/.cache/huggingface/` abgelegt.
[src/matching.py](src/matching.py) kennt zwei weitere Matcher (`zju-community/matchanything_eloftr`,
`zju-community/efficientloftr`), die die Pipeline nicht verwendet.

Rechenintensiv sind der einmalige Index (35'860 Fenster durch DINOv2) und pro Bild bis zu 50 (bzw. 80) Kandidaten ×
4 Drehungen im Matcher. Der Code nutzt die Apple-GPU (PyTorch `mps`) und fällt sonst auf die CPU zurück (langsamer;
nicht getestet). CUDA (NVIDIA) ist nicht eingebaut. Richtwerte auf einem Mac mit Apple-GPU: Index ~15 min (einmalig),
Pipeline ~15 s pro Bild (240 Bilder: ~1 h), `locate.py` 30–60 s pro Bild inklusive Laden der Modelle.

### Daten: lokal oder live?

| Was | Wo | Wie |
| --- | --- | --- |
| SWISSIMAGE-Mosaik, 5 m/px (≈ 54 × 67 km) | `data/cache/mosaic_5m.png`, ca. 280 MB | einmal per WMS geholt, dann lokal |
| DINOv2-Index (35'860 Fenster) | `data/cache/index_dinov2.npz`, ca. 210 MB | einmal aus dem Mosaik berechnet, dann lokal |
| SWISSIMAGE-Ausschnitte für den Feinabgleich | `data/cache/ref/` | **live** von `wms.geo.admin.ch`, danach gecacht |
| Ortsnamen (swissNAMES3D) | `data/cache/names/`, ca. 115 MB | einmalig von Hand entpackt, lokal |
| Gemeinden | geo.admin-API | **live**, bei jedem Bild, nicht gecacht |
| Modellgewichte | `~/.cache/huggingface/` | beim ersten Start, danach lokal |

Die Grobsuche läuft rein lokal. Der Feinabgleich holt pro neues Bild alle Kandidaten-Ausschnitte vorab und parallel
(auch wenn die Suche danach früh abbricht): ca. 0,4 MB pro Ausschnitt, also rund 20 MB bei 50 und rund 30 MB bei
80 Kandidaten (`locate.py`). Bekannte Gegenden kommen aus dem Cache; nach den 240 Testbildern umfasst `data/cache/ref/`
rund 5'700 Ausschnitte (2,2 GB). `data/cache/` ist nicht im Repo (`.gitignore`): Ein frischer Klon baut Mosaik (per WMS)
und Index (~15 min) beim ersten Start selbst auf; nur swissNAMES3D muss von Hand entpackt werden (siehe oben).

### Testaufbau und Auswertung

Die Verortung wird blind getestet: [src/fetch_images.py](src/fetch_images.py) lädt 240 Senkrechtbilder (200 aus
swisstopo LUBIS, 1930er–2000er, je 25 pro Jahrzehnt; 40 von GIS-ZH, 1981–2000), verkleinert sie auf höchstens 2048 px,
schneidet 5 % Rand ab und speichert sie als PNG mit Zufallsnamen und ohne Metadaten in `data/images/`. Lage, Umriss und
Jahr laut Archiv liegen getrennt in `data/gt/ground_truth.json`; der Localizer liest sie nie.
[src/evaluate.py](src/evaluate.py) vergleicht danach Zentrumsfehler (m), Überlappung der Umrisse (IoU) und die
Übereinstimmung der Siedlungs- und Quartiernamen (Jaccard) mit der Ground Truth.

Die Archiv-Metadaten sind selbst nicht exakt (bei GIS-ZH nur ein achsparalleles Rechteck um das Bild); die gemessenen
Abweichungen sind deshalb als Grössenordnung zu lesen.

### Ergebnisse

Stand: `output/results.csv` (240 Bilder).

- 140 von 240 verortet (58 %), davon 138 mit Konfidenz «hoch» und 2 «mittel».
- Zentrumsfehler: Median 75 m, Mittel 93 m, 90 % unter 185 m, maximal 400 m; 59 % unter 100 m, 97 % unter 250 m,
  alle unter 500 m.
- Überlappung der Umrisse (IoU): Median 0,88.
- Ortsnamen: Siedlungs- und Quartiernamen von Vorhersage und Archiv-Umriss stimmen im Mittel zu 83 % überein (Jaccard).

| Quelle | Zeitraum | Bilder | verortet |
| --- | --- | --- | --- |
| swisstopo (LUBIS) | 1930er | 25 | 6 (24 %) |
| swisstopo (LUBIS) | 1940er | 25 | 11 (44 %) |
| swisstopo (LUBIS) | 1950er | 25 | 10 (40 %) |
| swisstopo (LUBIS) | 1960er | 25 | 17 (68 %) |
| swisstopo (LUBIS) | 1970er | 25 | 16 (64 %) |
| swisstopo (LUBIS) | 1980er | 25 | 19 (76 %) |
| swisstopo (LUBIS) | 1990er | 25 | 8 (32 %) |
| swisstopo (LUBIS) | 2000er | 25 | 18 (72 %) |
| GIS-ZH | 1981–2000 | 40 | 35 (88 %) |

Warum die Quote zwischen den Jahrzehnten schwankt (etwa 32 % bei den swisstopo-Bildern der 1990er), wurde nicht
untersucht.

### Karte

[src/make_map.py](src/make_map.py) entzerrt jedes verortete Bild mit seiner Homographie auf ein Nord-oben-Raster
(Web Mercator, WebP) und rechnet denselben Ausschnitt aus dem heutigen SWISSIMAGE dazu;
[src/map_template.html](src/map_template.html) (Leaflet) macht daraus eine eigenständige Seite:

- Liste mit Filtern nach Jahrzehnt, Konfidenz und Fehler
- Vorhersage in Rot, Umriss laut Archiv in Grün, Ortsnamen als Punkte
- Regler zum Überblenden zwischen «damals» und «heute»

`--demo N` erzeugt eine kleine öffentliche Variante: eine zufällige, nach Quelle und Jahrzehnt geschichtete Auswahl von
etwa N Bildern, die Kennzahlen im Kopf gelten weiterhin für alle. Das ist die Seite in `docs/`, die GitHub Pages
ausliefert (~15 MB statt ~40 MB der Vollkarte). Einzige externe Ressource ist die Kartenbibliothek Leaflet von
`cdnjs.cloudflare.com`; Web-Fonts werden nicht geladen.

## Dateien

Code:

| Datei | Aufgabe |
| --- | --- |
| `src/locate.py` | Einzelbild verorten (Ende-zu-Ende), schreibt `.json` und `.html` |
| `src/fetch_images.py` | Testdatensatz holen (LUBIS, GIS-ZH), Metadaten abtrennen |
| `src/reference.py` | SWISSIMAGE per WMS: Kantons-Mosaik und beliebige Ausschnitte (gecacht) |
| `src/retrieval.py` | DINOv2-Index und Grobsuche |
| `src/matching.py` | SuperPoint/LightGlue-Abgleich, Homographie, Plausibilitätsprüfung |
| `src/localize.py` | Verortung des Testdatensatzes, schreibt `output/predictions.json` |
| `src/names.py` | Ortsnamen (swissNAMES3D) und Gemeinden |
| `src/evaluate.py` | Vergleich mit der Ground Truth, `results.csv` und `results.geojson` |
| `src/make_map.py`, `src/map_template.html` | interaktive Karte, `--demo` für die Online-Demo |
| `src/common.py` | Pfade, HTTP-Session, Kantonsgrenze |

Daten und Ergebnisse:

| Pfad | Inhalt |
| --- | --- |
| `data/images/` | Abfragebilder ohne Metadaten (Zufallsnamen); nicht im Repo |
| `data/gt/ground_truth.json` | Archiv-Metadaten (nur für die Auswertung) |
| `data/cache/` | Mosaik, Index, Referenzausschnitte, Ortsnamen; nicht im Repo |
| `output/predictions.json` | Rohvorhersagen inkl. Homographie Bild→LV95 |
| `output/predictions_pass1.json` | Stand nach dem ersten Durchgang (vor `--retry`) |
| `output/results.csv`, `results.geojson` | Ergebnisse mit Fehlern, Gemeinden, Orten |
| `output/summary.txt` | Kennzahlen des Testlaufs (Ausgabe von `evaluate.py`) |
| `output/map/` | Vollkarte; lokal erzeugt, nicht im Repo |
| `docs/` | Online-Demo für GitHub Pages |
| `beispiele/` | Beispielbilder und -ergebnisse für `locate.py` |

## Quellen und Lizenzen

Luftbilder (LUBIS), SWISSIMAGE, swissNAMES3D, swissBOUNDARIES3D © swisstopo, freie Nutzung mit Quellenangabe.
Historische Luftbilder 1981–2000 © GIS-ZH, Kanton Zürich (`wms.zh.ch/HistLuftZHWMS`).
