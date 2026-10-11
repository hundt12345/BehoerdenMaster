"""Auswertung von Beratungsfolgen: Ergebnisklassen, Status und Kurzzusammenfassung.

Rein regelbasiert und nachvollziehbar: Jede Aussage stammt aus einem konkreten
Verlaufseintrag (Gremium, Datum, TOP, Ergebnis). Ein Sprachmodell wird nicht benötigt.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from .normalize import norm

BERLIN = ZoneInfo("Europe/Berlin")


def jetzt() -> datetime:
    """Aktueller Zeitpunkt in Berlin (Beteiligungsfristen können Uhrzeiten enthalten)."""
    return datetime.now(BERLIN)


def heute() -> date:
    """Heutiges Datum in Berlin (für Termine: „anstehend“ vs. „vergangen“)."""
    return jetzt().date()


ENTSCHEIDEND = {"beschlossen", "abgelehnt", "kenntnis", "erledigt", "zurueckgezogen"}

KATEGORIE_TEXT = {
    "beschlossen": "Beschluss gefasst",
    "abgelehnt": "abgelehnt",
    "kenntnis": "zur Kenntnis genommen",
    "erledigt": "erledigt",
    "zurueckgezogen": "zurückgezogen",
    "vertagt": "vertagt oder verwiesen",
    "uebergang": "ohne Beschluss (Übergang zum nächsten TOP)",
    "sonstig": "Ergebnis laut Protokoll",
}


def ergebnis_kategorie(ergebnis: str | None) -> str | None:
    """Ordnet ein TOP-Ergebnis einer Kategorie zu (Reihenfolge ist wichtig)."""
    n = norm(ergebnis)
    if not n:
        return None
    if "uebergang" in n:
        return "uebergang"
    if "abgelehnt" in n:
        return "abgelehnt"
    if "beschlossen" in n or "angenommen" in n:
        return "beschlossen"
    if "kenntnis" in n:
        return "kenntnis"
    if "erledigt" in n:
        return "erledigt"
    if "zurueckgezogen" in n:
        return "zurueckgezogen"
    if any(w in n for w in ("verwiesen", "zurueckgestellt", "vertagt")):
        return "vertagt"
    return "sonstig"


def datum_de(iso: str | None) -> str:
    """2026-10-08 -> 08.10.2026 (leer, wenn kein gültiges Datum)."""
    if not iso or len(iso) < 10 or iso[4] != "-":
        return ""
    jahr, monat, tag = iso[:10].split("-")
    return f"{tag}.{monat}.{jahr}"


def bestimme_status(eintraege: list[dict]) -> dict:
    """Leitet den Gesamtstatus aus der Beratungsfolge ab.

    Eintrag-Schlüssel: datum, gremium, kategorie, anstehend, top_nummer.
    """
    anstehend = sorted((e for e in eintraege if e["anstehend"]), key=lambda e: e["datum"])
    entscheidungen = sorted((e for e in eintraege if e["kategorie"] in ENTSCHEIDEND), key=lambda e: e["datum"])
    letzte = entscheidungen[-1] if entscheidungen else None
    naechste = anstehend[0] if anstehend else None

    if naechste:
        kurz = "anstehend"
        text = f"Nächste Beratung: {naechste['gremium']} am {datum_de(naechste['datum'])}"
        if naechste.get("top_nummer"):
            text += f" (TOP {naechste['top_nummer']})"
    elif letzte:
        kurz = KATEGORIE_TEXT[letzte["kategorie"]]
        text = f"Letzte Entscheidung: {letzte['gremium']} am {datum_de(letzte['datum'])}: {KATEGORIE_TEXT[letzte['kategorie']]}"
    elif eintraege:
        kurz = "in Beratung"
        text = "Beratungsfolge erfasst, noch ohne Ergebnis"
    else:
        kurz = "unbekannt"
        text = "Keine Beratungsfolge erfasst"

    return {
        "kurz": kurz,
        "text": text,
        "offen": kurz in ("anstehend", "in Beratung", "unbekannt"),
        "naechster_termin": naechste,
        "letzte_entscheidung": letzte,
    }


def zusammenfassung(titel: str, art: str, referenz: str, datum: str, status: dict,
                    ortsnamen: list[str], anzahl_dokumente: int) -> str:
    """Kurzbeschreibung in drei bis vier Sätzen, ausschließlich aus erfassten Daten."""
    kopf = f"„{titel}“" if titel else "Ohne Betreff"
    details = [x for x in (art, f"Nr. {referenz}" if referenz else "",
                           f"vom {datum_de(datum)}" if datum_de(datum) else "") if x]
    if details:
        kopf += " (" + ", ".join(details) + ")"
    teile = [kopf + "."]
    if ortsnamen:
        teile.append("Ortsbezug: " + ", ".join(ortsnamen) + ".")
    teile.append(status["text"] + ".")
    if anzahl_dokumente:
        teile.append(f"{anzahl_dokumente} verlinkte(s) Dokument(e).")
    return " ".join(teile)
