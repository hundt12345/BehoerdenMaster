"""Regelbasiertes Verstehen von Fragen wie „Welche Bauvorhaben sind in Köln Ehrenfeld geplant?“.

Erkannt werden: Stadt, Ortsangaben (Stadtteil und Stadtbezirk), Themen aus der
Stadtkonfiguration, PLZ, Zeitbezüge (Jahr, konkretes Datum wie „seit dem 9.10.“
oder „seit dem 9. Oktober“, Relative wie „seit gestern“ / „letzten 7 Tage“), die
Absicht „geplant/offen“ und die Stream-Absicht „neueste zuerst“.
Übrig gebliebene inhaltliche Wörter dienen als Freitextsuche. Es gibt bewusst
kein Sprachmodell: die Auslegung bleibt nachvollziehbar und getestet.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

from .config import Stadt
from .geo import Gazetteer, phrase_erlaubt
from .normalize import norm

_WORT = re.compile(r"\w+")
_PLZ = re.compile(r"(?<!\d)(\d{5})(?!\d)")
_JAHR = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")

# Monatsnamen in normalisierter Schreibweise (Umlaute sind bereits ausgeschrieben).
MONATE = {
    "januar": 1, "februar": 2, "maerz": 3, "april": 4, "mai": 5, "juni": 6,
    "juli": 7, "august": 8, "september": 9, "oktober": 10, "november": 11, "dezember": 12,
}
_MONAT_ALTERNATIVEN = "|".join(MONATE)

# Wörter, die einen chronologischen Stream verlangen („die neuesten Vorgänge …“).
# Sie werden nicht zu Stichworten: Wer nach den Neuesten fragt, will keine Vorgänge
# finden, in deren Titel zufällig „neu“ steht, sondern eine Zeit-Sortierung.
NEU_WOERTER = {
    "neu", "neue", "neuen", "neues", "neuer", "neuem",
    "neueste", "neuesten", "neuestem", "neuester", "neuestes",
    "juengste", "juengsten", "juengstem", "juengster", "juengstes",
    "aktuellste", "aktuellsten", "aktuellstem", "aktuellster",
}

STOPWORTE = {
    "welche", "welcher", "welches", "welchen", "welchem", "wer", "wie", "was", "wo", "wann",
    "gibt", "gibts", "es", "sind", "ist", "war", "waren", "wird", "werden", "wurde", "wurden",
    "hat", "haben", "habe", "bei", "der", "die", "das", "den", "dem", "des", "ein", "eine",
    "einen", "einem", "einer", "und", "oder", "in", "im", "am", "an", "auf", "aus", "mit",
    "fuer", "von", "vom", "zu", "zum", "zur", "nach", "seit", "bis", "ueber", "unter", "ob",
    "alle", "alles", "allen", "mir", "bitte", "zeige", "zeig", "zeigen", "gib", "gibt's", "stadt",
    "vorgaenge", "vorgang", "vorlagen", "vorlage", "sich", "noch", "auch", "dazu", "dort",
    "hier", "koennen", "kann", "soll", "sollen", "geben", "liste", "uebersicht",
    "neue", "neuen", "neues", "neu", "letzte", "letzten", "wichtige", "wichtig", "geht", "bitte",
    "damit", "gibt's",
    # Straßenkürzel sind für sich genommen keine Suchworte: „Str.“ träfe in fast jedem Text.
    # Den Namen der Straße liefert das Straßenverzeichnis (siehe geo.strassen_im_text).
    "str", "strasse", "strassen",
}
OFFEN_WOERTER = {
    "geplant", "plant", "planen", "vorhaben", "vorhabens", "anstehend", "kommende", "kommenden",
    "kommend", "offen", "laufend", "laufende", "aktuell", "aktuelle", "aktuellen",
}


@dataclass
class Verstaendnis:
    stadt: str | None
    orte: list[dict] = field(default_factory=list)
    themen: list[str] = field(default_factory=list)
    freitext: list[str] = field(default_factory=list)
    plz: str | None = None
    von: str | None = None
    bis: str | None = None
    offen: bool = False
    # Stream-Absicht: chronologisch nach Vorgangsdatum, neueste zuerst (siehe suche.py).
    neueste: bool = False
    hinweise: list[str] = field(default_factory=list)

    def als_dict(self) -> dict:
        return asdict(self)


def _datum_de(d: date) -> str:
    return f"{d.day:02d}.{d.month:02d}.{d.year}"


_MONATSNAMEN = {1: "Januar", 2: "Februar", 3: "März", 4: "April", 5: "Mai", 6: "Juni",
                7: "Juli", 8: "August", 9: "September", 10: "Oktober", 11: "November", 12: "Dezember"}


def _datums_angabe(tag: int, monat_wort: str, jahr: int | None) -> str:
    """Anzeigeform einer erkannten Datumsangabe, z. B. „9.10.“ oder „9. Oktober 2026“."""
    if monat_wort.isdigit():
        basis = f"{tag}.{int(monat_wort)}."
        return f"{basis}{jahr}" if jahr else basis
    return f"{tag}. {_MONATSNAMEN[MONATE[monat_wort]]}" + (f" {jahr}" if jahr else "")


def _datum_bauen(tag: int, monat: int, jahr: int | None, referenz: date) -> date:
    """Datum bauen; ohne Jahr gilt das jüngste Vorkommen (nie ein Datum in der Zukunft).

    „seit dem 9.10.“ meint am 14.10. den 9.10. dieses Jahres, im Januar dagegen den
    9.10. des Vorjahres – ein zukünftiges Startdatum lieferte sonst still null Treffer.
    """
    if jahr is not None:
        return date(jahr, monat, tag)
    kandidat = date(referenz.year, monat, tag)
    if kandidat > referenz:
        kandidat = date(referenz.year - 1, monat, tag)
    return kandidat


def parse_datum(wert: str, referenz: date) -> date:
    """Eine Datumsangabe lesen: `2026-10-09`, `9.10.`, `9.10.2026`, `9. Oktober 2026`,
    `heute` oder `gestern`. Ohne Jahr gilt wie in Fragen das jüngste Vorkommen.

    Wirft ValueError mit deutschem Text, wenn die Angabe kein gültiges Datum ist –
    API und CLI machen daraus eine verständliche Fehlermeldung statt still zu raten.
    """
    roh = (wert or "").strip()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", roh)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError as exc:
            raise ValueError(f"Ungültiges Datum: „{wert}“.") from exc
    t = norm(roh)
    if t == "heute":
        return referenz
    if t == "gestern":
        return referenz - timedelta(days=1)
    m = re.fullmatch(r"(\d{1,2}) (\d{1,2})(?: (\d{4}))?", t)
    if m:
        try:
            return _datum_bauen(int(m.group(1)), int(m.group(2)),
                                int(m.group(3)) if m.group(3) else None, referenz)
        except ValueError as exc:
            raise ValueError(f"Ungültiges Datum: „{wert}“.") from exc
    m = re.fullmatch(rf"(\d{{1,2}}) ({_MONAT_ALTERNATIVEN})(?: (\d{{4}}))?", t)
    if m:
        try:
            return _datum_bauen(int(m.group(1)), MONATE[m.group(2)],
                                int(m.group(3)) if m.group(3) else None, referenz)
        except ValueError as exc:
            raise ValueError(f"Ungültiges Datum: „{wert}“.") from exc
    raise ValueError(f"Datum nicht verstanden: „{wert}“. Erlaubt sind z. B. 2026-10-09, "
                     "9.10., 9.10.2026 oder 9. Oktober 2026.")


def _passt(token: str, begriff: str) -> bool:
    """Wortvergleich mit einfachen Komposita-Regeln („Wohnungsbauvorhaben“ -> „bauvorhaben“)."""
    if token == begriff:
        return True
    if len(begriff) >= 4 and token.startswith(begriff):
        return True
    if len(begriff) >= 6 and begriff in token:
        return True
    return False


def verstehe(frage: str, stadt: Stadt, gaz: Gazetteer, referenz_datum: date | None = None) -> Verstaendnis:
    v = Verstaendnis(stadt=stadt.schluessel)
    # Bezugsdatum für Angaben ohne Jahr („seit dem 9.10.“) und für relative Angaben
    # („seit gestern“, „letzten 7 Tage“). In Tests fest vorgebbar, sonst heute.
    referenz = referenz_datum or date.today()
    woerter = _WORT.findall(frage)
    s = f" {norm(frage)} "

    # 0) Ortsnamen, die den Stadtnamen enthalten („Münster-Mitte“, „Bonn-Castell“), zuerst
    #    erkennen – sonst entfernt Schritt 1 den Stadtnamen und vom Ort bleibt nur „Mitte“.
    aliase = sorted({norm(a) for a in (stadt.name, *stadt.aliase)} - {""}, key=len, reverse=True)
    gefunden: list[tuple[str, str]] = []
    for phrase in gaz.sortiert:
        if any(f" {a} " in f" {phrase} " for a in aliase) and f" {phrase} " in s:
            gefunden.extend(gaz.phrasen[phrase])
            s = s.replace(f" {phrase} ", " ")

    # 1) Stadt – nur entfernen, damit sie nicht als Freitext zählt.
    stadt_tokens: set[str] = set()
    for alias in aliase:
        if f" {alias} " in s:
            s = s.replace(f" {alias} ", " ")
            stadt_tokens.update(alias.split())

    # 2) PLZ
    m = _PLZ.search(frage)
    if m:
        v.plz = m.group(1)
        s = s.replace(f" {v.plz} ", " ")

    # 3) Zeitbezug
    # 3a) Konkretes Datum – zuerst, damit Tag/Monat/Jahr nicht einzeln als Jahreszahl
    #     oder Stichwort weiterleben: „seit dem 9.10.“, „ab 09.10.2026“,
    #     „seit dem 9. Oktober“, „am 9. Oktober 2026“ (genau dieser Tag).
    datum_m = re.search(
        rf"\b(seit|ab|vom|nach|am) (?:dem |den |der )?(\d{{1,2}}) (\d{{1,2}}|{_MONAT_ALTERNATIVEN})(?: (\d{{4}}))?\b", s)
    datum_teile: tuple[str, str, str, str | None] | None = None
    if datum_m is not None:
        datum_teile = (datum_m.group(1), datum_m.group(2), datum_m.group(3), datum_m.group(4))
    else:
        # Monatsname ohne Präposition („Vorgänge 9. Oktober“) gilt als dieser Tag.
        datum_m = re.search(rf"\b(\d{{1,2}}) ({_MONAT_ALTERNATIVEN})(?: (\d{{4}}))?\b", s)
        if datum_m is not None:
            datum_teile = ("", datum_m.group(1), datum_m.group(2), datum_m.group(3))
    if datum_m is not None and datum_teile is not None:
        praep, tag_wort, monat_wort, jahr_wort = datum_teile
        tag = int(tag_wort)
        monat = int(monat_wort) if monat_wort.isdigit() else MONATE[monat_wort]
        jahr = int(jahr_wort) if jahr_wort else None
        try:
            ziel = _datum_bauen(tag, monat, jahr, referenz)
        except ValueError:
            v.hinweise.append(f"Das Datum „{_datums_angabe(tag, monat_wort, jahr)}“ gibt es nicht "
                              "und wurde ignoriert.")
        else:
            if praep == "am" or (not praep and monat_wort in MONATE):
                v.von = ziel.isoformat()
                v.bis = (ziel + timedelta(days=1)).isoformat()
            else:
                v.von = ziel.isoformat()
                # „alle seit dem 9.10.“ ist eine Stream-Frage: ab dem Datum, neueste zuerst.
                v.neueste = True
            if jahr is None:
                v.hinweise.append(f"„{_datums_angabe(tag, monat_wort, jahr)}“ ohne Jahr wurde als "
                                  f"{_datum_de(ziel)} verstanden (jüngstes Vorkommen dieses Datums).")
        s = s[:datum_m.start()] + " " + s[datum_m.end():]
    else:
        # 3b) Relative Angaben: „seit gestern/heute“, „in den letzten 7 Tagen“,
        #     „seit 2 Wochen“, „letzte Woche“. Sie verlangen den Stream (neueste zuerst).
        relativ_m = re.search(r"\bseit (?:dem |den )?gestern\b", s)
        if relativ_m is not None:
            v.von = (referenz - timedelta(days=1)).isoformat()
            v.neueste = True
            s = s[:relativ_m.start()] + " " + s[relativ_m.end():]
        if v.von is None:
            relativ_m = re.search(r"\bseit (?:dem |den )?heute\b", s)
            if relativ_m is not None:
                v.von = referenz.isoformat()
                v.neueste = True
                s = s[:relativ_m.start()] + " " + s[relativ_m.end():]
        if v.von is None:
            relativ_m = re.search(
                r"\b(?:in den |waehrend der |seit den |seit |den |der )?letzten (\d{1,3}) (tagen|wochen)\b", s)
            tage = None
            if relativ_m is not None:
                tage = int(relativ_m.group(1)) * (7 if relativ_m.group(2) == "wochen" else 1)
            elif re.search(r"\bletzte woche\b", s) is not None:
                relativ_m = re.search(r"\bletzte woche\b", s)
                tage = 7
            if relativ_m is not None and tage is not None:
                # „Letzte 7 Tage“ einschließlich heute: heute minus 6 Tage.
                v.von = (referenz - timedelta(days=max(tage - 1, 0))).isoformat()
                v.neueste = True
                s = s[:relativ_m.start()] + " " + s[relativ_m.end():]
        if v.von is None:
            relativ_m = re.search(r"\bseit (\d{1,3}) (tagen|wochen)\b", s)
            if relativ_m is not None:
                tage = int(relativ_m.group(1)) * (7 if relativ_m.group(2) == "wochen" else 1)
                v.von = (referenz - timedelta(days=tage)).isoformat()
                v.neueste = True
                s = s[:relativ_m.start()] + " " + s[relativ_m.end():]
    # 3c) Jahreszahlen wie bisher („seit 2024“, „2023“ als ganzes Jahr). Ein bereits
    #     gesetztes konkretes Datum hat Vorrang; seine Jahreszahl ist schon entfernt.
    seit = re.search(r"\bseit\b", s) is not None
    jahre = [int(j) for j in _JAHR.findall(s)]
    for j in _JAHR.findall(s):
        s = s.replace(f" {j} ", " ")
    if jahre and v.von is None:
        if len(jahre) == 1 and not seit:
            v.von, v.bis = f"{jahre[0]}-01-01", f"{jahre[0] + 1}-01-01"
        else:
            v.von = f"{min(jahre)}-01-01"
            if not seit and len(jahre) > 1:
                v.bis = f"{max(jahre) + 1}-01-01"

    # 4) Orte (längste Phrasen zuerst; verbrauchte Phrasen werden entfernt)
    for phrase in gaz.sortiert:
        if f" {phrase} " in s and phrase_erlaubt(phrase, woerter):
            gefunden.extend(gaz.phrasen[phrase])
            s = s.replace(f" {phrase} ", " ")
    for typ, nr in dict.fromkeys(gefunden):
        if typ == "stadtteil":
            st = stadt.stadtteile[nr]
            bezirk = stadt.bezirk_fuer(nr)
            v.orte.append({
                "typ": "stadtteil", "schluessel": nr, "name": st.name,
                "bezirk": bezirk, "bezirk_name": stadt.bezirke.get(bezirk or "", ""),
            })
        else:
            v.orte.append({"typ": "bezirk", "schluessel": nr, "name": stadt.bezirke[nr],
                           "anzeige": stadt.bezirk_anzeige(nr)})
    # Name zugleich Stadtteil und Stadtbezirk (z. B. Ehrenfeld): ausdrücklich erklären.
    for typ, nr in dict.fromkeys(gefunden):
        if typ == "bezirk":
            name = stadt.bezirke[nr]
            stadtteil_gleichen_namens = [x for x in gefunden if x[0] == "stadtteil" and stadt.stadtteile[x[1]].name.lower() == name.lower()]
            if stadtteil_gleichen_namens:
                anzahl = sum(1 for st in stadt.stadtteile.values() if st.bezirk == nr)
                v.hinweise.append(
                    f"„{name}“ wird als Stadtteil und als Stadtbezirk gesucht "
                    f"(der Stadtbezirk {name} umfasst {anzahl} Stadtteile)."
                )

    # 4b) Straßen: „Feldgärtenstraße“ oder „Venloer Straße“ in der Frage.
    #
    # Straßen konnten bisher nur gefunden werden, wenn sie im Titel eines Vorgangs standen –
    # wer den Stadtteil nannte, sah den Vorgang dagegen nicht. Umgekehrt ließ sich eine Frage
    # mit Straße gar nicht nach Stadtteilen einordnen. Deshalb wird hier der Straßenname über
    # das Verzeichnis der Stadt aufgelöst. Die Straßenwörter bleiben zusätzlich Suchwörter,
    # damit die Suche eng bleibt; nur wenn dazu nichts gefunden wird, erweitert sie sich.
    # Auf der **Originalfrage** gesucht, damit der Hinweis die Schreibweise der Nutzerin oder
    # des Nutzers zeigt („Feldgärtenstraße“) und nicht die Vergleichsform.
    for strasse, genannt in gaz.strassen_im_text(frage):
        nr = strasse.stadtteil
        if nr is not None:
            if all(o.get("schluessel") != nr or o["typ"] != "stadtteil" for o in v.orte):
                bezirk = stadt.bezirk_fuer(nr)
                v.orte.append({"typ": "stadtteil", "schluessel": nr, "name": stadt.stadtteile[nr].name,
                               "bezirk": bezirk, "bezirk_name": stadt.bezirke.get(bezirk or "", ""),
                               "herkunft": "strasse"})
            v.hinweise.append(f"„{genannt}“ ist laut Straßenverzeichnis dem Stadtteil "
                              f"{stadt.stadtteile[nr].name} zugeordnet (Näherung).")
        else:
            namen = ", ".join(stadt.stadtteile[k].name for k in strasse.kandidaten if k in stadt.stadtteile)
            for k in strasse.kandidaten:
                if k in stadt.stadtteile and all(o.get("schluessel") != k or o["typ"] != "stadtteil" for o in v.orte):
                    v.orte.append({"typ": "stadtteil", "schluessel": k, "name": stadt.stadtteile[k].name,
                                   "bezirk": stadt.bezirk_fuer(k),
                                   "bezirk_name": stadt.bezirke.get(stadt.bezirk_fuer(k) or "", ""),
                                   "herkunft": "strasse"})
            v.hinweise.append(f"„{genannt}“ kommt in mehreren Stadtteilen vor ({namen}); "
                              "gesucht wird in allen, das Straßenwort bleibt Teil der Suche.")

    # 5) Themen
    tokens = s.split()
    verbraucht: set[str] = set()
    for thema in stadt.themen:
        for token in tokens:
            if any(_passt(token, b) for b in thema.begriffe):
                if thema.schluessel not in v.themen:
                    v.themen.append(thema.schluessel)
                verbraucht.add(token)

    # 6) Absicht „geplant / offen / laufend“
    if any(t in OFFEN_WOERTER for t in tokens):
        v.offen = True

    # 6b) Stream-Absicht: „die neuesten/neuen Vorgänge“ heißt chronologisch sortieren,
    #     nicht nach dem Wort „neu“ im Titel suchen.
    if any(t in NEU_WOERTER for t in tokens):
        v.neueste = True

    # 7) Freitext: übrig gebliebene inhaltliche Wörter
    for t in tokens:
        if t in verbraucht or t in OFFEN_WOERTER or t in STOPWORTE or t in stadt_tokens or t in NEU_WOERTER:
            continue
        if len(t) < 3 or t.isdigit() or t in v.freitext:
            continue
        v.freitext.append(t)

    if v.neueste:
        v.hinweise.append("Stream: Die Treffer werden nach dem Datum des Vorgangs sortiert "
                          "(neueste zuerst) – nicht nach dem Zeitpunkt des lokalen Abrufs.")
    if not (v.orte or v.plz):
        v.hinweise.append("Keine Ortsangabe erkannt: Die Suche umfasst alle Stadtteile und Bezirke der Stadt.")
    return v
