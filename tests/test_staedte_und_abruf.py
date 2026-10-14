"""Weitere Städte, Laden auf Abruf, Städteübersicht, Sicherungen und Rechtstexte."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from behoerdenmaster import cli
from behoerdenmaster.api import create_app, stadt_aus_frage
from behoerdenmaster.bedarf import aktive_staedte, bedarf_melden, bedarfe
from behoerdenmaster.betrieb import Betrieb, SyncDienst
from behoerdenmaster.config import PROJEKTWURZEL, lade_staedte
from behoerdenmaster.demo import lade_demo
from behoerdenmaster.geo import Gazetteer
from behoerdenmaster.nlq import verstehe
from behoerdenmaster.rechtliches import Betreiber, datenschutz, impressum
from behoerdenmaster.speicher import Speicher
from behoerdenmaster.staedte import beispiele, markdown

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
NEUE = ("aachen", "bonn", "duesseldorf", "muenster", "wuppertal")


@pytest.fixture(scope="module")
def staedte():
    return lade_staedte()


# --- Konfiguration ---------------------------------------------------------------

def test_alle_staedte_laden_mit_gleichem_system(staedte):
    assert {"koeln", *NEUE} <= set(staedte)
    assert staedte["koeln"].abruf == "dauerhaft"
    ids = set()
    for schluessel in NEUE:
        st = staedte[schluessel]
        assert st.auf_abruf, schluessel
        assert any(q.typ == "oparl" and q.automatisch for q in st.quellen), schluessel
        assert st.bezirke and st.plz, schluessel  # Straßen nur, wo der OSM-Auszug Stadtteile führt
        for q in st.quellen:
            assert st.quell_id(q) not in ids
            ids.add(st.quell_id(q))


@pytest.mark.parametrize("schluessel", ["koeln", *NEUE])
def test_beispielfragen_werden_verstanden(staedte, schluessel):
    st = staedte[schluessel]
    gaz = Gazetteer(st)
    erste, zweite, _ = beispiele(st)
    assert verstehe(erste, st, gaz).orte, erste
    assert verstehe(zweite, st, gaz).orte, zweite


def test_stadtteile_bleiben_in_ihrer_stadt(staedte):
    # „Altstadt“ gibt es in Düsseldorf und Münster – die Ortsgliederung gehört je zur Stadt.
    for schluessel in ("duesseldorf", "muenster"):
        st = staedte[schluessel]
        v = verstehe("Bauvorhaben in der Altstadt", st, Gazetteer(st))
        assert v.orte and all(o["name"] == "Altstadt" for o in v.orte)


def test_staedte_md_passt_zur_konfiguration(staedte):
    datei = PROJEKTWURZEL / "docs" / "staedte.md"
    erwartet = markdown(staedte, standard="koeln")
    assert datei.read_text(encoding="utf-8").strip() == erwartet.strip(), \
        "docs/staedte.md neu erzeugen: python -m behoerdenmaster staedte --markdown > docs/staedte.md"


# --- Bedarf (Laden auf Abruf) --------------------------------------------------------

def test_bedarf_nur_fuer_auf_abruf_und_schreibarm(tmp_path, staedte):
    with Speicher(tmp_path / "b.sqlite") as sp:
        assert bedarf_melden(sp, staedte["koeln"], T0) is False
        assert bedarf_melden(sp, staedte["bonn"], T0) is True
        assert bedarf_melden(sp, staedte["bonn"], T0 + timedelta(minutes=5)) is False
        assert bedarf_melden(sp, staedte["bonn"], T0 + timedelta(minutes=11)) is True
        eintrag = bedarfe(sp)["bonn"]
        assert eintrag["anforderungen"] == 2 and "koeln" not in bedarfe(sp)


def test_aktive_staedte_verfall_und_maximum(tmp_path, staedte):
    with Speicher(tmp_path / "b.sqlite") as sp:
        bedarf_melden(sp, staedte["bonn"], T0 - timedelta(days=20))
        bedarf_melden(sp, staedte["aachen"], T0 - timedelta(days=2))
        bedarf_melden(sp, staedte["muenster"], T0 - timedelta(days=1))
        assert aktive_staedte(sp, staedte, tage=14, maximum=3, jetzt=T0) == ["muenster", "aachen"]
        assert aktive_staedte(sp, staedte, tage=14, maximum=1, jetzt=T0) == ["muenster"]
        assert aktive_staedte(sp, staedte, tage=30, maximum=5, jetzt=T0) == ["muenster", "aachen", "bonn"]
        assert aktive_staedte(sp, staedte, tage=14, maximum=0, jetzt=T0) == []


def test_sync_plant_auf_abruf_staedte_erst_nach_anforderung(tmp_path, staedte):
    pfad = tmp_path / "s.sqlite"
    betrieb = Betrieb(sync_aktiv=True, kontakt="test@example.org")
    dienst = SyncDienst(pfad, staedte, betrieb, clock=lambda: T0)
    bonn_ids = {staedte["bonn"].quell_id(q) for q in staedte["bonn"].quellen if q.automatisch}
    with Speicher(pfad) as sp:
        geplant = dienst.geplante_quellen(sp)
        assert all(qid.startswith("koeln:") for qid in geplant)
        bedarf_melden(sp, staedte["bonn"], T0 - timedelta(hours=1))
        assert bonn_ids <= set(dienst.geplante_quellen(sp))
        aus = SyncDienst(pfad, staedte, Betrieb(sync_aktiv=True, kontakt="x@example.org", auf_abruf=False),
                         clock=lambda: T0)
        assert not bonn_ids & set(aus.geplante_quellen(sp))
        spaeter = SyncDienst(pfad, staedte, betrieb, clock=lambda: T0 + timedelta(days=15))
        assert not bonn_ids & set(spaeter.geplante_quellen(sp))


# --- Automatische Sicherung -------------------------------------------------------------

def test_sicherung_faellig_und_rotation(tmp_path, stadt, staedte):
    pfad = tmp_path / "db.sqlite"
    with Speicher(pfad) as sp:
        lade_demo(sp, stadt)
    ziel = tmp_path / "sicherungen"
    ziel.mkdir()
    fremd = ziel / "andere-datei.sqlite"
    fremd.write_text("bleibt")
    uhr = {"t": T0}
    betrieb = Betrieb(backup_verzeichnis=str(ziel), backup_intervall=3600, backup_behalten=2)
    dienst = SyncDienst(pfad, staedte, betrieb, clock=lambda: uhr["t"])
    assert dienst.sicherung() is not None
    assert dienst.sicherung() is None  # Intervall noch nicht vorbei
    for stunden in (2, 4, 6):
        uhr["t"] = T0 + timedelta(hours=stunden)
        assert dienst.sicherung() is not None
    eigene = sorted(p.name for p in ziel.glob("behoerdenmaster-*.sqlite"))
    assert eigene == ["behoerdenmaster-20261001T160000Z.sqlite", "behoerdenmaster-20261001T180000Z.sqlite"]
    assert fremd.read_text() == "bleibt"
    with Speicher(ziel / eigene[-1]) as kopie:
        assert kopie.db.execute("SELECT COUNT(*) FROM vorgang").fetchone()[0] == 4
    with Speicher(pfad) as sp:
        assert sp.betrieb_wert("sicherung_zuletzt").startswith("2026-10-01T18:00")


def test_betrieb_umgebung_fuer_abruf_und_sicherung():
    b = Betrieb.aus_umgebung({"BEHOERDEN_AUF_ABRUF": "false", "BEHOERDEN_AUF_ABRUF_TAGE": "30",
                              "BEHOERDEN_AUF_ABRUF_MAX": "1", "BEHOERDEN_BACKUP_VERZEICHNIS": "/geheim/pfad",
                              "BEHOERDEN_BACKUP_BEHALTEN": "3", "BEHOERDEN_SYNC_ENABLED": "false"})
    assert (b.auf_abruf, b.auf_abruf_tage, b.auf_abruf_max, b.backup_behalten) == (False, 30, 1, 3)
    status = b.oeffentlicher_status()
    assert "/geheim/pfad" not in repr(status) and "/geheim/pfad" not in repr(b)
    locker = Betrieb.aus_umgebung({"BEHOERDEN_AUF_ABRUF_TAGE": "0", "BEHOERDEN_SYNC_ENABLED": "false"}, streng=False)
    assert locker.auf_abruf_tage == 14 and locker.warnungen
    with pytest.raises(ValueError):
        Betrieb.aus_umgebung({"BEHOERDEN_AUF_ABRUF_TAGE": "0", "BEHOERDEN_SYNC_ENABLED": "false"})


# --- API ------------------------------------------------------------------------------

def _client(tmp_path, stadt, **betrieb) -> TestClient:
    pfad = tmp_path / "api.sqlite"
    with Speicher(pfad) as sp:
        lade_demo(sp, stadt)
    return TestClient(create_app(pfad, betrieb=Betrieb(**betrieb)))


def test_api_staedte_uebersicht(tmp_path, stadt):
    daten = _client(tmp_path, stadt).get("/api/staedte").json()
    assert daten["standard"] == "koeln"
    liste = daten["staedte"]
    assert liste[0]["schluessel"] == "koeln" and liste[0]["standard"]
    assert {s["schluessel"] for s in liste} >= {"koeln", *NEUE}
    bonn = next(s for s in liste if s["schluessel"] == "bonn")
    assert bonn["abruf"] == "auf_abruf" and bonn["bestand_vorgaenge"] == 0
    assert bonn["beispiel_plz"].startswith("53") and len(bonn["beispiele"]) == 3
    assert bonn["gliederung"]["stufe"] == "voll"


def test_api_anfordern_vermerkt_nur_bedarf(tmp_path, stadt):
    client = _client(tmp_path, stadt, sync_aktiv=True, kontakt="test@example.org")
    antwort = client.post("/api/staedte/bonn/anfordern")
    assert antwort.status_code == 200
    daten = antwort.json()
    assert daten["stadt"]["zustand"] == "laedt" and daten["stadt"]["bestand_vorgaenge"] == 0
    assert any("wird auf Abruf geladen" in h for h in daten["hinweise"])
    with Speicher(tmp_path / "api.sqlite") as sp:
        assert bedarfe(sp)["bonn"]["anforderungen"] == 1
    # Dauerhafte Städte brauchen keine Anforderung, unbekannte gibt es nicht.
    assert client.post("/api/staedte/koeln/anfordern").json()["hinweise"] == []
    assert client.post("/api/staedte/atlantis/anfordern").status_code in (404, 422)


def test_api_anfordern_ohne_sync_erklaert(tmp_path, stadt):
    client = _client(tmp_path, stadt)
    daten = client.post("/api/staedte/aachen/anfordern").json()
    assert daten["stadt"]["zustand"] == "aus"
    assert any("ausgeschaltet" in h for h in daten["hinweise"])
    with Speicher(tmp_path / "api.sqlite") as sp:
        assert "aachen" not in bedarfe(sp)


def test_frage_erkennt_oder_schlaegt_stadt_vor(tmp_path, stadt):
    client = _client(tmp_path, stadt)
    ohne = client.post("/api/frage", json={"frage": "Was plant Düsseldorf in Bilk?"}).json()
    assert ohne["stadt"]["schluessel"] == "duesseldorf"
    assert any("Stadt aus der Frage erkannt" in h for h in ohne["hinweise"])
    assert ohne["stadt_vorschlag"] is None
    mit = client.post("/api/frage", json={"frage": "Was plant Düsseldorf in Bilk?", "stadt": "koeln"}).json()
    assert mit["stadt"]["schluessel"] == "koeln"
    assert mit["stadt_vorschlag"]["schluessel"] == "duesseldorf"
    strasse = client.post("/api/frage", json={"frage": "Baustelle Bonner Straße", "stadt": "koeln"}).json()
    assert strasse["stadt_vorschlag"] is None


def test_stadt_aus_frage_mehrdeutig(staedte):
    koeln = staedte["koeln"]
    assert stadt_aus_frage("Verkehr zwischen Bonn und Köln", staedte, koeln) is None
    assert stadt_aus_frage("Verkehr zwischen Bonn und Aachen", staedte, koeln) is None
    assert stadt_aus_frage("Verkehr in Münster", staedte, koeln).schluessel == "muenster"


def test_plz_einer_auf_abruf_stadt_ohne_daten(tmp_path, stadt):
    daten = _client(tmp_path, stadt).get("/api/plz/53111?stadt=bonn").json()
    assert daten["gesamt"] == 0
    assert any("Bonn" in h for h in daten["hinweise"])


# --- Rechtstexte ------------------------------------------------------------------------

def test_impressum_ohne_angaben_sagt_das_offen(tmp_path, stadt):
    client = _client(tmp_path, stadt)
    seite = client.get("/impressum")
    assert seite.status_code == 200 and "noch nicht hinterlegt" in seite.text
    assert client.get("/api/status").json()["rechtliches"]["impressum_vollstaendig"] is False
    assert client.get("/datenschutz").status_code == 200


def test_impressum_aus_umgebung_und_escaping():
    b = Betreiber.aus_umgebung({"BEHOERDEN_BETREIBER_NAME": "Muster <GmbH>",
                                "BEHOERDEN_BETREIBER_ANSCHRIFT": "Hauptstr. 1|50667 Köln",
                                "BEHOERDEN_BETREIBER_EMAIL": "kontakt@example.org"})
    assert b.vollstaendig and not b.fehlend
    seite = impressum(b)
    assert "Muster &lt;GmbH&gt;" in seite and "<GmbH>" not in seite
    assert "50667 Köln" in seite and "noch nicht hinterlegt" not in seite
    for text in (seite, datenschutz(b)):
        # Keine externen Ressourcen (Schriften, Skripte, Tracker).
        assert "<script" not in text and 'src="http' not in text and "<link" not in text


def test_cli_staedte_json(tmp_path, capsys):
    code = cli.main(["--db", str(tmp_path / "c.sqlite"), "staedte", "--json"])
    assert code == 0
    assert '"standard": "koeln"' in capsys.readouterr().out
