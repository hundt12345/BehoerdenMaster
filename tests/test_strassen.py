"""Straßenverzeichnis: Straßennamen lösen Stadtteile auf – nachvollziehbar und ohne Raten.

Hintergrund: Der Ortsbezug entsteht beim Abruf aus den Texten der Quelle. Nennt eine
Vorlage nur eine Straße, war sie bisher keinem Stadtteil zuzuordnen – wer den Stadtteil
suchte, fand sie nicht, und wer den Stadtteil nicht kannte, musste die Straße erraten.
Hier wird geprüft, dass das Verzeichnis genau diesen Fall schließt und dabei ehrlich
bleibt: mehrdeutige Straßennamen liefern **keinen** Ortsbezug.
"""

from datetime import date

import httpx
import pytest

from behoerdenmaster.abruf import Abrufer
from behoerdenmaster.crawler import LaufOptionen, crawle_oparl
from behoerdenmaster.geo import OrtsBezug
from behoerdenmaster.normalize import strassen_kanonisch
from behoerdenmaster.nlq import verstehe
from behoerdenmaster.ortsbezug import aktualisiere
from behoerdenmaster.speicher import Speicher
from behoerdenmaster.suche import Suchauftrag, suche

from conftest import KOERPER, FakeOParl, demo_objekte, synth_sitzung, synth_vorlage

HEUTE = date(2026, 10, 13)


# --- Schreibweisen und Kürzel ---------------------------------------------------

@pytest.mark.parametrize("geschrieben", [
    "Feldgärtenstraße", "Feldgärtenstr.", "Feldgärtenstr", "Feldgaertenstrasse",
])
def test_kuerzel_und_umlaute_ergeben_dieselbe_form(geschrieben):
    assert strassen_kanonisch(geschrieben) == "feldgaertenstrasse"
    assert strassen_kanonisch("Venloer Str.") == "venloer strasse"


def test_kurze_woerter_bleiben_unberuehrt():
    """„Rat“ oder „Fenster“ sind keine Straßenkürzel und dürfen nicht umgeschrieben werden."""
    assert strassen_kanonisch("Rat") == "rat"
    assert strassen_kanonisch("Fenster") == "fenster"
    assert strassen_kanonisch("Bahnhofstr") == "bahnhofstrasse"


# --- Auflösung in Texten --------------------------------------------------------

def test_eindeutige_strasse_liefert_stadtteil_und_bezirk(gaz):
    treffer = gaz.ortsbezuege_aus_text("Neubau Feldgärtenstr. 12")
    assert OrtsBezug("stadtteil", "504", "strasse") in treffer, "Feldgärtenstraße liegt in Niehl"
    assert OrtsBezug("bezirk", "5", "strasse") in treffer


def test_text_bleibt_genauer_als_die_ableitung(gaz):
    """Nennt der Text den Stadtteil selbst, zählt die Angabe der Quelle – nicht die Ableitung."""
    treffer = gaz.ortsbezuege_aus_text("Feldgärtenstraße in Köln-Niehl")
    assert OrtsBezug("stadtteil", "504", "betreff") in treffer
    assert not any(o.typ == "stadtteil" and o.quelle == "strasse" for o in treffer)


def test_mehrdeutige_strasse_raet_nicht(gaz):
    """Erlenweg gibt es in Bickendorf, Esch/Auweiler und Grengel – also keinen Ortsbezug."""
    assert gaz.ortsbezuege_aus_text("Erlenweg 5") == set()
    assert gaz.ortsbezuege_aus_text("Erlenweg in Köln-Bickendorf") == {
        OrtsBezug("stadtteil", "403", "betreff"), OrtsBezug("bezirk", "4", "betreff")}


def test_gremienname_liefert_keinen_strassenbezug(gaz):
    assert gaz.ortsbezuege_aus_text("Bezirksvertretung Feldgärtenstr.", "gremium", nur_bezirk=True) == set()


def test_anzeigetext_bleibt_die_schreibweise_der_eingabe(gaz):
    treffer = gaz.strassen_im_text("Baumaßnahme Feldgärtenstr 12 in Köln")
    assert [text for _, text in treffer] == ["Feldgärtenstr"]
    assert treffer[0][0].stadtteil == "504"


# --- Fragen verstehen -----------------------------------------------------------

def test_frage_mit_strasse_wird_dem_stadtteil_zugeordnet(stadt, gaz):
    v = verstehe("Bauvorhaben Feldgärtenstraße", stadt, gaz)
    assert ("stadtteil", "504") in {(o["typ"], o["schluessel"]) for o in v.orte}
    assert any("Straßenverzeichnis" in h and "Niehl" in h for h in v.hinweise)
    assert "feldgaertenstrasse" in v.freitext, "das Straßenwort bleibt Suchwort"


def test_frage_mit_mehrdeutiger_strasse_nennt_die_kandidaten(stadt, gaz):
    v = verstehe("Erlenweg", stadt, gaz)
    schluessel = {o["schluessel"] for o in v.orte if o["typ"] == "stadtteil"}
    assert schluessel == {"403", "607", "709"}
    assert any("mehreren Stadtteilen" in h for h in v.hinweise)


# --- Zusammenspiel mit Abruf und Suche -----------------------------------------

def _lade_street_vorlagen(stadt, db, uhr):
    """Zwei Vorlagen über den echten Crawl-Pfad: eine mit Straße, eine mit Stadtteil im Titel."""
    gremien, sitzungen, vorlagen = demo_objekte()
    sitzungen = sitzungen + [synth_sitzung(50, "Stadtentwicklungsausschuss", "2026-11-10T16:00:00+01:00", 5, tops=[])]
    neu = "2026-10-01T10:00:00+02:00"
    vorlagen = vorlagen + [
        synth_vorlage(701, "Feldgärtenstr. 12: Neubau eines Mehrfamilienhauses", datum="2026-04-01", modified=neu),
        synth_vorlage(702, "Neues Quartier in Köln-Niehl", datum="2026-04-02", modified=neu),
        synth_vorlage(703, "Erlenweg 5: Umbau des Spielplatzes", datum="2026-04-03", modified=neu),
    ]
    fake = FakeOParl(papers=vorlagen, meetings=sitzungen, orgs=gremien, seite=50)
    abrufer = Abrufer(kontakt="t@example.test", min_intervall=3.0, sleep=uhr.schlaf, clock=uhr,
                      client=httpx.Client(transport=fake.transport()))
    bericht = crawle_oparl(db, stadt, stadt.quellen[0], abrufer, LaufOptionen(seit="2017-01-01"))
    assert bericht.status == "ok", bericht.fehlertext
    return f"{KOERPER}/papers/vo/701", f"{KOERPER}/papers/vo/702"


def test_abruf_loest_strasse_auf_stadtteil_auf(stadt, db, uhr):
    nur_strasse, mit_stadtteil = _lade_street_vorlagen(stadt, db, uhr)
    erg = suche(db, Suchauftrag(stadt=stadt, orte=[{"typ": "stadtteil", "schluessel": "504"}]), HEUTE)
    ids = {t["id"] for t in erg["treffer"]}
    assert nur_strasse in ids, "die Straße genügt, um den Stadtteil zu finden"
    assert mit_stadtteil in ids
    treffer = next(t for t in erg["treffer"] if t["id"] == nur_strasse)
    assert ["strasse"] == next(o["herkunft"] for o in treffer["orte"] if o["typ"] == "stadtteil")
    assert treffer["orte_herkunft"] == ["strasse"]


def test_mehrdeutige_strasse_erscheint_in_keinem_stadtteil(stadt, db, uhr):
    """Die Vorlage „Erlenweg 5“ nennt keinen Stadtteil – und bekommt deshalb auch keinen."""
    _lade_street_vorlagen(stadt, db, uhr)
    erg = suche(db, Suchauftrag(stadt=stadt, orte=[{"typ": "stadtteil", "schluessel": "403"}]), HEUTE)
    assert "papers/vo/703" not in {t["id"] for t in erg["treffer"]}
    detail = suche(db, Suchauftrag(stadt=stadt, freitext=["erlenweg"]), HEUTE)
    assert detail["gesamt"] == 1, "über die Straße bleibt der Vorgang findbar"


def test_abgekuerzte_strasse_wird_mit_langer_schreibweise_gefunden(stadt, db, uhr):
    """Im Bestand steht „Feldgärtenstr.“; gefragt wird nach der „Feldgärtenstraße“."""
    _lade_street_vorlagen(stadt, db, uhr)
    erg = suche(db, Suchauftrag(stadt=stadt, freitext=["Feldgärtenstraße"]), HEUTE)
    assert [t["titel"][:14] for t in erg["treffer"]] == ["Feldgärtenstr."]


def _quelle_anlegen(db, stadt):
    db.quelle_anlegen("koeln:ris", stadt.schluessel, "ris", "oparl", "Ratsinformationssystem (Test)", "")


def test_pflege_ergaenzt_strassenbezug_ohne_abruf(stadt, db):
    """Ein vorhandener Bestand bekommt den neuen Ortsbezug nachgerechnet – ohne Behördenabruf."""
    _quelle_anlegen(db, stadt)
    db.vorgang_speichern(
        {"id": "test:strasse", "quelle_id": "koeln:ris", "referenz": "", "titel": "Feldgärtenstr. 12",
         "art": "Antrag", "datum": "2026-04-01", "geaendert": "", "web": "",
         "suchtext": "feldgaertenstr 12 antrag", "geloescht": 0},
        [], [], [], [],
    )
    # Ein Objekt der Schnittstelle trägt einen Ortsbezug, der sich nicht aus Texten ergibt.
    db.ortsbezug_ersetzen("test:strasse", {("plz", "50827", "oparl")}, ("oparl",))

    prueflauf = aktualisiere(db, stadt)
    # Stadtteil und sein Bezirk – zwei Einträge, beide aus dem Straßennamen abgeleitet.
    assert prueflauf["aktualisiert"] == 1 and prueflauf["strasse"] == 2
    assert db.db.execute("SELECT COUNT(*) FROM ortsbezug WHERE vorgang_id='test:strasse'").fetchone()[0] == 1

    zaehler = aktualisiere(db, stadt, schreiben=True)
    assert zaehler["geprueft"] == 1 and zaehler["aktualisiert"] == 1
    orte = {(r["typ"], r["schluessel"], r["quelle"]) for r in db.db.execute(
        "SELECT typ, schluessel, quelle FROM ortsbezug WHERE vorgang_id='test:strasse'")}
    assert ("stadtteil", "504", "strasse") in orte
    assert ("plz", "50827", "oparl") in orte, "übernommene Angaben bleiben erhalten"

    assert aktualisiere(db, stadt, schreiben=True)["aktualisiert"] == 0, "der zweite Lauf ändert nichts"


def test_suche_nutzt_die_plz_des_stadtteils(stadt, db):
    """Ein Vorgang mit bloßem PLZ-Bezug wird über den Stadtteil gefunden."""
    _quelle_anlegen(db, stadt)
    db.vorgang_speichern(
        {"id": "test:plz", "quelle_id": "koeln:ris", "referenz": "", "titel": "Antrag 50735",
         "art": "Antrag", "datum": "2026-04-01", "geaendert": "", "web": "",
         "suchtext": "antrag 50735", "geloescht": 0},
        [], [], [], [("plz", "50735", "betreff")],
    )
    erg = suche(db, Suchauftrag(stadt=stadt, orte=[{"typ": "stadtteil", "schluessel": "504"}]), HEUTE)
    assert "test:plz" in {t["id"] for t in erg["treffer"]}


def test_cli_ortsbezug_ist_standards_pruelflauf(tmp_path, stadt, capsys):
    """Ohne --schreiben ändert der Wartungsbefehl nichts; mit --schreiben übernimmt er."""
    from behoerdenmaster.cli import main

    pfad = tmp_path / "cli.sqlite"
    with Speicher(pfad) as sp:
        sp.quelle_anlegen("koeln:ris", stadt.schluessel, "ris", "oparl", "Ratsinformationssystem (Test)", "")
        sp.vorgang_speichern(
            {"id": "test:strasse", "quelle_id": "koeln:ris", "referenz": "", "titel": "Feldgärtenstr. 12",
             "art": "Antrag", "datum": "2026-04-01", "geaendert": "", "web": "",
             "suchtext": "feldgaertenstr 12 antrag", "geloescht": 0}, [], [], [], [])
    assert main(["--db", str(pfad), "ortsbezug"]) == 0
    assert "Prüflauf (nichts geändert)" in capsys.readouterr().out
    with Speicher(pfad) as sp:
        assert sp.db.execute("SELECT COUNT(*) FROM ortsbezug").fetchone()[0] == 0

    assert main(["--db", str(pfad), "ortsbezug", "--schreiben"]) == 0
    assert "Ortsbezug aktualisiert" in capsys.readouterr().out
    with Speicher(pfad) as sp:
        orte = {(r["typ"], r["schluessel"], r["quelle"]) for r in sp.db.execute("SELECT * FROM ortsbezug")}
    assert ("stadtteil", "504", "strasse") in orte
