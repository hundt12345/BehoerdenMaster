"""Stream: „die neuesten Vorgänge für Bonn“, „alle seit dem 9.10.“.

Geprüft werden die drei Schichten getrennt und zusammen: die Datumserkennung der
Fragesprache (nlq), die rein chronologische Sortierung (suche/stream) und die
Endpunkte bzw. der CLI-Befehl. Alles läuft gegen die lokale Testdatenbank –
der Stream darf wie jede Suche keinen Behördenabruf auslösen.
"""

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from behoerdenmaster.api import create_app
from behoerdenmaster.cli import main
from behoerdenmaster.config import lade_stadt
from behoerdenmaster.geo import Gazetteer
from behoerdenmaster.nlq import parse_datum, verstehe
from behoerdenmaster.normalize import norm
from behoerdenmaster.speicher import Speicher
from behoerdenmaster.stream import hole_stream

REFERENZ = date(2026, 10, 14)  # ein Mittwoch nach dem 9.10.2026


def _speichern(sp, vid: str, titel: str, datum: str, zusatz: str = "") -> None:
    sp.vorgang_speichern(
        {"id": vid, "quelle_id": "koeln:ris", "referenz": "", "titel": titel,
         "art": "Beschlussvorlage", "datum": datum, "geaendert": "", "web": "",
         "suchtext": norm(f"{titel} {zusatz}"), "geloescht": 0},
        [], [], [], set(),
    )


@pytest.fixture
def bestand(db, stadt):
    db.quelle_anlegen("koeln:ris", "koeln", "ris", "oparl", "Ratsinformation Köln (OParl)", "")
    _speichern(db, "test:alt", "Verkehrsberuhigung alt", "2026-10-01")
    _speichern(db, "test:grenze", "Neubau an der Grenze", "2026-10-09")
    _speichern(db, "test:neu", "Verkehrsberuhigung neu", "2026-10-12")
    _speichern(db, "test:davor", "Neubau davor", "2026-10-08")
    _speichern(db, "test:ohne", "Vorgang ohne Datum", "")
    return db


# --- Datumserkennung ---------------------------------------------------------

@pytest.mark.parametrize("angabe,erwartet", [
    ("2026-10-09", date(2026, 10, 9)),
    ("9.10.", date(2026, 10, 9)),
    ("09.10.2026", date(2026, 10, 9)),
    ("9.10.2025", date(2025, 10, 9)),
    ("9. Oktober", date(2026, 10, 9)),
    ("9. Oktober 2026", date(2026, 10, 9)),
    ("heute", REFERENZ),
    ("gestern", date(2026, 10, 13)),
])
def test_parse_datum_formen(angabe, erwartet):
    assert parse_datum(angabe, REFERENZ) == erwartet


@pytest.mark.parametrize("angabe", ["31.2.", "2026-13-01", "bald", "9.13."])
def test_parse_datum_fehler(angabe):
    with pytest.raises(ValueError):
        parse_datum(angabe, REFERENZ)


def test_parse_datum_ohne_jahr_nimmt_juengstes_vorkommen():
    # Im Januar meint „9.10.“ den Oktober des Vorjahres, kein Zukunftsdatum.
    assert parse_datum("9.10.", date(2027, 1, 15)) == date(2026, 10, 9)


def test_nlq_neueste_vorgaenge_fuer_bonn():
    bonn = lade_stadt("bonn")
    v = verstehe("Gib mir die neuesten Vorgänge für Bonn alle", bonn, Gazetteer(bonn),
                 referenz_datum=REFERENZ)
    assert v.neueste is True
    assert v.von is None and v.freitext == [] and v.themen == []
    assert any("Stream:" in h for h in v.hinweise)


def test_nlq_alle_seit_dem_9_10(stadt, gaz):
    v = verstehe("Alle Vorgänge seit dem 9.10.", stadt, gaz, referenz_datum=REFERENZ)
    assert v.von == "2026-10-09" and v.bis is None
    assert v.neueste is True, "„alle seit …“ ist eine Stream-Frage: ab Datum, neueste zuerst"
    assert v.freitext == []
    assert any("09.10.2026" in h for h in v.hinweise)


def test_nlq_seit_dem_9_oktober_und_mit_jahr(stadt, gaz):
    v = verstehe("Neueste Vorgänge seit dem 9. Oktober", stadt, gaz, referenz_datum=REFERENZ)
    assert v.von == "2026-10-09" and v.neueste is True
    v = verstehe("Vorgänge seit dem 9.10.2025", stadt, gaz, referenz_datum=REFERENZ)
    assert v.von == "2025-10-09"


def test_nlq_am_datum_ist_genau_dieser_tag(stadt, gaz):
    v = verstehe("Was wurde am 9.10. beschlossen?", stadt, gaz, referenz_datum=REFERENZ)
    assert (v.von, v.bis) == ("2026-10-09", "2026-10-10")
    assert v.neueste is False


def test_nlq_relative_angaben(stadt, gaz):
    v = verstehe("Was gibt es in Ehrenfeld seit gestern?", stadt, gaz, referenz_datum=REFERENZ)
    assert v.von == "2026-10-13" and v.neueste is True
    v = verstehe("Neueste Bauvorhaben in den letzten 7 Tagen", stadt, gaz, referenz_datum=REFERENZ)
    assert v.von == "2026-10-08" and v.neueste is True and v.themen == ["bauen"]


def test_nlq_ungueltiges_datum_wird_erklaert(stadt, gaz):
    v = verstehe("Neueste Vorgänge seit dem 31.2.", stadt, gaz, referenz_datum=REFERENZ)
    assert v.von is None and v.neueste is True
    assert any("gibt es nicht" in h for h in v.hinweise)


def test_nlq_neu_wird_kein_stichwort(stadt, gaz):
    # Ohne die Stream-Absicht suchte „neuesten“ im Titel und fand nichts.
    v = verstehe("Die neuesten Vorgänge", stadt, gaz, referenz_datum=REFERENZ)
    assert v.neueste is True and v.freitext == []


# --- Stream-Logik ------------------------------------------------------------

def test_stream_sortiert_rein_chronologisch(bestand, stadt):
    daten = hole_stream(bestand, stadt, REFERENZ)
    assert [t["id"] for t in daten["treffer"]] == [
        "test:neu", "test:grenze", "test:davor", "test:alt", "test:ohne"]
    assert daten["gesamt"] == 5 and daten["hat_mehr"] is False
    assert daten["sortierung"] == "vorgangsdatum_absteigend"


def test_stream_ignoriert_trefferpunkte(bestand, stadt):
    # „Verkehrsberuhigung alt“ träfe das Thema im Titel (mehr Punkte); im Stream
    # zählt nur das Datum, das neuere Verfahren steht deshalb vorn.
    daten = hole_stream(bestand, stadt, REFERENZ, themen=["verkehr"])
    assert [t["id"] for t in daten["treffer"]] == ["test:neu", "test:alt"]


def test_stream_seit_und_blaettern(bestand, stadt):
    erste = hole_stream(bestand, stadt, REFERENZ, von=date(2026, 10, 9), limit=1)
    assert erste["gesamt"] == 2
    assert [t["id"] for t in erste["treffer"]] == ["test:neu"]
    assert erste["hat_mehr"] is True and erste["naechster_offset"] == 1
    zweite = hole_stream(bestand, stadt, REFERENZ, von=date(2026, 10, 9), limit=1, offset=1)
    assert [t["id"] for t in zweite["treffer"]] == ["test:grenze"]
    assert zweite["hat_mehr"] is False and zweite["naechster_offset"] is None


def test_stream_bis_ist_einschliesslich(bestand, stadt):
    daten = hole_stream(bestand, stadt, REFERENZ, von=date(2026, 10, 9), bis=date(2026, 10, 9))
    assert [t["id"] for t in daten["treffer"]] == ["test:grenze"]
    assert daten["bis"] == "2026-10-09" and daten["bis_de"] == "09.10.2026"


# --- API und CLI ---------------------------------------------------------------

@pytest.fixture
def client(tmp_path, stadt):
    pfad = tmp_path / "stream.sqlite"
    with Speicher(pfad) as sp:
        sp.quelle_anlegen("koeln:ris", "koeln", "ris", "oparl", "Ratsinformation Köln (OParl)", "")
        _speichern(sp, "test:alt", "Verkehrsberuhigung alt", "2026-10-01")
        _speichern(sp, "test:grenze", "Neubau an der Grenze", "2026-10-09")
        _speichern(sp, "test:neu", "Verkehrsberuhigung neu", "2026-10-12")
    return TestClient(create_app(pfad))


def test_api_stream_seit_datum(client):
    antwort = client.get("/api/stream", params={"stadt": "koeln", "seit": "9.10."})
    assert antwort.status_code == 200
    daten = antwort.json()
    # „9.10.“ ohne Jahr hängt vom heutigen Datum ab; fest geprüft werden Form und Filter.
    assert daten["stream"]["seit"] and daten["stream"]["seit_de"]
    assert [t["id"] for t in daten["treffer"]] == ["test:neu", "test:grenze"]
    assert daten["gesamt"] == 2 and daten["stream"]["hat_mehr"] is False
    assert any("Stream für Köln" in h for h in daten["hinweise"])


def test_api_stream_iso_tage_und_validierung(client):
    daten = client.get("/api/stream", params={"seit": "2026-10-10"}).json()
    assert [t["id"] for t in daten["treffer"]] == ["test:neu"]
    assert client.get("/api/stream", params={"seit": "bald"}).status_code == 422
    assert client.get("/api/stream", params={"seit": "2026-10-09", "tage": 7}).status_code == 422
    assert client.get("/api/stream", params={"seit": "2026-10-09", "bis": "2026-10-01"}).status_code == 422
    assert client.get("/api/stream", params={"thema": "unsinn"}).status_code == 422
    assert client.get("/api/stream", params={"stadt": "atlantis"}).status_code == 404


def test_api_frage_versteht_stream(client):
    daten = client.post("/api/frage", json={
        "frage": "Gib mir die neuesten Vorgänge seit dem 9.10.2026"}).json()
    assert daten["verstanden"]["neueste"] is True
    assert daten["verstanden"]["von"] == "2026-10-09"
    assert [t["id"] for t in daten["treffer"]] == ["test:neu", "test:grenze"]


def test_startseite_hat_stream_tab(client):
    text = client.get("/").text
    assert "Neueste (Stream)" in text and "/api/stream" in text


def test_cli_stream_json(tmp_path, stadt, capsys):
    pfad = tmp_path / "cli.sqlite"
    with Speicher(pfad) as sp:
        sp.quelle_anlegen("koeln:ris", "koeln", "ris", "oparl", "Ratsinformation Köln (OParl)", "")
        _speichern(sp, "test:alt", "Verkehrsberuhigung alt", "2026-10-01")
        _speichern(sp, "test:neu", "Verkehrsberuhigung neu", "2026-10-12")
    assert main(["--db", str(pfad), "--stadt", "koeln", "stream",
                 "--seit", "9.10.2026", "--json"]) == 0
    daten = json.loads(capsys.readouterr().out)
    assert [t["id"] for t in daten["treffer"]] == ["test:neu"]
    assert daten["seit"] == "2026-10-09"


def test_cli_stream_text_und_fehler(tmp_path, stadt, capsys):
    pfad = tmp_path / "cli.sqlite"
    with Speicher(pfad) as sp:
        sp.quelle_anlegen("koeln:ris", "koeln", "ris", "oparl", "Ratsinformation Köln (OParl)", "")
        _speichern(sp, "test:neu", "Verkehrsberuhigung neu", "2026-10-12")
    assert main(["--db", str(pfad), "stream", "--tage", "30"]) == 0
    out = capsys.readouterr().out
    assert "Stream Köln:" in out and "Verkehrsberuhigung neu" in out
    with pytest.raises(SystemExit):
        main(["--db", str(pfad), "stream", "--seit", "bald"])
