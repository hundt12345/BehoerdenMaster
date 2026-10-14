"""Chronologischer Stream: die neuesten Vorgänge einer Stadt, optional ab einem Datum.

Der Stream ist eine **Lese-Sicht auf die lokale Datenbank** – er löst wie jede Suche
keinen Behördenabruf aus. Sortiert wird nach dem **Datum des Vorgangs** (bei
Ratsvorlagen das Vorlagendatum, bei Bauleitverfahren der Beginn der Beteiligung, bei
Meldungen ihr Listendatum), neueste zuerst. Der Zeitpunkt, zu dem ein Vorgang lokal
abgerufen oder in der Quelle zuletzt geändert wurde, ist dafür bewusst **nicht**
maßgeblich: Ein Abrufdatum ist kein amtliches Datum, und die Quellen liefern kein
einheitliches, verlässliches „neu eingestellt am“.

Es gibt bewusst kein serverseitiges Pushen (kein WebSocket/SSE): Der Bestand ändert
sich nur durch die gedrosselten Hintergrund- bzw. CLI-Abrufe. Ein Stream, der per
Polling oder „Weitere Treffer laden“ blättert, zeigt deshalb denselben Stand wie
die Suche – ohne eine dauerhafte Verbindung pro Besucher zu halten.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from .auswertung import datum_de
from .config import Stadt
from .nlq import parse_datum
from .speicher import Speicher
from .suche import Suchauftrag, suche

__all__ = ["parse_datum", "hole_stream", "SORTIERUNG_TEXT"]

SORTIERUNG_TEXT = ("Neueste zuerst – sortiert nach dem Datum des Vorgangs "
                   "(Vorlagendatum bzw. Beginn der Beteiligung), nicht nach dem Abrufzeitpunkt.")


def hole_stream(speicher: Speicher, stadt: Stadt, referenz: date, *,
                von: date | None = None, bis: date | None = None,
                themen: list[str] | None = None, freitext: list[str] | None = None,
                limit: int = 20, offset: int = 0,
                zeitpunkt: datetime | None = None) -> dict:
    """Neuesten Vorgänge der Stadt lesen – nur lokal, ohne Abruf.

    ``von``/``bis`` sind Kalendertage (``bis`` einschließlich) und filtern auf das
    Datum des Vorgangs. Themen und Stichworte verengen wie in der Suche; Ort und
    PLZ laufen bewusst über Frage-/PLZ-Suche, der Stream bleibt der Zeit-Überblick.
    Das Ergebnis trägt neben den Treffern (dieselben Objekte wie die Suche) die
    Stream-Metadaten samt Blätterangabe (``naechster_offset``/``hat_mehr``).
    """
    auftrag = Suchauftrag(
        stadt=stadt,
        themen=list(themen or []),
        freitext=list(freitext or []),
        von=von.isoformat() if von else None,
        # Die Suche behandelt `bis` als exklusive Grenze; ein Stream-`bis` meint
        # den Kalendertag einschließlich, also einen Tag weitergeben.
        bis=(bis + timedelta(days=1)).isoformat() if bis else None,
        neueste=True,
        limit=limit,
        offset=offset,
    )
    ergebnis = suche(speicher, auftrag, referenz, zeitpunkt=zeitpunkt)
    gezeigt = offset + len(ergebnis["treffer"])
    naechster = gezeigt if gezeigt < ergebnis["gesamt"] else None
    return {
        "sortierung": "vorgangsdatum_absteigend",
        "sortierung_text": SORTIERUNG_TEXT,
        "seit": von.isoformat() if von else None,
        "seit_de": datum_de(von.isoformat()) if von else None,
        "bis": bis.isoformat() if bis else None,
        "bis_de": datum_de(bis.isoformat()) if bis else None,
        "gesamt": ergebnis["gesamt"],
        "treffer": ergebnis["treffer"],
        "kandidaten_gekappt": ergebnis["kandidaten_gekappt"],
        "freitext_verworfen": ergebnis.get("freitext_verworfen", False),
        "freitext_oder": ergebnis.get("freitext_oder", False),
        "limit": limit,
        "offset": offset,
        "naechster_offset": naechster,
        "hat_mehr": naechster is not None,
    }
