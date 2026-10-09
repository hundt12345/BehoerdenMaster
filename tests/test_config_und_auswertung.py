from collections import Counter

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
    assert stadt.plz["50827"].stadtteile == ("401", "402", "403", "406")
    assert stadt.plz["50858"].stadtteile == ("306", "307")


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
