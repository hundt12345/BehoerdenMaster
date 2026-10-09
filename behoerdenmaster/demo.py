"""Beispieldaten aus fixtures/ laden, damit die Anwendung ohne Netzzugang ausprobiert werden kann.

Die Dateien sind Auszüge echter OParl-Antworten der Stadt Köln (Datenlizenz Deutschland –
Zero – 2.0). Sie werden über denselben Verarbeitungspfad wie der Crawler gespeichert.
Die Quelle heißt „demo“ und ist in der Oberfläche als Beispieldaten gekennzeichnet.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .config import PROJEKTWURZEL, Stadt
from .crawler import LaufBericht, _verarbeite
from .geo import Gazetteer
from .speicher import Speicher

FIXTURES = PROJEKTWURZEL / "fixtures" / "oparl" / "koeln"
REIHENFOLGE = {"Organization": 0, "Meeting": 1, "Paper": 2}
ENTITAET = {"Organization": "gremien", "Meeting": "sitzungen", "Paper": "vorlagen"}


def demo_quell_id(stadt: Stadt) -> str:
    return f"{stadt.schluessel}:demo"


def lade_demo(speicher: Speicher, stadt: Stadt, verzeichnis: Path = FIXTURES) -> dict[str, int]:
    quell_id = demo_quell_id(stadt)
    speicher.quelle_anlegen(
        quell_id, stadt.schluessel, "demo", "demo",
        "Beispieldaten (Auszug aus der OParl-Schnittstelle, offline)", "", demo=True,
    )
    speicher.quelle_status(quell_id, "demo", "", erfolg=True)

    objekte: list[dict] = []
    for pfad in sorted(verzeichnis.glob("*.json")):
        daten = json.loads(pfad.read_text(encoding="utf-8"))
        if isinstance(daten, dict) and isinstance(daten.get("data"), list):
            objekte.extend(daten["data"])
        elif isinstance(daten, list):
            objekte.extend(daten)
        elif isinstance(daten, dict):
            objekte.append(daten)

    def typ_schluessel(obj: dict) -> str:
        return str(obj.get("type", "")).rsplit("/", 1)[-1]

    objekte = [o for o in objekte if typ_schluessel(o) in REIHENFOLGE]
    objekte.sort(key=lambda o: REIHENFOLGE[typ_schluessel(o)])

    gaz = Gazetteer(stadt)
    bericht = LaufBericht(quelle_id=quell_id)
    zaehler: Counter[str] = Counter()
    for obj in objekte:
        typ = typ_schluessel(obj)
        _verarbeite(speicher, gaz, quell_id, ENTITAET[typ], obj, bericht)
        zaehler[typ] += 1
    return dict(zaehler)


def loesche_demo(speicher: Speicher, stadt: Stadt) -> None:
    speicher.loesche_quelle_daten(demo_quell_id(stadt))
    speicher.db.execute("DELETE FROM quelle WHERE id = ?", (demo_quell_id(stadt),))
    speicher.db.commit()
