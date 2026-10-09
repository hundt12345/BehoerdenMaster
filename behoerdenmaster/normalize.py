"""Normalisierung deutscher Texte für Textabgleich und Suche.

Umlaute werden ausgeschrieben (ä -> ae, ß -> ss), Akzente entfernt, alles
kleingeschrieben und Satzzeichen durch Leerzeichen ersetzt. So finden sich
"Bocklemünd/Mengenich", "Bocklemuend Mengenich" und "Köln-Ehrenfeld" gleichermaßen.
"""

from __future__ import annotations

import re
import unicodedata

_ERSETZUNGEN = {
    "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
    "Ä": "ae", "Ö": "oe", "Ü": "ue",
}
_NICHT_ALNUM = re.compile(r"[^0-9a-z]+")


def norm(text: str | None) -> str:
    """Gibt den normalisierten Text zurück (leer bei None)."""
    if not text:
        return ""
    t = text
    for alt, neu in _ERSETZUNGEN.items():
        t = t.replace(alt, neu)
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = _NICHT_ALNUM.sub(" ", t.lower())
    return " ".join(t.split())
