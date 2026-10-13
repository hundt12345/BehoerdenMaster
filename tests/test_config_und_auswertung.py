from collections import Counter, defaultdict

from behoerdenmaster.auswertung import bestimme_status, datum_de, ergebnis_kategorie, zusammenfassung
from behoerdenmaster.config import lade_stadt


def test_konfiguration_vollstaendig(stadt):
    assert len(stadt.bezirke) == 9
    assert len(stadt.stadtteile) == 86
    zaehl = Counter(st.bezirk for st in stadt.stadtteile.values())
    assert [zaehl[str(n)] for n in range(1, 10)] == [5, 13, 9, 6, 7, 12, 16, 9, 9]
    assert len(stadt.plz) == 46
    assert {t.schluessel for t in stadt.themen} == {"bauen", "verkehr", "schulen", "gruen"}
    assert stadt.quellen[0].schluessel == "ris"
    assert stadt.quellen[0].min_intervall_sekunden >= 2.0


def test_plz_zuordnung_verweist_auf_bekannte_stadtteile(stadt):
    for code, eintrag in stadt.plz.items():
        for nr in eintrag.stadtteile:
            assert nr in stadt.stadtteile, (code, nr)
    assert stadt.plz["50827"].stadtteile == ("403", "406")
    assert stadt.plz["50858"].stadtteile == ("306", "307")


def test_jede_plz_ist_geklaert(stadt):
    """Alle Kölner PLZ sind in der Konfiguration – mit oder ohne Zuordnung, aber begründet."""
    assert len(stadt.plz) == 46
    ohne = {code for code, eintrag in stadt.plz.items() if not eintrag.stadtteile}
    assert ohne == {"50923"}, "die übrigen PLZ haben eine abgeleitete Zuordnung"
    for code in ohne:
        assert stadt.plz[code].quelle, f"PLZ {code} muss begründen, warum nichts zugeordnet ist"
    for code, eintrag in stadt.plz.items():
        assert eintrag.quelle, f"PLZ {code} nennt keine Herkunft der Zuordnung"

    # Ein PLZ-Gebiet kann zwei Stadtbezirke berühren – belegt ist das für 51105
    # (Poll im Bezirk 7 und Humboldt/Gremberg im Bezirk 8). Mehr als zwei wären ein Hinweis
    # auf eine fehlerhafte Ableitung.
    for code, eintrag in stadt.plz.items():
        bezirke = {stadt.stadtteile[nr].bezirk for nr in eintrag.stadtteile}
        assert len(bezirke) <= 2, (code, bezirke)
    assert {stadt.stadtteile[nr].bezirk for nr in stadt.plz["51105"].stadtteile} == {"7", "8"}


def test_plz_zuordnung_passt_zum_strassenverzeichnis(stadt):
    """Die Zuordnung wird aus dem Straßenverzeichnis abgeleitet – beides muss zusammenpassen.

    Schwellen wie im Werkzeug ``tools/strassen_aus_osm.py``: Ein Stadtteil gehört zur PLZ,
    wenn mindestens 10 % und mindestens drei Straßen der PLZ darin liegen. Wer das Verzeichnis
    erneuert, muss die Ausgabe des Werkzeugs in die Konfiguration übernehmen.
    """
    assert stadt.strassen, "Straßenverzeichnis erwartet"
    zaehler: dict[str, Counter] = defaultdict(Counter)
    for strasse in stadt.strassen.values():
        for code in strasse.plz:
            for nr, anzahl in strasse.stadtteile:
                zaehler[code][nr] += anzahl
    for code, verteilung in zaehler.items():
        gesamt = sum(verteilung.values())
        erwartet = {nr for nr, anzahl in verteilung.items()
                    if anzahl >= 3 and anzahl / gesamt >= 0.10}
        assert set(stadt.plz[code].stadtteile) == erwartet, (
            f"PLZ {code}: Konfiguration {stadt.plz[code].stadtteile} ≠ Ableitung {sorted(erwartet)} – "
            "tools/strassen_aus_osm.py erneut ausführen und die Ausgabe übernehmen"
        )


def test_strassenverzeichnis_ist_vollstaendig_und_benannt(stadt):
    """Herkunft und Lizenz des Verzeichnisses müssen in der Anwendung ankommen (ODbL)."""
    assert len(stadt.strassen) > 3000
    assert stadt.strassen_quelle and stadt.strassen_lizenz and stadt.strassen_stand
    assert "OpenStreetMap" in stadt.strassen_lizenz or "ODbL" in stadt.strassen_lizenz
    eindeutig = [s for s in stadt.strassen.values() if s.stadtteil]
    assert len(eindeutig) > 3000, "die meisten Straßennamen sind eindeutig"
    assert stadt.strasse("Feldgärtenstraße") == stadt.strasse("Feldgärtenstr.")
    assert stadt.strasse("Feldgärtenstraße").stadtteil == "504"


def test_ehrenfeld_hat_sechs_stadtteile(stadt):
    namen = sorted(st.name for st in stadt.stadtteile.values() if st.bezirk == "4")
    assert namen == ["Bickendorf", "Bocklemünd/Mengenich", "Ehrenfeld", "Neuehrenfeld", "Ossendorf", "Vogelsang"]


def test_unbekannte_stadt_wirft_fehler():
    try:
        lade_stadt("gotham")
    except KeyError as exc:
        assert "koeln" in str(exc)
    else:
        raise AssertionError("KeyError erwartet")


def test_ergebnis_kategorien():
    assert ergebnis_kategorie("ungeändert beschlossen") == "beschlossen"
    assert ergebnis_kategorie("endgültig abgelehnt") == "abgelehnt"
    assert ergebnis_kategorie("Kenntnis genommen") == "kenntnis"
    assert ergebnis_kategorie("Sache ist erledigt") == "erledigt"
    assert ergebnis_kategorie("verwiesen in nachfolgende Gremien") == "vertagt"
    assert ergebnis_kategorie("Übergang zum nächsten Tagesordnungspunkt") == "uebergang"
    assert ergebnis_kategorie("") is None
    assert ergebnis_kategorie("etwas anderes") == "sonstig"


def test_datum_de():
    assert datum_de("2026-10-08") == "08.10.2026"
    assert datum_de("") == ""
    assert datum_de("unbekannt") == ""


def _eintrag(datum, gremium, kategorie=None, anstehend=False, top=""):
    return {"datum": datum, "gremium": gremium, "kategorie": kategorie, "anstehend": anstehend, "top_nummer": top}


def test_status_anstehend_hat_vorrang_und_nennt_termin():
    status = bestimme_status([
        _eintrag("2017-07-10", "BV 4", "beschlossen"),
        _eintrag("2026-11-10", "Stadtentwicklungsausschuss", None, True, "4"),
    ])
    assert status["kurz"] == "anstehend"
    assert "Stadtentwicklungsausschuss am 10.11.2026 (TOP 4)" in status["text"]
    assert status["letzte_entscheidung"]["gremium"] == "BV 4"


def test_status_letzte_entscheidung_und_leer():
    status = bestimme_status([_eintrag("2024-01-01", "Rat", "kenntnis"), _eintrag("2024-05-01", "Rat", "beschlossen")])
    assert status["kurz"] == "Beschluss gefasst"
    assert bestimme_status([])["kurz"] == "unbekannt"
    assert bestimme_status([_eintrag("", "Rat", None)])["kurz"] == "in Beratung"


def test_zusammenfassung_ist_faktisch():
    status = {"text": "Letzte Entscheidung: Rat am 08.10.2026: Beschluss gefasst"}
    text = zusammenfassung("Erlenweg", "Beschlussvorlage", "V/1/2024", "2024-03-01", status, ["Bickendorf"], 2)
    assert text.startswith("„Erlenweg“ (Beschlussvorlage, Nr. V/1/2024, vom 01.03.2024).")
    assert "Ortsbezug: Bickendorf." in text
    assert "2 verlinkte(s) Dokument(e)." in text
