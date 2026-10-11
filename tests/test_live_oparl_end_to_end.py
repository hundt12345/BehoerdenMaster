"""End-to-End-Prüfung des OParl-Abrufs gegen Echtantworten vom 11.10.2026.

Die Fixtures in ``fixtures/oparl/koeln/live_2026-10-11`` sind unveränderte Antworten der
Schnittstelle der Stadt Köln. Geprüft wird der komplette Pfad, der auf der deployed Instanz
für sichtbare Daten sorgen muss:

robots.txt → System → Body → Listen (mit echtem ``links.next``) → SQLite → Suche → API.

Zusätzlich wird die Abrufreihenfolge gesichert: Vorlagen müssen vor Sitzungen abgerufen
werden, damit ein knappes Anfragebudget nicht nur Sitzungsdaten, sondern sichtbare Treffer
erzeugt. Genau das war ein Grund für eine Startseite ohne Daten.
"""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from behoerdenmaster.abruf import Abrufer
from behoerdenmaster.api import create_app
from behoerdenmaster.auswertung import heute, jetzt
from behoerdenmaster.betrieb import Betrieb
from behoerdenmaster.config import PROJEKTWURZEL, lade_stadt
from behoerdenmaster.crawler import ENTITAETEN, LaufOptionen, crawle_oparl
from behoerdenmaster.speicher import Speicher
from behoerdenmaster.suche import Suchauftrag, suche, vorgang_detail

from conftest import Uhr

LIVE = PROJEKTWURZEL / "fixtures" / "oparl" / "koeln" / "live_2026-10-11"
HOST = "https://buergerinfo.stadt-koeln.de"
KOERPER_PFAD = "/oparl/bodies/stadtverwaltung_koeln"
KONTAKT = "test@example.test"


def _liste(name: str) -> dict:
    return json.loads((LIVE / name).read_text(encoding="utf-8"))


class LiveOParl:
    """Spielt die echten Antworten nach; Folgeseiten sind leer (nicht aufgezeichnet)."""

    def __init__(self) -> None:
        self.system = _liste("system.json")
        self.bodies = _liste("bodies.json")
        self.listen = {
            "organizations": _liste("organizations.json"),
            "papers": _liste("papers.json"),
            "meetings": _liste("meetings.json"),
        }
        self.robots = (LIVE / "robots.txt").read_text(encoding="utf-8")
        self.pfade: list[str] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._antworte)

    def _antworte(self, request: httpx.Request) -> httpx.Response:
        pfad = request.url.path
        self.pfade.append(pfad + (f"?{request.url.query}" if request.url.query else ""))
        if pfad == "/robots.txt":
            return httpx.Response(200, text=self.robots)
        if pfad == "/oparl/system":
            return httpx.Response(200, json=self.system)
        if pfad == "/oparl/bodies":
            return httpx.Response(200, json=self.bodies)
        for schluessel, antwort in self.listen.items():
            if pfad == f"{KOERPER_PFAD}/{schluessel}":
                if request.url.params.get("page", "1") != "1":
                    # Der reale links.next aus papers.json wird ausgewertet; Seite 2 war
                    # nicht Teil der Aufzeichnung und beendet den Abruf planmäßig.
                    return httpx.Response(200, json={"data": [], "pagination": {}, "links": {}})
                return httpx.Response(200, json=antwort)
        return httpx.Response(404, text="nicht gefunden")


@pytest.fixture
def live() -> LiveOParl:
    return LiveOParl()


def abrufer(live: LiveOParl, uhr: Uhr, budget: int | None = None) -> Abrufer:
    return Abrufer(kontakt=KONTAKT, min_intervall=3.0, max_anfragen=budget,
                   client=httpx.Client(transport=live.transport()),
                   sleep=uhr.schlaf, clock=uhr)


def crawle(live: LiveOParl, uhr: Uhr, sp: Speicher, budget: int | None = None):
    stadt = lade_stadt("koeln")
    quelle = next(q for q in stadt.quellen if q.schluessel == "ris")
    ab = abrufer(live, uhr, budget)
    try:
        return crawle_oparl(sp, stadt, quelle, ab, LaufOptionen(max_anfragen=budget))
    finally:
        ab.schliessen()


def test_echte_robots_txt_erlaubt_unsere_kennung():
    """Die reale robots.txt der Stadt erlaubt unsere Kennung; andere Bots sind blockiert."""
    from protego import Protego

    regeln = Protego.parse((LIVE / "robots.txt").read_text(encoding="utf-8"))
    url = HOST + KOERPER_PFAD + "/papers"
    assert regeln.can_fetch(url, "BehoerdenMaster") is True
    assert regeln.crawl_delay("BehoerdenMaster") is None  # eigener Mindestabstand gilt
    assert regeln.can_fetch(url, "GPTBot") is False  # Kontrollfall: Regelwerk wird verstanden


def test_vollstaendiger_lauf_speichert_echte_vorlagen(db, live, uhr):
    bericht = crawle(live, uhr, db)

    assert bericht.status == "ok", bericht.fehlertext
    assert bericht.zaehler["vorlagen:gespeichert"] == 4
    assert bericht.zaehler["gremien:gespeichert"] == 10
    assert bericht.zaehler["sitzungen:gespeichert"] == 1

    zaehler = db.zaehle()
    assert zaehler["vorgang"] == 4
    # 4 Vorlagendateien plus die 3 echten Sitzungsdateien der Sitzung 33698.
    assert zaehler["dokument"] == 7
    assert zaehler["beratung"] == 4

    vorgang = db.db.execute("SELECT * FROM vorgang WHERE id LIKE '%/vo/135089'").fetchone()
    assert vorgang["titel"] == "Feierabend- und Abendmärkte in Köln schaffen"
    assert vorgang["referenz"] == "AN/1614/2026"
    assert vorgang["datum"] == "2026-10-08"
    assert vorgang["art"] == "Gem. Dringlichkeitsantrag (Rat)"
    assert vorgang["geloescht"] == 0


def test_beratungsfolge_ist_ueber_echte_daten_geschlossen(db, live, uhr):
    """Vorlage → Consultation → TOP → überliefertes Ergebnis, alles aus Originalantworten."""
    crawle(live, uhr, db)
    stadt = lade_stadt("koeln")

    detail = vorgang_detail(db, stadt, HOST + KOERPER_PFAD + "/papers/vo/135088", heute(), zeitpunkt=jetzt())
    assert detail is not None
    assert detail["titel"].startswith("Änderungsantrag zu TOP 3.2")
    assert len(detail["verlauf"]) == 1

    schritt = detail["verlauf"][0]
    assert schritt["gremium"] == "Rat"
    assert schritt["top"]["ergebnis"] == "ungeändert beschlossen"
    assert schritt["sitzung"]["name"] == "11. Sitzung des Rates"
    assert schritt["datum"] == "2026-10-08"
    assert schritt["sitzung"]["url"] == "https://ratsinformation.stadt-koeln.de/si0057.asp?__ksinr=33698"

    assert detail["links"]["original"] == "https://ratsinformation.stadt-koeln.de/vo0050.asp?__kvonr=135088"
    dokumente = {d["name"] for d in detail["dokumente"]}
    assert "Gem. Änderungsantrag nach § 13 (Rat)" in dokumente


def test_suche_findet_echte_vorlagen(db, live, uhr):
    crawle(live, uhr, db)
    stadt = lade_stadt("koeln")

    # Natürliche Frageform im Dativ Plural: muss den Titel „…Abendmärkte…“ trotzdem finden.
    ergebnis = suche(db, Suchauftrag(stadt=stadt, freitext=["Abendmärkten"], limit=10), heute(), zeitpunkt=jetzt())
    assert ergebnis["gesamt"] == 1
    treffer = ergebnis["treffer"][0]
    assert treffer["referenz"] == "AN/1614/2026"
    assert treffer["quelle"]["demo"] is False
    assert treffer["links"]["original"].endswith("__kvonr=135089")


def test_vorlagen_kommen_vor_sitzungen(live, uhr, tmp_path):
    """Abrufreihenfolge: Erst die durchsuchbaren Vorlagen, dann die Sitzungen."""
    assert ENTITAETEN == ("gremien", "vorlagen", "sitzungen")

    with Speicher(tmp_path / "reihenfolge.sqlite") as sp:
        crawle(live, uhr, sp)

    reihenfolge = [p for p in live.pfade if p.startswith(KOERPER_PFAD)]
    erste_vorlage = next(i for i, p in enumerate(reihenfolge) if "/papers" in p)
    erste_sitzung = next(i for i, p in enumerate(reihenfolge) if "/meetings" in p)
    assert erste_vorlage < erste_sitzung


def test_knappes_budget_liefert_trotzdem_sichtbare_treffer(live, uhr, tmp_path):
    """Reicht das Budget nicht für alle Entitäten, müssen trotzdem Vorlagen gespeichert sein.

    Sechs Anfragen: robots.txt, System, Body, Gremien Seite 1, Vorlagen Seite 1 und 2.
    Für die Sitzungen bleibt nichts übrig – die Startseite ist trotzdem nicht leer.
    """
    with Speicher(tmp_path / "budget.sqlite") as sp:
        bericht = crawle(live, uhr, sp, budget=6)

        assert bericht.status == "teilweise"
        assert "meetings" not in " ".join(live.pfade)
        assert sp.zaehle()["vorgang"] == 4

        stadt = lade_stadt("koeln")
        ergebnis = suche(sp, Suchauftrag(stadt=stadt, limit=10), heute(), zeitpunkt=jetzt())
        assert ergebnis["gesamt"] == 4

        # Der Fortschritt ist gespeichert: ein Folgeluf setzt bei den Sitzungen fort.
        stand = sp.stand_lesen("koeln:ris", "sitzungen")
        assert stand is None or stand["naechste_url"] == ""


def test_web_api_zeigt_echte_treffer_nach_dem_abruf(tmp_path, live, uhr):
    pfad = tmp_path / "web.sqlite"
    with Speicher(pfad) as sp:
        crawle(live, uhr, sp)

    app = create_app(pfad, betrieb=Betrieb(demo=False))
    with TestClient(app) as client:
        status = client.get("/api/status").json()
        assert status["bestand_leer"] is False
        assert status["hinweise"] == []
        assert status["zaehler"]["vorgang"] == 4

        antwort = client.post("/api/frage", json={"frage": "Was wurde zu Abendmärkten beschlossen?"})
        assert antwort.status_code == 200
        daten = antwort.json()
        assert daten["gesamt"] >= 1
        assert any(t["referenz"] == "AN/1614/2026" for t in daten["treffer"])
