"""Bonn: neue /oparl/-Adresse und die Eigenheiten der ALLRIS-Schnittstelle.

Hintergrund: Die früher konfigurierte Adresse /public/oparl/system steht seit dem
24.02.2026 hinter einer Zugangsprüfung (HTTP 403, mandariOSS/mandari, Issue #88); seit
dem 02.10.2026 liefert Bonn OParl 1.1 (ALLRIS) unter /oparl/system ohne Prüfung aus.
Der Fake-Server bildet genau diese Form nach: Körperschaft „Bundesstadt Bonn“,
Gremium mit Typ-Kürzel, Beratung mit kleingeschriebenem `agendaitem` und
eingebetteten Verweis-Objekten, Folgeseiten ohne modified_since-Parameter.
Kein Test greift aufs Netz zu (httpx.MockTransport).
"""

from __future__ import annotations

import httpx
import pytest

from behoerdenmaster.abruf import Abrufer
from behoerdenmaster.config import lade_stadt
from behoerdenmaster.crawler import LaufOptionen, crawle_oparl

BASIS = "https://www.bonn.sitzung-online.de/oparl"
KOERPER = BASIS + "/bodies/bundesstadt_bonn"


@pytest.fixture
def bonn():
    return lade_stadt("bonn")


def _objekte():
    gremium = {
        "id": f"{KOERPER}/organizations/7", "type": "gr", "Type": "https://schema.oparl.org/1.1/Organization",
        "name": "Bezirksvertretung Bonn", "modified": "2026-09-01T10:00:00+02:00",
    }
    top = {
        "id": f"{KOERPER}/agendaitems/34", "type": "https://schema.oparl.org/1.1/AgendaItem",
        "number": "3.1", "order": 1, "name": "Radweg an der Beispielstraße", "public": True,
        "consultation": f"{KOERPER}/consultations/99", "modified": "2026-10-01T10:00:00+02:00",
    }
    sitzung = {
        "id": f"{KOERPER}/meetings/12", "type": "https://schema.oparl.org/1.1/Meeting",
        "name": "Bezirksvertretung Bonn", "start": "2026-10-20T17:00:00+02:00",
        "end": "2026-10-20T19:00:00+02:00", "organization": [gremium["id"]],
        "agendaItem": [top], "modified": "2026-10-01T10:00:00+02:00",
    }
    vorlage = {
        "id": f"{KOERPER}/papers/4711", "type": "https://schema.oparl.org/1.1/Paper",
        "name": "Radweg an der Beispielstraße in Bonn", "reference": "DS-Nr. 251234",
        "paperType": "Beschlussvorlage", "date": "2026-09-30",
        "created": "2026-09-30T09:00:00+02:00", "modified": "2026-10-01T10:00:00+02:00",
        "consultation": [{
            "id": f"{KOERPER}/consultations/99", "type": "https://schema.oparl.org/1.1/Consultation",
            # ALLRIS: TOP-Verweis kleingeschrieben, Verweise als eingebettete Objekte.
            "meeting": {"id": sitzung["id"]}, "agendaitem": {"id": top["id"]},
            "organization": [{"id": gremium["id"]}], "role": "Beschlussfassung",
            "authoritative": True, "modified": "2026-10-01T10:00:00+02:00",
        }],
    }
    return gremium, sitzung, vorlage


class FakeAllris:
    def __init__(self):
        self.anfragen: list[str] = []
        gremium, sitzung, vorlage = _objekte()
        self.listen = {"organizations": [gremium], "meetings": [sitzung], "papers": [vorlage]}

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.anfragen.append(str(req.url))
        pfad = req.url.path
        if pfad == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\n")
        if pfad == "/oparl/system":
            return httpx.Response(200, json={
                "id": BASIS + "/system", "type": "https://schema.oparl.org/1.1/System",
                "oparlVersion": "https://schema.oparl.org/1.1/", "body": BASIS + "/bodies"})
        if pfad == "/oparl/bodies":
            return httpx.Response(200, json={"data": [{
                "id": KOERPER, "type": "https://schema.oparl.org/1.1/Body",
                "name": "Bundesstadt Bonn", "organization": KOERPER + "/organizations",
                "meeting": KOERPER + "/meetings", "paper": KOERPER + "/papers"}], "links": {}})
        for name, objekte in self.listen.items():
            if pfad == f"/oparl/bodies/bundesstadt_bonn/{name}":
                return httpx.Response(200, json={"data": objekte, "links": {}})
        return httpx.Response(404, text="nicht gefunden")


def test_bonn_nutzt_die_neue_oparl_adresse(bonn):
    (quelle,) = bonn.quellen
    assert quelle.system_url == "https://www.bonn.sitzung-online.de/oparl/system"
    assert "/public/" not in quelle.system_url  # alte Adresse: seit 24.02.2026 hinter Zugangsprüfung


def test_allris_lauf_speichert_vorgang_sitzung_und_beratung(bonn, db, uhr):
    fake = FakeAllris()
    abrufer = Abrufer(kontakt="test@example.test", min_intervall=3.0, sleep=uhr.schlaf, clock=uhr,
                      client=httpx.Client(transport=httpx.MockTransport(fake)))
    quelle = bonn.quellen[0]
    bericht = crawle_oparl(db, bonn, quelle, abrufer, LaufOptionen(seit="2026-01-01"))
    assert bericht.status == "ok", bericht.fehlertext
    # Der Abruf beginnt an der neuen System-Adresse und wählt die Körperschaft über den Namen.
    assert fake.anfragen[1].startswith(BASIS + "/system")

    zahlen = db.zaehle_quelle("bonn:ris")
    assert zahlen["vorgaenge"] == 1 and zahlen["sitzungen"] == 1 and zahlen["gremien"] == 1
    beratung = db.db.execute(
        "SELECT sitzung_id, top_id FROM beratung WHERE vorgang_id = ?", (f"{KOERPER}/papers/4711",)
    ).fetchone()
    assert beratung["sitzung_id"] == f"{KOERPER}/meetings/12"
    assert beratung["top_id"] == f"{KOERPER}/agendaitems/34"  # aus `agendaitem`, nicht leer
    gremium = db.db.execute("SELECT typ FROM gremium WHERE id = ?", (f"{KOERPER}/organizations/7",)).fetchone()
    assert gremium["typ"]  # Typ-URL/Kürzel der ALLRIS-Antwort statt leer
