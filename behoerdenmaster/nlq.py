"""Regelbasiertes Verstehen von Fragen wie „Welche Bauvorhaben sind in Köln Ehrenfeld geplant?“.

Erkannt werden: Stadt, Ortsangaben (Stadtteil und Stadtbezirk), Themen aus der
Stadtkonfiguration, PLZ, Jahres- bzw. Zeitbezug und die Absicht „geplant/offen“.
Übrig gebliebene inhaltliche Wörter dienen als Freitextsuche. Es gibt bewusst
kein Sprachmodell: die Auslegung bleibt nachvollziehbar und getestet.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from .config import Stadt
from .geo import Gazetteer, phrase_erlaubt
from .normalize import norm

_WORT = re.compile(r"\w+")
_PLZ = re.compile(r"(?<!\d)(\d{5})(?!\d)")
_JAHR = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")

STOPWORTE = {
    "welche", "welcher", "welches", "welchen", "welchem", "wer", "wie", "was", "wo", "wann",
    "gibt", "gibts", "es", "sind", "ist", "war", "waren", "wird", "werden", "wurde", "wurden",
    "hat", "haben", "habe", "bei", "der", "die", "das", "den", "dem", "des", "ein", "eine",
    "einen", "einem", "einer", "und", "oder", "in", "im", "am", "an", "auf", "aus", "mit",
    "fuer", "von", "vom", "zu", "zum", "zur", "nach", "seit", "bis", "ueber", "unter", "ob",
    "alle", "alles", "allen", "mir", "bitte", "zeige", "zeig", "zeigen", "gibt's", "stadt",
    "vorgaenge", "vorgang", "vorlagen", "vorlage", "sich", "noch", "auch", "dazu", "dort",
    "hier", "koennen", "kann", "soll", "sollen", "geben", "liste", "uebersicht",
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
    hinweise: list[str] = field(default_factory=list)

    def als_dict(self) -> dict:
        return asdict(self)


def _passt(token: str, begriff: str) -> bool:
    """Wortvergleich mit einfachen Komposita-Regeln („Wohnungsbauvorhaben“ -> „bauvorhaben“)."""
    if token == begriff:
        return True
    if len(begriff) >= 4 and token.startswith(begriff):
        return True
    if len(begriff) >= 6 and begriff in token:
        return True
    return False


def verstehe(frage: str, stadt: Stadt, gaz: Gazetteer) -> Verstaendnis:
    v = Verstaendnis(stadt=stadt.schluessel)
    woerter = _WORT.findall(frage)
    s = f" {norm(frage)} "

    # 1) Stadt – nur entfernen, damit sie nicht als Freitext zählt.
    stadt_tokens: set[str] = set()
    for alias in sorted({norm(a) for a in (stadt.name, *stadt.aliase)} - {""}, key=len, reverse=True):
        if f" {alias} " in s:
            s = s.replace(f" {alias} ", " ")
            stadt_tokens.update(alias.split())

    # 2) PLZ
    m = _PLZ.search(frage)
    if m:
        v.plz = m.group(1)
        s = s.replace(f" {v.plz} ", " ")

    # 3) Zeitbezug
    seit = re.search(r"\bseit\b", s) is not None
    jahre = [int(j) for j in _JAHR.findall(s)]
    for j in _JAHR.findall(s):
        s = s.replace(f" {j} ", " ")
    if jahre:
        if len(jahre) == 1 and not seit:
            v.von, v.bis = f"{jahre[0]}-01-01", f"{jahre[0] + 1}-01-01"
        else:
            v.von = f"{min(jahre)}-01-01"
            if not seit and len(jahre) > 1:
                v.bis = f"{max(jahre) + 1}-01-01"

    # 4) Orte (längste Phrasen zuerst; verbrauchte Phrasen werden entfernt)
    gefunden: list[tuple[str, str]] = []
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
            v.orte.append({"typ": "bezirk", "schluessel": nr, "name": stadt.bezirke[nr]})
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

    # 7) Freitext: übrig gebliebene inhaltliche Wörter
    for t in tokens:
        if t in verbraucht or t in OFFEN_WOERTER or t in STOPWORTE or t in stadt_tokens:
            continue
        if len(t) < 3 or t.isdigit() or t in v.freitext:
            continue
        v.freitext.append(t)

    if not (v.orte or v.plz):
        v.hinweise.append("Keine Ortsangabe erkannt: Die Suche umfasst alle Stadtteile und Bezirke der Stadt.")
    return v
