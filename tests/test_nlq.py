import pytest

from behoerdenmaster.nlq import verstehe


def test_bauvorhaben_in_ehrenfeld(stadt, gaz):
    v = verstehe("Welche Bauvorhaben sind in Köln Ehrenfeld geplant?", stadt, gaz)
    assert v.stadt == "koeln"
    schluessel = {(o["typ"], o["schluessel"]) for o in v.orte}
    assert ("stadtteil", "401") in schluessel and ("bezirk", "4") in schluessel
    assert v.themen == ["bauen"]
    assert v.offen is True
    assert v.freitext == []
    assert any("Stadtteil und als Stadtbezirk" in h for h in v.hinweise)


def test_verkehr_seit_jahr(stadt, gaz):
    v = verstehe("Was gibt es zu Verkehr in Lindenthal seit 2024?", stadt, gaz)
    assert v.themen == ["verkehr"]
    assert v.von == "2024-01-01" and v.bis is None
    assert {o["schluessel"] for o in v.orte if o["typ"] == "stadtteil"} == {"303"}


def test_jahr_ohne_seit_ergibt_zeitraum(stadt, gaz):
    v = verstehe("Schulbau 2023 in Nippes", stadt, gaz)
    assert (v.von, v.bis) == ("2023-01-01", "2024-01-01")
    assert v.freitext == ["schulbau"]


def test_plz_und_thema(stadt, gaz):
    v = verstehe("50827 Bauvorhaben", stadt, gaz)
    assert v.plz == "50827"
    assert v.themen == ["bauen"]
    assert v.orte == []


@pytest.mark.parametrize("frage,erwartet", [
    ("Wohnungsbau in Köln", ["bauen"]),
    ("Neubau und Bebauungspläne", ["bauen"]),
    ("Radverkehr in Porz", ["verkehr"]),
    ("Kitas und Schulen", ["schulen"]),
])
def test_themen_erkennung(stadt, gaz, frage, erwartet):
    assert verstehe(frage, stadt, gaz).themen == erwartet


def test_stadtteil_mit_schraegstrich(stadt, gaz):
    v = verstehe("Vorgänge zu Bocklemünd/Mengenich", stadt, gaz)
    assert [o["schluessel"] for o in v.orte] == ["405"]


def test_kleinschreibung_bei_kurzen_namen_ist_kein_ort(stadt, gaz):
    v = verstehe("was ist mit eil", stadt, gaz)
    assert v.orte == []
    assert "eil" in v.freitext


def test_ohne_erkennung_gibt_hinweis(stadt, gaz):
    v = verstehe("Hallo", stadt, gaz)
    assert v.hinweise and "Keine Ortsangabe" in v.hinweise[0]


def test_freitext_bleibt_erhalten(stadt, gaz):
    v = verstehe("Digitalisierung Ausschuss", stadt, gaz)
    assert "digitalisierung" in v.freitext and "ausschuss" in v.freitext
