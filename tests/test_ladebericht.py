"""Ladeübersicht: Dokumente je Quelle, Grafikdaten und Abrufverlauf – vollständig offline.

Geprüft wird die Datenschicht hinter dem Tab „Datenladen“ und der API-Endpunkt
`/api/ladebericht`. Die Übersicht darf **nur** die lokale Datenbank lesen: Suchanfragen und
Statusabfragen lösen in diesem Projekt grundsätzlich keinen Behördenabruf aus.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from behoerdenmaster.api import create_app
from behoerdenmaster.betrieb import Betrieb
from behoerdenmaster.demo import lade_demo
from behoerdenmaster.ladebericht import ladebericht
from behoerdenmaster.speicher import Speicher, jetzt_iso


def test_dokumente_je_quelle_und_rolle(db, stadt):
    """Die Grafik summiert Dokumentverweise je Quelle und Rolle – aus derselben Datenbank wie die Suche."""
    lade_demo(db, stadt)
    bericht = ladebericht(db, stadt)

    assert bericht["gesamt"]["vorgaenge"] == 4
    assert bericht["gesamt"]["dokumente"] == 8
    assert {r["rolle"] for r in bericht["dokumente_nach_rolle"]} == {
        "plandokument", "tagesordnung", "einladung", "dokument"}
    summe = sum(r["anzahl"] for r in bericht["dokumente_nach_rolle"])
    assert summe == 8

    nach_id = {q["id"]: q for q in bericht["quellen"]}
    demo_plan = next(q for q in bericht["quellen"] if q["schluessel"] == "bauleitplanung-demo")
    assert demo_plan["dokumente_nach_rolle"] == [{"rolle": "plandokument", "anzahl": 3}]
    assert demo_plan["ladezustand"] == "beispiel"
    assert demo_plan["dokumente"][0]["rolle_text"] == "Planungsdokument"
    assert all(d["url"].startswith("https://nw.bauleitplanung-online.de/") for d in demo_plan["dokumente"])
    # Dokumentverweise werden verlinkt, nicht geladen: Pflichtfelder der Quelle bleiben unverändert.
    assert demo_plan["zaehler"]["dokumente"] == 3
    assert "koeln:demo" in nach_id


def test_konfigurierte_quelle_ohne_bestand_erscheint_mit_null_werten(db, stadt):
    """Auch eine noch nie abgerufene Quelle steht in der Übersicht – mit ehrlichen Nullen."""
    bericht = ladebericht(db, stadt)
    ris = next(q for q in bericht["quellen"] if q["schluessel"] == "ris")
    assert ris["ladezustand"] == "nie"
    assert ris["zaehler"]["dokumente"] == 0
    assert ris["dokumente"] == []
    assert ris["naechster_abruf"] is None
    assert [w["schritt"] for w in ris["abrufweg"]][0] == "Zugriff"
    assert any("noch nicht abgerufen" in q["ladezustand_text"] for q in bericht["quellen"])


def test_abrufverlauf_zeigt_anfragen_zaehler_und_dauer(db, stadt):
    """Der Verlauf nennt nur, was der Lauf wirklich getan hat: Anfragen, Zähler, Dauer."""
    lauf = db.lauf_beginnen("koeln:ris")
    db.lauf_beenden(lauf, "ok", 42, {"vorlagen:gespeichert": 7, "sitzungen:gespeichert": 2})
    bericht = ladebericht(db, stadt)
    ris = next(q for q in bericht["quellen"] if q["schluessel"] == "ris")

    assert len(ris["laeufe"]) == 1
    eintrag = ris["laeufe"][0]
    assert eintrag["status"] == "ok" and eintrag["anfragen"] == 42
    assert eintrag["dauer_sekunden"] is not None and eintrag["dauer_sekunden"] >= 0
    assert {z["text"] for z in eintrag["zaehler"]} == {"Vorlagen übernommen", "Sitzungen übernommen"}
    assert eintrag["zaehler"][0]["anzahl"] == 7  # absteigend sortiert
    assert not any("kein Abruflauf" in h for h in bericht["hinweise"])


def test_unterbrochene_liste_und_wartende_details_werden_sichtbar(db, stadt):
    """Eine offene Abrufreihe und wartende Detailabrufe sind genau die Gründe für einen Teilbestand."""
    db.quelle_anlegen("koeln:ris", "koeln", "ris", "oparl", "RIS", "https://example.test/oparl")
    db.stand_schreiben("koeln:ris", "vorlagen", "2026-10-01T00:00:00+02:00",
                       "https://example.test/oparl/papers?page=4", jetzt_iso())
    db.aufgabe_anlegen("koeln:bauleitplanung", "https://example.test/verfahren/1", "https://example.test/verfahren/1")
    bericht = ladebericht(db, stadt)

    ris = next(q for q in bericht["quellen"] if q["schluessel"] == "ris")
    assert ris["staende"][0]["offen"] is True
    assert any(w["schritt"] == "Listen" and w["zustand"] == "offen" for w in ris["abrufweg"])

    plan = next(q for q in bericht["quellen"] if q["schluessel"] == "bauleitplanung")
    assert plan["offene_details"] == 1
    assert any("Warteschlange" in w["wert"] for w in plan["abrufweg"])


def test_abkuehlzeit_und_fehler_werden_gesondert_gekennzeichnet(db, stadt):
    """Eine Server-Pause ist kein gewöhnlicher Fehler und wird deshalb als eigener Zustand gezeigt."""
    db.quelle_anlegen("koeln:ris", "koeln", "ris", "oparl", "RIS", "https://example.test/oparl")
    bis = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(timespec="seconds")
    db.quelle_status("koeln:ris", "gesperrt", "Quelle hat den Abruf abgelehnt", abkuehlung_bis=bis)
    bericht = ladebericht(db, stadt)
    ris = next(q for q in bericht["quellen"] if q["schluessel"] == "ris")
    assert ris["ladezustand"] == "pause"
    assert ris["abkuehlung_bis"] == bis
    assert ris["fehlertext"] == "Quelle hat den Abruf abgelehnt"
    assert any("Abkühlzeit" in h for h in bericht["hinweise"])


def test_naechster_abruf_nur_bei_aktivem_hintergrunddienst(db, stadt):
    """Ohne automatische Abrufe gibt es keinen fälligen Termin – auch keinen erfundenen."""
    db.quelle_anlegen("koeln:ris", "koeln", "ris", "oparl", "RIS", "https://example.test/oparl")
    db.quelle_status("koeln:ris", "ok", erfolg=True)
    ohne = ladebericht(db, stadt, intervall_sekunden=21600, sync_aktiv=False)
    mit = ladebericht(db, stadt, intervall_sekunden=21600, sync_aktiv=True)
    assert next(q for q in ohne["quellen"] if q["schluessel"] == "ris")["naechster_abruf"] is None
    frist = next(q for q in mit["quellen"] if q["schluessel"] == "ris")["naechster_abruf"]
    assert frist and datetime.fromisoformat(frist) > datetime.now(timezone.utc)
    assert any("ausgeschaltet" in h for h in ohne["hinweise"])


def test_zahlen_werden_deutsch_beschriftet():
    """„1 Vorgang“ statt „1 Vorgänge“ – die Beschriftung entsteht an einer Stelle, nicht in jeder Ansicht."""
    from behoerdenmaster.ladebericht import einheit, entitaet_name, rolle_text, zaehler_text

    assert einheit(1, "Vorgang", "Vorgänge") == "1 Vorgang"
    assert einheit(4, "Vorgang", "Vorgänge") == "4 Vorgänge"
    assert entitaet_name("vorlagen") == "Vorlagen" and entitaet_name("unbekanntes") == "Unbekanntes"
    assert rolle_text("plandokument") == "Planungsdokument"
    assert zaehler_text("vorlagen:gespeichert") == "Vorlagen übernommen"
    assert zaehler_text("verfahren:entfernt") == "Verfahren nicht mehr gelistet"
    assert zaehler_text("sitzungen:geloescht") == "Sitzungen als gelöscht markiert"


def test_leerer_bestand_bleibt_erklaerbar(db, stadt):
    bericht = ladebericht(db, stadt)
    assert bericht["gesamt"]["dokumente"] == 0
    assert bericht["dokumente_nach_rolle"] == []
    assert bericht["quellen"] and all(q["dokumente"] == [] for q in bericht["quellen"])
    assert any("kein Abruflauf protokolliert" in h for h in bericht["hinweise"])


@pytest.fixture
def client_laden(tmp_path, stadt):
    pfad = tmp_path / "laden.sqlite"
    with Speicher(pfad) as sp:
        lade_demo(sp, stadt)
        lauf = sp.lauf_beginnen("koeln:bauleitplanung-demo")
        sp.lauf_beenden(lauf, "ok", 5, {"verfahren:gespeichert": 3, "details:gespeichert": 3})
    return TestClient(create_app(pfad, betrieb=Betrieb(sync_aktiv=False)))


def test_api_ladebericht_liefert_diagrammdaten(client_laden):
    antwort = client_laden.get("/api/ladebericht")
    assert antwort.status_code == 200
    daten = antwort.json()
    assert daten["stadt"]["schluessel"] == "koeln"
    assert daten["gesamt"]["dokumente"] == 8
    assert daten["dokumente_nach_rolle"][0]["text"] == "Planungsdokument"
    assert daten["betrieb"]["automatische_abrufe"] is False
    quelle = next(q for q in daten["quellen"] if q["schluessel"] == "bauleitplanung-demo")
    assert quelle["laeufe"][0]["anfragen"] == 5
    assert quelle["abrufweg"][-1]["schritt"] == "Suchbestand"
    assert json.dumps(daten, ensure_ascii=False)  # serialisierbar, keine Row-Objekte


def test_api_ladebericht_laesst_sich_begrenzen_und_prueft_die_eingabe(client_laden):
    assert client_laden.get("/api/ladebericht?laeufe=1").status_code == 200
    assert client_laden.get("/api/ladebericht?laeufe=0").status_code == 422
    assert client_laden.get("/api/ladebericht?laeufe=99").status_code == 422
    assert client_laden.get("/api/ladebericht?stadt=berlin").status_code == 404


def test_ladebericht_liest_nur_lokal_und_ruft_nichts_ab(tmp_path, stadt, monkeypatch):
    """Kein Behördenabruf: Jeder Netzaufbau schlägt fehl, die Übersicht liefert trotzdem Zahlen.

    Die Oberfläche darf keine Abrufe auslösen – sonst würde ein Seitenaufruf die Stadt belasten.
    """
    def kein_netz(*args, **kwargs):  # pragma: no cover - darf nie greifen
        raise AssertionError("Die Ladeübersicht darf keine Netzverbindung aufbauen")

    monkeypatch.setattr(httpx, "Client", kein_netz)
    pfad = tmp_path / "lokal.sqlite"
    with Speicher(pfad) as sp:
        lade_demo(sp, stadt)
    client = TestClient(create_app(pfad, betrieb=Betrieb(sync_aktiv=False)))
    daten = client.get("/api/ladebericht").json()
    assert daten["gesamt"]["dokumente"] == 8
    assert client.get("/api/status").status_code == 200


def test_cli_ladebericht_gibt_json_aus(tmp_path, stadt, capsys):
    from behoerdenmaster import cli

    pfad = tmp_path / "cli.sqlite"
    with Speicher(pfad) as sp:
        lade_demo(sp, stadt)
    code = cli.main(["--db", str(pfad), "ladebericht", "--json"])
    daten = json.loads(capsys.readouterr().out)
    assert code == 0
    assert daten["gesamt"]["vorgaenge"] == 4
    assert any(q["schluessel"] == "ris" for q in daten["quellen"])


def test_cli_ladebericht_textfassung_nennt_quelle_und_weg(tmp_path, stadt, capsys):
    from behoerdenmaster import cli

    pfad = tmp_path / "cli-text.sqlite"
    with Speicher(pfad) as sp:
        lade_demo(sp, stadt)
    code = cli.main(["--db", str(pfad), "ladebericht"])
    ausgabe = capsys.readouterr().out
    assert code == 0
    assert "Ladeübersicht Köln" in ausgabe
    assert "Dokumente nach Rolle" in ausgabe
    assert "Weg:" in ausgabe and "Suchbestand" in ausgabe
