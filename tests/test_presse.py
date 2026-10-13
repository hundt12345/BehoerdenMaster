"""Zeitungsverweise zu Vorgängen: Konfiguration, Zuordnung und Anzeige.

Es werden nur Medium, Schlagzeile, Datum und URL verlinkt – nie ein Artikeltext.
Die Zuordnung ist redaktionell gesteuert (Stichworte/Referenzen aus der
Stadtkonfiguration) und rein lokal; eine Suche löst keine Abrufe aus.
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from behoerdenmaster.api import create_app
from behoerdenmaster.config import lade_staedte, lade_stadt
from behoerdenmaster.demo import lade_demo
from behoerdenmaster.normalize import norm
from behoerdenmaster.presse import PresseVerweis, finde_presse
from behoerdenmaster.speicher import Speicher
from behoerdenmaster.suche import Suchauftrag, suche, vorgang_detail

HEUTE = date(2026, 10, 9)


def _vorgang_speichern(db, vid: str, titel: str, referenz: str = "",
                        art: str = "Beschlussvorlage", datum: str = "2026-10-01") -> None:
    zeile = {
        "id": vid, "quelle_id": "koeln:ris", "referenz": referenz, "titel": titel,
        "art": art, "datum": datum, "geaendert": "", "web": f"https://example.test/{vid}",
        "suchtext": norm(f"{titel} {referenz} {art}"), "geloescht": 0,
    }
    db.vorgang_speichern(zeile, [], [], [], set())


@pytest.fixture
def bestand(db, stadt):
    """Drei Vorgänge, zwei davon mit passenden Zeitungsartikeln."""
    db.quelle_anlegen("koeln:ris", "koeln", "ris", "oparl", "Ratsinformationssystem Köln", "")
    _vorgang_speichern(db, "https://example.test/papers/1", "Neues Quartier Bickendorf")
    _vorgang_speichern(db, "https://example.test/papers/2",
                       "Sicherung der Clubkultur im Bereich Lichtstraße", referenz="100/2026")
    _vorgang_speichern(db, "https://example.test/papers/3", "Haushaltsplanung Lindenthal Schulen")
    return db


# --- Konfiguration ------------------------------------------------------------

def test_koeln_konfiguration_enthaelt_ksta_und_weitere_verweise(stadt):
    assert len(stadt.presse) >= 8
    ksta = [v for v in stadt.presse if v.medium == "Kölner Stadt-Anzeiger"]
    assert len(ksta) >= 4, "KSTA-Verweise aus docs/presse-koeln.md müssen enthalten sein"
    for v in stadt.presse:
        assert v.medium and v.titel and v.url.startswith("https://")
        assert v.stichworte or v.referenzen
        assert all(s == norm(s) and s for s in v.stichworte), v
        assert all(r == norm(r) and r for r in v.referenzen), v


def _minimale_stadt(presse_block: str) -> str:
    return '[stadt]\nschluessel = "teststadt"\nname = "Teststadt"\n' + presse_block


@pytest.mark.parametrize("block,grund", [
    ('[[presse]]\nmedium = "KSTA"\ntitel = "Titel"\nurl = "https://example.test/a"\n',
     "stichworte oder referenzen"),
    ('[[presse]]\nmedium = "KSTA"\ntitel = ""\nurl = "https://example.test/a"\nstichworte = ["x"]\n',
     "medium, titel und url"),
    ('[[presse]]\nmedium = "KSTA"\ntitel = "Titel"\nurl = "example.test/a"\nstichworte = ["x"]\n',
     r"http\(s\)-URL"),
    ('[[presse]]\nmedium = "KSTA"\ntitel = "Titel"\nurl = "https://example.test/a"\n'
     'datum = "05.02.2026"\nstichworte = ["x"]\n',
     "YYYY-MM-DD"),
])
def test_fehlerhafte_presse_eintraege_sind_klar(tmp_path, block, grund):
    (tmp_path / "teststadt.toml").write_text(_minimale_stadt(block), encoding="utf-8")
    with pytest.raises(ValueError, match=grund):
        lade_staedte(tmp_path)


def test_stadt_ohne_presse_abschnitt_bleibt_moeglich(tmp_path):
    (tmp_path / "teststadt.toml").write_text(_minimale_stadt(""), encoding="utf-8")
    staedte = lade_staedte(tmp_path)
    assert staedte["teststadt"].presse == ()


# --- Zuordnung ----------------------------------------------------------------

def test_stichworte_verknuepfen_und_referenz_trifft_exakt():
    a = PresseVerweis(medium="KSTA", titel="Club-Artikel", url="https://example.test/1",
                      stichworte=("clubkultur", "ehrenfeld"))
    assert a.passt(norm("Sicherung der Clubkultur in Ehrenfeld"))
    assert not a.passt(norm("Sicherung der Clubkultur in Bickendorf")), "UND der Stichworte"
    assert not a.passt(norm("Neues Quartier Bickendorf"))
    b = PresseVerweis(medium="Express", titel="Markt-Artikel", url="https://example.test/2",
                      referenzen=(norm("AN/1614/2026"),))
    assert b.passt(norm("Feierabendmärkte"), norm("AN/1614/2026"))
    assert not b.passt(norm("Feierabendmärkte"), norm("AN/9999/2026"))
    leer = PresseVerweis(medium="X", titel="Ohne Kriterium", url="https://example.test/3")
    assert not leer.passt(norm("irgendetwas"))


def test_konfigurierte_verweise_ordenen_sich_den_demo_vorgaengen_zu(stadt):
    """Die gepflegten Stichworte treffen die Vorgänge, für die sie gedacht sind."""
    bickendorf = finde_presse(stadt.presse, "Neues Quartier Bickendorf Bauleitplanung Bickendorf")
    assert [v["medium"] for v in bickendorf] == ["Kölner Stadt-Anzeiger"] * 2
    club = finde_presse(stadt.presse, "Sicherung der Clubkultur im Bereich Lichtstraße")
    assert {v["medium"] for v in club} == {"Kölner Stadt-Anzeiger", "Rheinische Anzeigenblätter"}
    markt = finde_presse(stadt.presse, "Feierabend- und Abendmärkte in Köln schaffen", "AN/1614/2026")
    assert {v["medium"] for v in markt} == {"Radio Köln", "Express"}
    assert finde_presse(stadt.presse, "Haushaltsplanung Lindenthal Schulen") == []


# --- Anzeige in Detail, Suche und API ----------------------------------------

def test_vorgang_detail_zeigt_zeitungsartikel_mit_hinweis(stadt, bestand):
    detail = vorgang_detail(bestand, stadt, "https://example.test/papers/1", HEUTE)
    assert [v["medium"] for v in detail["presse"]] == ["Kölner Stadt-Anzeiger"] * 2
    for v in detail["presse"]:
        assert v["url"].startswith("https://") and v["titel"] and v["datum_de"]
    assert any("Artikeltext wird nicht abgerufen" in h for h in detail["hinweise"])
    club = vorgang_detail(bestand, stadt, "https://example.test/papers/2", HEUTE)
    assert {v["medium"] for v in club["presse"]} == {"Kölner Stadt-Anzeiger", "Rheinische Anzeigenblätter"}
    ohne = vorgang_detail(bestand, stadt, "https://example.test/papers/3", HEUTE)
    assert ohne["presse"] == [] and not any("Artikeltext" in h for h in ohne["hinweise"])


def test_suche_zeigt_zeitungsartikel(stadt, bestand):
    ergebnis = suche(bestand, Suchauftrag(stadt=stadt, freitext=["Bickendorf"]), HEUTE)
    assert ergebnis["gesamt"] == 1
    treffer = ergebnis["treffer"][0]
    assert treffer["titel"] == "Neues Quartier Bickendorf"
    assert len(treffer["presse"]) == 2


def test_api_liefert_zeitungsartikel_zu_demo_vorgaengen(tmp_path, stadt):
    pfad = tmp_path / "presse.sqlite"
    with Speicher(pfad) as sp:
        lade_demo(sp, stadt)
    with TestClient(create_app(pfad)) as client:
        # Demo-Bauleitverfahren „Neues Quartier Bickendorf“ erbt die KSTA-Verweise.
        daten = client.post("/api/frage", json={"frage": "Bauvorhaben in Bickendorf"}).json()
        assert daten["gesamt"] >= 1
        bickendorf = [t for t in daten["treffer"] if "Bickendorf" in t["titel"]]
        assert bickendorf and any(t["presse"] for t in bickendorf)
        for t in bickendorf:
            for v in t["presse"]:
                assert v["medium"] and v["titel"] and v["url"].startswith("https://")
                assert "url" in v and "datum" in v
        detail = client.get("/api/vorgang",
                            params={"id": bickendorf[0]["id"]}).json()
        assert detail["presse"] == bickendorf[0]["presse"]
