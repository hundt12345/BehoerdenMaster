"""Render-Laufzeit und Hintergrundabruf vollständig offline prüfen."""

from __future__ import annotations

import json
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from behoerdenmaster.abruf import Abrufer, BudgetErschoepft
from behoerdenmaster.api import create_app
from behoerdenmaster.betrieb import Betrieb, SyncDienst, initialisiere, naechster_abruf
from behoerdenmaster.config import PROJEKT_KONTAKT
from behoerdenmaster.crawl_sperre import crawl_sperre
from behoerdenmaster.demo import loesche_demo
from behoerdenmaster.speicher import Speicher

from conftest import FakeOParl
from test_bauleitplanung import FakeBauleitplanung


UTC = timezone.utc


def test_betrieb_standard_ruft_ab_ohne_die_kontaktadresse_zu_veroeffentlichen():
    """Die Umgebungsvoreinstellung füllt den Bestand von selbst – ohne die Adresse preiszugeben.

    Genau daran scheiterte die veröffentlichte Instanz: Sie war ohne eigene Env-Werte
    gestartet, `BEHOERDEN_SYNC_ENABLED` war nicht gesetzt und `BEHOERDEN_KONTAKT` fehlte,
    also blieb der Abruf aus und der Bestand leer.
    """
    betrieb = Betrieb.aus_umgebung({})
    assert not betrieb.demo and betrieb.sync_aktiv
    assert betrieb.kontakt == PROJEKT_KONTAKT
    assert betrieb.sync_intervall == 21600 and betrieb.sync_budget == 100
    status = betrieb.oeffentlicher_status()
    assert status["kontakt_hinterlegt"] is True
    assert PROJEKT_KONTAKT not in json.dumps(status, ensure_ascii=False)
    assert "kontakt" not in status
    # Nur der Hinweis auf die Voreinstellung ist öffentlich, nie die Adresse selbst.
    assert len(betrieb.warnungen) == 1 and "BEHOERDEN_KONTAKT" in betrieb.warnungen[0]
    kontakt = "nicht-oeffentlich@example.test"
    assert kontakt not in repr(replace(betrieb, kontakt=kontakt))


def test_eigene_kontaktadresse_ersetzt_die_projektvoreinstellung():
    betrieb = Betrieb.aus_umgebung({"BEHOERDEN_KONTAKT": "eigene-adresse@example.test"})
    assert betrieb.kontakt == "eigene-adresse@example.test" and betrieb.sync_aktiv
    assert betrieb.warnungen == ()


def test_leer_gesetzte_kontaktadresse_verzichtet_auf_die_voreinstellung():
    """Explizites Opt-out: Ein leerer Wert fällt nicht auf die Repository-Adresse zurück."""
    betrieb = Betrieb.aus_umgebung({"BEHOERDEN_KONTAKT": ""}, streng=False)
    assert betrieb.kontakt == "" and betrieb.sync_aktiv is False
    assert len(betrieb.warnungen) == 1 and "BEHOERDEN_KONTAKT" in betrieb.warnungen[0]
    with pytest.raises(ValueError, match="BEHOERDEN_KONTAKT"):
        Betrieb.aus_umgebung({"BEHOERDEN_KONTAKT": "  "})


def test_sync_laesst_sich_weiterhin_bewusst_abschalten():
    betrieb = Betrieb.aus_umgebung({"BEHOERDEN_SYNC_ENABLED": "false"})
    assert betrieb.sync_aktiv is False
    assert betrieb.warnungen == ()
    # Auch mit eigener Kontaktadresse bleibt ein ausdrückliches "false" wirksam.
    mit_adresse = Betrieb.aus_umgebung({"BEHOERDEN_SYNC_ENABLED": "false",
                                        "BEHOERDEN_KONTAKT": "eigene@example.test"})
    assert mit_adresse.sync_aktiv is False and mit_adresse.kontakt == "eigene@example.test"
    # Ein ungültiger Wert fällt im Webdienst auf die Voreinstellung "true" zurück.
    locker = Betrieb.aus_umgebung({"BEHOERDEN_SYNC_ENABLED": "vielleicht"}, streng=False)
    assert locker.sync_aktiv is True and "BEHOERDEN_SYNC_ENABLED" in locker.warnungen[0]


@pytest.mark.parametrize("wert,erwartet", [("true", True), ("  TRUE ", True), ("1", True),
                                         ("yes", True), ("on", True), ("false", False),
                                         ("0", False), ("no", False), ("off", False)])
def test_boolesche_umgebungswerte(wert, erwartet):
    betrieb = Betrieb.aus_umgebung({"BEHOERDEN_DEMO": wert, "BEHOERDEN_SYNC_ENABLED": wert,
                                   "BEHOERDEN_KONTAKT": "test@example.test"})
    assert betrieb.demo is erwartet and betrieb.sync_aktiv is erwartet


@pytest.mark.parametrize("env", [
    {"BEHOERDEN_SYNC_ENABLED": "vielleicht"}, {"BEHOERDEN_DEMO": "falschgeschrieben"},
    {"BEHOERDEN_SYNC_ENABLED": "true", "BEHOERDEN_KONTAKT": "  "},
    {"BEHOERDEN_KONTAKT": "test@example.test\nHeader: injected"},
    {"BEHOERDEN_SYNC_INTERVALL": "299"}, {"BEHOERDEN_SYNC_INTERVALL": "abc"},
    {"BEHOERDEN_SYNC_MAX_ANFRAGEN": "0"}, {"BEHOERDEN_SYNC_MAX_ANFRAGEN": "1.5"},
    {"BEHOERDEN_SYNC_STARTVERZOEGERUNG": "-1"},
])
def test_ungueltige_umgebung_scheitert_klar(env):
    with pytest.raises(ValueError, match="BEHOERDEN_"):
        Betrieb.aus_umgebung(env)


def test_quellenauswahl_ist_geordnet_und_eindeutig(stadt, tmp_path):
    betrieb = Betrieb.aus_umgebung({"BEHOERDEN_SYNC_QUELLEN": " koeln:bauleitplanung, koeln:ris,koeln:ris "})
    assert betrieb.sync_quellen == ("koeln:bauleitplanung", "koeln:ris")
    dienst = SyncDienst(tmp_path / "sync.sqlite", {"koeln": stadt}, betrieb)
    assert tuple(dienst.quellen) == betrieb.sync_quellen
    with pytest.raises(ValueError, match="Unbekannte BEHOERDEN_SYNC_QUELLEN"):
        SyncDienst(tmp_path / "sync.sqlite", {"koeln": stadt}, replace(betrieb, sync_quellen=("koeln:unbekannt",)))
    assert not (tmp_path / "sync.sqlite").exists()


def test_factory_initialisiert_nicht_im_build_erst_im_lifespan(tmp_path):
    pfad = tmp_path / "disk" / "neu.sqlite"
    app = create_app(pfad, betrieb=Betrieb())
    assert not pfad.parent.exists()
    with TestClient(app) as client:
        assert pfad.exists()
        assert client.get("/healthz").json()["status"] == "ok"
        assert client.head("/healthz").status_code == 200
        status = client.get("/api/status").json()
        assert status["zaehler"]["vorgang"] == 0
        assert {q["id"] for q in status["quellen"]} == {
            "koeln:ris", "koeln:bauleitplanung", "koeln:ris-rss", "koeln:presse-rss",
            "koeln:bekanntmachungen", "koeln:beteiligung", "koeln:opendata"}
        assert not status["betrieb"]["sync_dienst_laeuft"]


def test_healthcheck_legt_fehlende_db_nicht_an(tmp_path):
    pfad = tmp_path / "fehlende_disk" / "fehlt.sqlite"
    client = TestClient(create_app(pfad, betrieb=Betrieb()))  # absichtlich ohne Startup-Lifespan
    antwort = client.get("/healthz")
    assert antwort.status_code == 503
    assert str(pfad) not in antwort.text
    assert not pfad.parent.exists()


def test_healthcheck_meldet_verlorene_db_ohne_neuinitialisierung(tmp_path):
    pfad = tmp_path / "disk.sqlite"
    with TestClient(create_app(pfad, betrieb=Betrieb())) as client:
        assert client.get("/healthz").status_code == 200
        pfad.unlink()
        assert client.get("/healthz").status_code == 503
        assert not pfad.exists()


def test_healthcheck_meldet_defektes_schema(tmp_path):
    pfad = tmp_path / "disk.sqlite"
    with TestClient(create_app(pfad, betrieb=Betrieb())) as client:
        with Speicher(pfad) as sp:
            sp.db.execute("DROP TABLE quelle")
        assert client.get("/healthz").status_code == 503


def test_demo_ist_einmalig_und_kommt_nach_loeschen_nicht_wieder(tmp_path, stadt):
    pfad = tmp_path / "disk" / "demo.sqlite"
    staedte = {"koeln": stadt}
    betrieb = Betrieb(demo=True)
    initialisiere(pfad, staedte, betrieb)
    with Speicher(pfad) as sp:
        assert sp.zaehle()["vorgang"] == 4
        assert sp.betrieb_wert("demo_initialisiert")
        assert len([q for q in sp.quellen() if q["demo"]]) == 2
    initialisiere(pfad, staedte, betrieb)
    with Speicher(pfad) as sp:
        assert sp.zaehle()["vorgang"] == 4
        assert sp.db.execute("SELECT COUNT(*) FROM planstand").fetchone()[0] == 3
        loesche_demo(sp, stadt)
    initialisiere(pfad, staedte, betrieb)
    with Speicher(pfad) as sp:
        assert sp.zaehle()["vorgang"] == 0
        assert not any(q["demo"] for q in sp.quellen())


@pytest.mark.parametrize("tabelle", ["gremium", "sitzung", "vorgang"])
@pytest.mark.parametrize("geloescht", [0, 1])
def test_bestehender_auch_teilweiser_oder_geloeschter_bestand_wird_nie_gesaet(tmp_path, stadt, tabelle, geloescht):
    pfad = tmp_path / "vorhanden.sqlite"
    with Speicher(pfad) as sp:
        with sp.transaktion():
            sp.db.execute(f"INSERT INTO {tabelle} (id, quelle_id, geloescht) VALUES ('echt', 'koeln:ris', ?)",
                          (geloescht,))
    initialisiere(pfad, {"koeln": stadt}, Betrieb(demo=True))
    with Speicher(pfad) as sp:
        assert not any(q["demo"] for q in sp.quellen())
        assert sp.db.execute(f"SELECT COUNT(*) FROM {tabelle}").fetchone()[0] == 1
        assert sp.betrieb_wert("demo_initialisiert")


def test_demo_marker_erst_nach_erfolgreichem_import(tmp_path, stadt, monkeypatch):
    pfad = tmp_path / "disk.sqlite"

    def kaputt(sp, stadt):
        sp.quelle_anlegen("halb-demo", "koeln", "demo", "demo", "Teilimport", "", demo=True)
        raise ValueError("Fehlende Fixture simuliert")

    with monkeypatch.context() as patch:
        patch.setattr("behoerdenmaster.demo.lade_demo", kaputt)
        with pytest.raises(ValueError, match="Fehlende Fixture"):
            initialisiere(pfad, {"koeln": stadt}, Betrieb(demo=True))
    with Speicher(pfad) as sp:
        assert not sp.betrieb_wert("demo_initialisiert")
        assert sp.quellen() == []
    initialisiere(pfad, {"koeln": stadt}, Betrieb(demo=True))
    with Speicher(pfad) as sp:
        assert sp.zaehle()["vorgang"] == 4


def test_demo_behindert_laufenden_crawl_nicht(tmp_path, stadt):
    pfad = tmp_path / "disk.sqlite"
    with crawl_sperre(pfad):
        initialisiere(pfad, {"koeln": stadt}, Betrieb(demo=True))
    with Speicher(pfad) as sp:
        assert sp.zaehle()["vorgang"] == 0
        assert not sp.betrieb_wert("demo_initialisiert")
    initialisiere(pfad, {"koeln": stadt}, Betrieb(demo=True))
    with Speicher(pfad) as sp:
        assert sp.zaehle()["vorgang"] == 4


def test_api_und_startup_brauchen_standardmaessig_keinen_httpclient(tmp_path, monkeypatch):
    def unerwartet(self):
        pytest.fail("API/Offline-Startup darf keinen Behörden-Client anlegen")

    monkeypatch.setattr(Abrufer, "__post_init__", unerwartet)
    kontakt = "privater-kontakt@example.test"
    pfad = tmp_path / "demo.sqlite"
    with TestClient(create_app(pfad, betrieb=Betrieb(demo=True, kontakt=kontakt))) as client:
        assert client.get("/").status_code == 200
        assert client.get("/healthz").status_code == 200
        assert client.post("/api/frage", json={"frage": "Welche Bauvorhaben sind in Köln Ehrenfeld geplant?"}).json()["gesamt"] == 3
        assert client.get("/api/plz/50827?thema=bauen").status_code == 200
        antwort = client.get("/api/status")
        assert kontakt not in antwort.text and str(pfad) not in antwort.text
        assert not antwort.json()["betrieb"]["automatische_abrufe"]
        assert client.post("/api/crawl").status_code == 404
        assert client.post("/api/admin").status_code == 404


def test_persistierte_faelligkeit_und_lange_serverpause():
    letzter = datetime(2026, 10, 10, 12, tzinfo=UTC)
    assert naechster_abruf(None, 21600) is None
    assert naechster_abruf({"letzter_versuch": "", "abkuehlung_bis": ""}, 21600) is None
    daten = {"letzter_versuch": letzter.isoformat(), "abkuehlung_bis": ""}
    assert naechster_abruf(daten, 21600) == letzter + timedelta(hours=6)
    daten["abkuehlung_bis"] = (letzter + timedelta(days=2)).isoformat()
    assert naechster_abruf(daten, 21600) == letzter + timedelta(days=2)
    daten["letzter_versuch"] = "2026-10-10T12:00:00"  # alte naive UTC-Zeit
    assert naechster_abruf(daten, 21600) == letzter + timedelta(days=2)


def _sync(tmp_path, stadt, uhr, fake, budget=100, **kw):
    pfad = tmp_path / "sync.sqlite"
    betrieb = Betrieb(sync_aktiv=True, kontakt="test@example.test",
                      sync_quellen=("koeln:bauleitplanung",), sync_budget=budget)
    initialisiere(pfad, {"koeln": stadt}, Betrieb())
    erzeugte = []

    def factory(**optionen):
        a = Abrufer(**(optionen | {"client": httpx.Client(transport=httpx.MockTransport(fake)),
                                  "sleep": uhr.schlaf, "clock": uhr, "wiederholungen": 0}))
        erzeugte.append(a)
        return a

    return SyncDienst(pfad, {"koeln": stadt}, betrieb, abrufer_factory=factory, **kw), erzeugte


def test_sync_budget_checkpoint_und_neustart_keine_abrufserie(tmp_path, stadt, uhr):
    quelle = next(q for q in stadt.quellen if q.typ == "bauleitplanung")
    fake = FakeBauleitplanung(quelle)
    dienst, erzeugte = _sync(tmp_path, stadt, uhr, fake, budget=2)
    assert dienst.etappe()[0].status == "teilweise"
    assert erzeugte[0].anfragen == 2 and erzeugte[0].client.is_closed
    assert erzeugte[0].kontakt == dienst.betrieb.kontakt
    assert erzeugte[0].min_intervall >= 3
    with Speicher(dienst.db_pfad) as sp:
        assert len(sp.aufgaben("koeln:bauleitplanung")) == 3
        letzter = datetime.fromisoformat(sp.db.execute("SELECT letzter_versuch FROM quelle WHERE id='koeln:bauleitplanung'").fetchone()[0])
    nach_neustart = SyncDienst(dienst.db_pfad, {"koeln": stadt}, dienst.betrieb,
                              abrufer_factory=dienst.abrufer_factory)
    assert nach_neustart.etappe() == []
    assert len(erzeugte) == 1
    nach_neustart.clock = lambda: letzter + timedelta(seconds=dienst.betrieb.sync_intervall + 1)
    assert nach_neustart.etappe()[0].status == "teilweise"
    assert len(fake.listenabrufe()) == 1  # setzt nur die Detail-Warteschlange fort
    with Speicher(dienst.db_pfad) as sp:
        assert len(sp.aufgaben("koeln:bauleitplanung")) == 2


def test_sync_beachtet_retry_after_auch_nach_neustart(tmp_path, stadt, uhr):
    quelle = next(q for q in stadt.quellen if q.typ == "bauleitplanung")
    fake = FakeBauleitplanung(quelle)
    fake.list_error, fake.retry_after = 429, "86400"
    dienst, erzeugte = _sync(tmp_path, stadt, uhr, fake)
    assert dienst.etappe()[0].status == "gesperrt"
    with Speicher(dienst.db_pfad) as sp:
        zeile = sp.db.execute("SELECT * FROM quelle WHERE id='koeln:bauleitplanung'").fetchone()
        letzter = datetime.fromisoformat(zeile["letzter_versuch"])
        assert datetime.fromisoformat(zeile["abkuehlung_bis"]) > letzter + timedelta(hours=23)
    neustart = SyncDienst(dienst.db_pfad, {"koeln": stadt}, dienst.betrieb,
                         abrufer_factory=dienst.abrufer_factory,
                         clock=lambda: letzter + timedelta(hours=7))
    assert neustart.etappe() == [] and len(erzeugte) == 1


def test_sync_nicht_parallel_zu_cli(tmp_path, stadt, uhr):
    fake = FakeBauleitplanung(next(q for q in stadt.quellen if q.typ == "bauleitplanung"))
    dienst, erzeugte = _sync(tmp_path, stadt, uhr, fake)
    with crawl_sperre(dienst.db_pfad):
        assert dienst.etappe() == []
    assert erzeugte == [] and fake.aufrufe == []
    assert dienst.etappe()[0].status == "ok"


def test_sync_shutdown_vor_naechster_httpanfrage_behaelt_checkpoint(tmp_path, stadt, uhr):
    fake = FakeBauleitplanung(next(q for q in stadt.quellen if q.typ == "bauleitplanung"))

    def handler(req):
        antwort = fake(req)
        if req.url.path == "/verfahren/suche/ajax":
            dienst.stoppen()
        return antwort

    dienst, erzeugte = _sync(tmp_path, stadt, uhr, handler)
    assert dienst.etappe()[0].status == "teilweise"
    assert len(fake.aufrufe) == 2 and erzeugte[0].client.is_closed
    assert dienst.etappe() == []
    with Speicher(dienst.db_pfad) as sp:
        assert sp.zaehle()["vorgang"] == 3
        assert len(sp.aufgaben("koeln:bauleitplanung")) == 3
        assert sp.letzte_laeufe()[0]["status"] == "teilweise"


def test_sync_aus_sowie_startverzoegerung_werden_sofort_beendet(tmp_path, stadt):
    def unerwartet(**kw):
        pytest.fail("Kein HTTP-Client vor Aktivierung/Startverzögerung")

    dienst = SyncDienst(tmp_path / "sync.sqlite", {"koeln": stadt}, Betrieb(), abrufer_factory=unerwartet)
    dienst.starten()
    assert not dienst.laeuft and dienst.etappe() == []
    dienst = SyncDienst(tmp_path / "sync.sqlite", {"koeln": stadt},
                        Betrieb(sync_aktiv=True, kontakt="t@example.test", sync_startverzoegerung=3600),
                        abrufer_factory=unerwartet)
    dienst.starten()
    dienst.stoppen()
    dienst.warten(timeout=2)
    assert not dienst.laeuft
    assert not (tmp_path / "sync.sqlite").exists()


def test_lange_backoff_pause_ist_interruptibel(tmp_path, stadt):
    dienst = SyncDienst(tmp_path / "sync.sqlite", {"koeln": stadt}, Betrieb())
    bereit = threading.Event()
    abbrueche = []

    def warte():
        bereit.set()
        try:
            dienst._pause(86400)
        except BudgetErschoepft as exc:
            abbrueche.append(str(exc))

    thread = threading.Thread(target=warte, daemon=True)
    thread.start()
    assert bereit.wait(2)
    dienst.stoppen()
    thread.join(2)
    assert not thread.is_alive() and abbrueche


def test_lifespan_startet_und_beendet_aktivierten_dienst(tmp_path):
    betrieb = Betrieb(sync_aktiv=True, kontakt="nicht-oeffentlich@example.test", sync_startverzoegerung=3600)
    app = create_app(tmp_path / "sync.sqlite", betrieb=betrieb)
    with TestClient(app) as client:
        dienst = app.state.sync_dienst
        assert dienst.laeuft
        status = client.get("/api/status")
        assert status.json()["betrieb"]["sync_dienst_laeuft"]
        assert betrieb.kontakt not in status.text
        assert client.get("/healthz").status_code == 200
    assert not dienst.laeuft


def test_unerwarteter_quellenfehler_blockiert_nicht_andere_quelle(tmp_path, stadt, uhr):
    fake_ris = FakeOParl()
    fake_plan = FakeBauleitplanung(next(q for q in stadt.quellen if q.typ == "bauleitplanung"))
    reihenfolge = []

    def handler(req):
        if req.url.host == "nw.bauleitplanung-online.de":
            reihenfolge.append("bauleitplanung")
            if req.url.path.endswith("/ajax"):
                raise RuntimeError("Simulierter unerwarteter Transportfehler")
            return fake_plan(req)
        reihenfolge.append("ris")
        return fake_ris._antworte(req)

    dienst, erzeugte = _sync(tmp_path, stadt, uhr, handler)
    dienst.betrieb = replace(dienst.betrieb, sync_quellen=("koeln:bauleitplanung", "koeln:ris"))
    dienst = SyncDienst(dienst.db_pfad, {"koeln": stadt}, dienst.betrieb, abrufer_factory=dienst.abrufer_factory)
    berichte = dienst.etappe()
    assert len(berichte) == 1 and berichte[0].quelle_id == "koeln:ris" and berichte[0].status == "ok"
    assert all(a.client.is_closed for a in erzeugte)
    assert reihenfolge[:2] == ["bauleitplanung"] * 2 and set(reihenfolge[2:]) == {"ris"}
    with Speicher(dienst.db_pfad) as sp:
        zeilen = {q["id"]: dict(q) for q in sp.quellen()}
        assert zeilen["koeln:bauleitplanung"]["status"] == "fehler"
        assert zeilen["koeln:bauleitplanung"]["letzter_versuch"]
        assert {r["status"] for r in sp.letzte_laeufe()} == {"ok", "fehler"}
    assert dienst.etappe() == [] and len(erzeugte) == 2
