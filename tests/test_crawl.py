from datetime import datetime, timedelta, timezone

import httpx
import pytest

from behoerdenmaster.abruf import Abrufer
from behoerdenmaster.crawler import CrawlVerweigert, LaufOptionen, crawle_oparl

from conftest import (
    FakeOParl, demo_objekte, synth_beratung, synth_gremium, synth_sitzung,
    synth_top, synth_vorlage,
)


def _abrufer(uhr, fake, **kw):
    return Abrufer(kontakt="test@example.test", min_intervall=3.0, sleep=uhr.schlaf, clock=uhr,
                   client=httpx.Client(transport=fake.transport()), **kw)


def _jetzt_iso(minuten=0):
    return (datetime.now(timezone.utc) + timedelta(minutes=minuten)).isoformat(timespec="seconds")


def _papierabrufe(fake):
    return [(u, p) for u, p in fake.anfragen if "/papers" in u.split("?")[0]]


def test_erster_lauf_paginiert_und_speichert(stadt, db, uhr):
    gremien, sitzungen, vorlagen = demo_objekte()
    extra = [synth_vorlage(900 + i, f"Erlenweg {i} in Köln-Bickendorf", datum="2024-05-01",
                           modified="2024-05-02T10:00:00+02:00") for i in range(3)]
    fake = FakeOParl(papers=vorlagen + extra, meetings=sitzungen, orgs=gremien, seite=2)
    bericht = crawle_oparl(db, stadt, stadt.quellen[0], _abrufer(uhr, fake), LaufOptionen(seit="2020-01-01"))

    assert bericht.status == "ok"
    zaehl = db.zaehle()
    assert zaehl["vorgang"] == 4
    assert zaehl["sitzung"] == 2
    assert zaehl["gremium"] == 2
    assert zaehl["tagesordnungspunkt"] == 16
    # Paginierung: mehrere Seiten, jede Folgeseite trägt den Filter weiter.
    papierabrufe = _papierabrufe(fake)
    assert len(papierabrufe) >= 2
    assert all("modified_since" in url for url, _ in papierabrufe)
    quelle = db.db.execute("SELECT status, letzter_erfolg FROM quelle WHERE id = ?", ("koeln:ris",)).fetchone()
    assert quelle["status"] == "ok" and quelle["letzter_erfolg"]


def test_ortsbezug_aus_betreff_und_gremium(stadt, db, uhr):
    gremien = [synth_gremium(4, "Bezirksvertretung 4 (Ehrenfeld)"), synth_gremium(5, "Stadtentwicklungsausschuss")]
    top = synth_top(501, 40, "3", "Bebauungsplan", ergebnis="ungeändert beschlossen", consultation_nr=601)
    sitzung = synth_sitzung(40, "Bezirksvertretung 4 (Ehrenfeld) - 10.07.2017", "2017-07-10T17:00:00+02:00",
                            gremium_nr=4, tops=[top])
    vorlage = synth_vorlage(601, "Erlenweg in Köln-Bickendorf: Aufstellungsbeschluss", datum="2017-03-01",
                            modified=_jetzt_iso(), consultations=[synth_beratung(601, 601, 40, 501, gremium_nr=4)])
    fake = FakeOParl(papers=[vorlage], meetings=[sitzung], orgs=gremien, seite=5)
    bericht = crawle_oparl(db, stadt, stadt.quellen[0], _abrufer(uhr, fake), LaufOptionen(seit="2017-01-01"))
    assert bericht.status == "ok"
    orte = {(r["typ"], r["schluessel"], r["quelle"]) for r in db.db.execute(
        "SELECT typ, schluessel, quelle FROM ortsbezug WHERE vorgang_id = ?", (vorlage["id"],))}
    assert ("stadtteil", "403", "betreff") in orte
    assert ("bezirk", "4", "betreff") in orte
    assert ("bezirk", "4", "gremium") in orte
    suchtext = db.db.execute("SELECT suchtext FROM vorgang WHERE id = ?", (vorlage["id"],)).fetchone()[0]
    assert "erlenweg" in suchtext and "bickendorf" in suchtext


def test_folgelauf_holt_nur_aenderungen(stadt, db, uhr):
    gremien, sitzungen, vorlagen = demo_objekte()
    fake = FakeOParl(papers=vorlagen, meetings=sitzungen, orgs=gremien, seite=5)
    crawle_oparl(db, stadt, stadt.quellen[0], _abrufer(uhr, fake), LaufOptionen(seit="2020-01-01"))

    neu = synth_vorlage(777, "Neue Vorlage in Köln-Nippes", datum=_jetzt_iso()[:10], modified=_jetzt_iso())
    fake.objekte["papers"].append(neu)
    fake.anfragen.clear()
    bericht = crawle_oparl(db, stadt, stadt.quellen[0], _abrufer(uhr, fake), LaufOptionen())
    assert bericht.status == "ok"
    assert bericht.zaehler.get("vorlagen:gespeichert") == 1
    assert db.zaehle()["vorgang"] == 2
    assert any("modified_since" in p for _, p in _papierabrufe(fake))


def test_unterbrochener_lauf_setzt_an_derselben_stelle_fort(stadt, db, uhr):
    gremien, sitzungen, vorlagen = demo_objekte()
    extra = [synth_vorlage(910 + i, f"Vorlage {i}", modified="2024-05-02T10:00:00+02:00") for i in range(3)]
    fake = FakeOParl(papers=vorlagen + extra, meetings=sitzungen, orgs=gremien, seite=2)

    # 4 Anfragen Stammdaten + robots, dann genau eine Seite Vorlagen
    erster = crawle_oparl(db, stadt, stadt.quellen[0], _abrufer(uhr, fake, max_anfragen=6),
                          LaufOptionen(seit="2020-01-01"))
    assert erster.status == "teilweise"
    assert db.zaehle()["vorgang"] == 2
    stand = db.stand_lesen("koeln:ris", "vorlagen")
    assert stand["naechste_url"].endswith("page=2") or "page=2" in stand["naechste_url"]

    fake.anfragen.clear()
    zweiter = crawle_oparl(db, stadt, stadt.quellen[0], _abrufer(uhr, fake), LaufOptionen())
    assert zweiter.status == "ok"
    erste_url, erste_params = _papierabrufe(fake)[0]
    # Die Fortsetzung ruft die gespeicherte URL mit Seite 2 und dem ursprünglichen Filter auf.
    assert erste_params.get("page") == "2"
    assert erste_params.get("modified_since") == "2020-01-01T00:00:00+01:00"
    assert db.zaehle()["vorgang"] == 4


def test_403_bricht_ohne_wiederholung_ab_und_kuehlt_ab(stadt, db, uhr):
    gremien, sitzungen, vorlagen = demo_objekte()
    fake = FakeOParl(papers=vorlagen, meetings=sitzungen, orgs=gremien)
    fake.skript["papers"] = [403, 200]
    bericht = crawle_oparl(db, stadt, stadt.quellen[0], _abrufer(uhr, fake), LaufOptionen(seit="2020-01-01"))
    assert bericht.status == "gesperrt"
    assert len(_papierabrufe(fake)) == 1, "bei 403 darf nicht wiederholt werden"
    assert db.db.execute("SELECT status FROM quelle WHERE id='koeln:ris'").fetchone()[0] == "gesperrt"

    with pytest.raises(CrawlVerweigert):
        crawle_oparl(db, stadt, stadt.quellen[0], _abrufer(uhr, fake), LaufOptionen(seit="2020-01-01"))


def test_503_wartet_und_setzt_fort(stadt, db, uhr):
    gremien, sitzungen, vorlagen = demo_objekte()
    fake = FakeOParl(papers=vorlagen, meetings=sitzungen, orgs=gremien)
    fake.skript["papers"] = [503]
    bericht = crawle_oparl(db, stadt, stadt.quellen[0], _abrufer(uhr, fake), LaufOptionen(seit="2020-01-01"))
    assert bericht.status == "ok"
    assert 60.0 in uhr.schlaefe
    assert db.zaehle()["vorgang"] == 1


def test_robots_verbot_startet_keinen_abruf_der_listen(stadt, db, uhr):
    gremien, sitzungen, vorlagen = demo_objekte()
    fake = FakeOParl(papers=vorlagen, meetings=sitzungen, orgs=gremien,
                     robots="User-agent: *\nDisallow: /oparl/\n")
    bericht = crawle_oparl(db, stadt, stadt.quellen[0], _abrufer(uhr, fake), LaufOptionen(seit="2020-01-01"))
    assert bericht.status == "gesperrt"
    assert "robots" in bericht.fehlertext.lower()
    assert _papierabrufe(fake) == []


def test_geloeschte_vorlage_wird_markiert(stadt, db, uhr):
    gremien, sitzungen, vorlagen = demo_objekte()
    weg = synth_vorlage(555, "Wird gelöscht: Bebauungsplan in Köln-Ehrenfeld", modified="2024-05-02T10:00:00+02:00")
    fake = FakeOParl(papers=vorlagen + [weg], meetings=sitzungen, orgs=gremien)
    crawle_oparl(db, stadt, stadt.quellen[0], _abrufer(uhr, fake), LaufOptionen(seit="2020-01-01"))
    assert db.db.execute("SELECT COUNT(*) FROM ortsbezug WHERE vorgang_id=?", (weg["id"],)).fetchone()[0] > 0

    fake.objekte["papers"] = [v for v in fake.objekte["papers"] if v["id"] != weg["id"]]
    fake.objekte["papers"].append({"id": weg["id"], "type": weg["type"], "deleted": True, "modified": _jetzt_iso()})
    crawle_oparl(db, stadt, stadt.quellen[0], _abrufer(uhr, fake), LaufOptionen())
    zeile = db.db.execute("SELECT geloescht FROM vorgang WHERE id=?", (weg["id"],)).fetchone()
    assert zeile["geloescht"] == 1
    assert db.db.execute("SELECT COUNT(*) FROM ortsbezug WHERE vorgang_id=?", (weg["id"],)).fetchone()[0] == 0


def test_budget_wird_als_teilweise_gemeldet(stadt, db, uhr):
    gremien, sitzungen, vorlagen = demo_objekte()
    fake = FakeOParl(papers=vorlagen, meetings=sitzungen, orgs=gremien)
    bericht = crawle_oparl(db, stadt, stadt.quellen[0], _abrufer(uhr, fake, max_anfragen=2),
                           LaufOptionen(seit="2020-01-01"))
    assert bericht.status == "teilweise"
    assert db.db.execute("SELECT status FROM quelle WHERE id='koeln:ris'").fetchone()[0] == "teilweise"


def test_abkuehlzeit_waechst_mit_fehlschlaegen(stadt, db, uhr):
    """Nach jedem Fehlschlag in Folge verdoppelt sich die Pause (10 min, 20, 40 …)."""
    from datetime import timedelta as td
    gremien, sitzungen, vorlagen = demo_objekte()
    fake = FakeOParl(papers=vorlagen, meetings=sitzungen, orgs=gremien)
    fake.skript["papers"] = [403, 403]
    crawle_oparl(db, stadt, stadt.quellen[0], _abrufer(uhr, fake), LaufOptionen(seit="2020-01-01"))
    # Zweiter Fehlschlag: erst simulieren, dass die letzte Sperre 11 Minuten zurückliegt.
    db.db.execute("UPDATE quelle SET letzter_versuch = ? WHERE id = 'koeln:ris'",
                  ((datetime.now(timezone.utc) - td(minutes=11)).isoformat(timespec="seconds"),))
    crawle_oparl(db, stadt, stadt.quellen[0], _abrufer(uhr, fake), LaufOptionen(seit="2020-01-01"))
    assert db.fehlschlaege_in_folge("koeln:ris") == 2
    # Nach zwei Fehlschlägen gilt eine Pause von 20 min: 11 min reichen nicht mehr.
    with pytest.raises(CrawlVerweigert) as info:
        crawle_oparl(db, stadt, stadt.quellen[0], _abrufer(uhr, fake), LaufOptionen(seit="2020-01-01"))
    assert "Frühestens" in str(info.value)
