"""Redaktionell gepflegte Zeitungsverweise zu Vorgängen.

Zeitungsartikel sind urheberrechtlich geschützt und stehen teils hinter
Bezahlschranken. BehoerdenMaster ruft sie deshalb **nicht** ab und speichert
keinen Artikeltext (siehe docs/quellen-koeln.md, „Was hier bewusst fehlt“).
Angezeigt werden nur Verweise: Medium, Schlagzeile, Datum und URL – gepflegt in
`config/cities/<stadt>.toml` unter `[[presse]]` (z. B. Kölner Stadt-Anzeiger).

Zuordnung zu Vorgängen: Jeder Verweis nennt Stichworte und/oder Referenzen.
Er passt zu einem Vorgang, wenn seine Referenz der Vorgangsreferenz entspricht
oder **alle** seine Stichworte im gespeicherten Suchtext des Vorgangs vorkommen
(normalisierter Teilwortvergleich wie in der Suche). Die Auswahl ist redaktionell
gesteuert: Stichworte bewusst distinktiv wählen, damit nicht jeder Wohnungsbauvorgang
denselben Artikel erbt.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .auswertung import datum_de
from .normalize import norm


@dataclass(frozen=True)
class PresseVerweis:
    """Ein einzelner Artikelverweis – nur Metadaten, nie ein Artikeltext."""

    medium: str
    titel: str
    url: str
    datum: str = ""  # YYYY-MM-DD, optional
    stichworte: tuple[str, ...] = ()  # normalisiert
    referenzen: tuple[str, ...] = ()  # normalisiert

    def passt(self, suchtext_norm: str, referenz_norm: str = "") -> bool:
        """True, wenn Referenz oder alle Stichworte zum Vorgang passen."""
        if referenz_norm and any(r == referenz_norm for r in self.referenzen):
            return True
        return bool(self.stichworte) and all(s in suchtext_norm for s in self.stichworte)

    def als_dict(self) -> dict:
        return {
            "medium": self.medium,
            "titel": self.titel,
            "url": self.url,
            "datum": self.datum,
            "datum_de": datum_de(self.datum),
        }


def finde_presse(presse: Sequence[PresseVerweis], suchtext: str, referenz: str = "") -> list[dict]:
    """Alle passenden Verweise eines Vorgangs, in der Reihenfolge der Konfiguration."""
    text = norm(suchtext)
    ref = norm(referenz)
    return [v.als_dict() for v in presse if v.passt(text, ref)]
