#!/usr/bin/env python3
"""Ortsgliederung einer weiteren Stadt aus dem OpenStreetMap-Auszug erzeugen.

Gegenstück zu ``tools/strassen_aus_osm.py`` – derselbe Datenexport, dieselbe Lizenz, aber
für eine **neue** Stadt: Statt Bezirke und Stadtteile von Hand abzutippen, liest das Werkzeug
sie aus dem OSM-Auszug und schreibt sie in die Stadtkonfiguration. Danach gilt für die neue
Stadt dasselbe System wie für Köln (Gazetteer, PLZ-Näherung, Straßenverzeichnis).

Das Werkzeug ist **nicht Teil der Anwendung**: Es läuft einmalig auf einem Rechner mit Netz.
Die Anwendung liest danach nur die erzeugten Dateien und ruft selbst nie OpenStreetMap ab.

Datenquelle: ``https://github.com/openpotato/openplzapi.data`` – Datei
``src/de/osm/streets.updated.csv`` (Spalten Name, PostalCode, Locality, RegionalKey, Borough,
Suburb). Lizenz **ODbL 1.0** („© OpenStreetMap-Mitwirkende“).

Gesteuert wird das Werkzeug über den Abschnitt ``[erzeugung]`` der Stadtkonfiguration, den
die Anwendung selbst ignoriert::

    [erzeugung]
    stadtteile = true                 # Stadtteile aus „Suburb“ übernehmen (sonst nur Bezirke)
    bezirke = [                        # OSM-„Borough“ → Schlüssel und Anzeigename
      { osm = "Stadtbezirk Bonn", nr = "1", name = "Stadtbezirk Bonn" },
    ]
    ergaenzte_bezirke = [              # fehlt im Auszug, ist aber amtlich vorhanden
      { nr = "10", name = "Ronsdorf", grund = "…" },
    ]

Aufruf::

    curl -L -H "Accept: application/vnd.github.raw" -o /tmp/streets.updated.csv \\
      https://api.github.com/repos/openpotato/openplzapi.data/contents/src/de/osm/streets.updated.csv
    python tools/stadt_aus_osm.py /tmp/streets.updated.csv config/cities/bonn.toml

Ergebnis: Der Block zwischen ``# >>> ortsgliederung`` und ``# <<< ortsgliederung`` in der
TOML-Datei wird ersetzt (Bezirke, Stadtteile, PLZ), und bei ``stadtteile = true`` entsteht
``<stadt>.strassen.tsv`` daneben. Stadtteil-Schlüssel sind **projektinterne Nummern**
(Bezirk × 100 + laufende Nummer in alphabetischer Folge), keine amtliche Nummerierung.
"""

from __future__ import annotations

import argparse
import collections
import csv
import re
import sys
import tomllib
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from behoerdenmaster.normalize import strassen_kanonisch  # noqa: E402
from strassen_aus_osm import (  # noqa: E402
    MINDESTANTEIL, MINDESTZAHL, baue_register, plz_verteilung, plz_zuordnung, schreibe,
)

ANFANG = "# >>> ortsgliederung"
ENDE = "# <<< ortsgliederung"
# Eine PLZ gehört zur Stadt, wenn mindestens so viele Straßen des Auszugs sie tragen.
# Streufälle (Grenzstraßen mit der PLZ der Nachbargemeinde, z. B. 42857 Remscheid in
# Wuppertal mit 5 Straßen) fallen so heraus; echte PLZ der geprüften Städte haben ≥ 26.
PLZ_MINDESTSTRASSEN = 10


def _q(text: str) -> str:
    """TOML-Zeichenkette (doppelte Anführungszeichen, Backslash geschützt)."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def lies_auszug(csv_pfad: Path, ags: str) -> list[dict]:
    with csv_pfad.open(newline="", encoding="utf-8") as fh:
        return [z for z in csv.DictReader(fh) if z.get("RegionalKey") == ags]


def gliederung(zeilen: list[dict], erzeugung: dict) -> tuple[dict, dict, dict]:
    """Bezirke (nr → Name), Stadtteile (nr → (Name, Bezirk)) und Anzahl Straßen je PLZ."""
    zuordnung = {b["osm"]: (str(b["nr"]), b["name"]) for b in erzeugung.get("bezirke", [])}
    bezirke = {nr: name for nr, name in zuordnung.values()}
    for extra in erzeugung.get("ergaenzte_bezirke", []):
        bezirke[str(extra["nr"])] = extra["name"]

    suburbs: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    plz_strassen: collections.Counter = collections.Counter()
    unbekannte_bezirke: collections.Counter = collections.Counter()
    for z in zeilen:
        code = (z.get("PostalCode") or "").strip()
        if code:
            plz_strassen[code] += 1
        borough = z.get("Borough", "")
        if borough and borough not in zuordnung:
            unbekannte_bezirke[borough] += 1
        if borough in zuordnung and z.get("Suburb"):
            suburbs[z["Suburb"]][zuordnung[borough][0]] += 1
    if unbekannte_bezirke:
        raise SystemExit("Bezirke im Auszug ohne Zuordnung in [erzeugung]: "
                         + ", ".join(f"{b} ({n})" for b, n in unbekannte_bezirke.most_common()))

    stadtteile: dict[str, tuple[str, str]] = {}
    if erzeugung.get("stadtteile"):
        je_bezirk: dict[str, list[str]] = collections.defaultdict(list)
        for name, zaehler in suburbs.items():
            # Ein Stadtteil gehört zu dem Bezirk, in dem die meisten seiner Straßen liegen.
            je_bezirk[zaehler.most_common(1)[0][0]].append(name)
        for bezirk_nr in sorted(je_bezirk, key=int):
            for i, name in enumerate(sorted(je_bezirk[bezirk_nr], key=strassen_kanonisch), start=1):
                stadtteile[f"{int(bezirk_nr) * 100 + i}"] = (name, bezirk_nr)
    plz = {code: n for code, n in plz_strassen.items() if n >= PLZ_MINDESTSTRASSEN}
    return bezirke, stadtteile, plz


def toml_block(bezirke: dict, aliase: dict, stadtteile: dict, plz: dict,
               plz_stadtteile: dict, je_plz: dict, *, stand: str, mit_stadtteilen: bool) -> str:
    z = [ANFANG,
         f"# Erzeugt von tools/stadt_aus_osm.py am {stand} – nicht von Hand ändern, sondern neu erzeugen.",
         "# Quelle: OpenStreetMap-Auszug der OpenPLZ API (github.com/openpotato/openplzapi.data),",
         "# Lizenz ODbL 1.0, © OpenStreetMap-Mitwirkende. Bezirks-/Stadtteilgrenzen und PLZ-Zuordnung",
         "# sind eine NÄHERUNG; Stadtteil-Schlüssel sind projektinterne Nummern.",
         "", "[bezirke]"]
    for nr in sorted(bezirke, key=int):
        z.append(f"{_q(nr)} = {_q(bezirke[nr])}")
    if aliase:
        z += ["", "[bezirk_aliase]"]
        for nr in sorted(aliase, key=int):
            z.append(f"{_q(nr)} = [{', '.join(_q(a) for a in aliase[nr])}]")
    z += ["", "[stadtteile]"]
    aktueller = None
    for nr, (name, bezirk) in sorted(stadtteile.items(), key=lambda e: int(e[0])):
        if bezirk != aktueller:
            z.append(f"# {bezirke[bezirk]}")
            aktueller = bezirk
        z.append(f"{_q(nr)} = {{ name = {_q(name)}, bezirk = {_q(bezirk)} }}")
    namen = {nr: name for nr, (name, _) in stadtteile.items()}
    for code in sorted(plz):
        z += ["", f"[plz.{_q(code)}]"]
        zugeordnet = plz_stadtteile.get(code, [])
        z.append(f"stadtteile = [{', '.join(_q(nr) for nr in zugeordnet)}]")
        if zugeordnet:
            gesamt = sum(je_plz[code].values())
            anteile = "; ".join(f"{namen[nr]} ({je_plz[code][nr]}/{gesamt})" for nr in zugeordnet)
            z.append(f"quelle = {_q('Straßenverzeichnis (OSM-Auszug): ' + anteile)}")
        elif mit_stadtteilen:
            z.append(f"quelle = {_q(f'Keine Straße erreicht die Schwelle ({MINDESTANTEIL:.0%}, mindestens {MINDESTZAHL} Straßen) – keine Zuordnung ableitbar')}")
        else:
            z.append(f"quelle = {_q('Für diese Stadt ist nur die Bezirksgliederung hinterlegt (der OSM-Auszug führt keine Stadtteile); die PLZ gehört laut Auszug zur Stadt')}")
    z.append(ENDE)
    return "\n".join(z) + "\n"


def ersetze_block(text: str, block: str) -> str:
    muster = re.compile(re.escape(ANFANG) + r".*?" + re.escape(ENDE) + r"\n?", re.S)
    if muster.search(text):
        return muster.sub(lambda _m: block, text, count=1)
    return text.rstrip("\n") + "\n\n" + block


def main() -> int:
    ap = argparse.ArgumentParser(description="Ortsgliederung einer Stadt aus dem OSM-Auszug erzeugen")
    ap.add_argument("csv", type=Path, help="streets.updated.csv aus openpotato/openplzapi.data")
    ap.add_argument("stadt_toml", type=Path, help="Stadtkonfiguration mit [stadt] ags und [erzeugung]")
    ap.add_argument("--stand", default=None, help="Stand des Auszugs (Vorgabe: heutiges Datum)")
    args = ap.parse_args()

    text = args.stadt_toml.read_text(encoding="utf-8")
    daten = tomllib.loads(text)
    ags = daten["stadt"]["ags"]
    erzeugung = daten.get("erzeugung") or {}
    if not erzeugung.get("bezirke"):
        raise SystemExit(f"{args.stadt_toml}: [erzeugung] bezirke fehlt")
    stand = args.stand or date.today().isoformat()

    zeilen = lies_auszug(args.csv, ags)
    if not zeilen:
        raise SystemExit(f"Keine Straßen mit Regionalschlüssel {ags} im Auszug")
    bezirke, stadtteile, plz = gliederung(zeilen, erzeugung)
    aliase = {str(b["nr"]): list(b["aliase"]) for b in erzeugung.get("bezirke", []) if b.get("aliase")}

    plz_stadtteile: dict = {}
    je_plz: dict = {}
    if stadtteile:
        suchnamen = {strassen_kanonisch(name): nr for nr, (name, _) in stadtteile.items()}
        namen, plz_je_name, verworfen = baue_register(args.csv, ags, suchnamen, set(plz))
        tsv = args.stadt_toml.with_name(f"{args.stadt_toml.stem}.strassen.tsv")
        schreibe(tsv, namen, plz_je_name, ags=ags, stand=stand)
        eindeutig = sum(1 for v in namen.values() if len(v) == 1)
        print(f"{tsv}: {len(namen)} Straßennamen, davon {eindeutig} eindeutig")
        print("Verworfen:", ", ".join(f"{k}={v}" for k, v in verworfen.items() if v) or "nichts")
        je_plz = plz_verteilung(namen, plz_je_name)
        plz_stadtteile = plz_zuordnung(je_plz)

    block = toml_block(bezirke, aliase, stadtteile, plz, plz_stadtteile, je_plz,
                       stand=stand, mit_stadtteilen=bool(stadtteile))
    args.stadt_toml.write_text(ersetze_block(text, block), encoding="utf-8")
    print(f"{args.stadt_toml}: {len(bezirke)} Bezirke, {len(stadtteile)} Stadtteile, {len(plz)} PLZ "
          f"(davon {sum(1 for c in plz if plz_stadtteile.get(c))} mit Stadtteil-Zuordnung)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
