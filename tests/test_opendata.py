"""Offene Daten Köln (DKAN 2): Katalog, Lizenzen und Tabellenzeilen.

Das Netz gibt es nicht: Ein Fake-Portal beantwortet Katalogsuche, Datensatz-Metadaten
und die Datastore-Abfragen so, wie es die DKAN-2-Routen vorsehen
(`/api/1/search`, `/api/1/metastore/schemas/dataset/items/<uuid>`,
`/api/1/datastore/query/<uuid>/<index>?limit=&offset=`).
"""

from __future__ import annotations

import dataclasses

import httpx
import pytest

from behoerdenmaster.abruf import Abrufer
from behoerdenmaster.auswertung import heute
from behoerdenmaster.crawler import LaufBericht, LaufOptionen
from behoerdenmaster.ladebericht import ladebericht
from behoerdenmaster.meldungen import MAX_KURZTEXT
from behoerdenmaster.opendata import (_zeilen, crawle_opendata, daten_verteilungen, datensatz_lizenz,
                                      datensaetze_finden, katalog_treffer, lizenz_art,
                                      lizenz_text, passende_datensaetze, unscharfe_datensaetze,
                                      zeile_zu_eintrag, zeilen_kennung, SEITE)
from behoerdenmaster.speicher import Speicher
from behoerdenmaster.suche import Suchauftrag, suche

PORTAL = "https://www.offenedaten-koeln.de"
BAUSTELLEN_UUID = "6a1c3d52-1f3a-4c3b-9b6e-8f0d2a7c4e11"
AUFSTELLUNG_UUID = "2f9b8e10-57d4-4a61-9c02-1a2b3c4d5e6f"
CSV_PFAD = "/sites/default/files/baustellen_koeln.csv"


def baustellen_datensatz(uuid: str = BAUSTELLEN_UUID, titel: str = "Baustellen Köln",
                         lizenz: str = "http://dcat-ap.de/def/licenses/dl-zero-de/2.0",
                         herausgeber: str = "Stadt Köln",
                         geaendert: str = "2026-10-05",
                         verteilungen: list[dict] | None = None) -> dict:
    return {
        "identifier": uuid, "title": titel, "description": "Genehmigte Baumaßnahmen",
        "modified": geaendert, "license": lizenz, "publisher": {"name": herausgeber},
        "distribution": verteilungen if verteilungen is not None else [{
            "identifier": f"{uuid}-csv", "title": "Baustellen als Tabelle",
            "data": {"downloadURL": PORTAL + CSV_PFAD, "format": "csv", "mediaType": "text/csv"},
        }],
    }


BAUSTELLEN_ZEILEN = [
    {"id": "V-2026-014", "massnahme": "Fahrbahnsanierung Feldgärtenstraße",
     "strasse": "Feldgärtenstr.", "beginn": "2026-09-14", "ende": "2026-12-18",
     "amt": "Amt für Verkehrsmanagement", "art": "Baustelle"},
    {"id": "V-2026-015", "massnahme": "Neubau einer Bushaltestelle",
     "strasse": "Venloer Straße", "beginn": "2026-10-01", "ende": "2026-10-30",
     "amt": "Amt für Verkehrsmanagement", "art": "Baustelle"},
]


class FakePortal:
    """Ein Portal mit robots.txt, Katalog und Tabellenspeicher."""

    def __init__(self, datensaetze: list[dict], tabellen: dict[str, list[dict]] | None = None,
                 schema: dict | None = None, robots: str = "User-agent: *\n") -> None:
        self.datensaetze = datensaetze
        self.tabellen = tabellen or {}
        self.schema = schema or {}
        self.robots = robots
        self.aufrufe: list[str] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.aufrufe.append(str(req.url))
        assert "BehoerdenMaster/" in req.headers["User-Agent"]
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text=self.robots)
        if req.url.path == "/api/1/search":
            return httpx.Response(200, json={"total": len(self.datensaetze), "results": self.datensaetze})
        teile = [t for t in req.url.path.split("/") if t]
        if teile[:4] == ["api", "1", "metastore", "schemas"] and len(teile) == 7:
            for datensatz in self.datensaetze:
                if datensatz["identifier"] == teile[6]:
                    return httpx.Response(200, json=datensatz)
            return httpx.Response(404, text="nicht vorhanden")
        if teile[:4] == ["api", "1", "datastore", "query"] and len(teile) == 6:
            schluessel = f"{teile[4]}/{teile[5]}"
            zeilen = self.tabellen.get(schluessel)
            if zeilen is None:
                return httpx.Response(404, json={"message": "not in datastore"})
            limit = int(req.url.params.get("limit", "100"))
            offset = int(req.url.params.get("offset", "0"))
            return httpx.Response(200, json={
                "count": len(zeilen),
                "results": zeilen[offset:offset + limit],
                "schema": self.schema,
            })
        return httpx.Response(404, text="nicht vorhanden")


def _abrufer(fake, uhr, **kw) -> Abrufer:
    return Abrufer(kontakt="test@example.test", client=httpx.Client(transport=httpx.MockTransport(fake)),
                   sleep=uhr.schlaf, clock=uhr, wiederholungen=0, **kw)


@pytest.fixture
def quelle(stadt):
    """Die konfigurierte Quelle, aber mit Stichworten für den Fake-Katalog."""
    basis = next(q for q in stadt.quellen if q.typ == "opendata")
    return dataclasses.replace(basis, datensaetze=("Baustellen Köln",))


def _lauf(sp, stadt, quelle, fake, uhr, **kw):
    return crawle_opendata(sp, stadt, quelle, _abrufer(fake, uhr, **kw), LaufOptionen())


# --- Lizenz ---------------------------------------------------------------------

def test_lizenz_darf_auch_an_der_verteilung_stehen():
    """Manche Portale nennen die Lizenz am Datensatz, andere je Verteilung."""
    nur_verteilung = baustellen_datensatz(lizenz="")
    nur_verteilung["distribution"][0]["license"] = "http://dcat-ap.de/def/licenses/dl-by-de/2.0"
    assert lizenz_text(nur_verteilung)[0] == "by"
    assert datensatz_lizenz(baustellen_datensatz()) == "http://dcat-ap.de/def/licenses/dl-zero-de/2.0"


@pytest.mark.parametrize("lizenz, erwartet", [
    ("http://dcat-ap.de/def/licenses/dl-zero-de/2.0", "zero"),
    ("https://www.govdata.de/dl-de/by-2-0", "by"),
    ("dl-by-de/2.0", "by"),
    ("CC0 1.0", "zero"),
    ("", ""),
    ("http://example.org/eigene-lizenz", ""),
])
def test_nur_bekannte_offene_lizenzen_werden_erkannt(lizenz, erwartet):
    assert lizenz_art(lizenz) == (erwartet or None)


def test_namensnennung_nennt_herausgeber_oder_stadt():
    by = lizenz_text(baustellen_datensatz(lizenz="dl-by-de/2.0",
                                          herausgeber="Stadt Köln, Amt für Verkehrsmanagement"))
    assert by[0] == "by" and "Amt für Verkehrsmanagement" in by[1] and "Namensnennung" in by[1]
    assert "Stadt Köln" in lizenz_text(baustellen_datensatz(lizenz="dl-by-de/2.0", herausgeber=""))[1]
    assert lizenz_text(baustellen_datensatz(lizenz="http://example.org/x")) == ("", "")


def test_datensatz_ohne_offene_lizenz_wird_uebersprungen(tmp_path, uhr, stadt, quelle):
    fake = FakePortal([baustellen_datensatz(lizenz="http://example.org/nur-mit-vertrag")],
                      {f"{BAUSTELLEN_UUID}/0": BAUSTELLEN_ZEILEN})
    with Speicher(tmp_path / "m.db") as sp:
        bericht = _lauf(sp, stadt, quelle, fake, uhr)
        assert bericht.status == "ok"
        assert bericht.zaehler["opendata:uebersprungen_lizenz"] == 1
        assert sp.db.execute("SELECT COUNT(*) FROM vorgang").fetchone()[0] == 0
        assert not any("datastore" in a for a in fake.aufrufe), "gesperrte Lizenz heißt: keine Datenabfrage"


def test_by_datensatz_wird_mit_namensnennung_uebernommen(tmp_path, uhr, stadt, quelle):
    fake = FakePortal([baustellen_datensatz(lizenz="http://dcat-ap.de/def/licenses/dl-by-de/2.0",
                                            herausgeber="Stadt Köln, Amt für Verkehrsmanagement")],
                      {f"{BAUSTELLEN_UUID}/0": BAUSTELLEN_ZEILEN})
    with Speicher(tmp_path / "m.db") as sp:
        bericht = _lauf(sp, stadt, quelle, fake, uhr)
        assert bericht.zaehler["opendata:namensnennung"] == 1
        zeile = sp.db.execute("SELECT m.status_text, m.zusatz FROM meldung m").fetchone()
        assert "Amt für Verkehrsmanagement" in zeile["status_text"]
        assert "Namensnennung" in zeile["status_text"] and "Amt für Verkehrsmanagement" in zeile["zusatz"]


# --- Katalog und Stichworte -----------------------------------------------------

def test_passende_datensaetze_vergleicht_den_titel():
    treffer = [baustellen_datensatz(), baustellen_datensatz(titel="Baumkataster Köln")]
    assert [d["title"] for d in passende_datensaetze(treffer, "Baustellen Köln")] == ["Baustellen Köln"]
    # Ein Stichwort mit Katalogtreffern, aber ohne Titeltreffer bleibt leer …
    assert passende_datensaetze(treffer, "Baustellen im Stadtgebiet") == []
    # … und wird nur als unscharfer Treffer angeboten, wenn ein Wort übereinstimmt.
    assert unscharfe_datensaetze(treffer, "Baustellen im Stadtgebiet") == [treffer[0]]
    assert unscharfe_datensaetze(treffer, "Haushaltsplan") == []


def test_unscharfes_stichwort_wird_gezaehlt(tmp_path, uhr, stadt, quelle):
    """Ein anders formulierter Titel wird genommen – aber sichtbar als „unscharf“."""
    unscharf = dataclasses.replace(quelle, datensaetze=("Baustellen Köln",))
    fake = FakePortal([baustellen_datensatz(titel="Baustellen im Stadtgebiet Köln")],
                      {f"{BAUSTELLEN_UUID}/0": BAUSTELLEN_ZEILEN})
    with Speicher(tmp_path / "m.db") as sp:
        bericht = _lauf(sp, stadt, unscharf, fake, uhr)
        assert bericht.zaehler["opendata:unscharf"] == 1
        assert bericht.zaehler["opendata:datensaetze"] == 1


def test_stichwort_ohne_katalogtreffer_wird_gemeldet(tmp_path, uhr, stadt, quelle):
    fake = FakePortal([])
    with Speicher(tmp_path / "m.db") as sp:
        bericht = _lauf(sp, stadt, quelle, fake, uhr)
        assert bericht.status == "ok" and bericht.zaehler["opendata:nicht_gefunden"] == 1
        assert sp.db.execute("SELECT COUNT(*) FROM vorgang").fetchone()[0] == 0


def test_direkte_kennung_umgeht_die_katalogsuche(tmp_path, uhr, stadt, quelle):
    direkt = dataclasses.replace(quelle, datensaetze=(BAUSTELLEN_UUID,))
    fake = FakePortal([baustellen_datensatz()], {f"{BAUSTELLEN_UUID}/0": BAUSTELLEN_ZEILEN})
    with Speicher(tmp_path / "m.db") as sp:
        bericht = _lauf(sp, stadt, direkt, fake, uhr)
        assert bericht.zaehler["opendata:datensaetze"] == 1
        assert not any("/api/1/search" in a for a in fake.aufrufe)
        assert any(f"/api/1/metastore/schemas/dataset/items/{BAUSTELLEN_UUID}" in a for a in fake.aufrufe)


def test_unbekannte_kennung_ist_kein_netzfehler(tmp_path, uhr, stadt, quelle):
    direkt = dataclasses.replace(quelle, datensaetze=("00000000-0000-0000-0000-000000000000",))
    with Speicher(tmp_path / "m.db") as sp:
        bericht = _lauf(sp, stadt, direkt, FakePortal([]), uhr)
        assert bericht.status == "ok" and bericht.zaehler["opendata:nicht_gefunden"] == 1


def test_datensatz_ohne_tabellenverteilung_wird_gezaehlt(tmp_path, uhr, stadt, quelle):
    pdf = [{"identifier": "x-pdf", "title": "Plan als PDF",
            "data": {"downloadURL": PORTAL + "/sites/default/files/plan.pdf", "format": "pdf",
                     "mediaType": "application/pdf"}}]
    fake = FakePortal([baustellen_datensatz(verteilungen=pdf)], {})
    with Speicher(tmp_path / "m.db") as sp:
        bericht = _lauf(sp, stadt, quelle, fake, uhr)
        assert bericht.zaehler["opendata:ohne_tabelle"] == 1
        assert daten_verteilungen(baustellen_datensatz(verteilungen=pdf)) == []
        assert not any("datastore" in a for a in fake.aufrufe), "PDF wird nicht geladen"


def test_verteilung_im_tabellenspeicher_wird_erkannt():
    ohne_format = [{"identifier": "x", "data": {"accessURL": PORTAL + "/x.csv"}}]
    assert daten_verteilungen(baustellen_datensatz(verteilungen=ohne_format)) == [(0, ohne_format[0])]


def test_katalogsuche_fragt_nach_dem_stichwort(stadt, quelle, uhr):
    fake = FakePortal([baustellen_datensatz()])
    treffer = katalog_treffer(_abrufer(fake, uhr), quelle, "Baustellen Köln")
    assert [d["title"] for d in treffer] == ["Baustellen Köln"]
    assert "/api/1/search?fulltext=Baustellen+K%C3%B6ln&page-size=25" in fake.aufrufe[1]


# --- Zeilenabbildung ------------------------------------------------------------

def test_zeile_ohne_kennung_bleibt_ueber_ihre_felder_stabil():
    zeile = {"massnahme": "Fahrbahnsanierung", "strasse": "Feldgärtenstr.", "beginn": "2026-09-14"}
    umgedreht = dict(reversed(list(zeile.items())))
    assert zeilen_kennung(zeile, None) == zeilen_kennung(umgedreht, None)
    assert zeilen_kennung({"id": "V-2026-014"}, "id") == "V-2026-014"
    assert zeilen_kennung({"id": "V 2026 014"}, "id") == "V-2026-014"


def test_zeile_wird_vorgang_mit_kurztext_und_zeitraum(gaz):
    datensatz = baustellen_datensatz()
    eintrag = zeile_zu_eintrag(datensatz, BAUSTELLEN_ZEILEN[0], 1, "2026-10-13T09:00:00+00:00",
                               heute().isoformat())
    assert eintrag.vorgang_id == f"opendata:{BAUSTELLEN_UUID}:V-2026-014"
    assert eintrag.titel == "Fahrbahnsanierung Feldgärtenstraße"
    assert eintrag.datum == "2026-09-14" and eintrag.frist == "2026-12-18"
    assert eintrag.web == PORTAL + CSV_PFAD and eintrag.referenz == "V-2026-014"
    assert "strasse: Feldgärtenstr." in eintrag.kurztext and len(eintrag.kurztext) <= MAX_KURZTEXT
    assert "Zeitraum laut Quelle: 2026-09-14 – 2026-12-18" in eintrag.status_text
    assert "feldgaertenstr" in eintrag.suchtext(), "alle Feldwerte sind Suchtext"
    assert eintrag.dokumente("Originaldatei", "datensatz")[0]["url"] == PORTAL + CSV_PFAD


def test_ohne_titel_und_datum_wird_nichts_erfunden(gaz):
    zeile = {"bemerkung": "noch ohne Angaben", "stand": "in Planung"}
    eintrag = zeile_zu_eintrag(baustellen_datensatz(), zeile, 7, "2026-10-13T09:00:00+00:00",
                               heute().isoformat())
    assert eintrag.titel == "Baustellen Köln – Zeile 7"
    assert eintrag.datum == "" and eintrag.offen is False
    assert "bemerkung: noch ohne Angaben" in eintrag.kurztext


def test_offen_nur_mit_zeitraum_aus_der_quelle(gaz):
    datensatz = baustellen_datensatz()
    laufend = zeile_zu_eintrag(datensatz, {"id": "A", "massnahme": "Weiterbau", "ende": "2099-01-01"},
                               1, "2026-10-13T09:00:00+00:00", "2026-10-13")
    vergangen = zeile_zu_eintrag(datensatz, {"id": "B", "massnahme": "Altbau", "ende": "2020-01-01"},
                                 2, "2026-10-13T09:00:00+00:00", "2026-10-13")
    ohne = zeile_zu_eintrag(datensatz, {"id": "C", "massnahme": "Unbekannt"}, 3,
                            "2026-10-13T09:00:00+00:00", "2026-10-13")
    assert (laufend.offen, vergangen.offen, ohne.offen) == (True, False, False)


def test_wertelisten_werden_ueber_das_schema_zu_zeilen():
    antwort = {"count": 2,
               "results": [["V-1", "Fahrbahnsanierung"], ["V-2", "Bushaltestelle"]],
               "schema": {"res": {"fields": {"id": {"type": "string"},
                                             "massnahme": {"type": "string"}}}}}
    zeilen, anzahl = _zeilen(antwort)
    assert anzahl == 2 and [z["massnahme"] for z in zeilen] == ["Fahrbahnsanierung", "Bushaltestelle"]
    assert zeilen[0]["id"] == "V-1"


# --- Crawl: Speichern, Blättern, Fortsetzen -------------------------------------

def _vorgaenge(sp) -> list:
    return sp.db.execute("SELECT * FROM vorgang ORDER BY id").fetchall()


def test_crawl_speichert_zeilen_mit_ortsbezug_und_dokument(tmp_path, uhr, stadt, quelle):
    fake = FakePortal([baustellen_datensatz()], {f"{BAUSTELLEN_UUID}/0": BAUSTELLEN_ZEILEN})
    with Speicher(tmp_path / "m.db") as sp:
        bericht = _lauf(sp, stadt, quelle, fake, uhr)
        assert bericht.status == "ok"
        assert bericht.zaehler["opendata:neu"] == 2 and bericht.zaehler["opendata:datensaetze"] == 1
        assert sp.db.execute("SELECT COUNT(*) FROM vorgang").fetchone()[0] == 2

        zeile = sp.db.execute(
            "SELECT v.*, m.status_kurz, m.art_quelle, m.offen, m.kurztext FROM vorgang v "
            "JOIN meldung m ON m.vorgang_id=v.id WHERE v.id LIKE '%V-2026-014'").fetchone()
        assert zeile["quelle_id"] == "koeln:opendata"
        assert zeile["art"] == "Datensatz: Baustellen Köln" and zeile["art_quelle"] == "Baustellen Köln"
        assert zeile["status_kurz"] == "Im offenen Datensatz geführt" and zeile["offen"] == 1
        assert zeile["web"] == PORTAL + CSV_PFAD

        rolle = sp.db.execute("SELECT rolle, url FROM dokument WHERE vorgang_id LIKE '%V-2026-014'").fetchone()
        assert rolle["rolle"] == "datensatz" and rolle["url"] == PORTAL + CSV_PFAD

        orte = {(r["typ"], r["schluessel"], r["quelle"]) for r in sp.db.execute(
            "SELECT * FROM ortsbezug WHERE vorgang_id LIKE '%V-2026-014'")}
        assert ("stadtteil", "504", "strasse") in orte, "Feldgärtenstr. → Niehl"
        assert ("bezirk", "5", "strasse") in orte


def test_zeilen_sind_ueber_stadtteil_und_strasse_findbar(tmp_path, uhr, stadt, quelle):
    fake = FakePortal([baustellen_datensatz()], {f"{BAUSTELLEN_UUID}/0": BAUSTELLEN_ZEILEN})
    with Speicher(tmp_path / "m.db") as sp:
        _lauf(sp, stadt, quelle, fake, uhr)
        nach_ort = suche(sp, Suchauftrag(stadt=stadt, orte=[{"typ": "stadtteil", "schluessel": "504"}]), heute())
        assert [t["titel"] for t in nach_ort["treffer"]] == ["Fahrbahnsanierung Feldgärtenstraße"]
        nach_strasse = suche(sp, Suchauftrag(stadt=stadt, freitext=["Feldgärtenstraße"]), heute())
        assert nach_strasse["gesamt"] == 1, "die Straße steht nur in einer Nebenspalte"


def test_zweiter_lauf_aktualisiert_statt_zu_verdoppeln(tmp_path, uhr, stadt, quelle):
    fake = FakePortal([baustellen_datensatz()], {f"{BAUSTELLEN_UUID}/0": BAUSTELLEN_ZEILEN})
    with Speicher(tmp_path / "m.db") as sp:
        _lauf(sp, stadt, quelle, fake, uhr)
        zweiter = _lauf(sp, stadt, quelle, fake, uhr)
        assert zweiter.zaehler["opendata:aktualisiert"] == 2 and "opendata:neu" not in zweiter.zaehler
        assert sp.db.execute("SELECT COUNT(*) FROM vorgang").fetchone()[0] == 2


def test_abbruch_am_budget_setzt_an_der_seite_fort(tmp_path, uhr, stadt, quelle):
    zeilen = [{"id": f"V-{i:04d}", "massnahme": f"Maßnahme {i}", "strasse": "Venloer Straße"}
              for i in range(250)]
    fake = FakePortal([baustellen_datensatz()], {f"{BAUSTELLEN_UUID}/0": zeilen})
    with Speicher(tmp_path / "m.db") as sp:
        # robots.txt + Katalogsuche + erste Seite passen ins Budget, die zweite nicht mehr.
        erster = _lauf(sp, stadt, quelle, fake, uhr, max_anfragen=4)
        assert erster.status == "teilweise" and "Anfragebudget" in erster.fehlertext
        stand = sp.stand_lesen("koeln:opendata", f"datensatz:{BAUSTELLEN_UUID}:0")
        # Zwei Seiten (je 100 Zeilen) sind gelesen; der Fortsetzungspunkt steht auf der dritten.
        assert "offset=200" in stand["naechste_url"]
        assert sp.db.execute("SELECT COUNT(*) FROM vorgang").fetchone()[0] == 200

        zweiter = _lauf(sp, stadt, quelle, fake, uhr)
        assert zweiter.status == "ok"
        assert sp.db.execute("SELECT COUNT(*) FROM vorgang").fetchone()[0] == 250
        assert sp.stand_lesen("koeln:opendata", f"datensatz:{BAUSTELLEN_UUID}:0")["naechste_url"] == ""
        assert any("offset=100" in a for a in fake.aufrufe) and any("offset=200" in a for a in fake.aufrufe)


def test_alle_zeilen_werden_in_seiten_von_hundert_gelesen(tmp_path, uhr, stadt, quelle):
    zeilen = [{"id": f"V-{i:04d}", "massnahme": f"Maßnahme {i}"} for i in range(SEITE + 1)]
    fake = FakePortal([baustellen_datensatz()], {f"{BAUSTELLEN_UUID}/0": zeilen})
    with Speicher(tmp_path / "m.db") as sp:
        bericht = _lauf(sp, stadt, quelle, fake, uhr)
        assert bericht.status == "ok" and bericht.zaehler["listen:seiten"] == 2
        assert sp.db.execute("SELECT COUNT(*) FROM vorgang").fetchone()[0] == SEITE + 1


def test_fehlende_tabelle_beendet_nur_diesen_datensatz(tmp_path, uhr, stadt, quelle):
    zwei = dataclasses.replace(quelle, datensaetze=("Baustellen Köln", "Aufstellungsbeschlüsse Köln"))
    fake = FakePortal([baustellen_datensatz(),
                       baustellen_datensatz(uuid=AUFSTELLUNG_UUID, titel="Aufstellungsbeschlüsse Köln")],
                      {f"{BAUSTELLEN_UUID}/0": BAUSTELLEN_ZEILEN})
    with Speicher(tmp_path / "m.db") as sp:
        bericht = _lauf(sp, stadt, zwei, fake, uhr)
        assert bericht.status == "ok"
        assert bericht.zaehler["opendata:datensaetze"] == 2 and bericht.zaehler["opendata:ohne_tabelle"] == 1
        assert sp.db.execute("SELECT COUNT(*) FROM vorgang").fetchone()[0] == 2


def test_ohne_datensaetze_in_der_konfiguration_passiert_nichts(tmp_path, uhr, stadt, quelle):
    leer = dataclasses.replace(quelle, datensaetze=())
    fake = FakePortal([baustellen_datensatz()])
    with Speicher(tmp_path / "m.db") as sp:
        bericht = _lauf(sp, stadt, leer, fake, uhr)
        assert bericht.status == "ok" and bericht.anfragen == 0, "ohne Stichworte wird nichts abgerufen"
        assert not fake.aufrufe


def test_datensaetze_finden_ueberspringt_doppelte_kennungen(stadt, quelle, uhr):
    """Zwei Stichworte, die auf denselben Datensatz zeigen: Er wird nur einmal gelesen."""
    doppelt = dataclasses.replace(quelle, datensaetze=("Baustellen Köln", "Baustellen"))
    fake = FakePortal([baustellen_datensatz()])
    gefunden = datensaetze_finden(_abrufer(fake, uhr), doppelt, LaufBericht(quelle_id="koeln:opendata"))
    assert [d["identifier"] for d in gefunden] == [BAUSTELLEN_UUID]


def test_ladebericht_zeigt_das_offene_datenportal(tmp_path, uhr, stadt, quelle):
    fake = FakePortal([baustellen_datensatz()], {f"{BAUSTELLEN_UUID}/0": BAUSTELLEN_ZEILEN})
    with Speicher(tmp_path / "m.db") as sp:
        _lauf(sp, stadt, quelle, fake, uhr)
        bericht = ladebericht(sp, stadt)
        zeile = next(q for q in bericht["quellen"] if q["id"] == "koeln:opendata")
        assert zeile["typ_text"] == "Offene Daten Köln (Datensätze)"
        schritte = [s["schritt"] for s in zeile["abrufweg"]]
        assert "Datensätze" in schritte and "Lizenz" in schritte and "Tabellenzeilen" in schritte
        assert bericht["gesamt"]["vorgaenge"] == 2


def test_cli_probelauf_zeigt_katalogtitel_und_lizenz(tmp_path, uhr, monkeypatch, capsys):
    """Der Probelauf ist der Weg, die Stichworte in koeln.toml gegen den Katalog zu prüfen."""
    from behoerdenmaster.cli import main

    fake = FakePortal([baustellen_datensatz()])

    def abrufer(**args):
        return Abrufer(**args, sleep=uhr.schlaf, clock=uhr,
                       client=httpx.Client(transport=httpx.MockTransport(fake)))

    monkeypatch.setattr("behoerdenmaster.cli.Abrufer", abrufer)
    pfad = tmp_path / "cli.sqlite"
    assert main(["--db", str(pfad), "probe", "--quelle", "opendata", "--kontakt", "test@example.test"]) == 0
    aus = capsys.readouterr().out
    assert "Baustellen Köln" in aus and "Datenlizenz Deutschland" in aus
    assert "Tabellen: 1" in aus
    assert not pfad.exists(), "probe darf keine Datenbank erzeugen"
