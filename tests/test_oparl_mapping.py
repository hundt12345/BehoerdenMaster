import json

from behoerdenmaster.oparl import map_gremium, map_sitzung, map_vorgang, rolle_aus_name

from conftest import demo_objekte, synth_beratung, synth_top, synth_vorlage, KOERPER


def test_vorlage_aus_echtem_auszug(stadt):
    _, _, vorlagen = demo_objekte()
    zeile, beratungen, dokumente, beziehungen, orte = map_vorgang(vorlagen[0], "koeln:ris")
    assert zeile["referenz"] == "1724/2021"
    assert zeile["art"] == "Mitteilung Ausschuss"
    assert zeile["datum"] == "2021-05-10"
    assert zeile["titel"].startswith("Ratsinformationssystem: OParl-Schnittstelle")
    assert len(beratungen) == 1
    assert beratungen[0]["rolle"] == "Kenntnisnahme (Mitteilung)"
    assert beratungen[0]["federfuehrend"] == 1
    assert json.loads(beratungen[0]["gremium_ids"]) == [KOERPER + "/organizations/gr/177"]
    assert [d["rolle"] for d in dokumente] == ["dokument"]
    assert beziehungen == [] and orte == []


def test_sitzung_mit_tops_und_dokumenten(stadt):
    _, sitzungen, _ = demo_objekte()
    rat = next(s for s in sitzungen if s["id"].endswith("/meetings/33698"))
    zeile, tops, dokumente = map_sitzung(rat, "koeln:ris")
    assert zeile["datum"] == "2026-10-08"
    assert zeile["ort"] == "Ratssaal"
    assert zeile["abgesagt"] == 0
    assert len(tops) == 11
    ergebnisse = {t["nummer"]: t["ergebnis"] for t in tops}
    assert ergebnisse["3.1"] == "endgültig abgelehnt"
    assert ergebnisse["19.5"] == "ungeändert beschlossen"
    rollen = {d["name"]: d["rolle"] for d in dokumente}
    assert rollen["Tagesordnung Rat"] == "tagesordnung"
    assert rollen["Einladung Rat"] == "einladung"


def test_tops_verweisen_auf_beratung(stadt):
    _, sitzungen, _ = demo_objekte()
    ausschuss = next(s for s in sitzungen if s["id"].endswith("/meetings/34706"))
    _, tops, _ = map_sitzung(ausschuss, "koeln:ris")
    beratungen = {t["nummer"]: t["beratung_id"] for t in tops}
    assert beratungen["10.1"].endswith("/consultations/205174")
    assert beratungen["1"] == ""


def test_gremium_name(stadt):
    gremien, _, _ = demo_objekte()
    zeile = map_gremium(gremien[0], "koeln:ris")
    assert zeile["name"] == "Rat"
    assert zeile["geloescht"] == 0


def test_rolle_aus_name():
    assert rolle_aus_name("Niederschrift über die Sitzung") == "protokoll"
    assert rolle_aus_name("Tagesordnung") == "tagesordnung"
    assert rolle_aus_name("Gem. Änderungsantrag nach § 13 (Rat)") == "dokument"


def test_vorlage_mit_fehlenden_feldern_und_urls_als_verweise():
    obj = {"id": KOERPER + "/papers/vo/1", "type": "x", "consultation": [KOERPER + "/consultations/9"],
           "location": {"postalCode": "50827", "subLocality": "Ehrenfeld"},
           "relatedPaper": [KOERPER + "/papers/vo/2"]}
    zeile, beratungen, dokumente, beziehungen, orte = map_vorgang(obj, "koeln:ris")
    assert zeile["titel"] == "" and zeile["datum"] == ""
    assert beratungen[0]["id"].endswith("/consultations/9")
    assert ("plz", "50827") in orte and ("stadtteil_name", "Ehrenfeld") in orte
    assert beziehungen == [(KOERPER + "/papers/vo/2", "verwandt")]


def test_geloeschte_vorlage_wird_erkannt():
    obj = synth_vorlage(5, "egal", deleted=True)
    assert obj == {"id": KOERPER + "/papers/vo/5", "type": "https://schema.oparl.org/1.1/Paper",
                   "deleted": True, "modified": "2024-03-02T10:00:00+01:00",
                   "created": "2024-03-01T09:00:00+01:00"}


def test_top_mit_beratungsverweis():
    top = synth_top(7, 3, "2", "Antrag", ergebnis="beschlossen", consultation_nr=11)
    from behoerdenmaster.oparl import map_top
    zeile = map_top(top, KOERPER + "/meetings/3")
    assert zeile["beratung_id"] == KOERPER + "/consultations/11"
    assert zeile["ergebnis"] == "beschlossen"
    assert synth_beratung(11, 1, 3, 7)["agendaItem"].endswith("/agendaitems/7")


def _allris_vorlage():
    """Eigenheiten der Bonner ALLRIS-Schnittstelle (öffentlich dokumentiert, mandari PR #746):

    Beratungen verweisen kleingeschrieben auf den TOP (``agendaitem``), Verweise kommen
    teils als eingebettetes Objekt statt als URL, Gremien tragen das Typ-Kürzel in ``type``.
    """
    basis = "https://www.bonn.sitzung-online.de/oparl"
    return {
        "id": f"{basis}/papers/4711",
        "type": "https://schema.oparl.org/1.1/Paper",
        "name": "Radweg an der Beispielstraße",
        "reference": "DS-Nr. 251234",
        "paperType": "Beschlussvorlage",
        "date": "2026-09-30",
        "consultation": [{
            "id": f"{basis}/consultations/99",
            "meeting": {"id": f"{basis}/meetings/12"},
            "agendaitem": {"id": f"{basis}/agendaitems/34"},
            "organization": [{"id": f"{basis}/organizations/7"}, f"{basis}/organizations/8"],
            "role": "Beschlussfassung",
            "authoritative": True,
            "modified": "2026-10-01T10:00:00+02:00",
        }],
    }, basis


def test_allris_beratung_mit_agendaitem_und_eingebetteten_verweisen(stadt):
    obj, basis = _allris_vorlage()
    _, beratungen, _, _, _ = map_vorgang(obj, "bonn:ris")
    (beratung,) = beratungen
    assert beratung["sitzung_id"] == f"{basis}/meetings/12"
    assert beratung["top_id"] == f"{basis}/agendaitems/34"
    assert json.loads(beratung["gremium_ids"]) == [f"{basis}/organizations/7", f"{basis}/organizations/8"]


def test_allris_gremium_hat_typkuerzel(stadt):
    obj, basis = _allris_vorlage()
    zeile = map_gremium({
        "id": f"{basis}/organizations/7", "name": "Bezirksvertretung Bonn",
        "type": "gr", "Type": "https://schema.oparl.org/1.1/Organization",
    }, "bonn:ris")
    assert zeile["name"] == "Bezirksvertretung Bonn"
    assert zeile["typ"] == "https://schema.oparl.org/1.1/Organization"  # Type-URL statt leer


def test_folgeseite_erhaelt_verlorenen_filterparameter_zurueck():
    """ALLRIS hängt an `links.next` den modified_since-Parameter nicht wieder an."""
    from behoerdenmaster.oparl import OParlQuelle

    class FakeAbrufer:
        def __init__(self):
            self.urls = []

        def get_json(self, url, params=None):
            self.urls.append((url, params))
            if "seite=2" in url:
                assert params is None and "modified_since=" in url
                return {"data": [{"id": "x/2"}], "links": {}}
            assert params == {"modified_since": "2026-10-01T00:00:00+02:00"}
            return {"data": [{"id": "x/1"}],
                    "links": {"next": "https://x.test/oparl/papers?seite=2"}}

    fake = FakeAbrufer()
    quelle = OParlQuelle(fake, "https://x.test/oparl/system")
    seiten = list(quelle.seiten("https://x.test/oparl/papers",
                                params={"modified_since": "2026-10-01T00:00:00+02:00"}))
    assert [s.objekte[0]["id"] for s in seiten] == ["x/1", "x/2"]
    # Der Wert der Quelle (URL) gewinnt, der fehlende Parameter kommt hinzu.
    assert "modified_since=2026-10-01T00%3A00%3A00%2B02%3A00" in fake.urls[1][0]
