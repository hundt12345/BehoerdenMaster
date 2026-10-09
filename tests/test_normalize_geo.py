from behoerdenmaster.geo import OrtsBezug, phrase_erlaubt
from behoerdenmaster.normalize import norm


def test_norm_umlaute_und_satzzeichen():
    assert norm("Köln-Ehrenfeld") == "koeln ehrenfeld"
    assert norm("Bocklemünd/Mengenich") == "bocklemuend mengenich"
    assert norm("Straße  Weiß") == "strasse weiss"
    assert norm(None) == ""


def test_stadtteil_nennt_auch_bezirk(gaz):
    treffer = gaz.ortsbezuege_aus_text("Erlenweg in Köln-Bickendorf")
    assert OrtsBezug("stadtteil", "403", "betreff") in treffer
    assert OrtsBezug("bezirk", "4", "betreff") in treffer


def test_stadtteil_in_zusammengesetztem_wort_wird_nicht_erkannt(gaz):
    # „Ehrenfeldgürtel“ ist ein Straßenname, kein Verweis auf den Stadtteil Ehrenfeld.
    assert gaz.ortsbezuege_aus_text("Ehrenfeldgürtel Ausbau") == set()


def test_zusatz_im_titel_wird_erkannt(gaz):
    treffer = gaz.ortsbezuege_aus_text("Lichtstraße / Ehrenfeldgürtel in Köln- Ehrenfeld")
    assert OrtsBezug("stadtteil", "401", "betreff") in treffer


def test_alias_und_kombinierter_name(gaz):
    treffer = gaz.ortsbezuege_aus_text("Neues Quartier Mengenich")
    assert OrtsBezug("stadtteil", "405", "betreff") in treffer
    treffer = gaz.ortsbezuege_aus_text("Projekt Bocklemünd/Mengenich")
    assert OrtsBezug("stadtteil", "405", "betreff") in treffer


def test_laengster_name_gewinnt_bei_neuehrenfeld(gaz):
    treffer = gaz.ortsbezuege_aus_text("Wohnungsbau in Köln-Neuehrenfeld")
    stadtteile = {o.schluessel for o in treffer if o.typ == "stadtteil"}
    assert stadtteile == {"402"}


def test_kurze_namen_nur_mit_grossem_anfangsbuchstaben(gaz):
    assert gaz.ortsbezuege_aus_text("Ein eil-schneller Antrag") == set()
    assert OrtsBezug("stadtteil", "705", "betreff") in gaz.ortsbezuege_aus_text("Eil: Radweg")


def test_gremium_liefert_nur_bezirk(gaz):
    treffer = gaz.ortsbezuege_aus_text("Bezirksvertretung 4 (Ehrenfeld)", "gremium", nur_bezirk=True)
    assert treffer == {OrtsBezug("bezirk", "4", "gremium")}


def test_bezirksnummer_im_titel(gaz):
    assert OrtsBezug("bezirk", "3", "betreff") in gaz.ortsbezuege_aus_text("Antrag an die Bezirksvertretung 3")


def test_plz_nur_wenn_in_konfiguration(gaz):
    assert OrtsBezug("plz", "50827", "betreff") in gaz.ortsbezuege_aus_text("Vorhaben 50827 Köln")
    assert not any(o.typ == "plz" for o in gaz.ortsbezuege_aus_text("Auftrag Nr. 12345 vom 01.01.2024"))


def test_phrase_erlaubt_regel():
    assert phrase_erlaubt("ehrenfeld", ["ehrenfeld"])
    assert not phrase_erlaubt("eil", ["eil"])
    assert phrase_erlaubt("eil", ["Eil"])


def test_stadtteil_nach_name_fuer_oparl_subLocality(gaz):
    assert gaz.stadtteil_nach_name("Bickendorf") == "403"
    assert gaz.stadtteil_nach_name("Unbekannt") is None
