"""Adapter, Fortsetzung, Archivierung, Verlauf und gemeinsame Suche ohne Netz testen."""

from __future__ import annotations

import json
from datetime import date
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from behoerdenmaster.abruf import Abrufer
from behoerdenmaster.api import create_app
from behoerdenmaster.bauleitplanung import klartext, parse_dokumente, parse_liste
from behoerdenmaster.config import PROJEKTWURZEL
from behoerdenmaster.crawler import CRAWLER, CrawlVerweigert, LaufOptionen
from behoerdenmaster.demo import lade_demo, lade_plan_demo, loesche_demo
from behoerdenmaster.plan_auswertung import plan_status
from behoerdenmaster.suche import Suchauftrag, suche, vorgang_detail

FIXTURES = PROJEKTWURZEL / "fixtures" / "bauleitplanung" / "koeln"
HEUTE = date(2026, 10, 9)


def beispiele():
    return json.loads((FIXTURES / "verfahren.json").read_text(encoding="utf-8"))


@pytest.fixture
def quelle(stadt):
    return next(q for q in stadt.quellen if q.typ == "bauleitplanung")


class FakeBauleitplanung:
    def __init__(self, quelle):
        self.quelle = quelle
        self.data = beispiele()["data"]
        self.aufrufe: list[str] = []
        self.page_size = 20
        self.list_error: int | None = None
        self.detail_error: int | None = None
        self.robots = "User-agent: *\n"
        self.retry_after = None
        self.fremder_link = None

    def __call__(self, req):
        self.aufrufe.append(str(req.url))
        assert "BehoerdenMaster/" in req.headers["User-Agent"]
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text=self.robots)
        if req.url.path == "/verfahren/suche/ajax":
            assert req.url.params.get("orgaSlug") == "koeln"
            if self.list_error:
                return httpx.Response(self.list_error, headers={"Retry-After": self.retry_after} if self.retry_after else {})
            page = int(req.url.params.get("page", "1"))
            section = self.data[(page - 1) * self.page_size: page * self.page_size]
            links = {}
            if page * self.page_size < len(self.data):
                # Filter absichtlich nicht im Link: Adapter muss ihn wieder hinzufügen.
                links["next"] = f"?page={page + 1}"
            if self.fremder_link:
                links["next"] = self.fremder_link
            return httpx.Response(200, json={"data": section, "links": links})
        if req.url.path.endswith("/public/detail"):
            if self.detail_error == 302:
                return httpx.Response(302, headers={"Location": "/dplan/login"})
            if self.detail_error:
                return httpx.Response(self.detail_error)
            html = (FIXTURES / "clubkultur-detail.html").read_text(encoding="utf-8")
            return httpx.Response(200, text=html, headers={"Content-Type": "text/html"})
        raise AssertionError(f"Unerwarteter Abruf (z. B. PDF/Login): {req.url}")

    def listenabrufe(self):
        return [u for u in self.aufrufe if urlsplit(u).path == "/verfahren/suche/ajax"]


def run(db, stadt, quelle, uhr, fake, budget=None, **options):
    abrufer = Abrufer(kontakt="test@example.test", client=httpx.Client(transport=httpx.MockTransport(fake)),
                      sleep=uhr.schlaf, clock=uhr, max_anfragen=budget, wiederholungen=0)
    try:
        return CRAWLER[quelle.typ](db, stadt, quelle, abrufer, LaufOptionen(**options))
    finally:
        abrufer.schliessen()


def test_oeffentliche_felder_und_original_links(quelle):
    verfahren, next_url = parse_liste(beispiele(), quelle)
    assert len(verfahren) == 3 and next_url is None
    club = verfahren[-1]
    assert club.url.endswith("/bf1b9a19-a120-446b-99a0-42f1b6e9269c/public/detail")
    assert club.phase == "Beteiligung Öffentlichkeit"
    assert club.ende == "2026-11-13T02:00:00+01:00"
    assert "Clubs planungsrechtlich" in club.beschreibung


def test_neue_phase_definition_name_und_unbekannte_phase(quelle):
    daten = beispiele()
    attr = daten["data"][0]["attributes"]
    attr["externalPhaseDefinitionName"] = "Auswertung der Stellungnahmen"
    assert parse_liste(daten, quelle)[0][0].phase == "Auswertung der Stellungnahmen"
    del attr["externalPhaseDefinitionName"]
    attr["externalPhaseTranslationKey"] = "unbekannter.schritt"
    assert parse_liste(daten, quelle)[0][0].phase == "Verfahrensphase unbekannt"


def test_html_normalisierung_ignoriert_skripte():
    assert klartext("<p>Wohnen &amp; Grün</p><script>fake data</script><p>Quartier</p>") == "Wohnen & Grün Quartier"


def test_keine_internen_felder_als_fallback(quelle):
    daten = beispiele()
    obj = daten["data"][0]
    obj["attributes"].pop("externalName")
    obj["attributes"]["name"] = "Geheimer interner Titel"
    with pytest.raises(ValueError, match="öffentlicher Titel"):
        parse_liste(daten, quelle)


def test_andere_kommune_und_hidden_sicht_werden_nicht_importiert(quelle):
    daten = beispiele()
    daten["data"][0]["attributes"]["owningOrganisationName"] = "Stadt Bonn"
    daten["data"][1]["attributes"]["externalPhasePermissionset"] = "hidden"
    assert len(parse_liste(daten, quelle)[0]) == 1


@pytest.mark.parametrize("daten", [{}, {"data": None}, {"data": {}}, {"data": ["kaputt"]},
                                  {"data": [], "errors": [{"message": "Fehler"}]}])
def test_fehlerantwort_nicht_als_leere_liste_interpretieren(quelle, daten):
    with pytest.raises(ValueError):
        parse_liste(daten, quelle)


@pytest.mark.parametrize("datum", ["kaputt", "2026-99-01", 123])
def test_ungueltige_fristeingabe_wird_gemeldet(quelle, datum):
    daten = beispiele()
    daten["data"][0]["attributes"]["externalEndDate"] = datum
    with pytest.raises(ValueError):
        parse_liste(daten, quelle)


def test_datum_null_und_placeholder(quelle):
    daten = beispiele()
    attr = daten["data"][0]["attributes"]
    attr["externalStartDate"] = None
    attr["externalEndDate"] = "1970-01-01T00:00:00+00:00"
    plan = parse_liste(daten, quelle)[0][0]
    assert plan.beginn == "" and plan.ende == ""


@pytest.mark.parametrize("ident", ["../admin", "", "https://fremd.test", "../login"])
def test_verfahrens_id_kann_keine_arbitraere_url_sein(quelle, ident):
    daten = beispiele()
    daten["data"][0]["id"] = ident
    with pytest.raises(ValueError):
        parse_liste(daten, quelle)


def test_folgelink_behaelt_organisationsfilter(quelle):
    daten = beispiele()
    daten["links"]["next"] = "?page=2"
    params = parse_qs(urlsplit(parse_liste(daten, quelle)[1]).query)
    assert params == {"page": ["2"], "orgaSlug": ["koeln"]}


@pytest.mark.parametrize("url", ["https://fremd.test/verfahren/suche/ajax", "/admin", "?orgaSlug=bonn&page=2"])
def test_folgelink_darf_weder_scope_noch_host_wechseln(quelle, url):
    daten = beispiele()
    daten["links"]["next"] = url
    with pytest.raises(ValueError):
        parse_liste(daten, quelle)


def test_dokumentlinks_dedupliziert_und_benannt(quelle):
    plan = parse_liste(beispiele(), quelle)[0][-1]
    html = (FIXTURES / "clubkultur-detail.html").read_text(encoding="utf-8")
    docs = parse_dokumente(html, plan.url)
    assert len(docs) == 3
    assert docs[0]["name"] == "254.FNP-Änderung - Lage des Aenderungsbereiches"
    assert all(d["rolle"] == "plandokument" for d in docs)
    assert all(d["url"].startswith("https://nw.bauleitplanung-online.de/file/") for d in docs)


def test_dokumentlinks_nicht_aus_fremden_hosts_oder_javascript(quelle):
    plan = parse_liste(beispiele(), quelle)[0][-1]
    html = '<div id="helpPublicDetailTimelimit"></div>' \
           '<a href="javascript:alert(1)">Böse</a>' \
           '<a href="https://fremd.test/file/1/2">Fremd</a>'
    assert parse_dokumente(html, plan.url) == []


def test_login_oder_wartung_ist_keine_erfolgreiche_detailseite(quelle):
    from behoerdenmaster.abruf import QuelleGesperrt
    with pytest.raises(QuelleGesperrt):
        parse_dokumente('<h1>Anmelden</h1><input type="password">', quelle.system_url)
    with pytest.raises(ValueError):
        parse_dokumente("<h1>Wartung</h1>", quelle.system_url)


def test_crawl_ist_idempotent_und_ohne_pdfabrufe(db, stadt, quelle, uhr):
    fake = FakeBauleitplanung(quelle)
    first = run(db, stadt, quelle, uhr, fake)
    second = run(db, stadt, quelle, uhr, fake)
    assert first.status == second.status == "ok"
    assert db.zaehle()["vorgang"] == 3
    assert db.db.execute("SELECT COUNT(*) FROM planstand").fetchone()[0] == 3
    assert not any("/file/" in u for u in fake.aufrufe)
    assert first.anfragen == 5  # robots, Liste, drei Detailseiten
    assert max(uhr.schlaefe) >= 3


def test_budget_nach_liste_setzt_nur_details_fort(db, stadt, quelle, uhr):
    fake = FakeBauleitplanung(quelle)
    first = run(db, stadt, quelle, uhr, fake, budget=2)
    assert first.status == "teilweise" and len(db.aufgaben("koeln:bauleitplanung")) == 3
    assert db.db.execute("SELECT COUNT(*) FROM planstand").fetchone()[0] == 3
    fake.aufrufe.clear()
    second = run(db, stadt, quelle, uhr, fake)
    assert second.status == "ok"
    assert fake.listenabrufe() == [], "abgeschlossene Liste bei Fortsetzung nicht erneut abrufen"
    assert db.aufgaben("koeln:bauleitplanung") == []


def test_budget_vor_erster_liste(db, stadt, quelle, uhr):
    fake = FakeBauleitplanung(quelle)
    assert run(db, stadt, quelle, uhr, fake, budget=1).status == "teilweise"
    assert db.zaehle()["vorgang"] == 0
    assert run(db, stadt, quelle, uhr, fake).status == "ok"
    assert db.zaehle()["vorgang"] == 3


def test_paginierung_und_fortsetzung(db, stadt, quelle, uhr):
    fake = FakeBauleitplanung(quelle)
    fake.page_size = 1
    assert run(db, stadt, quelle, uhr, fake, budget=2).status == "teilweise"
    assert db.zaehle()["vorgang"] == 1
    fake.aufrufe.clear()
    assert run(db, stadt, quelle, uhr, fake).status == "ok"
    assert "page=2" in fake.listenabrufe()[0]
    assert db.zaehle()["vorgang"] == 3


def test_komplette_liste_archiviert_verschwundene_verfahren(db, stadt, quelle, uhr):
    fake = FakeBauleitplanung(quelle)
    run(db, stadt, quelle, uhr, fake)
    club = fake.data.pop()
    run(db, stadt, quelle, uhr, fake)
    assert db.zaehle()["vorgang"] == 3
    original = quelle.web_vorlage.format(id=club["id"])
    detail = vorgang_detail(db, stadt, original, HEUTE)
    assert detail["status"]["kurz"] == "nicht mehr gelistet"
    assert not detail["beteiligung"]["gelistet"]
    assert "kein Beschluss" in detail["status"]["text"]
    assert len(detail["dokumente"]) == 3


def test_teilweise_liste_archiviert_keine_alten_verfahren(db, stadt, quelle, uhr):
    fake = FakeBauleitplanung(quelle)
    run(db, stadt, quelle, uhr, fake)
    fake.page_size = 1
    assert run(db, stadt, quelle, uhr, fake, budget=2).status == "teilweise"
    assert db.db.execute("SELECT SUM(gelistet) FROM planverfahren").fetchone()[0] == 3


def test_zeitraum_und_phasenaenderung_erzeugt_einen_verlaufsschritt(db, stadt, quelle, uhr):
    fake = FakeBauleitplanung(quelle)
    run(db, stadt, quelle, uhr, fake)
    fake.data[-1]["attributes"]["externalEndDate"] = "2026-11-20T23:59:59+01:00"
    fake.data[-1]["attributes"]["externalPhaseDefinitionName"] = "Erneute Beteiligung"
    run(db, stadt, quelle, uhr, fake)
    url = quelle.web_vorlage.format(id=fake.data[-1]["id"])
    detail = vorgang_detail(db, stadt, url, HEUTE)
    beobachtung = [e for e in detail["verlauf"] if e["typ"] == "beobachtung"]
    assert len(beobachtung) == 2
    assert "Phase, Beteiligungsfrist" in beobachtung[-1]["beschreibung"]
    assert detail["beteiligung"]["ende_de"] == "20.11.2026"
    run(db, stadt, quelle, uhr, fake)
    assert db.db.execute("SELECT COUNT(*) FROM planstand").fetchone()[0] == 4


@pytest.mark.parametrize("code", [403, 429, 302])
def test_sperre_und_login_werden_nicht_umgangen(db, stadt, quelle, uhr, code):
    fake = FakeBauleitplanung(quelle)
    fake.detail_error = code
    report = run(db, stadt, quelle, uhr, fake)
    assert report.status == "gesperrt"
    assert len(db.aufgaben("koeln:bauleitplanung")) == 3
    assert not any("/login" in u for u in fake.aufrufe)
    with pytest.raises(CrawlVerweigert):
        run(db, stadt, quelle, uhr, fake)


def test_robots_verbot(db, stadt, quelle, uhr):
    fake = FakeBauleitplanung(quelle)
    fake.robots = "User-agent: *\nDisallow: /verfahren/\n"
    assert run(db, stadt, quelle, uhr, fake).status == "gesperrt"
    assert fake.listenabrufe() == []


def test_detail_404_loescht_keine_vorhandenen_dokumente(db, stadt, quelle, uhr):
    fake = FakeBauleitplanung(quelle)
    run(db, stadt, quelle, uhr, fake)
    old_count = db.zaehle()["dokument"]
    fake.detail_error = 404
    assert run(db, stadt, quelle, uhr, fake).status == "ok"
    assert db.zaehle()["dokument"] == old_count
    assert db.db.execute("SELECT DISTINCT detail_status FROM planverfahren").fetchone()[0] == "nicht verfügbar"


def test_falscher_folgelink_schreibt_keinen_falschen_erfolg(db, stadt, quelle, uhr):
    fake = FakeBauleitplanung(quelle)
    fake.fremder_link = "https://fremd.test/admin"
    report = run(db, stadt, quelle, uhr, fake)
    assert report.status == "fehler"
    assert db.zaehle()["vorgang"] == 0
    assert not any("fremd.test" in u for u in fake.aufrufe)


def test_frage_und_plz_finden_beide_quelltypen(db, stadt):
    lade_demo(db, stadt)
    ergebnis = suche(db, Suchauftrag(stadt=stadt, orte=[{"typ": "bezirk", "schluessel": "4"}], themen=["bauen"]), HEUTE)
    assert len(ergebnis["treffer"]) == 3
    assert all(t["quelle"]["typ"] == "bauleitplanung" for t in ergebnis["treffer"])
    plz = suche(db, Suchauftrag(stadt=stadt, plz="50827"), HEUTE)
    assert plz["gesamt"] == 3
    club = ergebnis["treffer"][0]
    assert club["links"]["original"].startswith("https://nw.bauleitplanung-online.de/verfahren/")
    assert club["links"]["oparl"] is None and club["links"]["ratsinfo_vorlage"] is None
    assert not any("sitzung" in e for e in club["verlauf"]), "keine erfundene Ratsberatung"


def test_plan_beschreibung_ist_suchbar(db, stadt):
    lade_demo(db, stadt)
    ergebnis = suche(db, Suchauftrag(stadt=stadt, freitext=["wohneinheiten", "2030"]), HEUTE)
    assert ergebnis["gesamt"] == 1
    assert "voraussichtlich ab 2030" in ergebnis["treffer"][0]["zusammenfassung"]


def test_demo_ueberschreibt_niemals_echten_bauleitbestand(db, stadt, quelle, uhr):
    fake = FakeBauleitplanung(quelle)
    run(db, stadt, quelle, uhr, fake)
    assert lade_plan_demo(db, stadt) == 0
    assert db.db.execute("SELECT DISTINCT quelle_id FROM vorgang").fetchone()[0] == "koeln:bauleitplanung"
    loesche_demo(db, stadt)
    assert db.zaehle()["vorgang"] == 3


def test_echter_abruf_uebernimmt_keine_demo_historie(db, stadt, quelle, uhr):
    lade_plan_demo(db, stadt)
    fake = FakeBauleitplanung(quelle)
    run(db, stadt, quelle, uhr, fake)
    assert db.db.execute("SELECT COUNT(*) FROM planstand").fetchone()[0] == 3
    assert db.db.execute("SELECT DISTINCT quelle_id FROM vorgang").fetchone()[0] == "koeln:bauleitplanung"


def test_api_verfahren_hat_beteiligung_verlauf_und_keinen_ratslink(db, stadt):
    lade_demo(db, stadt)
    client = TestClient(create_app(db.pfad))
    result = client.get("/api/plz/50827?thema=bauen").json()
    assert result["gesamt"] == 3
    club = result["treffer"][0]
    detail = client.get("/api/vorgang", params={"id": club["id"]}).json()
    assert detail["beteiligung"]["ende_de"] == "13.11.2026"
    assert len(detail["dokumente"]) == 3
    assert detail["hinweise"]
    assert detail["links"]["ratsinfo_vorlage"] is None


def test_status_beendete_beteiligung_ist_nicht_bauabschluss(db, stadt):
    lade_plan_demo(db, stadt)
    plan = dict(db.db.execute("SELECT * FROM planverfahren ORDER BY ende DESC LIMIT 1").fetchone())
    result = plan_status(plan, date(2027, 1, 1))
    assert result["kurz"] == "Beteiligungsfrist beendet" and not result["offen"]
    assert "keine Aussage" in result["text"]


def test_konfiguration_bauleitplanung_scope(quelle):
    assert quelle.system_url.endswith("?orgaSlug=koeln")
    assert quelle.min_intervall_sekunden >= 3
    assert "Stadt Köln" in quelle.organisation
    assert "bauleitplanung" in CRAWLER


def test_abbruch_mitten_in_der_seite_ist_atomar(db, stadt, quelle, uhr, monkeypatch):
    fake = FakeBauleitplanung(quelle)
    original = db.stand_schreiben

    def fehler_am_checkpoint(qid, entitaet, modified, naechste, reihe=""):
        if entitaet == "verfahren" and not naechste and reihe:
            raise ValueError("Simulierter Abbruch vor Checkpoint")
        return original(qid, entitaet, modified, naechste, reihe)

    monkeypatch.setattr(db, "stand_schreiben", fehler_am_checkpoint)
    assert run(db, stadt, quelle, uhr, fake).status == "fehler"
    assert db.zaehle()["vorgang"] == 0
    assert db.aufgaben("koeln:bauleitplanung") == []
    assert db.db.execute("SELECT COUNT(*) FROM planstand").fetchone()[0] == 0
    monkeypatch.setattr(db, "stand_schreiben", original)
    assert run(db, stadt, quelle, uhr, fake).status == "ok"
    assert db.zaehle()["vorgang"] == 3


def test_keyboardinterrupt_speichert_keine_halbe_seite(db, stadt, quelle, uhr, monkeypatch):
    fake = FakeBauleitplanung(quelle)
    original = db.aufgabe_anlegen
    calls = 0

    def abbrechen(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt
        return original(*args)

    monkeypatch.setattr(db, "aufgabe_anlegen", abbrechen)
    with pytest.raises(KeyboardInterrupt):
        run(db, stadt, quelle, uhr, fake)
    assert db.zaehle()["vorgang"] == 0
    assert db.letzte_laeufe()[0]["status"] == "teilweise"
    monkeypatch.setattr(db, "aufgabe_anlegen", original)
    assert run(db, stadt, quelle, uhr, fake).status == "ok"


def test_zwei_laeufe_in_derselben_sekunde_werden_unterschieden(db, stadt, quelle, uhr, monkeypatch):
    monkeypatch.setattr("behoerdenmaster.bauleitplanung.jetzt_iso", lambda: "2026-10-09T12:00:00+00:00")
    fake = FakeBauleitplanung(quelle)
    run(db, stadt, quelle, uhr, fake)
    fake.data.pop()
    run(db, stadt, quelle, uhr, fake)
    assert db.db.execute("SELECT SUM(gelistet) FROM planverfahren").fetchone()[0] == 2


def test_schemamigration_ist_additiv(tmp_path):
    import sqlite3
    from behoerdenmaster.speicher import SCHEMA, Speicher

    pfad = tmp_path / "alt.sqlite"
    with sqlite3.connect(pfad) as alt:
        alt.executescript(SCHEMA.split("-- Ergänzungen für öffentliche Bauleitverfahren")[0]
                          .replace(",\n    abkuehlung_bis TEXT NOT NULL DEFAULT ''", ""))
        alt.execute("INSERT INTO quelle (id, stadt, schluessel, typ) VALUES ('koeln:ris', 'koeln', 'ris', 'oparl')")
        alt.execute("INSERT INTO vorgang (id, quelle_id, titel) VALUES ('vorhanden', 'koeln:ris', 'Bestehende Ratsvorlage')")
    with Speicher(pfad) as neu:
        assert neu.zaehle()["vorgang"] == 1
        assert neu.quellen()[0]["abkuehlung_bis"] == ""
        assert neu.db.execute("SELECT COUNT(*) FROM planverfahren").fetchone()[0] == 0
        assert neu.db.execute("SELECT titel FROM vorgang").fetchone()[0] == "Bestehende Ratsvorlage"
    with Speicher(pfad) as nochmal:
        assert nochmal.zaehle()["vorgang"] == 1


def test_frist_wird_mit_uhrzeit_und_zeitzone_geprueft(db, stadt):
    from datetime import datetime
    lade_plan_demo(db, stadt)
    plan = dict(db.db.execute("SELECT * FROM planverfahren WHERE ende LIKE '2026-10-09%'").fetchone())
    um_eins = datetime.fromisoformat("2026-10-09T01:00:00+02:00")
    um_drei = datetime.fromisoformat("2026-10-09T03:00:00+02:00")
    assert plan_status(plan, HEUTE, um_eins)["offen"]
    assert not plan_status(plan, HEUTE, um_drei)["offen"]
    assert "02:00 Uhr" in plan_status(plan, HEUTE, um_drei)["text"]


def test_ungueltige_reihenfolge_innerhalb_eines_tages(quelle):
    daten = beispiele()
    attr = daten["data"][0]["attributes"]
    attr["externalStartDate"] = "2026-10-09T15:00:00+02:00"
    attr["externalEndDate"] = "2026-10-09T14:00:00+02:00"
    with pytest.raises(ValueError, match="Ende des Beteiligungszeitraums"):
        parse_liste(daten, quelle)


def test_iso_reine_datumsangabe_endet_am_tagesende(db, stadt):
    from datetime import datetime
    lade_plan_demo(db, stadt)
    plan = dict(db.db.execute("SELECT * FROM planverfahren LIMIT 1").fetchone())
    plan["beginn"], plan["ende"] = "2026-10-01", "2026-10-09"
    assert plan_status(plan, HEUTE, datetime.fromisoformat("2026-10-09T23:59:00+02:00"))["offen"]


def test_rueckkehr_zum_alten_stand_bleibt_im_verlauf(db, stadt, quelle, uhr):
    fake = FakeBauleitplanung(quelle)
    run(db, stadt, quelle, uhr, fake)
    alt = fake.data[-1]["attributes"]["externalEndDate"]
    fake.data[-1]["attributes"]["externalEndDate"] = "2026-11-20T23:59:59+01:00"
    run(db, stadt, quelle, uhr, fake)
    fake.data[-1]["attributes"]["externalEndDate"] = alt
    run(db, stadt, quelle, uhr, fake)
    assert db.db.execute("SELECT COUNT(*) FROM planstand").fetchone()[0] == 5


def test_detail_einer_anderen_stadt_ist_nicht_auslesbar(db, stadt, quelle):
    from dataclasses import replace
    lade_plan_demo(db, stadt)
    original = quelle.web_vorlage.format(id=beispiele()["data"][0]["id"])
    assert vorgang_detail(db, replace(stadt, schluessel="bonn"), original, HEUTE) is None


def test_fehlendes_organisationsfeld_ist_schemabruch(quelle):
    daten = beispiele()
    daten["data"][0]["attributes"].pop("owningOrganisationName")
    with pytest.raises(ValueError, match="Organisationsfeld"):
        parse_liste(daten, quelle)


def test_demo_dokumente_werden_nicht_als_frisch_abgerufen_ausgegeben(db, stadt, quelle, uhr):
    lade_plan_demo(db, stadt)
    fake = FakeBauleitplanung(quelle)
    run(db, stadt, quelle, uhr, fake, budget=2)
    assert db.zaehle()["dokument"] == 0
    assert db.db.execute("SELECT DISTINCT detail_status FROM planverfahren").fetchone()[0] == "offen"


def test_demo_loeschen_entfernt_auch_planstaende_und_aufgaben(db, stadt):
    lade_demo(db, stadt)
    loesche_demo(db, stadt)
    assert db.zaehle()["vorgang"] == 0
    assert db.db.execute("SELECT COUNT(*) FROM planstand").fetchone()[0] == 0
    assert db.db.execute("SELECT COUNT(*) FROM planverfahren").fetchone()[0] == 0
    assert db.aufgaben("koeln:bauleitplanung-demo") == []


def test_unbekannte_quelle_erhaelt_keine_oparl_links(db, stadt):
    db.quelle_anlegen("koeln:weitere", "koeln", "weitere", "andere", "Weitere Quelle", "https://example.test")
    db.vorgang_speichern({"id": "fremdes-verfahren", "quelle_id": "koeln:weitere", "titel": "Anderes Verfahren",
                         "web": "https://example.test/detail"}, [], [], [], set())
    t = vorgang_detail(db, stadt, "fremdes-verfahren", HEUTE)
    assert t["links"]["oparl"] is None and t["links"]["ratsinfo_vorlage"] is None
    assert t["links"]["original"] == "https://example.test/detail"
    assert t["quelle"]["typ"] == "andere"


@pytest.mark.parametrize("args", [["--seit", "2026-01-01"], ["--nur", "vorlagen"], ["--max-anfragen", "0"]])
def test_cli_weist_oparl_optionen_und_falsches_budget_zurueck(args):
    from behoerdenmaster.cli import main
    with pytest.raises(SystemExit) as exc:
        main(["crawl", "--quelle", "bauleitplanung", *args])
    assert "Bauleitplanung" in str(exc.value) or "mindestens 1" in str(exc.value)


def test_cli_probe_und_etappen_crawl(tmp_path, quelle, uhr, monkeypatch, capsys):
    from behoerdenmaster.cli import main
    fake = FakeBauleitplanung(quelle)

    def abrufer(**args):
        return Abrufer(**args, sleep=uhr.schlaf, clock=uhr, client=httpx.Client(transport=httpx.MockTransport(fake)))

    monkeypatch.setattr("behoerdenmaster.cli.Abrufer", abrufer)
    pfad = tmp_path / "cli.sqlite"
    assert main(["--db", str(pfad), "probe", "--quelle", "bauleitplanung", "--kontakt", "test@example.test"]) == 0
    assert "3 öffentliche Verfahren" in capsys.readouterr().out
    assert not pfad.exists(), "probe darf keine Datenbank erzeugen"
    assert main(["--db", str(pfad), "crawl", "--quelle", "bauleitplanung", "--kontakt", "test@example.test", "--max-anfragen", "2"]) == 0
    assert "Status teilweise" in capsys.readouterr().out
    assert main(["--db", str(pfad), "crawl", "--quelle", "bauleitplanung", "--kontakt", "test@example.test", "--max-anfragen", "10"]) == 0
    assert "Status ok" in capsys.readouterr().out


def test_server_retry_after_gilt_auch_im_naechsten_prozess(db, stadt, quelle, uhr):
    from datetime import datetime, timedelta, timezone
    from behoerdenmaster.speicher import Speicher

    fake = FakeBauleitplanung(quelle)
    fake.list_error, fake.retry_after = 503, "86400"
    report = run(db, stadt, quelle, uhr, fake)
    assert report.status == "nicht erreichbar"
    assert datetime.fromisoformat(report.abkuehlung_bis) > datetime.now(timezone.utc) + timedelta(hours=23)
    # Die gewöhnliche 10-Minuten-Pause wäre abgelaufen; Retry-After ist aber noch aktiv.
    with db.db:
        db.db.execute("UPDATE quelle SET letzter_versuch=? WHERE id='koeln:bauleitplanung'",
                      ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),))
    seen = len(fake.aufrufe)
    with Speicher(db.pfad) as neu:
        with pytest.raises(CrawlVerweigert):
            run(neu, stadt, quelle, uhr, fake)
    assert len(fake.aufrufe) == seen


@pytest.mark.parametrize("next_link", [{"falsch": "?page=2"}, {"href": 5}, [], False])
def test_kaputter_next_link_ist_keine_abgeschlossene_liste(quelle, next_link):
    daten = beispiele()
    daten["links"]["next"] = next_link
    with pytest.raises(ValueError):
        parse_liste(daten, quelle)


def test_utc_beginn_filtert_und_erscheint_als_berliner_datum(db, stadt, quelle):
    daten = beispiele()
    attr = daten["data"][0]["attributes"]
    attr["externalStartDate"] = "2026-09-25T23:00:00Z"
    plan = parse_liste(daten, quelle)[0][0]
    qid = stadt.quell_id(quelle)
    db.quelle_anlegen(qid, "koeln", quelle.schluessel, quelle.typ, quelle.name, quelle.system_url)
    plan.speichern(db, stadt, qid, "zyklus")
    detail = vorgang_detail(db, stadt, plan.url, HEUTE)
    assert detail["datum"] == "2026-09-26"
    assert detail["beteiligung"]["beginn_de"] == "26.09.2026"
    assert detail["beteiligung"]["beginn_zeit_de"] == "26.09.2026, 01:00 Uhr"


def test_neue_liste_markiert_alte_dokumente_bis_zur_pruefung(db, stadt, quelle, uhr):
    fake = FakeBauleitplanung(quelle)
    run(db, stadt, quelle, uhr, fake)
    docs = db.zaehle()["dokument"]
    run(db, stadt, quelle, uhr, fake, budget=2)
    assert db.zaehle()["dokument"] == docs
    assert db.db.execute("SELECT DISTINCT detail_status FROM planverfahren").fetchone()[0] == "offen"


def test_keine_koelner_demo_unter_anderer_kommune(db, stadt):
    from dataclasses import replace
    andere_stadt = replace(stadt, schluessel="bonn", name="Bonn")
    with pytest.raises(ValueError, match="nur für Köln"):
        lade_demo(db, andere_stadt)
    assert db.zaehle()["vorgang"] == 0


def test_verlauf_ordnen_auch_innerhalb_eines_tages(db, stadt, quelle):
    plan = parse_liste(beispiele(), quelle)[0][0]
    from dataclasses import replace
    plan = replace(plan, beginn="2026-10-09T08:00:00+02:00", ende="2026-10-09T23:59:00+02:00")
    qid = stadt.quell_id(quelle)
    db.quelle_anlegen(qid, "koeln", quelle.schluessel, quelle.typ, quelle.name, quelle.system_url)
    plan.speichern(db, stadt, qid, "zyklus", festgestellt="2026-10-09T12:00:00+00:00")
    detail = vorgang_detail(db, stadt, plan.url, HEUTE)
    assert [e["typ"] for e in detail["verlauf"]] == ["beteiligung", "beobachtung", "beteiligung"]
