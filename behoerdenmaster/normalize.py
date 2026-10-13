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


def strassen_kanonisch(text: str | None) -> str:
    """Wie :func:`norm`, löst aber Straßenkürzel auf („…str“ → „…strasse“).

    Wird auf **beide Seiten** angewendet: auf die Namen im Straßenverzeichnis und auf den
    Text, in dem eine Straße gesucht wird. So trifft „Feldgärtenstr.“ im Betreff einer
    Vorlage den Eintrag „feldgaertenstrasse“ im Verzeichnis. Nur echte Kürzel werden
    ersetzt: „str“ allein oder als Wortende mit genug Rumpf davor („adlerstr“ →
    „adlerstrasse“); kurze Wörter wie „Rat“ bleiben unberührt.
    """
    teile = []
    for wort in norm(text).split():
        if wort == "str":
            wort = "strasse"
        elif len(wort) >= 5 and wort.endswith("str"):
            wort = wort[:-3] + "strasse"
        teile.append(wort)
    return " ".join(teile)
