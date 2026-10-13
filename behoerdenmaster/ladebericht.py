"""Ladeübersicht: welche Dokumente aus welcher Quelle bereits im Bestand sind.

Die Übersicht ist **ausschließlich lesend**. Sie beantwortet die Fragen, die man
sich beim Blick auf eine halb gefüllte Datenbank stellt:

* Welche Quellen sind eingerichtet, welche wurden schon abgerufen?
* Wie viele Vorgänge und Dokumente kommen aus welcher Quelle und in welcher Rolle?
* Welche Listen sind vollständig gelesen, welche Abrufreihe ist unterbrochen?
* Wann ist der nächste automatische Abruf fällig, läuft gerade eine Abkühlzeit?
* Was hat der letzte Lauf tatsächlich getan (Anfragen, Zähler, Dauer)?

Es werden keine Behörden abgerufen und keine Dokumente heruntergeladen: Verlinkte
Dateien bleiben verlinkt. Dokumente mit `geloescht=1` werden ausgeblendet.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping

from .betrieb import naechster_abruf
from .config import Stadt
from .speicher import Speicher
from .suche import ROLLEN_TEXT

TYP_TEXT = {
    "oparl": "Ratsinformationssystem (OParl)",
    "bauleitplanung": "Bauleitplanung",
    "bekanntmachungen": "Öffentliche Bekanntmachungen (Amtsblatt)",
    "beteiligung": "Bürgerbeteiligung (Meinung für Köln)",
    "ris-rss": "Sitzungstermine (RSS)",
    "presse-rss": "Pressemeldungen (RSS)",
    "demo": "Offline-Beispiele",
}

# Zustände einer Quelle: Die Oberfläche färbt danach, die Bedeutung bleibt im Text.
LADEZUSTAND_TEXT = {
    "beispiel": "Beispieldaten – kein Live-Abruf",
    "nie": "noch nicht abgerufen",
    "laeuft": "Abruf läuft",
    "aktuell": "Bestand aktuell",
    "teilweise": "teilweise geladen (Budget erreicht)",
    "pause": "Abkühlzeit nach Sperre oder Netzfehler",
    "fehler": "letzter Abruf fehlgeschlagen",
}

ZAEHLER_TEXT = {
    "gremien:gespeichert": "Gremien übernommen",
    "gremien:geloescht": "Gremien als gelöscht markiert",
    "sitzungen:gespeichert": "Sitzungen übernommen",
    "sitzungen:geloescht": "Sitzungen als gelöscht markiert",
    "vorlagen:gespeichert": "Vorlagen übernommen",
    "vorlagen:geloescht": "Vorlagen als gelöscht markiert",
    "verfahren:gespeichert": "Verfahren übernommen",
    "verfahren:entfernt": "Verfahren nicht mehr gelistet",
    "details:gespeichert": "Detailseiten gelesen",
    "details:nicht_verfuegbar": "Detailseiten nicht verfügbar",
    "dokumente:neu": "Dokumentverweise neu",
    "bekanntmachungen:neu": "Bekanntmachungen neu übernommen",
    "bekanntmachungen:aktualisiert": "Bekanntmachungen aktualisiert",
    "bekanntmachungen:ohne_datum": "Bekanntmachungen ohne erkanntes Datum",
    "beteiligungen:neu": "Beteiligungsangebote neu übernommen",
    "beteiligungen:aktualisiert": "Beteiligungsangebote aktualisiert",
    "beteiligungen:verworfen": "Projektkarten ohne verwertbaren Link",
    "listen:seiten": "Listenseiten gelesen",
}

ENTITAETEN_TEXT = {
    "gremien": "Gremien", "sitzungen": "Sitzungen", "vorlagen": "Vorlagen",
    "organisationen": "Gremien", "verfahren": "Verfahren",
}

# Zähler einer Quelle, die noch nie abgerufen wurde: ehrliche Nullen statt fehlender Felder.
LEERE_ZAEHLER = {
    "vorgaenge": 0, "sitzungen": 0, "gremien": 0, "tagesordnungspunkte": 0,
    "beratungen": 0, "dokumente": 0, "planverfahren": 0, "planstaende": 0,
    "hinweise": 0, "meldungen": 0, "bekanntmachungen": 0, "beteiligungen": 0,
}

# Reihenfolge der Balken im Diagramm „Dokumente je Quelle“.
ROLLEN_DARSTELLUNG = [
    "vorlage", "plandokument", "tagesordnung", "einladung",
    "ergebnisprotokoll", "wortprotokoll", "protokoll", "dokument",
]


def rolle_text(rolle: str) -> str:
    return ROLLEN_TEXT.get(rolle, "Dokument")


def einheit(anzahl: int, singular: str, plural: str) -> str:
    """„1 Vorgang“ statt „1 Vorgänge“ – Zahl und Wort gehören zusammen."""
    return f"{anzahl} {singular if anzahl == 1 else plural}"


def entitaet_name(entitaet: str) -> str:
    """Lesbare Bezeichnung einer OParl-Liste (auch in der CLI-Ausgabe)."""
    return ENTITAETEN_TEXT.get(entitaet, entitaet.capitalize())


def zaehler_text(schluessel: str) -> str:
    if schluessel in ZAEHLER_TEXT:
        return ZAEHLER_TEXT[schluessel]
    entitaet, _, art = schluessel.partition(":")
    return f"{entitaet.capitalize()} {art.replace('_', ' ')}".strip()


def _sekunden_zwischen(start: str, ende: str) -> int | None:
    if not start or not ende:
        return None
    try:
        a, b = datetime.fromisoformat(start), datetime.fromisoformat(ende)
    except ValueError:
        return None
    if a.tzinfo is None:
        a = a.replace(tzinfo=timezone.utc)
    if b.tzinfo is None:
        b = b.replace(tzinfo=timezone.utc)
    return max(0, int((b - a).total_seconds()))


def _zaehler_liste(statistik: str) -> list[dict]:
    """Gespeicherte Laufzähler als sortierte, beschriftete Liste."""
    import json

    try:
        roh = json.loads(statistik or "{}")
    except ValueError:
        roh = {}
    if not isinstance(roh, dict):
        roh = {}
    eintraege = [
        {"schluessel": k, "text": zaehler_text(k), "anzahl": int(v)}
        for k, v in roh.items() if isinstance(v, (int, float))
    ]
    return sorted(eintraege, key=lambda e: (-e["anzahl"], e["text"]))


def _lauf(row: Mapping) -> dict:
    return {
        "id": row["id"],
        "gestartet": row["gestartet"],
        "beendet": row["beendet"],
        "status": row["status"],
        "status_text": LADEZUSTAND_TEXT.get(row["status"], row["status"]),
        "anfragen": row["anfragen"],
        "dauer_sekunden": _sekunden_zwischen(row["gestartet"], row["beendet"]),
        "zaehler": _zaehler_liste(row["statistik"]),
        "fehlertext": row["fehlertext"],
    }


def _ladezustand(row: Mapping, zahlen: Mapping[str, int]) -> str:
    if row["demo"]:
        return "beispiel"
    if row["abkuehlung_bis"]:
        return "pause"
    status = row["status"]
    if status == "laeuft":
        return "laeuft"
    if status in ("gesperrt", "nicht erreichbar", "fehler"):
        return "fehler"
    if status == "teilweise":
        return "teilweise"
    if status == "ok" and (zahlen.get("vorgaenge") or zahlen.get("sitzungen") or zahlen.get("hinweise")):
        return "aktuell"
    return "nie"


def _abrufweg(zahlen: Mapping[str, int], staende: list[Mapping], aufgaben: int,
              typ: str, kurs: bool) -> list[dict]:
    """Der Weg der Daten von der Quelle bis in die Suche – mit den echten Zahlen aus der Datenbank."""
    offen = [s["entitaet"] for s in staende if s["naechste_url"]]
    schritte: list[dict] = []

    def schritt(name: str, wert: str, zustand: str) -> None:
        schritte.append({"schritt": name, "wert": wert, "zustand": zustand})

    if typ == "oparl":
        schritt("Zugriff", "robots.txt geprüft · Mindestabstand laut Stadtkonfiguration",
                "ok" if kurs else "wartet")
        listen = (f"Gremien {zahlen.get('gremien', 0)} · Vorlagen {zahlen.get('vorgaenge', 0)} · "
                  f"Sitzungen {zahlen.get('sitzungen', 0)}")
        schritt("Listen", listen, "offen" if offen else ("ok" if kurs else "wartet"))
        schritt("Objekte", f"Tagesordnungspunkte {zahlen.get('tagesordnungspunkte', 0)} · Beratungen {zahlen.get('beratungen', 0)}",
                "ok" if kurs else "wartet")
    elif typ == "bauleitplanung":
        schritt("Zugriff", "öffentliche Verfahrenssuche · Mindestabstand laut Stadtkonfiguration",
                "ok" if kurs else "wartet")
        schritt("Verfahrensliste", f"{einheit(zahlen.get('vorgaenge', 0), 'Verfahren', 'Verfahren')} gelistet",
                "offen" if offen else ("ok" if kurs else "wartet"))
        schritt("Detailseiten", f"{einheit(aufgaben, 'Detailabruf', 'Detailabrufe')} in der Warteschlange" if aufgaben else "Warteschlange leer",
                "offen" if aufgaben else ("ok" if kurs else "wartet"))
    elif typ in ("ris-rss", "presse-rss"):
        schritt("Zugriff", "ein Feed-Abruf je Lauf · Mindestabstand laut Stadtkonfiguration",
                "ok" if kurs else "wartet")
        schritt("Hinweise", f"{einheit(zahlen.get('hinweise', 0), 'Hinweis', 'Hinweise')} "
                "(Titel, Datum, Link, Kurztext) – nicht Teil der Suche",
                "ok" if zahlen.get("hinweise") else ("leer" if kurs else "wartet"))
    elif typ == "bekanntmachungen":
        schritt("Zugriff", "öffentliche Jahreslisten · Mindestabstand laut Stadtkonfiguration",
                "ok" if kurs else "wartet")
        schritt("Listen", f"{einheit(zahlen.get('bekanntmachungen', 0), 'Bekanntmachung', 'Bekanntmachungen')} gelistet"
                + (" · Abrufreihe unterbrochen, wird fortgesetzt" if offen else ""),
                "offen" if offen else ("ok" if kurs else "wartet"))
        schritt("Originale", "verlinkt; das PDF wird nicht geladen und nicht ausgewertet",
                "ok" if zahlen.get("dokumente") else "leer")
    elif typ == "beteiligung":
        schritt("Zugriff", "eine öffentliche Projektliste je Lauf · Mindestabstand laut Stadtkonfiguration",
                "ok" if kurs else "wartet")
        schritt("Projekte", f"{einheit(zahlen.get('beteiligungen', 0), 'Beteiligungsangebot', 'Beteiligungsangebote')} "
                f"· {einheit(zahlen.get('dokumente', 0), 'Dokumentverweis', 'Dokumentverweise')}",
                "ok" if zahlen.get("beteiligungen") else ("leer" if kurs else "wartet"))
        schritt("Beiträge Dritter", "nicht übernommen (keine Kommentare, Namen oder Fotos)",
                "ok" if kurs else "wartet")
    else:
        schritt("Zugriff", "Offline-Auszug, kein Netzabruf", "beispiel")
        schritt("Beispieldaten", f"{zahlen.get('vorgaenge', 0)} Vorgänge aus Fixtures", "beispiel")

    schritt("Dokumente", f"{einheit(zahlen.get('dokumente', 0), 'Dokumentverweis', 'Dokumentverweise')} (nur verlinkt)",
            "ok" if zahlen.get("dokumente") else "leer")
    schritt("Suchbestand", f"{einheit(zahlen.get('vorgaenge', 0), 'Vorgang', 'Vorgänge')} · " +
            f"{einheit(zahlen.get('planverfahren', 0), 'Planverfahren', 'Planverfahren')}",
            "ok" if zahlen.get("vorgaenge") else "leer")
    return schritte


def _quelle_zeile(stadt: Stadt, quelle, row: Mapping | None, *, intervall: int, sync_aktiv: bool,
                  zahlen: Mapping[str, int], rollen: list[dict], staende: list[Mapping],
                  aufgaben: int, laeufe: list[dict], dokumente: list[dict]) -> dict:
    demo = bool(row["demo"]) if row is not None else False
    kurs = bool(zahlen.get("vorgaenge") or zahlen.get("sitzungen") or zahlen.get("dokumente")
                or zahlen.get("hinweise"))
    zustand = _ladezustand(row, zahlen) if row is not None else "nie"
    naechster = None
    if row is not None and sync_aktiv and not demo:
        faellig = naechster_abruf(row, intervall)
        naechster = faellig.isoformat(timespec="seconds") if faellig else None
    return {
        "id": row["id"] if row is not None else stadt.quell_id(quelle),
        "schluessel": quelle.schluessel,
        "name": (row["name"] if row is not None else quel_name(quelle)) or quel_name(quelle),
        "typ": quelle.typ,
        "typ_text": TYP_TEXT.get(quelle.typ, quelle.typ),
        "demo": demo,
        "status": row["status"] if row is not None else "nie",
        "ladezustand": zustand,
        "ladezustand_text": LADEZUSTAND_TEXT[zustand],
        "letzter_erfolg": row["letzter_erfolg"] if row is not None else "",
        "letzter_versuch": row["letzter_versuch"] if row is not None else "",
        "abkuehlung_bis": row["abkuehlung_bis"] if row is not None else "",
        "fehlertext": row["fehlertext"] if row is not None else "",
        "naechster_abruf": naechster,
        "intervall_sekunden": intervall,
        "zaehler": {**LEERE_ZAEHLER, **dict(zahlen)},
        "dokumente_nach_rolle": rollen,
        "staende": [
            {"entitaet": s["entitaet"], "modified_seit": s["modified_seit"],
             "aktualisiert": s["aktualisiert"], "offen": bool(s["naechste_url"]),
             "reihe_beginn": s["reihe_beginn"]}
            for s in staende
        ],
        "offene_details": aufgaben,
        "dokumente": dokumente,
        "abrufweg": _abrufweg(zahlen, list(staende), aufgaben, quelle.typ, kurs),
        "laeufe": laeufe,
    }


def _dokumente(sp: Speicher, quelle_id: str, limit: int = 25) -> list[dict]:
    """Dokumentverweise einer Quelle mit Beschriftung – es wird nur der Link übernommen, keine Datei."""
    return [
        {
            "name": d["name"] or d["dateiname"] or "Dokument",
            "rolle": d["rolle"],
            "rolle_text": rolle_text(d["rolle"]),
            "datum": d["datum"],
            "url": d["url"] or d["download_url"] or "",
        }
        for d in sp.dokumente_quelle(quelle_id, limit)
    ]


def quel_name(quelle) -> str:
    return quelle.name or quelle.schluessel


def ladebericht(sp: Speicher, stadt: Stadt, *, intervall_sekunden: int = 0,
                sync_aktiv: bool = False, laeufe_pro_quelle: int = 8,
                jetzt: datetime | None = None) -> dict:
    """Kennzahlen und Verlauf des Dokumentladens für eine Stadt – nur aus der lokalen Datenbank."""
    jetzt = jetzt or datetime.now(timezone.utc)
    vorhanden = {r["id"]: r for r in sp.quellen() if r["stadt"] == stadt.schluessel}
    rollen_je_quelle: dict[str, list[dict]] = {}
    for r in sp.dokumente_nach_quelle():
        rollen_je_quelle.setdefault(r["quelle_id"], []).append({"rolle": r["rolle"], "anzahl": r["anzahl"]})
    for eintraege in rollen_je_quelle.values():
        eintraege.sort(key=lambda e: (ROLLEN_DARSTELLUNG.index(e["rolle"])
                                      if e["rolle"] in ROLLEN_DARSTELLUNG else 99, -e["anzahl"]))
    aufgaben = sp.aufgaben_nach_quelle()

    quellen: list[dict] = []
    for quelle in stadt.quellen:
        qid = stadt.quell_id(quelle)
        row = vorhanden.pop(qid, None)
        quellen.append(_quelle_zeile(
            stadt, quelle, row, intervall=intervall_sekunden, sync_aktiv=sync_aktiv,
            zahlen=sp.zaehle_quelle(qid) if row is not None else {},
            rollen=rollen_je_quelle.get(qid, []), staende=sp.staende(qid), aufgaben=aufgaben.get(qid, 0),
            laeufe=[_lauf(x) for x in sp.laeufe(laeufe_pro_quelle, qid)],
            dokumente=_dokumente(sp, qid)))
    # Quellen, die nur in der Datenbank stehen (z. B. Offline-Beispiele), bleiben sichtbar.
    for qid, row in sorted(vorhanden.items(), key=lambda p: (p[1]["demo"], p[0])):
        zahlen = sp.zaehle_quelle(qid)
        quellen.append({
            "id": qid, "schluessel": row["schluessel"], "name": row["name"] or qid, "typ": row["typ"],
            "typ_text": TYP_TEXT.get(row["typ"], row["typ"]), "demo": bool(row["demo"]),
            "status": row["status"], "ladezustand": "beispiel" if row["demo"] else "aktuell",
            "ladezustand_text": LADEZUSTAND_TEXT["beispiel" if row["demo"] else "aktuell"],
            "letzter_erfolg": row["letzter_erfolg"], "letzter_versuch": row["letzter_versuch"],
            "abkuehlung_bis": row["abkuehlung_bis"], "fehlertext": row["fehlertext"],
            "naechster_abruf": None, "intervall_sekunden": intervall_sekunden, "zaehler": zahlen,
            "dokumente_nach_rolle": rollen_je_quelle.get(qid, []), "staende": sp.staende(qid),
            "offene_details": aufgaben.get(qid, 0), "dokumente": _dokumente(sp, qid),
            "abrufweg": _abrufweg(zahlen, [], aufgaben.get(qid, 0), row["typ"], bool(zahlen.get("vorgaenge"))),
            "laeufe": [_lauf(x) for x in sp.laeufe(laeufe_pro_quelle, qid)],
        })

    gesamt_zaehle = sp.zaehle()
    rollen_gesamt: dict[str, int] = {}
    for eintraege in rollen_je_quelle.values():
        for e in eintraege:
            rollen_gesamt[e["rolle"]] = rollen_gesamt.get(e["rolle"], 0) + e["anzahl"]
    rollen_liste = [{"rolle": r, "text": rolle_text(r), "anzahl": n}
                    for r, n in sorted(rollen_gesamt.items(),
                                       key=lambda p: (ROLLEN_DARSTELLUNG.index(p[0])
                                                      if p[0] in ROLLEN_DARSTELLUNG else 99, -p[1]))]

    hinweise: list[str] = []
    if all(q["ladezustand"] == "beispiel" for q in quellen):
        hinweise.append("Es sind nur Offline-Beispieldaten geladen. Ein echter Abruf füllt diese Übersicht: "
                        "python -m behoerdenmaster crawl --quelle ris bzw. --quelle bauleitplanung.")
    if not sp.laeufe(1):
        hinweise.append("Es ist noch kein Abruflauf protokolliert; Verlauf und Anfragen erscheinen nach dem ersten Abruf.")
    if not sync_aktiv:
        hinweise.append("Automatische Abrufe sind ausgeschaltet; der Bestand wächst nur über manuelle Läufe (CLI).")
    if any(q["abkuehlung_bis"] for q in quellen):
        hinweise.append("Mindestens eine Quelle ist in Abkühlzeit. Der nächste Abruf wartet die Pause ab; "
                        "sie wird nicht verkürzt.")
    hinweise.append("Angezeigt werden nur bereits abgerufene Daten. Dokumente bleiben verlinkt – "
                    "es werden keine PDF-Dateien heruntergeladen.")

    return {
        "stadt": {"schluessel": stadt.schluessel, "name": stadt.name},
        "generiert": jetzt.isoformat(timespec="seconds"),
        "gesamt": {
            "vorgaenge": gesamt_zaehle.get("vorgang", 0),
            "dokumente": gesamt_zaehle.get("dokument", 0),
            "sitzungen": gesamt_zaehle.get("sitzung", 0),
            "gremien": gesamt_zaehle.get("gremium", 0),
            "tagesordnungspunkte": gesamt_zaehle.get("tagesordnungspunkt", 0),
            "beratungen": gesamt_zaehle.get("beratung", 0),
            "quellen": len(quellen),
            "quellen_live": sum(1 for q in quellen if not q["demo"]),
            "quellen_mit_bestand": sum(1 for q in quellen if q["zaehler"].get("vorgaenge") or q["zaehler"].get("dokumente")),
        },
        "dokumente_nach_rolle": rollen_liste,
        "quellen": quellen,
        "hinweise": hinweise,
    }
