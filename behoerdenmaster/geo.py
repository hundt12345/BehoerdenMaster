"""Ortsbezug aus Texten: Stadtteile, Stadtbezirke und PLZ anhand der Stadtkonfiguration.

Die Ratsinformation der Stadt Köln liefert (Stand Prüfung) keinen strukturierten
Ortsbezug an Vorlagen. Der Ortsbezug wird deshalb aus Betreff und Gremienname
abgeleitet. Das ist eine Heuristik; jede Zuordnung trägt ihre Quelle
(betreff, gremium, oparl), damit man sie in der Oberfläche nachvollziehen kann.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .config import Stadt, Strasse
from .normalize import norm, strassen_kanonisch

# Ein- oder zweistellig: Düsseldorf hat zehn Stadtbezirke („Bezirksvertretung 10“).
_BEZIRK_NUMMER = re.compile(r"\bbezirk(?:svertretung)?\s*(\d{1,2})\b", re.IGNORECASE)
_PLZ = re.compile(r"(?<!\d)(\d{5})(?!\d)")
_WORT = re.compile(r"\w+")
_WORT_ZEICHEN = re.compile(r"\S+")

# Ein Straßenname wird nur ausgewertet, wenn er unterscheidbar genug ist: mehrere Wörter
# oder ein einzelnes Wort mit mindestens sechs Zeichen. Sehr kurze Namen („Markt“,
# „Flora“) sind als Ganzes zu alltäglich, um daraus einen Stadtteil abzuleiten.
_STRASSE_MINDESTLAENGE = 6


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
            for alias in stadt.bezirk_aliase.get(nr, ()):
                self._ergaenze(alias, "bezirk", nr)
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

        Ein genannter Stadtteil liefert immer auch seinen Stadtbezirk. Zusätzlich werden
        **eindeutige Straßennamen** aus dem Straßenverzeichnis aufgelöst („Feldgärtenstraße“
        → Stadtteil Niehl): Nur so findet eine Suche nach dem Stadtteil auch Vorgänge, die
        im Betreff bloß die Straße nennen. Mehrdeutige Namen (derselbe Name in mehreren
        Stadtteilen) liefern bewusst **keinen** Ortsbezug – geraten wird nicht.

        Mit ``nur_bezirk`` (für Gremiennamen) werden Stadtteil- und Straßennamen ignoriert,
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

        if not nur_bezirk:
            for strasse, _ in self.strassen_im_text(text):
                nr = strasse.stadtteil
                if nr is None:
                    continue
                # Abgeleitete Orte tragen die eigene Herkunft „strasse“ (Näherung), damit die
                # Oberfläche sie von einer Angabe der Quelle unterscheiden kann. Nennt der Text
                # den Ort ohnehin, bleibt es bei der genaueren Herkunft.
                vorhanden = {(o.typ, o.schluessel) for o in treffer}
                bezirk = self.stadt.bezirk_fuer(nr)
                if ("stadtteil", nr) not in vorhanden:
                    treffer.add(OrtsBezug("stadtteil", nr, "strasse"))
                if bezirk and ("bezirk", bezirk) not in vorhanden:
                    treffer.add(OrtsBezug("bezirk", bezirk, "strasse"))

        for m in _BEZIRK_NUMMER.finditer(text):
            if m.group(1) in self.stadt.bezirke:
                treffer.add(OrtsBezug("bezirk", m.group(1), quelle))

        if not nur_bezirk:
            for m in _PLZ.finditer(text):
                if m.group(1) in self.stadt.plz:
                    treffer.add(OrtsBezug("plz", m.group(1), quelle))
        return treffer

    def strassen_im_text(self, text: str | None) -> list[tuple[Strasse, str]]:
        """Straßenverzeichnis-Einträge im Text, mit der Schreibweise des Textes.

        Gesucht wird über die kanonisierte Form jedes Wortes; dadurch trifft „Feldgärtenstr.“
        denselben Eintrag wie „Feldgärtenstraße“. Der zweite Wert ist der Textausschnitt in
        der Schreibweise der Eingabe – Hinweise können so „Feldgärtenstraße“ zeigen statt der
        Vergleichsform „feldgaertenstrasse“. Ein einmal verbrauchter Name wird nicht erneut
        geprüft, damit „Am Alten Turm“ nicht zusätzlich „Alten Turm“ auslöst.
        """
        if not text or not self.stadt.strassen:
            return []
        roh = _WORT_ZEICHEN.findall(text)
        vergleich: list[str] = []
        herkunft: list[int] = []
        for stelle, wort in enumerate(roh):
            for teil in strassen_kanonisch(wort).split():
                vergleich.append(teil)
                herkunft.append(stelle)

        max_gram = max(1, self.stadt.strassen_max_gram)
        treffer: list[tuple[Strasse, str]] = []
        i = 0
        while i < len(vergleich):
            gefunden = None
            for laenge in range(min(max_gram, len(vergleich) - i), 0, -1):
                name = " ".join(vergleich[i:i + laenge])
                if len(name.split()) < 2 and len(name) < _STRASSE_MINDESTLAENGE:
                    continue
                eintrag = self.stadt.strassen.get(name)
                if eintrag is not None:
                    gefunden = (eintrag, laenge)
                    break
            if gefunden is None:
                i += 1
                continue
            von, bis = herkunft[i], herkunft[i + gefunden[1] - 1]
            treffer.append((gefunden[0], " ".join(roh[von:bis + 1]).strip(" .,;:!?()[]")))
            i += gefunden[1]
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
