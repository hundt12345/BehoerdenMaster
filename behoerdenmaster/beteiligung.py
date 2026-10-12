"""Bürgerbeteiligung „Meinung für Köln“: öffentliche Beteiligungsangebote der Stadt.

Was diese Quelle beiträgt: Vorhaben und Angebote, zu denen die Stadt Köln die
Öffentlichkeit beteiligt – auch dann, wenn dazu (noch) kein Bauleitverfahren im
Bestand ist. Übernommen werden **Projekttitel, Statuszeile der Plattform, Kurztext
und Link**.

Bewusst **nicht** übernommen werden: Beiträge und Kommentare Dritter, Namen, Fotos,
Anhänge und Meinungen. Es wird nur die öffentliche Projektliste gelesen – ein Abruf
je Lauf; Detailseiten werden nicht verfolgt, weil die Liste die Statuszeile bereits
enthält.

Die Plattform nennt in der Liste keinen Beteiligungszeitraum, sondern nur eine
Statuszeile (z. B. „Online-Beteiligung seit 10 Tagen geschlossen“). Deshalb wird
kein Zeitraum erfunden: Der Wortlaut steht als Status und mit dem Datum des letzten
Abrufs in der Oberfläche, Fristen und Unterlagen stehen auf der Projektseite.
"""

from __future__ import annotations

import logging

from .abruf import Abrufer, BudgetErschoepft, NichtErlaubt, ObjektNichtVorhanden, QuelleGesperrt, QuelleNichtErreichbar
from .config import Quelle, Stadt
from .crawler import LaufBericht, LaufOptionen, merke_server_pause, pruefe_abkuehlung
from .geo import Gazetteer
from .meldungen import MeldungEintrag, ortsbezuege, parse_beteiligungen
from .speicher import Speicher, jetzt_iso

log = logging.getLogger("behoerdenmaster.beteiligung")


def _speichere(sp: Speicher, gaz: Gazetteer, quelle_id: str, eintrag: MeldungEintrag,
               bericht: LaufBericht, gesehen: str) -> None:
    orte = ortsbezuege(gaz, f"{eintrag.titel} {eintrag.kurztext}")
    neu = sp.meldung_speichern(
        eintrag.vorgang_zeile(quelle_id), eintrag.meldung_zeile(gesehen), orte, dokumente=[],
    )
    bericht.zaehle("beteiligungen:neu" if neu else "beteiligungen:aktualisiert")


def crawle_beteiligung(speicher: Speicher, stadt: Stadt, quelle: Quelle, abrufer: Abrufer,
                       optionen: LaufOptionen) -> LaufBericht:
    """Die öffentliche Projektliste einmal lesen und speichern (ein Abruf je Lauf)."""
    quelle_id = stadt.quell_id(quelle)
    gaz = Gazetteer(stadt)
    speicher.quelle_anlegen(quelle_id, stadt.schluessel, quelle.schluessel, quelle.typ,
                            quelle.name, quelle.system_url, demo=False)
    pruefe_abkuehlung(speicher, quelle_id, optionen)

    bericht = LaufBericht(quelle_id=quelle_id)
    lauf_id = speicher.lauf_beginnen(quelle_id)
    speicher.quelle_status(quelle_id, "laeuft")
    try:
        html = abrufer.get_text(quelle.system_url)
        gesehen = jetzt_iso()
        eintraege, verworfen = parse_beteiligungen(html, quelle.system_url)
        if not eintraege:
            # Ein unbekanntes Listenformat wird nicht als „alles geschlossen“ gedeutet.
            raise ValueError(
                f"Keine Projektkarte auf {quelle.system_url} erkannt; der Bestand bleibt unverändert"
            )
        for eintrag in eintraege:
            _speichere(speicher, gaz, quelle_id, eintrag, bericht, gesehen)
        for _ in range(verworfen):
            bericht.zaehle("beteiligungen:verworfen")
        speicher.stand_schreiben(quelle_id, "projekte", "", "", "")
        bericht.status = "ok"
        log.info("%s: %d Beteiligungsangebote übernommen, %d verworfen",
                 quelle_id, len(eintraege), verworfen)
    except BudgetErschoepft as exc:
        bericht.status, bericht.fehlertext = "teilweise", str(exc)
    except (QuelleGesperrt, NichtErlaubt) as exc:
        merke_server_pause(bericht, exc)
        bericht.status, bericht.fehlertext = "gesperrt", str(exc)
    except (QuelleNichtErreichbar, ObjektNichtVorhanden) as exc:
        merke_server_pause(bericht, exc)
        bericht.status, bericht.fehlertext = "nicht erreichbar", str(exc)
    except ValueError as exc:
        bericht.status, bericht.fehlertext = "fehler", str(exc)
    except KeyboardInterrupt:
        bericht.status, bericht.fehlertext = "teilweise", "Manuell abgebrochen; der nächste Lauf setzt fort"
        raise
    except Exception as exc:
        bericht.status, bericht.fehlertext = "fehler", f"Unerwarteter Abruffehler ({type(exc).__name__})"
        raise
    finally:
        bericht.anfragen = abrufer.anfragen
        speicher.lauf_beenden(lauf_id, bericht.status, bericht.anfragen, bericht.zaehler, bericht.fehlertext)
        speicher.quelle_status(quelle_id, bericht.status, bericht.fehlertext, erfolg=bericht.status == "ok",
                               abkuehlung_bis=bericht.abkuehlung_bis)
    return bericht
