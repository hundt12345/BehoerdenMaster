"""Crawl-Lauf: lädt Gremien, Sitzungen und Vorlagen einer Quelle in die lokale Datenbank.

Verhalten:
* Erster Lauf: Objekte seit dem konfigurierten Datum (modified_since).
* Folgeläufe: nur Änderungen seit dem letzten vollständigen Lauf (mit 1 h Überlappung).
* Unterbrochene Läufe (Anfragebudget, Sperre, Netzfehler) setzen beim nächsten Mal
  an derselben Stelle fort.
* Bei Sperre (401/403/429 trotz Wartezeit) oder robots.txt-Verbot bricht der Lauf
  sofort ab. Es gibt keinen Umgehungsversuch.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .abruf import Abrufer, BudgetErschoepft, NichtErlaubt, QuelleGesperrt, QuelleNichtErreichbar
from .config import Quelle, Stadt
from .geo import Gazetteer, OrtsBezug
from .normalize import norm
from .oparl import OParlQuelle, map_gremium, map_sitzung, map_vorgang
from .speicher import Speicher, jetzt_iso

log = logging.getLogger("behoerdenmaster.crawler")

ENTITAETEN = ("gremien", "sitzungen", "vorlagen")
PUFFER = timedelta(hours=1)
SPERR_ABKUEHLZEIT_BASIS = timedelta(minutes=10)
SPERR_ABKUEHLZEIT_MAX = timedelta(hours=6)


class CrawlVerweigert(Exception):
    """Der Lauf wird nicht gestartet (z. B. kurz nach einer Sperre)."""


@dataclass
class LaufOptionen:
    seit: str | None = None  # YYYY-MM-DD, nur für den ersten Lauf
    max_anfragen: int | None = None
    entitaeten: tuple[str, ...] = ENTITAETEN
    trotz_sperre: bool = False


@dataclass
class LaufBericht:
    quelle_id: str
    status: str = "laeuft"
    anfragen: int = 0
    zaehler: dict[str, int] = field(default_factory=dict)
    fehlertext: str = ""

    def zaehle(self, schluessel: str) -> None:
        self.zaehler[schluessel] = self.zaehler.get(schluessel, 0) + 1


def _modified_seit_iso(datum: str) -> str:
    """YYYY-MM-DD -> Mitternacht in Europe/Berlin (Winterzeit, UTC+1) als ISO-Zeitstempel."""
    return f"{datum}T00:00:00+01:00"


def _minus_puffer(iso: str) -> str:
    return (datetime.fromisoformat(iso) - PUFFER).isoformat(timespec="seconds")


def ortsbezuege(gaz: Gazetteer, titel: str, gremiennamen: list[str],
                ortsangaben: list[tuple[str, str]]) -> set[tuple[str, str, str]]:
    """Ortsbezug einer Vorlage aus Betreff, Gremiennamen und strukturierten Ortsangaben."""
    treffer: set[OrtsBezug] = set(gaz.ortsbezuege_aus_text(titel, "betreff"))
    for name in gremiennamen:
        treffer |= gaz.ortsbezuege_aus_text(name, "gremium", nur_bezirk=True)
    for typ, wert in ortsangaben:
        if typ == "plz" and wert in gaz.stadt.plz:
            treffer.add(OrtsBezug("plz", wert, "oparl"))
        elif typ == "stadtteil_name":
            nr = gaz.stadtteil_nach_name(wert)
            if nr:
                treffer.add(OrtsBezug("stadtteil", nr, "oparl"))
                bezirk = gaz.stadt.bezirk_fuer(nr)
                if bezirk:
                    treffer.add(OrtsBezug("bezirk", bezirk, "oparl"))
    return {(o.typ, o.schluessel, o.quelle) for o in treffer}


def _verarbeite(speicher: Speicher, gaz: Gazetteer, quelle_id: str, entitaet: str,
                obj: dict, bericht: LaufBericht) -> None:
    oid = obj.get("id")
    if not oid:
        return
    geloescht = bool(obj.get("deleted"))
    if entitaet == "gremien":
        if geloescht:
            speicher.markiere_geloescht("gremium", oid)
        else:
            speicher.gremium_speichern(map_gremium(obj, quelle_id))
    elif entitaet == "sitzungen":
        if geloescht:
            speicher.markiere_geloescht("sitzung", oid)
        else:
            zeile, tops, dokumente = map_sitzung(obj, quelle_id)
            speicher.sitzung_speichern(zeile, tops, dokumente)
    elif entitaet == "vorlagen":
        if geloescht:
            speicher.markiere_geloescht("vorgang", oid)
        else:
            zeile, beratungen, dokumente, beziehungen, ortsangaben = map_vorgang(obj, quelle_id)
            gremium_ids = [g for b in beratungen for g in json.loads(b["gremium_ids"])]
            gremiennamen = list(speicher.gremium_namen(gremium_ids).values())
            orte = ortsbezuege(gaz, zeile["titel"], gremiennamen, ortsangaben)
            zeile["suchtext"] = norm(f"{zeile['titel']} {zeile['referenz']} {zeile['art']}")
            speicher.vorgang_speichern(zeile, beratungen, dokumente, beziehungen, orte)
    bericht.zaehle(f"{entitaet}:{'geloescht' if geloescht else 'gespeichert'}")


def crawle_oparl(speicher: Speicher, stadt: Stadt, quelle: Quelle, abrufer: Abrufer,
                 optionen: LaufOptionen) -> LaufBericht:
    quelle_id = stadt.quell_id(quelle)
    gaz = Gazetteer(stadt)
    speicher.quelle_anlegen(quelle_id, stadt.schluessel, quelle.schluessel, quelle.typ,
                            quelle.name, quelle.system_url, demo=False)

    letzter = speicher.letzter_fehlschlag(quelle_id)
    if letzter and not optionen.trotz_sperre:
        # Nach jedem Fehlschlag in Folge wird die Pause verdoppelt (10 min, 20, 40 … höchstens 6 h).
        fehlschlaege = max(1, speicher.fehlschlaege_in_folge(quelle_id))
        abkuehlung = min(SPERR_ABKUEHLZEIT_MAX, SPERR_ABKUEHLZEIT_BASIS * 2 ** (fehlschlaege - 1))
        frueheste = datetime.fromisoformat(letzter) + abkuehlung
        if datetime.now(timezone.utc) < frueheste:
            raise CrawlVerweigert(
                f"Die Quelle war nach {fehlschlaege} Fehlschlag/Fehlschlägen in Folge gesperrt oder nicht erreichbar. "
                f"Frühestens erneut ab {frueheste.isoformat(timespec='minutes')}. "
                "Bitte warten oder --trotz-sperre angeben (nicht empfohlen)."
            )

    bericht = LaufBericht(quelle_id=quelle_id)
    lauf_id = speicher.lauf_beginnen(quelle_id)
    start_stempel = jetzt_iso()
    speicher.quelle_status(quelle_id, "laeuft")
    try:
        oparl = OParlQuelle(abrufer, quelle.system_url, quelle.body_id)
        body = oparl.body()
        listen = {
            "gremien": body.get("organization"),
            "sitzungen": body.get("meeting"),
            "vorlagen": body.get("paper"),
        }
        for entitaet in optionen.entitaeten:
            url = listen.get(entitaet)
            if not url:
                log.warning("Body liefert keine Liste für %s", entitaet)
                continue
            stand = speicher.stand_lesen(quelle_id, entitaet)
            offen = bool(stand and stand["naechste_url"])
            if offen:
                start, params = stand["naechste_url"], None
                modified = stand["modified_seit"]
                reihe = stand["reihe_beginn"] or start_stempel
                log.info("%s: setze unterbrochene Abrufreihe fort", entitaet)
            else:
                start = None
                modified = (stand["modified_seit"] if stand and stand["modified_seit"]
                            else _modified_seit_iso(optionen.seit or quelle.seit or "2020-01-01"))
                params = {"modified_since": modified}
                reihe = start_stempel
                log.info("%s: neue Abrufreihe seit %s", entitaet, modified)

            for seite in oparl.seiten(url, params=params, start=start):
                for obj in seite.objekte:
                    _verarbeite(speicher, gaz, quelle_id, entitaet, obj, bericht)
                speicher.stand_schreiben(quelle_id, entitaet, modified, seite.naechste or "", reihe)
                log.info("%s: %d Objekte, Anfragen bisher %d", entitaet,
                         sum(v for k, v in bericht.zaehler.items() if k.startswith(entitaet)),
                         abrufer.anfragen)

            speicher.stand_schreiben(quelle_id, entitaet, _minus_puffer(reihe), "", "")
        bericht.status = "ok"
    except BudgetErschoepft as exc:
        bericht.status = "teilweise"
        bericht.fehlertext = str(exc)
    except QuelleGesperrt as exc:
        bericht.status = "gesperrt"
        bericht.fehlertext = str(exc)
    except NichtErlaubt as exc:
        bericht.status = "gesperrt"
        bericht.fehlertext = str(exc)
    except QuelleNichtErreichbar as exc:
        bericht.status = "nicht erreichbar"
        bericht.fehlertext = str(exc)
    except ValueError as exc:
        bericht.status = "fehler"
        bericht.fehlertext = str(exc)
    finally:
        bericht.anfragen = abrufer.anfragen
        speicher.lauf_beenden(lauf_id, bericht.status, bericht.anfragen, bericht.zaehler, bericht.fehlertext)
        speicher.quelle_status(quelle_id, bericht.status, bericht.fehlertext, erfolg=(bericht.status == "ok"))
    return bericht


# Registry: Quelltyp aus config/cities -> Crawl-Funktion. Neue Quelltypen werden hier eingetragen.
CRAWLER = {
    "oparl": crawle_oparl,
}
