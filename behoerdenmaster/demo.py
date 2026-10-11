"""Beispieldaten aus fixtures/ laden, damit die Anwendung ohne Netzzugang ausprobiert werden kann.

OParl: Auszüge echter Antworten (Datenlizenz Deutschland – Zero – 2.0).
Bauleitplanung: kuratierte Auszüge öffentlicher JSON-Felder mit dokumentiertem Stand;
keine pauschale Übertragung der OParl-Lizenz. Echte Daten werden niemals durch Demo überschrieben.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .bauleitplanung import parse_dokumente, parse_liste
from .config import PROJEKTWURZEL, Stadt
from .crawler import LaufBericht, _verarbeite
from .geo import Gazetteer
from .speicher import Speicher

FIXTURES = PROJEKTWURZEL / "fixtures" / "oparl" / "koeln"
REIHENFOLGE = {"Organization": 0, "Meeting": 1, "Paper": 2}
ENTITAET = {"Organization": "gremien", "Meeting": "sitzungen", "Paper": "vorlagen"}
PLANE_FIXTURES = PROJEKTWURZEL / "fixtures" / "bauleitplanung" / "koeln"
TABELLEN = {"Organization": "gremium", "Meeting": "sitzung", "Paper": "vorgang"}


def _ist_echt(speicher: Speicher, tabelle: str, ident: str) -> bool:
    zeile = speicher.db.execute(
        f"SELECT q.demo FROM {tabelle} v JOIN quelle q ON q.id=v.quelle_id WHERE v.id=?", (ident,)
    ).fetchone()
    return bool(zeile and not zeile["demo"])


def demo_quell_id(stadt: Stadt) -> str:
    return f"{stadt.schluessel}:demo"


def lade_demo(speicher: Speicher, stadt: Stadt, verzeichnis: Path = FIXTURES) -> dict[str, int]:
    if stadt.schluessel != "koeln" and verzeichnis == FIXTURES:
        raise ValueError("Die mitgelieferten Demo-Fixtures sind nur für Köln vorhanden.")
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
        if _ist_echt(speicher, TABELLEN[typ], obj.get("id", "")):
            continue
        _verarbeite(speicher, gaz, quell_id, ENTITAET[typ], obj, bericht)
        zaehler[typ] += 1
    if verzeichnis == FIXTURES and any(q.typ == "bauleitplanung" for q in stadt.quellen):
        zaehler["Bauleitverfahren"] = lade_plan_demo(speicher, stadt)
    return dict(zaehler)


def lade_plan_demo(speicher: Speicher, stadt: Stadt) -> int:
    if stadt.schluessel != "koeln":
        raise ValueError("Die mitgelieferten Bauleit-Demo-Fixtures sind nur für Köln vorhanden.")
    cfg = next(q for q in stadt.quellen if q.typ == "bauleitplanung")
    qid = stadt.quell_id(cfg) + "-demo"
    daten = json.loads((PLANE_FIXTURES / "verfahren.json").read_text(encoding="utf-8"))
    stand = daten["meta"]["fixture"]["stand"]
    speicher.quelle_anlegen(qid, stadt.schluessel, cfg.schluessel + "-demo", cfg.typ,
                            f"Bauleitplanung Online (Offline-Auszug, Stand {stand})", cfg.system_url, demo=True)
    anzahl = 0
    for plan in parse_liste(daten, cfg)[0]:
        if _ist_echt(speicher, "vorgang", plan.url):
            continue
        plan.speichern(speicher, stadt, qid, stand, festgestellt=stand)
        if plan.url.endswith("/bf1b9a19-a120-446b-99a0-42f1b6e9269c/public/detail"):
            html = (PLANE_FIXTURES / "clubkultur-detail.html").read_text(encoding="utf-8")
            speicher.plan_dokumente_speichern(plan.url, parse_dokumente(html, plan.url), "ok")
        anzahl += 1
    speicher.quelle_status(qid, "demo")
    return anzahl


def loesche_demo(speicher: Speicher, stadt: Stadt) -> None:
    quellen = [q["id"] for q in speicher.quellen() if q["stadt"] == stadt.schluessel and q["demo"]]
    for qid in quellen:
        speicher.loesche_quelle_daten(qid)
        with speicher.db:
            speicher.db.execute("DELETE FROM quelle WHERE id=?", (qid,))
