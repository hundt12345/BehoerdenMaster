"""Leerer Datenbestand: Diagnose statt stiller, trefferloser Startseite.

Der beobachtete Fehler auf der veröffentlichten Instanz war keine kaputte Suche, sondern
eine Datenbank ohne jeden Vorgang – kombiniert mit einer Oberfläche, die dazu nichts sagte.
Diese Tests sichern drei Dinge:

1. Ein leerer Bestand wird erkannt und mit den zuständigen Einstellungen erklärt.
2. Ein einzelner fehlender oder ungültiger Umgebungswert macht den Webdienst nicht
   unerreichbar (er meldet das Problem stattdessen sichtbar).
3. Das konfigurierte Erstabruf-Fenster kommt tatsächlich im Abruf an.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from behoerdenmaster.abruf import Abrufer
from behoerdenmaster.api import create_app
from behoerdenmaster.betrieb import Betrieb, SyncDienst, initialisiere, pruefe_seit
from behoerdenmaster.config import lade_stadt
from behoerdenmaster.speicher import Speicher

from conftest import FakeOParl, Uhr, demo_objekte


def _client(tmp_path, betrieb: Betrieb):
    pfad = tmp_path / "leer.sqlite"
    initialisiere(pfad, {"koeln": lade_stadt("koeln")}, betrieb)
    return TestClient(create_app(pfad, betrieb=betrieb)), pfad


def test_status_erklert_leeren_bestand_bei_ausgeschaltetem_abruf(tmp_path):
    client, _ = _client(tmp_path, Betrieb(demo=False, sync_aktiv=False))
    with client:
        daten = client.get("/api/status").json()

    assert daten["zaehler"]["vorgang"] == 0
    assert daten["bestand_leer"] is True
    text = " ".join(daten["hinweise"])
    assert "leer" in text
    assert "BEHOERDEN_SYNC_ENABLED" in text
    assert "BEHOERDEN_KONTAKT" in text
    # Ohne Beispieldaten darf nichts anderes behauptet werden.
    assert daten["betrieb"]["demo_modus"] is False
    assert daten["betrieb"]["demo_status"] == "deaktiviert"


def test_status_zeigt_grund_einer_nicht_erreichbaren_quelle(tmp_path):
    client, pfad = _client(tmp_path, Betrieb(sync_aktiv=True, kontakt="t@example.test"))
    with Speicher(pfad) as sp:
        sp.quelle_status("koeln:ris", "nicht erreichbar", "Keine Antwort von buergerinfo.stadt-koeln.de")
    with client:
        daten = client.get("/api/status").json()

    assert daten["bestand_leer"] is True
    assert any("Keine Antwort von buergerinfo" in h for h in daten["hinweise"])
    ris = next(q for q in daten["quellen"] if q["id"] == "koeln:ris")
    assert ris["fehlertext"].startswith("Keine Antwort")
    assert ris["status"] == "nicht erreichbar"


def test_status_kuendigt_den_noch_nicht_gestarteten_hintergrundabruf_an(tmp_path):
    betrieb = Betrieb(sync_aktiv=True, kontakt="t@example.test", sync_startverzoegerung=15)
    client, _ = _client(tmp_path, betrieb)
    with client:
        daten = client.get("/api/status").json()

    assert daten["bestand_leer"] is True
    text = " ".join(daten["hinweise"])
    assert "15 s" in text
    assert daten["betrieb"]["sync_dienst_laeuft"] is True
    # Ohne BEHOERDEN_SYNC_QUELLEN sind alle konfigurierten Quellen gemeint,
    # in der Reihenfolge aus config/cities/koeln.toml.
    assert daten["betrieb"]["quellen_effektiv"] == ["koeln:ris", "koeln:bauleitplanung"]


def test_hinweis_nennt_nur_die_eingerichteten_quellen(tmp_path):
    """Der Hinweis darf keine Quelle ankündigen, für die gar kein Abruf eingerichtet ist."""
    betrieb = Betrieb(sync_aktiv=True, kontakt="t@example.test", sync_quellen=("koeln:ris",))
    client, _ = _client(tmp_path, betrieb)
    with client:
        daten = client.get("/api/status").json()

    assert daten["betrieb"]["quellen_effektiv"] == ["koeln:ris"]
    text = " ".join(daten["hinweise"])
    assert "Ratsinformationssystem" in text
    assert "Bauleitplanung Online" not in text


def test_volle_ladung_startet_ohne_warnung(tmp_path):
    client, pfad = _client(tmp_path, Betrieb())
    with Speicher(pfad) as sp, sp.transaktion():
        sp.db.execute("INSERT INTO vorgang (id, quelle_id, titel) VALUES ('x', 'koeln:ris', 't')")
    with client:
        daten = client.get("/api/status").json()

    assert daten["bestand_leer"] is False
    assert daten["hinweise"] == []


# --- Umgebungswerte: ein Fehler darf den Dienst nicht abschalten ------------------


def test_sync_ohne_kontaktadresse_wird_abgeschaltet_statt_abzubrechen():
    betrieb = Betrieb.aus_umgebung({"BEHOERDEN_SYNC_ENABLED": "true"}, streng=False)

    assert betrieb.sync_aktiv is False
    assert len(betrieb.warnungen) == 1
    assert "BEHOERDEN_KONTAKT" in betrieb.warnungen[0]
    # Die Warnung ist Teil des öffentlichen Status, die Adresse selbst nie.
    oeffentlich = betrieb.oeffentlicher_status()
    assert oeffentlich["automatische_abrufe"] is False
    assert "BEHOERDEN_KONTAKT" in oeffentlich["warnungen"][0]
    assert "kontakt" not in oeffentlich


def test_cli_modus_bricht_weiter_ab():
    with pytest.raises(ValueError, match="BEHOERDEN_KONTAKT"):
        Betrieb.aus_umgebung({"BEHOERDEN_SYNC_ENABLED": "true"})


def test_ungueltige_werte_werden_im_webdienst_zu_warnungen_mit_standard():
    betrieb = Betrieb.aus_umgebung(
        {"BEHOERDEN_SYNC_ENABLED": "true", "BEHOERDEN_KONTAKT": "t@example.test",
         "BEHOERDEN_SYNC_INTERVALL": "abc", "BEHOERDEN_SYNC_MAX_ANFRAGEN": "0",
         "BEHOERDEN_SYNC_SEIT": "gestern"},
        streng=False,
    )

    assert betrieb.sync_aktiv is True
    assert betrieb.sync_intervall == 21600
    assert betrieb.sync_budget == 100
    assert betrieb.sync_seit == ""
    assert len(betrieb.warnungen) == 3


def test_zeilenumbruch_in_der_kontaktadresse_bleibt_ein_harter_fehler():
    with pytest.raises(ValueError, match="Zeilenumbrüche"):
        Betrieb.aus_umgebung({"BEHOERDEN_SYNC_ENABLED": "true", "BEHOERDEN_KONTAKT": "a@b.de\nX"},
                             streng=False)


# --- Erstabruf-Fenster -----------------------------------------------------------


@pytest.mark.parametrize("wert,erwartet", [("90", "90d"), ("90d", "90d"), ("", ""),
                                           ("2026-01-15", "2026-01-15")])
def test_seit_wert_wird_geprueft(wert, erwartet):
    assert pruefe_seit(wert) == erwartet


@pytest.mark.parametrize("wert", ["gestern", "0", "-5d", "2026-13-40"])
def test_seit_wert_lehnt_ungueltiges_ab(wert):
    with pytest.raises(ValueError, match="BEHOERDEN_SYNC_SEIT"):
        pruefe_seit(wert)


def test_tagesfenster_ist_rollierend():
    betrieb = Betrieb(sync_seit="90d")
    referenz = date(2026, 10, 11)
    assert betrieb.seit_datum(referenz) == "2026-07-13"
    # Kein fester Wert, der veraltet: dieselbe Einstellung liefert morgen ein anderes Datum.
    assert betrieb.seit_datum(referenz + timedelta(days=1)) == "2026-07-14"
    assert Betrieb(sync_seit="2026-01-15").seit_datum(referenz) == "2026-01-15"
    assert Betrieb().seit_datum(referenz) is None


def test_hintergrundabruf_nutzt_das_konfigurierte_fenster(tmp_path):
    """Das Fenster muss im echten Abruf ankommen, sonst bleibt der Erstabruf riesig."""
    stadt = lade_stadt("koeln")
    gremien, sitzungen, vorlagen = demo_objekte()
    fake = FakeOParl(papers=vorlagen, meetings=sitzungen, orgs=gremien)
    uhr = Uhr()
    pfad = tmp_path / "fenster.sqlite"
    betrieb = Betrieb(sync_aktiv=True, kontakt="t@example.test", sync_quellen=("koeln:ris",),
                      sync_seit="90d")
    initialisiere(pfad, {"koeln": stadt}, Betrieb())

    def factory(**optionen):
        return Abrufer(**(optionen | {"client": httpx.Client(transport=fake.transport()),
                                      "sleep": uhr.schlaf, "clock": uhr, "wiederholungen": 0}))

    dienst = SyncDienst(pfad, {"koeln": stadt}, betrieb, abrufer_factory=factory)
    berichte = dienst.etappe()

    assert berichte[0].status == "ok"
    erwartet = (datetime.now(timezone.utc).date() - timedelta(days=90)).isoformat()
    papierabrufe = [(u, p) for u, p in fake.anfragen if u.split("?")[0].endswith("/papers")]
    assert papierabrufe, "der Lauf muss die Vorlagenliste abrufen"
    grenzen = {p["modified_since"] for _, p in papierabrufe if "modified_since" in p}
    assert grenzen and all(g.startswith(erwartet) for g in grenzen), grenzen
    # Nicht das Wahlperioden-Datum aus koeln.toml, sondern das konfigurierte Fenster.
    assert not any(g.startswith("2025-11-01") for g in grenzen), grenzen
