#!/usr/bin/env python3
"""Erzeugt das Straßenverzeichnis einer Stadt aus dem OpenStreetMap-Auszug.

Das Werkzeug ist **nicht Teil der Anwendung**: Es läuft einmalig auf einem Rechner mit Netz
und schreibt eine kleine, versionierbare Datei nach ``config/cities/``. Die Anwendung liest
danach nur diese Datei und ruft selbst nie OpenStreetMap ab.

Datenquelle (geprüft am 13.10.2026):

* ``https://github.com/openpotato/openplzapi.data`` – Datenexport der OpenPLZ API,
  Datei ``src/de/osm/streets.updated.csv`` (Straßenname, PLZ, Gemeinde, Regionalschlüssel,
  Bezirk, Stadtteil). Geometrisch aus OpenStreetMap abgeleitet, Lizenz **ODbL 1.0**
  (Namensnennung „© OpenStreetMap-Mitwirkende“).

Aufruf::

    curl -L -o /tmp/streets.updated.csv \\
      https://github.com/openpotato/openplzapi.data/raw/main/src/de/osm/streets.updated.csv
    python tools/strassen_aus_osm.py /tmp/streets.updated.csv config/cities/koeln.strassen.tsv

Zieldatei: je Zeile ``name<TAB>Stadtteile<TAB>PLZ``. Namen sind normalisiert (Umlaute
ausgeschrieben, Kleinschreibung) und Straßenkürzel sind aufgelöst
(„Feldgärtenstr.“ → „feldgaertenstrasse“), damit derselbe Vergleich wie in der Anwendung
gilt. Stadtteile stehen als ``Nummer:Anzahl`` – die Anzahl zeigt, wie viele Straßenobjekte
des Auszugs diesen Namen in diesem Stadtteil tragen. Mehrere Stadtteile bedeuten
**mehrdeutig**: Die Anwendung rät dann keinen Stadtteil, sondern nutzt die Kandidaten nur,
um eine Suchanfrage zu verstehen.

Zusätzlich gibt das Werkzeug die daraus abgeleitete **PLZ → Stadtteile**-Zuordnung aus
(Standard: Stadtteil ab 10 % Anteil und mindestens drei Straßen der PLZ). Diese Zeilen
werden in die Stadtkonfiguration übernommen und dort als Näherung gekennzeichnet.
"""

from __future__ import annotations

import argparse
import collections
import csv
import sys
import tomllib
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from behoerdenmaster.normalize import strassen_kanonisch  # noqa: E402

QUELLE = ("OpenStreetMap-Auszug aus dem OpenPLZ-API-Datenexport "
          "(github.com/openpotato/openplzapi.data, src/de/osm/streets.updated.csv)")
LIZENZ = "Open Database License (ODbL) 1.0 – © OpenStreetMap-Mitwirkende"
# Ein Stadtteil gehört zur PLZ, wenn er einen erkennbaren Anteil der Straßen dieser PLZ trägt.
MINDESTANTEIL = 0.10
MINDESTZAHL = 3


def lade_stadtkonfiguration(stadt_toml: Path) -> tuple[str, str, dict[str, str], dict[str, str], set[str]]:
    """Aus der Stadtkonfiguration: Regionalschlüssel, Stadtteile (normalisiert und lesbar), PLZ-Gebiet."""
    daten = tomllib.loads(stadt_toml.read_text(encoding="utf-8"))
    stadt = daten["stadt"]
    suchnamen: dict[str, str] = {}
    anzeige: dict[str, str] = {}
    for nr, eintrag in daten.get("stadtteile", {}).items():
        anzeige[str(nr)] = eintrag["name"]
        for name in (eintrag["name"], *eintrag.get("aliase", [])):
            suchnamen.setdefault(strassen_kanonisch(name), str(nr))
    return stadt["schluessel"], stadt["ags"], suchnamen, anzeige, set(daten.get("plz", {}))


def baue_register(csv_pfad: Path, ags: str, stadtteile: dict[str, str],
                  plz_gebiet: set[str]) -> tuple[dict[str, collections.Counter], dict[str, set[str]], dict[str, int]]:
    """Straßen aus dem Auszug: Name → Stadtteil-Zähler, Name → PLZ-Menge, Verworfene."""
    namen: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    plz: dict[str, set[str]] = collections.defaultdict(set)
    verworfen = {"fremde_gemeinde": 0, "ohne_namen": 0, "unbekannter_stadtteil": 0, "ausserhalb_plz": 0}
    with csv_pfad.open(newline="", encoding="utf-8") as fh:
        for zeile in csv.DictReader(fh):
            if zeile.get("RegionalKey") != ags:
                verworfen["fremde_gemeinde"] += 1
                continue
            name = strassen_kanonisch(zeile.get("Name", ""))
            if not name:
                verworfen["ohne_namen"] += 1
                continue
            stadtteil = stadtteile.get(strassen_kanonisch(zeile.get("Suburb", "")))
            if not stadtteil:
                # Stadtteile, die nicht in der Stadtkonfiguration stehen (z. B. „Marsdorf“),
                # sind kein Grund zu raten: Der Datensatz wird verworfen.
                verworfen["unbekannter_stadtteil"] += 1
                continue
            plz_code = (zeile.get("PostalCode") or "").strip()
            if plz_code and plz_code not in plz_gebiet:
                # Streufälle aus Nachbargemeinden (z. B. Leverkusener PLZ in Köln).
                verworfen["ausserhalb_plz"] += 1
                continue
            namen[name][stadtteil] += 1
            if plz_code:
                plz[name].add(plz_code)
    return namen, plz, verworfen


def schreibe(tsv_pfad: Path, namen: dict[str, collections.Counter], plz: dict[str, set[str]],
             *, ags: str, stand: str) -> None:
    kopf = [
        "# BehoerdenMaster – Straßenverzeichnis (abgeleitet, Näherung)",
        f"# Quelle: {QUELLE}",
        f"# Lizenz: {LIZENZ}",
        f"# Stand: {stand}",
        f"# Filter: Regionalschlüssel {ags} (Stadt aus der gleichnamigen .toml)",
        "# Aufbau: name<TAB>Stadtteile<TAB>PLZ  (Stadtteile als Nummer:Anzahl der Straßenobjekte)",
        "# PLZ-Ableitung: Stadtteil ab 10 % und mindestens drei Straßen der PLZ (Werkzeug-Ausgabe)",
        "# Mehrere Stadtteile = mehrdeutiger Name; die Anwendung ordnet dann keinen Stadtteil zu,",
        "# sondern nutzt die Kandidaten nur, um eine Suchanfrage zu verstehen.",
        "# Erzeugt von tools/strassen_aus_osm.py",
    ]
    zeilen = []
    for name in sorted(namen):
        stadtteile = ",".join(f"{nr}:{zahl}" for nr, zahl in sorted(namen[name].items()))
        zeilen.append(f"{name}\t{stadtteile}\t{','.join(sorted(plz.get(name, ())))}")
    tsv_pfad.write_text("\n".join(kopf + zeilen) + "\n", encoding="utf-8")


def plz_verteilung(namen: dict[str, collections.Counter],
                   plz: dict[str, set[str]]) -> dict[str, collections.Counter]:
    """Je PLZ: Wie viele Straßen liegen in welchem Stadtteil?"""
    je_plz: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for name, stadtteile in namen.items():
        for code in plz.get(name, ()):
            for nr, zahl in stadtteile.items():
                je_plz[code][nr] += zahl
    return je_plz


def plz_zuordnung(je_plz: dict[str, collections.Counter]) -> dict[str, list[str]]:
    """PLZ → Stadtteile nach der dokumentierten Schwelle (Näherung)."""
    zuordnung: dict[str, list[str]] = {}
    for code, zaehler in je_plz.items():
        gesamt = sum(zaehler.values())
        zuordnung[code] = [nr for nr, zahl in sorted(zaehler.items())
                           if zahl >= MINDESTZAHL and zahl / gesamt >= MINDESTANTEIL]
    return zuordnung


def main() -> int:
    ap = argparse.ArgumentParser(description="Straßenverzeichnis aus einem OSM-Auszug erzeugen")
    ap.add_argument("csv", type=Path, help="streets.updated.csv aus openpotato/openplzapi.data")
    ap.add_argument("ziel", type=Path, help="Zieldatei, z. B. config/cities/koeln.strassen.tsv")
    ap.add_argument("--stadt-toml", type=Path, default=None,
                    help="Stadtkonfiguration (Vorgabe: gleichnamige .toml neben der Zieldatei)")
    ap.add_argument("--stand", default=None, help="Stand des Auszugs (Vorgabe: heutiges Datum)")
    args = ap.parse_args()

    stadt_toml = args.stadt_toml or args.ziel.parent / f"{args.ziel.name.split('.strassen')[0]}.toml"
    schluessel, ags, stadtteile, anzeigenamen, plz_gebiet = lade_stadtkonfiguration(stadt_toml)
    namen, plz, verworfen = baue_register(args.csv, ags, stadtteile, plz_gebiet)
    schreibe(args.ziel, namen, plz, ags=ags, stand=args.stand or date.today().isoformat())

    eindeutig = sum(1 for v in namen.values() if len(v) == 1)
    print(f"{args.ziel}: {len(namen)} Straßennamen, davon {eindeutig} eindeutig "
          f"({len(namen) - eindeutig} mehrdeutig)")
    print("Verworfen:", ", ".join(f"{k}={v}" for k, v in verworfen.items() if v) or "nichts")

    je_plz = plz_verteilung(namen, plz)
    zuordnung = plz_zuordnung(je_plz)
    print("\n# PLZ → Stadtteile laut Straßenverzeichnis (Näherung, Schwelle "
          f"{MINDESTANTEIL:.0%} und mindestens {MINDESTZAHL} Straßen)")
    for code in sorted(plz_gebiet - set(zuordnung)):
        print(f"# {code}: keine Straßen im Auszug – keine Zuordnung ableitbar")
    for code in sorted(zuordnung):
        if not zuordnung[code]:
            print(f"# {code}: keine Straße erreicht die Schwelle – keine Zuordnung ableitbar")
            continue
        anteile = []
        gesamt = sum(je_plz[code].values())
        for nr in zuordnung[code]:
            anteile.append(f"{anzeigenamen.get(nr, nr)} ({je_plz[code][nr]}/{gesamt})")
        print(f'[plz."{code}"]')
        print(f'stadtteile = [{", ".join(chr(34) + nr + chr(34) for nr in zuordnung[code])}]')
        print(f'quelle = "Straßenverzeichnis (OSM-Auszug): {"; ".join(anteile)}"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
