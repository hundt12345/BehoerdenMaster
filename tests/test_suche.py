from datetime import date

import httpx

from behoerdenmaster.abruf import Abrufer
from behoerdenmaster.crawler import LaufOptionen, crawle_oparl
from behoerdenmaster.suche import Suchauftrag, suche, vorgang_detail

from conftest import (
    FakeOParl, KOERPER, demo_objekte, synth_beratung, synth_gremium, synth_sitzung,
    synth_top, synth_vorlage,
)

HEUTE = date(2026, 10, 9)


def _aufbau(stadt, db, uhr):
    """Demo-Auszug plus synthetische Ehrenfeld-Vorgänge, über den echten Crawl-Pfad gespeichert."""
    gremien, sitzungen, vorlagen = demo_objekte()
    gremien = gremien + [
        synth_gremium(4, "Bezirksvertretung 4 (Ehrenfeld)"),
        synth_gremium(5, "Stadtentwicklungsausschuss"),
    ]
    sitzungen = sitzungen + [
        synth_sitzung(40, "Bezirksvertretung 4 (Ehrenfeld) - 10.07.2017", "2017-07-10T17:00:00+02:00", 4, tops=[
            synth_top(5001, 40, "10.1", "Bebauungsplan Erlenweg in Köln-Bickendorf", "ungeändert beschlossen", 6001),
        ]),
        synth_sitzung(41, "Stadtentwicklungsausschuss", "2026-11-10T16:00:00+01:00", 5, tops=[
            synth_top(5002, 41, "4", "Aufstellungsbeschluss Güterbahnhof in Köln-Ehrenfeld", "", 6002),
        ]),
    ]
    neu = "2026-10-01T10:00:00+02:00"
    vorgaenge = vorlagen + [
        synth_vorlage(601, "Erlenweg in Köln-Bickendorf: Bebauungsplan Nr. 1234", datum="2017-03-01", modified=neu,
                      consultations=[synth_beratung(6001, 601, 40, 5001, gremium_nr=4)]),
        synth_vorlage(602, "Ehemaliger Güterbahnhof in Köln-Ehrenfeld: Aufstellungsbeschluss", datum="2026-09-01",
                      modified=neu, consultations=[synth_beratung(6002, 602, 41, 5002, gremium_nr=5)]),
        synth_vorlage(603, "Verkehrsberuhigung Venloer Straße in Köln-Ehrenfeld", datum="2025-06-01", modified=neu),
        synth_vorlage(604, "Haushaltsplanung Lindenthal Schulen", datum="2025-02-01", modified=neu),
        synth_vorlage(605, "Wohnungsbau in Köln-Bickendorf (PLZ 50827)", datum="2026-02-01", modified=neu,
                      art="Antrag"),
    ]
    fake = FakeOParl(papers=vorgaenge, meetings=sitzungen, orgs=gremien, seite=50)
    abrufer = Abrufer(kontakt="t@example.test", min_intervall=3.0, sleep=uhr.schlaf, clock=uhr,
                      client=httpx.Client(transport=fake.transport()))
    bericht = crawle_oparl(db, stadt, stadt.quellen[0], abrufer, LaufOptionen(seit="2017-01-01"))
    assert bericht.status == "ok", bericht.fehlertext
    return {"vorlagen": {v["id"].rsplit("/", 1)[-1]: v for v in vorgaenge}}


def _id(nr):
    return f"{KOERPER}/papers/vo/{nr}"


def test_ort_mit_bezirk_und_thema(stadt, db, uhr):
    _aufbau(stadt, db, uhr)
    auftrag = Suchauftrag(stadt=stadt, orte=[{"typ": "stadtteil", "schluessel": "401"},
                                              {"typ": "bezirk", "schluessel": "4"}],
                          themen=["bauen"], offen=True)
    erg = suche(db, auftrag, HEUTE)
    ids = {t["id"] for t in erg["treffer"]}
    assert _id(601) in ids and _id(602) in ids and _id(605) in ids
    assert _id(603) not in ids, "Verkehrsvorlage gehört nicht zum Thema Bauen"
    assert _id(604) not in ids, "Lindenthal liegt nicht im Bezirk Ehrenfeld"


def test_status_anstehend_und_beschlossen(stadt, db, uhr):
    _aufbau(stadt, db, uhr)
    erg = suche(db, Suchauftrag(stadt=stadt, freitext=["güterbahnhof"]), HEUTE)
    assert len(erg["treffer"]) == 1
    guter = erg["treffer"][0]
    assert guter["status"]["kurz"] == "anstehend"
    assert "Stadtentwicklungsausschuss" in guter["status"]["text"]
    erlenweg = vorgang_detail(db, stadt, _id(601), HEUTE)
    assert erlenweg["status"]["kurz"] == "Beschluss gefasst"
    assert erlenweg["verlauf"][0]["gremium"] == "Bezirksvertretung 4 (Ehrenfeld)"
    assert erlenweg["verlauf"][0]["top"]["ergebnis"] == "ungeändert beschlossen"
    assert erlenweg["verlauf"][0]["datum"] == "2017-07-10"


def test_plz_nutzt_zuordnung_und_ausdrueckliche_nennung(stadt, db, uhr):
    _aufbau(stadt, db, uhr)
    erg = suche(db, Suchauftrag(stadt=stadt, plz="50827"), HEUTE)
    ids = {t["id"] for t in erg["treffer"]}
    assert _id(602) in ids and _id(605) in ids
    assert _id(604) not in ids


def test_zeitraum_filter(stadt, db, uhr):
    _aufbau(stadt, db, uhr)
    erg = suche(db, Suchauftrag(stadt=stadt, orte=[{"typ": "bezirk", "schluessel": "4"}], von="2025-01-01"), HEUTE)
    assert _id(601) not in {t["id"] for t in erg["treffer"]}


def test_paginierung(stadt, db, uhr):
    _aufbau(stadt, db, uhr)
    basis = suche(db, Suchauftrag(stadt=stadt, orte=[{"typ": "bezirk", "schluessel": "4"}]), HEUTE)
    seite2 = suche(db, Suchauftrag(stadt=stadt, orte=[{"typ": "bezirk", "schluessel": "4"}], limit=2, offset=2), HEUTE)
    assert basis["gesamt"] == seite2["gesamt"]
    assert len(seite2["treffer"]) == min(2, basis["gesamt"] - 2)
    assert [t["id"] for t in seite2["treffer"]] == [t["id"] for t in basis["treffer"][2:4]]


def test_freitext_wirkt_als_und_bei_fehlendem_filter(stadt, db, uhr):
    _aufbau(stadt, db, uhr)
    erg = suche(db, Suchauftrag(stadt=stadt, freitext=["venloer", "verkehrsberuhigung"]), HEUTE)
    assert [t["id"] for t in erg["treffer"]] == [_id(603)]


def test_quellen_links_und_dokumente(stadt, db, uhr):
    _aufbau(stadt, db, uhr)
    detail = vorgang_detail(db, stadt, _id(601), HEUTE)
    assert detail["links"]["ratsinfo_vorlage"] == "https://ratsinformation.stadt-koeln.de/vo0050.asp?__kvonr=601"
    assert detail["verlauf"][0]["sitzung"]["url"] == "https://ratsinformation.stadt-koeln.de/si0057.asp?__ksinr=40"
    assert detail["quelle"]["demo"] is False


def test_demo_daten_sind_gekennzeichnet_und_verlinkt(stadt, db):
    from behoerdenmaster.demo import lade_demo
    lade_demo(db, stadt)
    erg = suche(db, Suchauftrag(stadt=stadt), HEUTE)
    assert erg["gesamt"] == 1
    treffer = erg["treffer"][0]
    assert treffer["quelle"]["demo"] is True
    assert treffer["links"]["ratsinfo_vorlage"].endswith("__kvonr=101373")
    assert treffer["verlauf"][0]["datum"] == "" and treffer["verlauf"][0]["gremium"] == "Gremium 177"


def test_unbekannter_vorgang(stadt, db):
    assert vorgang_detail(db, stadt, _id(999999), HEUTE) is None


def test_freitext_rueckfall_auf_ein_stichwort(stadt, db, uhr):
    _aufbau(stadt, db, uhr)
    # „Venloer“ und „Digitalisierung“ kommen nicht zusammen vor: UND liefert nichts, ODER findet 603.
    erg = suche(db, Suchauftrag(stadt=stadt, freitext=["venloer", "digitalisierung"]), HEUTE)
    assert [t["id"] for t in erg["treffer"]] == [_id(603)]
    assert erg["freitext_oder"] is True
    # Ein einzelnes Stichwort gilt ohne Rückfall.
    erg = suche(db, Suchauftrag(stadt=stadt, freitext=["digitalisierung"]), HEUTE)
    assert erg["gesamt"] == 0 and erg["freitext_oder"] is False
