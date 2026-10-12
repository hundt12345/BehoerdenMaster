"""Listenquellen: öffentliche Bekanntmachungen und Bürgerbeteiligung „Meinung für Köln“.

Die Fixtures sind echte, gekürzte Auszüge der öffentlichen Listen vom 12.10.2026
(siehe `fixtures/bekanntmachungen/koeln/README.md` und `fixtures/beteiligung/koeln/README.md`).
Das Netz gibt es nicht: Alle Abrufe laufen über `httpx.MockTransport`.
"""

from __future__ import annotations

import httpx
import pytest

from behoerdenmaster.abruf import Abrufer
from behoerdenmaster.auswertung import heute
from behoerdenmaster.bekanntmachungen import crawle_bekanntmachungen, jahrgaenge, listen_url
from behoerdenmaster.beteiligung import crawle_beteiligung
from behoerdenmaster.config import PROJEKTWURZEL, lade_stadt
from behoerdenmaster.crawler import CRAWLER, LaufOptionen
from behoerdenmaster.geo import Gazetteer
from behoerdenmaster.ladebericht import TYP_TEXT, ladebericht
from behoerdenmaster.meldungen import (
    beteiligungs_status, datum_anzeige, ortsbezuege, parse_bekanntmachungen, parse_beteiligungen,
)
from behoerdenmaster.speicher import Speicher
from behoerdenmaster.suche import Suchauftrag, suche, vorgang_detail

LISTE_URL = "https://www.stadt-koeln.de/politik-und-verwaltung/bekanntmachungen/oeffentliche-bekanntmachungen-2026"
PROJEKTE_URL = "https://meinungfuer.koeln/mitmachen/alle-projekte"


def _lese(pfad: str) -> str:
    return (PROJEKTWURZEL / "fixtures" / pfad).read_text(encoding="utf-8")


@pytest.fixture
def liste() -> str:
    return _lese("bekanntmachungen/koeln/liste.html")


@pytest.fixture
def projekte() -> str:
    return _lese("beteiligung/koeln/projekte.html")


@pytest.fixture
def quelle_bekanntmachungen():
    return next(q for q in lade_stadt("koeln").quellen if q.typ == "bekanntmachungen")


@pytest.fixture
def quelle_beteiligung():
    return next(q for q in lade_stadt("koeln").quellen if q.typ == "beteiligung")


class FakeListe:
    """Ein Server mit robots.txt und den Listenseiten der Quelle."""

    def __init__(self, seiten: dict[str, str], robots: str = "User-agent: *\n") -> None:
        self.seiten = seiten
        self.robots = robots
        self.aufrufe: list[str] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.aufrufe.append(str(req.url))
        assert "BehoerdenMaster/" in req.headers["User-Agent"]
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text=self.robots)
        text = self.seiten.get(str(req.url))
        if text is None:
            return httpx.Response(404, text="nicht vorhanden")
        return httpx.Response(200, text=text, headers={"Content-Type": "text/html; charset=utf-8"})


def _abrufer(fake, uhr, **kw) -> Abrufer:
    return Abrufer(kontakt="test@example.test", client=httpx.Client(transport=httpx.MockTransport(fake)),
                   sleep=uhr.schlaf, clock=uhr, wiederholungen=0, **kw)


# --- Bekanntmachungen: Parser ---------------------------------------------------------

def test_bekanntmachungen_aus_der_echten_liste(liste):
    eintraege, weiter, treffer = parse_bekanntmachungen(liste, LISTE_URL)

    assert [e.referenz for e in eintraege] == ["0191-01", "0190-01", "0187-01"]
    assert [e.datum for e in eintraege] == ["2026-10-07", "2026-10-07", "2026-10-01"]
    assert weiter == f"{LISTE_URL}?page=2"
    assert treffer == "Treffer 1–15 von 191"

    erster = eintraege[0]
    assert erster.vorgang_id == "bekanntmachung:2026.10.07_0191-01_feldgaertenstr_foeb"
    assert erster.web.startswith("https://www.stadt-koeln.de/mediaasset/content/bekanntmachungen/2026/")
    assert erster.art == "Bekanntmachung"
    # Die Typbezeichnung stammt aus der Quelle; sie wird nicht um einen Paragrafen ergänzt.
    assert erster.schritt == "Frühzeitige Beteiligung der Öffentlichkeit"
    assert "§" not in erster.status_text
    assert erster.kategorie == "Öffentliche Bekanntmachung von Bauleitplänen"
    assert erster.zusatz == "Hinweisveröffentlichung 07.10.2026"
    assert erster.offen is False

    # Ein Eintrag ohne Bauleitbezug behält seinen Titel und bekommt keinen Verfahrensschritt.
    assert eintraege[2].schritt == "" and eintraege[2].kategorie == ""
    assert "Stadtbahngesellschaft" in eintraege[2].titel

    # Der Paragraf steht nur dort, wo die Quelle ihn selbst nennt.
    assert "§ 10" in eintraege[1].titel


def test_bekanntmachungen_verwirft_fremde_und_unvollstaendige_eintraege():
    html = """
    <ul>
      <li><a href="https://boese.example/mediaasset/content/bekanntmachungen/2026/x.pdf">
        <time datetime="2026-01-02T00:00:00+00:00">2. Januar 2026</time>
        <figure>Fremder Server <figcaption>PDF</figcaption></figure></a></li>
      <li><a href="/mediaasset/content/bekanntmachungen/2026/leer.pdf"></a></li>
      <li><a href="/politik-und-verwaltung/bekanntmachungen/oeffentliche-bekanntmachungen-2026">
        <figure>Keine Datei, sondern eine Seite</figure></a></li>
      <li><a href="http://www.stadt-koeln.de/mediaasset/content/bekanntmachungen/2026/unsicher.pdf">
        <time datetime="2026-01-03T00:00:00+00:00">3. Januar 2026</time>
        <figure>Ohne HTTPS <figcaption>PDF</figcaption></figure></a></li>
    </ul>
    """
    eintraege, weiter, treffer = parse_bekanntmachungen(html, LISTE_URL)
    assert eintraege == [] and weiter == "" and treffer == ""


def test_bekanntmachung_ohne_datum_und_mit_schreibfehler_der_quelle():
    """Die Quelle schreibt auch „Hinweisveröffetnlichung“ – das darf nicht verloren gehen."""
    html = """
    <ul><li><a href="/mediaasset/content/bekanntmachungen/2026/0180-02_ja2025_awb_gmbh.pdf">
      <figure>Jahresabschluss 2025 für die AWB Abfallwirtschaftsbetriebe Köln GmbH
        <figcaption class="downloadbeschreibung"<abbr>PDF</abbr>, 111 <abbr>kb</abbr></figcaption></figure>
      <span>Hinweisveröffetnlichung 30.09.2026</span></a></li></ul>
    """
    eintraege, _, _ = parse_bekanntmachungen(html, LISTE_URL)
    assert len(eintraege) == 1
    assert eintraege[0].datum == ""          # keine <time>-Angabe: kein Datum erfinden
    assert eintraege[0].referenz == "0180-02"
    assert eintraege[0].zusatz == "Hinweisveröffentlichung 30.09.2026"


def test_weiterlink_der_quelle_hat_vorrang(liste):
    """Der Weiter-Link (`rel="next"`) bestimmt die Folgeseite – nicht eine eigene Rechnung."""
    _, weiter, _ = parse_bekanntmachungen(liste, LISTE_URL)
    assert weiter == f"{LISTE_URL}?page=2"


def test_seitennummerierung_ist_einbasiert():
    """Ohne Weiter-Link gilt: Ohne Parameter ist man auf Seite 1, die nächste ist Seite 2.

    Wird das verwechselt (``?page=1`` als zweite Seite gelesen), liest jeder Lauf die erste
    Seite erneut und zählt dieselben Bekanntmachungen doppelt.
    """
    pager = ("<ul><li>" + "</li><li>".join(
        f'<a href="?page={n}" data-start="{(n - 1) * 15}">{n}</a>' for n in range(1, 6)) + "</li></ul>")

    _, weiter, _ = parse_bekanntmachungen(pager, LISTE_URL)
    assert weiter == f"{LISTE_URL}?page=2"

    _, weiter, _ = parse_bekanntmachungen(pager, f"{LISTE_URL}?page=2")
    assert weiter == f"{LISTE_URL}?page=3"

    # Auf der letzten Seite gibt es keinen höheren Seitenlink: die Liste ist zu Ende.
    letzte = '<ul><li><a href="?page=4">4</a></li><li><a href="?page=5">5</a></li></ul>'
    assert parse_bekanntmachungen(letzte, f"{LISTE_URL}?page=5")[1] == ""


def test_weiterlink_auf_fremden_host_wird_ignoriert():
    html = '<a href="https://boese.example/?page=99" rel="next">weiter</a>' \
           '<a href="?page=4" title="Eine Seite vor">4</a>'
    _, weiter, _ = parse_bekanntmachungen(html, LISTE_URL)
    assert weiter == f"{LISTE_URL}?page=4"


def test_jahrgaenge_werden_auf_die_neuesten_begrenzt(quelle_bekanntmachungen):
    assert jahrgaenge(quelle_bekanntmachungen, "2026-10-12") == [2026, 2025]
    assert jahrgaenge(quelle_bekanntmachungen, "2030-01-05") == [2030, 2029, 2028]
    assert listen_url(quelle_bekanntmachungen, 2025).endswith("oeffentliche-bekanntmachungen-2025")


# --- Bekanntmachungen: Crawl ----------------------------------------------------------

JAHRES_URL = "https://www.stadt-koeln.de/politik-und-verwaltung/bekanntmachungen/oeffentliche-bekanntmachungen-{jahr}"


def _seiten_fuer_jahre(liste: str, jahre=(2026, 2025)) -> dict[str, str]:
    """Erste Seite je Jahrgang; die (hier leere) Folgeseite beendet die Abrufreihe.

    Der Weiter-Link der Fixture zeigt auf 2026; für andere Jahrgänge wird er wie in der
    Quelle jeweils auf den eigenen Jahrgang umgeschrieben.
    """
    seiten = {}
    for jahr in jahre:
        seite = liste.replace("oeffentliche-bekanntmachungen-2026?page=2",
                              f"oeffentliche-bekanntmachungen-{jahr}?page=2")
        seiten[JAHRES_URL.format(jahr=jahr)] = seite
        seiten[JAHRES_URL.format(jahr=jahr) + "?page=2"] = "<html></html>"
    return seiten


def _nur_erster_eintrag(liste: str) -> str:
    """Dieselbe Liste, aber mit nur einer Bekanntmachung – für den Vergleich zweier Läufe."""
    teile = liste.split("<li>")
    return teile[0] + "<li>" + teile[1] + "</ul></div></div>\n"


def test_crawl_speichert_bekanntmachung_mit_dokument_und_ortsbezug(tmp_path, uhr, liste, stadt):
    fake = FakeListe(_seiten_fuer_jahre(liste))
    abrufer = _abrufer(fake, uhr)
    quelle = next(q for q in stadt.quellen if q.typ == "bekanntmachungen")
    with Speicher(tmp_path / "m.db") as sp:
        bericht = crawle_bekanntmachungen(sp, stadt, quelle, abrufer, LaufOptionen())
        assert bericht.status == "ok"
        assert bericht.zaehler["bekanntmachungen:neu"] == 3
        assert bericht.zaehler["listen:seiten"] == 4  # zwei Jahrgänge, je Liste und leere Folgeseite

        zeile = sp.db.execute(
            "SELECT v.*, m.status_kurz, m.verfahrensschritt, m.gesehen FROM vorgang v "
            "JOIN meldung m ON m.vorgang_id=v.id WHERE v.id LIKE '%0191-01%'"
        ).fetchone()
        assert zeile["art"] == "Bekanntmachung" and zeile["datum"] == "2026-10-07"
        assert zeile["verfahrensschritt"] == "Frühzeitige Beteiligung der Öffentlichkeit"
        assert zeile["status_kurz"] == "amtlich bekannt gemacht" and zeile["gesehen"]
        assert zeile["quelle_id"] == "koeln:bekanntmachungen"

        # Ortsbezug aus dem Arbeitstitel „Feldgärtenstraße in Köln-Niehl“.
        orte = {(r["typ"], r["schluessel"]) for r in sp.db.execute(
            "SELECT typ, schluessel FROM ortsbezug WHERE vorgang_id=?", (zeile["id"],)).fetchall()}
        assert ("stadtteil", "504") in orte and ("bezirk", "5") in orte

        # Das Original ist verlinkt – die PDF-Datei wird nicht geladen.
        dok = sp.db.execute("SELECT * FROM dokument WHERE vorgang_id=?", (zeile["id"],)).fetchone()
        assert dok["rolle"] == "bekanntmachung" and dok["mime"] == ""
        assert dok["download_url"] == "" and dok["url"].endswith(".pdf")
        assert all(".pdf" not in a for a in fake.aufrufe)

        assert sp.zaehle_quelle("koeln:bekanntmachungen")["bekanntmachungen"] == 3
        # Die erste Seite wird nicht erneut gelesen (1-basierte Seitennummerierung der Quelle).
        assert not any("page=1" in a for a in fake.aufrufe)
        assert [a for a in fake.aufrufe if "page=" in a] == [
            f"{JAHRES_URL.format(jahr=2026)}?page=2", f"{JAHRES_URL.format(jahr=2025)}?page=2"]


def test_crawl_setzt_nach_budget_am_cursor_fort(tmp_path, uhr, liste, stadt):
    """Budget erreicht: Der Lauf endet „teilweise“ und setzt am gespeicherten Cursor fort."""
    seiten = _seiten_fuer_jahre(liste)
    seiten[f"{LISTE_URL}?page=2"] = liste  # dieselben Einträge; zählt nur die Anfragen
    fake = FakeListe(seiten)
    quelle = next(q for q in stadt.quellen if q.typ == "bekanntmachungen")
    with Speicher(tmp_path / "m.db") as sp:
        erster = crawle_bekanntmachungen(sp, stadt, quelle, _abrufer(fake, uhr, max_anfragen=2),
                                        LaufOptionen(max_anfragen=2))
        assert erster.status == "teilweise" and "Anfragebudget" in erster.fehlertext
        stand = sp.stand_lesen("koeln:bekanntmachungen", "liste:2026")
        assert stand["naechste_url"] == f"{LISTE_URL}?page=2"

        abrufer = _abrufer(fake, uhr)
        zweiter = crawle_bekanntmachungen(sp, stadt, quelle, abrufer, LaufOptionen())
        assert zweiter.status == "ok"
        assert f"{LISTE_URL}?page=2" in fake.aufrufe
        stand = sp.stand_lesen("koeln:bekanntmachungen", "liste:2026")
        assert stand["naechste_url"] == "" and stand["reihe_beginn"] == ""


def test_fehlende_bekanntmachung_wird_nicht_geloescht(tmp_path, uhr, liste, stadt):
    """Eine später fehlende Bekanntmachung bleibt gültig – sie wird nicht gelöscht."""
    quelle = next(q for q in stadt.quellen if q.typ == "bekanntmachungen")
    fake = FakeListe(_seiten_fuer_jahre(liste))
    with Speicher(tmp_path / "m.db") as sp:
        crawle_bekanntmachungen(sp, stadt, quelle, _abrufer(fake, uhr), LaufOptionen())
        vorher = sp.db.execute("SELECT COUNT(*) FROM vorgang").fetchone()[0]
        assert vorher == 3
        fake.seiten = _seiten_fuer_jahre(_nur_erster_eintrag(liste))
        bericht = crawle_bekanntmachungen(sp, stadt, quelle, _abrufer(fake, uhr), LaufOptionen())
        assert bericht.status == "ok"
        assert sp.db.execute("SELECT COUNT(*) FROM vorgang").fetchone()[0] == 3
        assert sp.db.execute("SELECT COUNT(*) FROM meldung").fetchone()[0] == 3
        einzeln = sp.db.execute(
            "SELECT COUNT(*) FROM meldung WHERE vorgang_id LIKE '%0190-01%'").fetchone()[0]
        assert einzeln == 1


# --- Bürgerbeteiligung: Parser --------------------------------------------------------

def test_beteiligungen_aus_der_echten_liste(projekte):
    eintraege, verworfen = parse_beteiligungen(projekte, PROJEKTE_URL)
    assert verworfen == 0
    assert [e.vorgang_id for e in eintraege] == [
        "beteiligung:gruenzug-flittard-stammheim",
        "beteiligung:node-33126",
        "beteiligung:qualifizierungsverfahren-neubruecker-ring",
    ]
    assert [e.status_kurz for e in eintraege] == [
        "Beteiligung geschlossen", "Beteiligung geschlossen", "ohne Statusangabe"]
    assert eintraege[0].zusatz == "101 Beiträge laut Plattform"
    assert eintraege[0].datum == ""  # die Liste nennt keinen Zeitraum
    assert eintraege[0].web == "https://meinungfuer.koeln/gruenzug-flittard-stammheim"
    assert eintraege[0].kurztext.startswith("In den Stadtteilen Flittard und Stammheim")
    assert len(eintraege[0].kurztext) <= 600
    # Keine Beiträge, Namen oder Bilder Dritter im Ergebnis.
    assert "Foto eines Weizenfeldes" not in eintraege[0].kurztext


def test_beteiligung_verwirft_gesperrte_pfade_und_karten_ohne_link(projekte):
    html = projekte.replace("/gruenzug-flittard-stammheim", "/search/node").replace(
        '<h3 class="title">', "<h3>")
    eintraege, verworfen = parse_beteiligungen(html, PROJEKTE_URL)
    assert eintraege == [] and verworfen == 3


@pytest.mark.parametrize("zeile,erwartet,offen", [
    ("Online-Beteiligung seit 10 Tagen geschlossen", "Beteiligung geschlossen", False),
    ("Online-Beteiligung noch 5 Tage", "Beteiligung läuft", True),
    ("Online-Beteiligung läuft", "Beteiligung läuft", True),
    ("Online-Beteiligung startet am 1. November", "Beteiligung angekündigt", True),
    ("", "ohne Statusangabe", False),
    ("Etwas ganz anderes", "Etwas ganz anderes", False),
])
def test_statuszeile_der_plattform_wird_nicht_geraten(zeile, erwartet, offen):
    kurz, text, ist_offen = beteiligungs_status(zeile)
    assert kurz == erwartet and ist_offen is offen
    assert zeile in text or not zeile


def test_crawl_beteiligung_speichert_und_haelt_unbekanntes_format_fest(tmp_path, uhr, projekte, stadt):
    quelle = next(q for q in stadt.quellen if q.typ == "beteiligung")
    fake = FakeListe({PROJEKTE_URL: projekte})
    with Speicher(tmp_path / "m.db") as sp:
        bericht = crawle_beteiligung(sp, stadt, quelle, _abrufer(fake, uhr), LaufOptionen())
        assert bericht.status == "ok" and bericht.zaehler["beteiligungen:neu"] == 3
        assert sp.zaehle_quelle("koeln:beteiligung")["beteiligungen"] == 3
        # Dokumente werden für Beteiligungen nicht angelegt: nur der Link auf die Projektseite.
        assert sp.db.execute("SELECT COUNT(*) FROM dokument").fetchone()[0] == 0

        # Ein unbekanntes Listenformat gilt als Fehler und verändert den Bestand nicht.
        fake.seiten[PROJEKTE_URL] = "<html><body>Wartung</body></html>"
        zweiter = crawle_beteiligung(sp, stadt, quelle, _abrufer(fake, uhr), LaufOptionen())
        assert zweiter.status == "fehler" and "Keine Projektkarte" in zweiter.fehlertext
        assert sp.zaehle_quelle("koeln:beteiligung")["beteiligungen"] == 3


# --- Ortsbezug, Suche und Ladeübersicht ----------------------------------------------

def test_ortsbezug_loest_koeln_stadtteil_schreibweise(stadt):
    gaz = Gazetteer(stadt)
    orte = ortsbezuege(gaz, "Arbeitstitel: „Feldgärtenstraße in Köln-Niehl“")
    assert ("stadtteil", "504", "betreff") in orte and ("bezirk", "5", "betreff") in orte


def test_suche_findet_bekanntmachung_und_beteiligung(tmp_path, uhr, liste, projekte, stadt):
    fake = FakeListe(_seiten_fuer_jahre(liste) | {PROJEKTE_URL: projekte})
    q_bekannt = next(q for q in stadt.quellen if q.typ == "bekanntmachungen")
    q_beteil = next(q for q in stadt.quellen if q.typ == "beteiligung")
    with Speicher(tmp_path / "m.db") as sp:
        crawle_bekanntmachungen(sp, stadt, q_bekannt, _abrufer(fake, uhr), LaufOptionen())
        crawle_beteiligung(sp, stadt, q_beteil, _abrufer(fake, uhr), LaufOptionen())

        auftrag = Suchauftrag(stadt=stadt, freitext=["Feldgärtenstraße"], limit=10)
        ergebnis = suche(sp, auftrag, heute())
        assert ergebnis["gesamt"] == 1
        treffer = ergebnis["treffer"][0]
        assert treffer["quelle"]["typ"] == "bekanntmachungen"
        assert treffer["meldung"]["verfahrensschritt"] == "Frühzeitige Beteiligung der Öffentlichkeit"
        assert treffer["status"]["kurz"] == "amtlich bekannt gemacht"
        assert treffer["links"]["original"].endswith(".pdf")
        assert treffer["links"]["original_text"] == "Bekanntmachung im Original öffnen"
        assert any("PDF" in h for h in treffer["hinweise"])
        assert treffer["dokumente"][0]["rolle_text"] == "Amtliche Bekanntmachung"
        assert [o["name"] for o in treffer["orte"]] == ["Niehl"]
        assert treffer["verlauf"][0]["datum"] == "2026-10-07"

        # Ein Beteiligungsangebot ist über Titel und Kurztext auffindbar.
        auftrag = Suchauftrag(stadt=stadt, freitext=["GRÜNZUG"], limit=10)
        ergebnis = suche(sp, auftrag, heute())
        assert ergebnis["gesamt"] == 1
        b = ergebnis["treffer"][0]
        assert b["quelle"]["typ"] == "beteiligung"
        assert b["meldung"]["kurztext"].startswith("In den Stadtteilen Flittard")
        assert b["status"]["offen"] is False
        assert b["links"]["original_text"] == "Projektseite der Stadt öffnen"
        assert "zuletzt abgerufen" in " ".join(b["hinweise"])

        detail = vorgang_detail(sp, stadt, b["id"], heute())
        assert detail["meldung"]["art_quelle"] == "Bürgerbeteiligung Meinung für Köln"


def test_ladebericht_kennt_die_neuen_quellentypen(tmp_path, uhr, liste, projekte, stadt):
    assert TYP_TEXT["bekanntmachungen"].startswith("Öffentliche Bekanntmachungen")
    assert TYP_TEXT["beteiligung"].startswith("Bürgerbeteiligung")
    fake = FakeListe(_seiten_fuer_jahre(liste) | {PROJEKTE_URL: projekte})
    with Speicher(tmp_path / "m.db") as sp:
        crawle_bekanntmachungen(sp, stadt, next(q for q in stadt.quellen if q.typ == "bekanntmachungen"),
                                _abrufer(fake, uhr), LaufOptionen())
        crawle_beteiligung(sp, stadt, next(q for q in stadt.quellen if q.typ == "beteiligung"),
                           _abrufer(fake, uhr), LaufOptionen())
        bericht = ladebericht(sp, stadt, intervall_sekunden=21600, sync_aktiv=True)
        bekannt = next(q for q in bericht["quellen"] if q["schluessel"] == "bekanntmachungen")
        beteil = next(q for q in bericht["quellen"] if q["schluessel"] == "beteiligung")
        assert bekannt["ladezustand"] == "aktuell" and bekannt["zaehler"]["bekanntmachungen"] == 3
        assert beteil["zaehler"]["beteiligungen"] == 3 and beteil["zaehler"]["dokumente"] == 0
        assert [s["schritt"] for s in bekannt["abrufweg"]][:2] == ["Zugriff", "Listen"]
        assert any("nicht übernommen" in s["wert"] for s in beteil["abrufweg"])


def test_registry_kennt_beide_typen():
    """Der Sync-Dienst findet für beide Typen eine Crawl-Funktion."""
    for typ in ("bekanntmachungen", "beteiligung"):
        assert typ in CRAWLER and callable(CRAWLER[typ])


def test_datum_anzeige():
    assert datum_anzeige("2026-10-07") == "07.10.2026"
    assert datum_anzeige("") == "" and datum_anzeige("2026-10") == ""
