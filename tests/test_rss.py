"""RSS-Hinweise (Sitzungstermine, Pressemeldungen): Parser, Crawl und Schutzregeln.

Die Fixtures sind rekonstruiert (siehe fixtures/rss/koeln/README.md). Netz gibt es nicht:
Alle Abrufe laufen über httpx.MockTransport.
"""

from __future__ import annotations

import httpx
import pytest

from behoerdenmaster.abruf import Abrufer
from behoerdenmaster.config import PROJEKTWURZEL, lade_stadt
from behoerdenmaster.crawler import CRAWLER, LaufOptionen
from behoerdenmaster.rss import RssFehler, parse_feed

FIXTURES = PROJEKTWURZEL / "fixtures" / "rss" / "koeln"
SITZUNGS_URL = "https://buergerinfo.stadt-koeln.de/rssfeed.asp?id=1&filter=snvf"
PRESSE_URL = "https://www.stadt-koeln.de/externe-dienste/rss/pressemeldungen.xml"


def _lese(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def quelle_ris():
    return next(q for q in lade_stadt("koeln").quellen if q.typ == "ris-rss")


@pytest.fixture
def quelle_presse():
    return next(q for q in lade_stadt("koeln").quellen if q.typ == "presse-rss")


class FakeRss:
    def __init__(self, feeds: dict[str, str], robots: str = "User-agent: *\n") -> None:
        self.feeds = feeds
        self.robots = robots
        self.aufrufe: list[str] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.aufrufe.append(str(req.url))
        assert "BehoerdenMaster/" in req.headers["User-Agent"]
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text=self.robots)
        text = self.feeds.get(str(req.url))
        if text is None:
            raise AssertionError(f"Unerwarteter Abruf: {req.url}")
        return httpx.Response(200, text=text, headers={"Content-Type": "application/rss+xml; charset=utf-8"})


def _abrufer(fake, uhr):
    return Abrufer(kontakt="test@example.test", client=httpx.Client(transport=httpx.MockTransport(fake)),
                   sleep=uhr.schlaf, clock=uhr, wiederholungen=0)


def test_sitzungen_werden_übernommen_und_gefiltert():
    treffer, verworfen = parse_feed(_lese("sitzungen.xml"), "sitzungen", SITZUNGS_URL)
    assert [h.id for h in treffer] == ["sitzung:AVR/0008/2026", "sitzung:BBü/0017/2026", "sitzung:BV3/0008/2026"]
    # Vorlage und fremder Host zählen als verworfen, nicht als Sitzung.
    assert verworfen == 2
    avr = treffer[0]
    assert avr.datum == "2026-10-12" and avr.zeit == "17:00"
    assert avr.bezug == "meetings/34706"  # rssgo.asp?si34706 = OParl meetings/34706
    assert avr.web == "https://buergerinfo.stadt-koeln.de/rssgo.asp?si34706"
    assert avr.ort.startswith("Rathaus Spanischer Bau")
    assert avr.kurztext.startswith("Sondersitzung des Ausschusses")
    assert "<" not in avr.kurztext  # HTML-Tabelle wird nicht als Kurztext übernommen


def test_presse_mit_datum_und_guid():
    treffer, verworfen = parse_feed(_lese("presse.xml"), "presse", PRESSE_URL)
    assert verworfen == 0 and len(treffer) == 2
    erster = treffer[0]
    assert erster.datum == "2026-10-12"
    assert erster.id.endswith("koeln-beschliesst-weitreichende-erleichterungen-fuer-den-wohnungsbau")
    assert erster.kurztext.startswith("\"Köln baut bezahlbar\"")
    assert erster.bezug == ""  # Presse wird nicht an Vorgänge gehängt


def test_ungueltiges_xml_und_fremdes_format_sind_fehler():
    with pytest.raises(RssFehler):
        parse_feed("<rss><channel>", "presse", PRESSE_URL)
    with pytest.raises(RssFehler):
        parse_feed("<html><body>Wartung</body></html>", "presse", PRESSE_URL)


def test_crawl_speichert_nur_hinweise_und_ist_wiederholbar(db, stadt, quelle_ris, uhr):
    fake = FakeRss({SITZUNGS_URL: _lese("sitzungen.xml")})
    bericht = CRAWLER[quelle_ris.typ](db, stadt, quelle_ris, _abrufer(fake, uhr), LaufOptionen())
    assert bericht.status == "ok" and bericht.anfragen == 2  # robots.txt + ein Feed-Abruf
    qid = stadt.quell_id(quelle_ris)
    zahlen = db.zaehle_quelle(qid)
    assert zahlen["hinweise"] == 3
    # Keine Vorgänge, keine Dokumente: Hinweise sind bewusst nicht Teil der Suche.
    assert zahlen["vorgaenge"] == 0 and zahlen["dokumente"] == 0

    CRAWLER[quelle_ris.typ](db, stadt, quelle_ris, _abrufer(FakeRss({SITZUNGS_URL: _lese("sitzungen.xml")}), uhr),
                            LaufOptionen())
    assert db.zaehle_quelle(qid)["hinweise"] == 3  # Upsert, keine Dubletten


def test_fehlende_eintraege_werden_nicht_geloescht(db, stadt, quelle_ris, uhr):
    CRAWLER[quelle_ris.typ](db, stadt, quelle_ris, _abrufer(FakeRss({SITZUNGS_URL: _lese("sitzungen.xml")}), uhr),
                            LaufOptionen())
    nur_einer = _lese("sitzungen.xml").split("<item>")[0] + "<item>" + _lese("sitzungen.xml").split("<item>")[1] + "</channel></rss>"
    bericht = CRAWLER[quelle_ris.typ](db, stadt, quelle_ris, _abrufer(FakeRss({SITZUNGS_URL: nur_einer}), uhr),
                                      LaufOptionen())
    assert bericht.status == "ok"
    assert db.zaehle_quelle(stadt.quell_id(quelle_ris))["hinweise"] == 3


def test_robots_verbot_bricht_ohne_daten_ab(db, stadt, quelle_presse, uhr):
    fake = FakeRss({PRESSE_URL: _lese("presse.xml")}, robots="User-agent: *\nDisallow: /\n")
    bericht = CRAWLER[quelle_presse.typ](db, stadt, quelle_presse, _abrufer(fake, uhr), LaufOptionen())
    assert bericht.status == "gesperrt"
    assert PRESSE_URL not in fake.aufrufe  # der Feed wird bei Verbot gar nicht angefragt
    assert db.zaehle_quelle(stadt.quell_id(quelle_presse))["hinweise"] == 0


def test_presse_crawl_und_registry(db, stadt, quelle_presse, uhr):
    fake = FakeRss({PRESSE_URL: _lese("presse.xml")})
    bericht = CRAWLER[quelle_presse.typ](db, stadt, quelle_presse, _abrufer(fake, uhr), LaufOptionen())
    assert bericht.status == "ok"
    assert db.zaehle_quelle(stadt.quell_id(quelle_presse))["hinweise"] == 2
    assert {"ris-rss", "presse-rss"} <= set(CRAWLER)


def test_ladebericht_zeigt_rss_quelle_als_aktuell(db, stadt, quelle_ris, uhr):
    from behoerdenmaster.ladebericht import ladebericht

    CRAWLER[quelle_ris.typ](db, stadt, quelle_ris, _abrufer(FakeRss({SITZUNGS_URL: _lese("sitzungen.xml")}), uhr),
                            LaufOptionen())
    bericht = ladebericht(db, stadt, intervall_sekunden=21600, sync_aktiv=False, laeufe_pro_quelle=3)
    quelle = next(q for q in bericht["quellen"] if q["typ"] == "ris-rss")
    assert quelle["ladezustand"] == "aktuell"
    assert quelle["zaehler"]["hinweise"] == 3
    schritte = {s["schritt"]: s for s in quelle["abrufweg"]}
    assert "Hinweise" in schritte and "nicht Teil der Suche" in schritte["Hinweise"]["wert"]
