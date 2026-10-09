import pytest
from fastapi.testclient import TestClient

from behoerdenmaster.api import create_app
from behoerdenmaster.demo import lade_demo
from behoerdenmaster.speicher import Speicher


@pytest.fixture
def client(tmp_path, stadt):
    pfad = tmp_path / "api.sqlite"
    with Speicher(pfad) as sp:
        lade_demo(sp, stadt)
    return TestClient(create_app(pfad))


def test_startseite_liefert_oberflaeche(client):
    antwort = client.get("/")
    assert antwort.status_code == 200
    assert "BehoerdenMaster" in antwort.text and "PLZ-Suche" in antwort.text


def test_status_zeigt_quellen_und_bestand(client):
    daten = client.get("/api/status").json()
    assert any(q["demo"] for q in daten["quellen"])
    assert daten["zaehler"]["vorgang"] == 1
    assert daten["staedte"][0]["schluessel"] == "koeln"


def test_frage_liefert_verstaendnis_und_treffer(client):
    antwort = client.post("/api/frage", json={"frage": "Welche Bauvorhaben sind in Köln Ehrenfeld geplant?"})
    assert antwort.status_code == 200
    daten = antwort.json()
    assert daten["verstanden"]["themen"] == ["bauen"]
    assert {o["name"] for o in daten["verstanden"]["orte"]} == {"Ehrenfeld"}
    assert daten["gesamt"] == 0  # Demo-Auszug enthält keine Ehrenfeld-Vorlagen
    assert daten["hinweise"]


def test_frage_findet_demo_vorgang(client):
    daten = client.post("/api/frage", json={"frage": "Gibt es Mitteilungen zur OParl-Schnittstelle?"}).json()
    assert daten["gesamt"] == 0 or daten["gesamt"] == 1
    daten = client.post("/api/frage", json={"frage": "OParl Schnittstelle"}).json()
    assert daten["gesamt"] == 1
    assert daten["treffer"][0]["referenz"] == "1724/2021"


def test_frage_validierung(client):
    assert client.post("/api/frage", json={"frage": "ab"}).status_code == 422


def test_plz_ungueltig_und_bekannt(client):
    assert client.get("/api/plz/5082").status_code == 422
    assert client.get("/api/plz/abcde").status_code == 422
    daten = client.get("/api/plz/50827").json()
    assert daten["plz"] == "50827"
    assert any("Näherung" in h for h in daten["hinweise"])
    fremd = client.get("/api/plz/10115").json()
    assert any("gehört nicht" in h for h in fremd["hinweise"])


def test_plz_unbekanntes_thema(client):
    assert client.get("/api/plz/50827?thema=unsinn").status_code == 422


def test_vorgang_detail_und_404(client):
    oparl_id = "https://buergerinfo.stadt-koeln.de/oparl/bodies/stadtverwaltung_koeln/papers/vo/101373"
    antwort = client.get("/api/vorgang", params={"id": oparl_id})
    assert antwort.status_code == 200
    assert antwort.json()["titel"].startswith("Ratsinformationssystem")
    assert client.get("/api/vorgang", params={"id": oparl_id + "9"}).status_code == 404
