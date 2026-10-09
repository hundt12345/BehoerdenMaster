"""Gemeinsame Testhilfen: Fake-OParl-Server, Fake-Uhr und Beispielobjekte.

Der Fake-Server bildet die Eigenschaften der OParl-Schnittstelle nach, die der Crawler
nutzt: Listen mit Paginierung über links.next, Filter modified_since (auch in den
Folgelinks), gelöschte Objekte mit deleted=true sowie Fehlerantworten nach Skript.
"""

from __future__ import annotations

import json
from datetime import datetime

import httpx
import pytest

from behoerdenmaster.config import PROJEKTWURZEL, lade_stadt
from behoerdenmaster.geo import Gazetteer
from behoerdenmaster.speicher import Speicher

FIXTURES = PROJEKTWURZEL / "fixtures" / "oparl" / "koeln"
BASIS = "https://buergerinfo.stadt-koeln.de/oparl"
KOERPER = BASIS + "/bodies/stadtverwaltung_koeln"
KOERPER_PFAD = "/oparl/bodies/stadtverwaltung_koeln"


@pytest.fixture
def stadt():
    return lade_stadt("koeln")


@pytest.fixture
def gaz(stadt):
    return Gazetteer(stadt)


@pytest.fixture
def db(tmp_path):
    sp = Speicher(tmp_path / "test.sqlite")
    yield sp
    sp.schliessen()


class Uhr:
    """Fake-Uhr: sleep() verschiebt die Zeit, ohne zu warten."""

    def __init__(self) -> None:
        self.t = 1000.0
        self.schlaefe: list[float] = []

    def __call__(self) -> float:
        return self.t

    def schlaf(self, sekunden: float) -> None:
        self.schlaefe.append(sekunden)
        self.t += sekunden


@pytest.fixture
def uhr():
    return Uhr()


def lade_fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def demo_objekte() -> tuple[list[dict], list[dict], list[dict]]:
    """Echte Auszüge: (Gremien, Sitzungen, Vorlagen)."""
    return (lade_fixture("gremien.json")["data"], lade_fixture("sitzungen.json")["data"],
            lade_fixture("vorlagen.json")["data"])


# --- Synthetische Objekte im OParl-1.1-Format ---------------------------------

def synth_vorlage(nr: int, titel: str, datum: str = "2024-03-01", art: str = "Beschlussvorlage",
                  modified: str = "2024-03-02T10:00:00+01:00", consultations: list[dict] | None = None,
                  location: list[dict] | None = None, deleted: bool = False) -> dict:
    obj = {"id": f"{KOERPER}/papers/vo/{nr}", "type": "https://schema.oparl.org/1.1/Paper",
           "body": KOERPER, "name": titel, "reference": f"V/{nr}/2024", "date": datum,
           "paperType": art, "created": datum + "T09:00:00+01:00", "modified": modified}
    if consultations is not None:
        obj["consultation"] = consultations
    if location is not None:
        obj["location"] = location
    if deleted:
        return {"id": obj["id"], "type": obj["type"], "deleted": True, "modified": modified,
                "created": obj["created"]}
    return obj


def synth_beratung(nr: int, vorlage_nr: int, sitzung_nr: int, top_nr: int | None,
                   gremium_nr: int = 4, rolle: str = "Entscheidung") -> dict:
    cons = {"id": f"{KOERPER}/consultations/{nr}", "type": "https://schema.oparl.org/1.1/Consultation",
            "paper": f"{KOERPER}/papers/vo/{vorlage_nr}", "meeting": f"{KOERPER}/meetings/{sitzung_nr}",
            "organization": [f"{KOERPER}/organizations/gr/{gremium_nr}"], "authoritative": True,
            "role": rolle, "created": "2024-03-02T10:00:00+01:00", "modified": "2024-03-02T10:00:00+01:00"}
    if top_nr is not None:
        cons["agendaItem"] = f"{KOERPER}/agendaitems/{top_nr}"
    return cons


def synth_sitzung(nr: int, name: str, start: str, gremium_nr: int = 4,
                  tops: list[dict] | None = None, modified: str = "2024-03-02T10:00:00+01:00") -> dict:
    return {"id": f"{KOERPER}/meetings/{nr}", "type": "https://schema.oparl.org/1.1/Meeting",
            "name": name, "start": start, "end": start[:11] + "23:00:00+01:00",
            "organization": [f"{KOERPER}/organizations/gr/{gremium_nr}"],
            "agendaItem": tops or [], "created": "2024-01-01T10:00:00+01:00", "modified": modified}


def synth_top(nr: int, sitzung_nr: int, nummer: str, name: str, ergebnis: str = "",
              consultation_nr: int | None = None) -> dict:
    top = {"id": f"{KOERPER}/agendaitems/{nr}", "type": "https://schema.oparl.org/1.1/AgendaItem",
           "meeting": f"{KOERPER}/meetings/{sitzung_nr}", "number": nummer, "order": nr,
           "name": name, "public": True, "modified": "2024-03-02T10:00:00+01:00"}
    if ergebnis:
        top["result"] = ergebnis
    if consultation_nr is not None:
        top["consultation"] = f"{KOERPER}/consultations/{consultation_nr}"
    return top


def synth_gremium(nr: int, name: str, modified: str = "2024-01-01T00:00:00+01:00") -> dict:
    return {"id": f"{KOERPER}/organizations/gr/{nr}", "type": "https://schema.oparl.org/1.1/Organization",
            "body": KOERPER, "name": name, "modified": modified}


class FakeOParl:
    """Simulierter OParl-Server für httpx.MockTransport."""

    def __init__(self, papers=(), meetings=(), orgs=(), seite: int = 2, robots: str | None = None):
        self.objekte = {"papers": list(papers), "meetings": list(meetings), "organizations": list(orgs)}
        self.seite = seite
        self.robots = robots
        self.anfragen: list[tuple[str, dict]] = []
        self.skript: dict[str, list[int]] = {}
        self.retry_after: str | None = None

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._antworte)

    def _antworte(self, request: httpx.Request) -> httpx.Response:
        pfad = request.url.path
        self.anfragen.append((str(request.url), dict(request.url.params)))
        if pfad == "/robots.txt":
            if self.robots is None:
                return httpx.Response(404)
            return httpx.Response(200, text=self.robots)
        for schluessel, codes in self.skript.items():
            if pfad.endswith("/" + schluessel) and codes:
                code = codes.pop(0)
                if code != 200:
                    kopf = {"Retry-After": self.retry_after} if self.retry_after else {}
                    return httpx.Response(code, headers=kopf, text="Fehler")
        if pfad == "/oparl/system":
            return httpx.Response(200, json={
                "id": BASIS + "/system", "type": "https://schema.oparl.org/1.1/System",
                "oparlVersion": "https://schema.oparl.org/1.1/", "body": BASIS + "/bodies",
                "name": "Ratsinformationssystem (Test)"})
        if pfad == "/oparl/bodies":
            return httpx.Response(200, json={"data": [{
                "id": KOERPER, "type": "https://schema.oparl.org/1.1/Body", "name": "Stadt Köln (Test)",
                "ags": "05315000", "organization": KOERPER + "/organizations",
                "meeting": KOERPER + "/meetings", "paper": KOERPER + "/papers"}],
                "links": {}})
        for schluessel in ("papers", "meetings", "organizations"):
            if pfad == f"{KOERPER_PFAD}/{schluessel}":
                return self._liste(schluessel, request.url)
        return httpx.Response(404, text="nicht gefunden")

    def _liste(self, schluessel: str, url: httpx.URL) -> httpx.Response:
        seite = int(url.params.get("page", "1"))
        seit = url.params.get("modified_since")
        daten = list(self.objekte[schluessel])
        if seit:
            grenze = datetime.fromisoformat(seit)
            # Objekte ohne modified (in Auszügen vorhanden) gelten als immer geändert.
            daten = [o for o in daten if not o.get("modified")
                     or datetime.fromisoformat(o["modified"]) >= grenze]
        daten.sort(key=lambda o: o["id"])
        abschnitt = daten[(seite - 1) * self.seite: seite * self.seite]
        links = {}
        if seite * self.seite < len(daten):
            links["next"] = str(url.copy_with(params={**dict(url.params), "page": str(seite + 1)}))
        return httpx.Response(200, json={
            "data": abschnitt,
            "pagination": {"currentPage": seite, "elementsPerPage": self.seite},
            "links": links,
        })
