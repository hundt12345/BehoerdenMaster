"""Ortsbezug des gespeicherten Bestands neu berechnen – ohne die Stadt abzurufen.

Der Ortsbezug entsteht beim Abruf aus den Texten der Quelle (Betreff, Beschreibung,
Gremienname und – neu – aus Straßennamen des Straßenverzeichnisses). Verbessert sich die
Auswertung oder kommt ein Straßenverzeichnis hinzu, wäre ein vollständiger Neuabruf die
einzige Möglichkeit, den Bestand zu aktualisieren – und das würde die Behörden-Server
unnötig belasten. Deshalb rechnet dieser Schritt den Ortsbezug aus den **bereits
gespeicherten** Texten nach.

Sicher für den Bestand:

* Nur Herkünfte, die sich aus den gespeicherten Texten wiederherstellen lassen
  (``betreff``, ``beschreibung``, ``strasse``), werden ersetzt. Aus der Schnittstelle
  übernommene Angaben (``oparl``) und Gremiennamen (``gremium``) bleiben unangetastet –
  sie stehen nicht in den gespeicherten Texten.
* Es wird nichts gelöscht und kein Objekt entfernt; nur der Ortsbezug wird aktualisiert.
* Ohne ``schreiben`` läuft der Schritt als Prüflauf und ändert nichts.
"""

from __future__ import annotations

import logging

from .config import Stadt
from .geo import Gazetteer
from .speicher import Speicher

log = logging.getLogger("behoerdenmaster.ortsbezug")

# Herkünfte, die dieser Schritt aus den gespeicherten Texten neu bilden kann.
ERSETZBARE_QUELLEN = ("betreff", "beschreibung", "strasse")


def _orte(gaz: Gazetteer, *texte_mit_quelle: tuple[str, str]) -> set[tuple[str, str, str]]:
    treffer = set()
    for text, quelle in texte_mit_quelle:
        if not text:
            continue
        treffer |= {(o.typ, o.schluessel, o.quelle) for o in gaz.ortsbezuege_aus_text(text, quelle)}
    return treffer


def aktualisiere(speicher: Speicher, stadt: Stadt, *, quelle_id: str | None = None,
                 schreiben: bool = False) -> dict[str, int]:
    """Ortsbezug aller Vorgänge der Stadt aus den gespeicherten Texten neu berechnen."""
    gaz = Gazetteer(stadt)
    bedingung = "v.geloescht = 0 AND v.quelle_id IN (SELECT id FROM quelle WHERE stadt = ?)"
    params: list = [stadt.schluessel]
    if quelle_id:
        bedingung += " AND v.quelle_id = ?"
        params.append(quelle_id)
    zeilen = speicher.db.execute(
        f"""
        SELECT v.id AS id, v.titel AS titel, v.art AS art, v.quelle_id AS quelle_id,
               m.kurztext AS kurztext, m.art_quelle AS art_quelle, m.verfahrensschritt AS schritt,
               p.beschreibung AS plan_beschreibung
        FROM vorgang v
        LEFT JOIN meldung m ON m.vorgang_id = v.id
        LEFT JOIN planverfahren p ON p.vorgang_id = v.id
        WHERE {bedingung}
        """,
        params,
    ).fetchall()

    zaehler = {"geprueft": 0, "aktualisiert": 0, "hinzugefuegt": 0, "strasse": 0}
    for zeile in zeilen:
        zaehler["geprueft"] += 1
        vid = zeile["id"]
        orte = _orte(gaz, (zeile["titel"], "betreff"),
                     (zeile["kurztext"], "betreff"),
                     (zeile["art_quelle"], "betreff"),
                     (zeile["schritt"], "betreff"),
                     (zeile["plan_beschreibung"], "beschreibung"))

        vorher = {(r["typ"], r["schluessel"], r["quelle"]) for r in speicher.db.execute(
            f"SELECT typ, schluessel, quelle FROM ortsbezug WHERE vorgang_id = ? AND quelle IN "
            f"({','.join('?' * len(ERSETZBARE_QUELLEN))})", (vid, *ERSETZBARE_QUELLEN)).fetchall()}
        neu = orte - vorher
        if orte == vorher:
            continue
        zaehler["aktualisiert"] += 1
        zaehler["hinzugefuegt"] += len(neu)
        zaehler["strasse"] += sum(1 for _, _, quelle in neu if quelle == "strasse")
        if schreiben:
            speicher.ortsbezug_ersetzen(vid, orte, ERSETZBARE_QUELLEN)
    log.info("%s: %s", stadt.name, zaehler)
    return zaehler
