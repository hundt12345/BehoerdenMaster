"""Ortsbezug aus Texten: Stadtteile, Stadtbezirke und PLZ anhand der Stadtkonfiguration.

Die Ratsinformation der Stadt Köln liefert (Stand Prüfung) keinen strukturierten
Ortsbezug an Vorlagen. Der Ortsbezug wird deshalb aus Betreff und Gremienname
abgeleitet. Das ist eine Heuristik; jede Zuordnung trägt ihre Quelle
(betreff, gremium, oparl), damit man sie in der Oberfläche nachvollziehen kann.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .config import Stadt
from .normalize import norm

_BEZIRK_NUMMER = re.compile(r"\bbezirk(?:svertretung)?\s*(\d)\b", re.IGNORECASE)
_PLZ = re.compile(r"(?<!\d)(\d{5})(?!\d)")
_WORT = re.compile(r"\w+")


@dataclass(frozen=True, order=True)
class OrtsBezug:
    typ: str  # stadtteil | bezirk | plz
    schluessel: str
    quelle: str  # betreff | gremium | oparl


def phrase_erlaubt(phrase: str, woerter: list[str]) -> bool:
    """Kurze Namen (z. B. Eil, Lind, Poll, Porz) nur mit großem Anfangsbuchstaben werten.

    Verhindert Fehltreffer bei alltäglichen Wörtern wie „eil“ oder „wahn“.
    """
    if " " in phrase or len(phrase) > 4:
        return True
    return any(norm(w) == phrase and w[:1].isupper() for w in woerter)


class Gazetteer:
    """Wörterbuch aus Bezirks- und Stadtteilnamen (inkl. Aliase) einer Stadt."""

    def __init__(self, stadt: Stadt):
        self.stadt = stadt
        self.phrasen: dict[str, list[tuple[str, str]]] = {}
        for nr, name in stadt.bezirke.items():
            self._ergaenze(name, "bezirk", nr)
        for nr, st in stadt.stadtteile.items():
            self._ergaenze(st.name, "stadtteil", nr)
            for alias in st.aliase:
                self._ergaenze(alias, "stadtteil", nr)
        # Längste Phrasen zuerst, damit „Neuehrenfeld“ vor „Ehrenfeld“ greift.
        self.sortiert: list[str] = sorted(self.phrasen, key=lambda p: (-len(p), p))

    def _ergaenze(self, text: str, typ: str, nr: str) -> None:
        key = norm(text)
        if not key:
            return
        eintraege = self.phrasen.setdefault(key, [])
        if (typ, nr) not in eintraege:
            eintraege.append((typ, nr))

    def ortsbezuege_aus_text(self, text: str | None, quelle: str = "betreff", nur_bezirk: bool = False) -> set[OrtsBezug]:
        """Alle Orte, die in einem Freitext genannt werden.

        Ein genannter Stadtteil liefert immer auch seinen Stadtbezirk.
        Mit ``nur_bezirk`` (für Gremiennamen) werden Stadtteilnamen ignoriert,
        weil „Bezirksvertretung 4 (Ehrenfeld)“ den ganzen Bezirk meint.
        """
        if not text:
            return set()
        padded = f" {norm(text)} "
        woerter = _WORT.findall(text)
        treffer: set[OrtsBezug] = set()

        for phrase in self.sortiert:
            if f" {phrase} " not in padded or not phrase_erlaubt(phrase, woerter):
                continue
            for typ, nr in self.phrasen[phrase]:
                if typ == "stadtteil":
                    if nur_bezirk:
                        continue
                    treffer.add(OrtsBezug("stadtteil", nr, quelle))
                    bezirk = self.stadt.bezirk_fuer(nr)
                    if bezirk:
                        treffer.add(OrtsBezug("bezirk", bezirk, quelle))
                else:
                    treffer.add(OrtsBezug("bezirk", nr, quelle))

        for m in _BEZIRK_NUMMER.finditer(text):
            if m.group(1) in self.stadt.bezirke:
                treffer.add(OrtsBezug("bezirk", m.group(1), quelle))

        if not nur_bezirk:
            for m in _PLZ.finditer(text):
                if m.group(1) in self.stadt.plz:
                    treffer.add(OrtsBezug("plz", m.group(1), quelle))
        return treffer

    def stadtteile_fuer_plz(self, plz: str) -> tuple[str, ...]:
        eintrag = self.stadt.plz.get(plz)
        return eintrag.stadtteile if eintrag else ()

    def stadtteil_nach_name(self, name: str) -> str | None:
        """Stadtteil-Nummer für einen Namen aus Datenquellen (z. B. OParl subLocality)."""
        key = norm(name)
        for typ, nr in self.phrasen.get(key, []):
            if typ == "stadtteil":
                return nr
        return None
