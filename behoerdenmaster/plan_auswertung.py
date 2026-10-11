"""Quellentreue Auswertung von Beteiligungsverfahren, unabhängig von Ratsbeschlüssen."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, time

from .auswertung import BERLIN, datum_de


def beteiligungs_zeitpunkt(wert: str, *, ende: bool = False) -> datetime | None:
    if not wert:
        return None
    moment = datetime.fromisoformat(wert.replace("Z", "+00:00"))
    if "T" not in wert and " " not in wert and ende:
        moment = datetime.combine(moment.date(), time.max)
    return moment if moment.tzinfo else moment.replace(tzinfo=BERLIN)


def zeit_de(wert: str) -> str:
    """Zeitstempel in Europe/Berlin; reine Datumsangaben bleiben ohne Uhrzeit."""
    moment = beteiligungs_zeitpunkt(wert)
    if moment is None:
        return ""
    moment = moment.astimezone(BERLIN)
    return moment.strftime("%d.%m.%Y, %H:%M Uhr") if "T" in wert or " " in wert else datum_de(wert)


def plan_status(plan: dict, heute: date, zeitpunkt: datetime | None = None) -> dict:
    # Expliziter Zeitpunkt in API/CLI; reine Datumstests beziehen sich auf Tagesbeginn.
    moment = zeitpunkt or datetime.combine(heute, time.min, BERLIN)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=BERLIN)
    start = beteiligungs_zeitpunkt(plan["beginn"])
    ende = beteiligungs_zeitpunkt(plan["ende"], ende=True)
    phase = plan["phase"]
    start_text, end_text = zeit_de(plan["beginn"]), zeit_de(plan["ende"])
    if not plan["gelistet"]:
        return {"kurz": "nicht mehr gelistet", "offen": False,
                "text": "Nicht mehr in der öffentlichen Liste; zuletzt gesehen am " + zeit_de(plan["gesehen"])
                        + ". Daraus folgt kein Beschluss oder Abschluss des Bauvorhabens"}
    if start and start > moment:
        return {"kurz": "Beteiligung anstehend", "offen": True,
                "text": f"{phase}: Beteiligung ab {start_text}"
                        + (f" bis {end_text}" if ende else "")}
    if ende and ende < moment:
        return {"kurz": "Beteiligungsfrist beendet", "offen": False,
                "text": f"Beteiligungsfrist seit {end_text} beendet; Phase laut Quelle: {phase}. "
                        "Dies ist keine Aussage über Genehmigung oder Bauabschluss"}
    if start and ende and plan["berechtigung"] == "write":
        return {"kurz": "Beteiligung läuft", "offen": True,
                "text": f"{phase}: Beteiligung vom {start_text} bis {end_text}"}
    return {"kurz": phase, "offen": False,
            "text": f"Phase laut Quelle: {phase}; eine laufende Online-Beteiligung ist nicht belegt"}


def beschreibungs_auszug(text: str, limit: int = 650) -> str:
    """Ausgewählte Original-Sätze zu Planungsziel, Umfang und Zeitplan – keine KI-Ergänzungen."""
    saetze = re.split(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ„])", text.strip())
    if len(text) <= limit:
        return text
    markers = ("ziel der planung", "ziel,", "wohneinheiten", "voraussichtlich", "sollen", "planungsrechtlich")
    bewertet = sorted(enumerate(saetze),
                       key=lambda x: (-sum(m in x[1].lower() for m in markers), x[0]))
    gewaehlt = sorted(bewertet[:3])
    auszug = " ".join(s for _, s in gewaehlt)
    return auszug if len(auszug) <= limit else auszug[:limit].rsplit(" ", 1)[0] + " …"


def plan_zusammenfassung(titel: str, plan: dict, status: dict) -> str:
    teile = [f"„{titel}“."]
    auszug = beschreibungs_auszug(plan["beschreibung"])
    if auszug:
        teile.append(auszug)
    teile.append(status["text"] + ".")
    return " ".join(teile)


def plan_verlauf(plan: dict, staende: list[dict], url: str, heute: date) -> list[dict]:
    """Aktueller Zeitraum sowie echte lokale Beobachtungen; keine erfundene Ratsberatung."""
    ereignisse = []

    def ereignis(datum, typ, titel, text):
        lokal = beteiligungs_zeitpunkt(datum).astimezone(BERLIN).date().isoformat()
        return {"typ": typ, "datum": lokal, "datum_de": zeit_de(datum), "zeitpunkt": datum,
                "ereignis": titel, "beschreibung": text, "url": url,
                "gremium": plan["organisation"], "rolle": plan["phase"],
                "anstehend": lokal > heute.isoformat()}

    if plan["beginn"]:
        ereignisse.append(ereignis(plan["beginn"], "beteiligung", "Beteiligungsbeginn laut Quelle", plan["phase"]))
    if plan["ende"]:
        ereignisse.append(ereignis(plan["ende"], "beteiligung", "Ende der Beteiligungsfrist laut Quelle", plan["phase"]))
    vorher = None
    labels = {"titel": "Titel", "phase": "Phase", "beginn": "Beteiligungsbeginn", "ende": "Beteiligungsfrist",
              "beschreibung": "Planungsbeschreibung", "organisation": "Organisation", "berechtigung": "Online-Beteiligung"}
    for zeile in staende:
        inhalt = json.loads(zeile["inhalt"])
        if vorher is None:
            titel = "Erstmals im lokalen Datenbestand erfasst"
            text = "Abrufbeobachtung, kein amtlicher Verfahrensbeginn. "
        else:
            titel = "Änderung des öffentlichen Stands festgestellt"
            anders = [label for key, label in labels.items() if vorher.get(key) != inhalt.get(key)]
            text = "Geändert: " + ", ".join(anders) + ". Zeitpunkt des Abrufs, nicht der amtlichen Änderung. "
        text += inhalt["phase"]
        if inhalt["beginn"] or inhalt["ende"]:
            text += f" · {zeit_de(inhalt['beginn']) or 'ohne Beginn'} – {zeit_de(inhalt['ende']) or 'ohne Frist'}"
        ereignisse.append(ereignis(zeile["festgestellt"], "beobachtung", titel, text))
        vorher = inhalt
    return sorted(ereignisse, key=lambda e: beteiligungs_zeitpunkt(e["zeitpunkt"]))
