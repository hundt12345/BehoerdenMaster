"""Übersicht der angebundenen Städte – für API, CLI, Oberfläche und docs/staedte.md.

Zwei Sichten auf dieselben Angaben:

* :func:`uebersicht` verbindet die Stadtkonfiguration mit dem **lokalen** Datenbestand
  (Zustand, letzter Erfolg, Anzahl Vorgänge, Anforderungen). Kein Behördenabruf.
* :func:`markdown` beschreibt nur die Konfiguration und ist damit reproduzierbar; sie
  erzeugt ``docs/staedte.md`` (ein Test prüft, dass die Datei aktuell ist).
"""

from __future__ import annotations

from datetime import datetime, timezone

from .bedarf import aktive_staedte, bedarf_gueltig_bis, bedarfe
from .config import Stadt
from .crawler import CRAWLER
from .speicher import Speicher

ABRUF_TEXT = {
    "dauerhaft": "dauerhaft – wird laufend aktualisiert",
    "auf_abruf": "auf Abruf – wird erst nach Anforderung geladen",
}

TYP_TEXT = {
    "oparl": "Ratsinformation (OParl)",
    "bauleitplanung": "Bauleitplanung Online NRW",
    "ris-rss": "Sitzungstermine (RSS)",
    "presse-rss": "Pressemeldungen (RSS)",
    "bekanntmachungen": "Öffentliche Bekanntmachungen",
    "beteiligung": "Bürgerbeteiligung",
    "opendata": "Offene Daten (DKAN 2)",
}


def sortiert(staedte: dict[str, Stadt], standard: str) -> list[Stadt]:
    """Standardstadt zuerst, dann dauerhaft abgerufene, dann alphabetisch."""
    return sorted(staedte.values(), key=lambda s: (s.schluessel != standard, s.auf_abruf, s.name))


def gliederung(stadt: Stadt) -> dict:
    if stadt.stadtteile:
        stufe, text = "voll", "Bezirke, Stadtteile, PLZ und Straßen"
    elif stadt.bezirke:
        stufe, text = "bezirke", "nur Stadtbezirke (keine Stadtteile im OSM-Auszug)"
    else:
        stufe, text = "keine", "keine Ortsgliederung hinterlegt"
    return {
        "stufe": stufe, "text": text,
        "bezirke": len(stadt.bezirke), "stadtteile": len(stadt.stadtteile),
        "plz": len(stadt.plz), "strassen": len(stadt.strassen),
        "quelle": stadt.gliederung_quelle,
    }


def beispiele(stadt: Stadt) -> list[str]:
    """Beispielfragen aus der Konfiguration – mit echten Orten der jeweiligen Stadt."""
    if stadt.schluessel == "koeln":
        return ["Welche Bauvorhaben sind in Köln Ehrenfeld geplant?",
                "Was gibt es zu Verkehr in Lindenthal seit 2024?",
                "Welche Vorlagen betreffen Wohnungsbau in Köln?"]
    orte = [t.name for t in stadt.stadtteile.values()] or list(stadt.bezirke.values())
    erster = orte[0] if orte else stadt.name
    zweiter = orte[len(orte) // 2] if len(orte) > 1 else erster
    if not erster.startswith(stadt.name):
        erster = f"{stadt.name} {erster}"  # „Düsseldorf Altstadt“ statt nur „Altstadt“
    return [f"Welche Bauvorhaben sind in {erster} geplant?",
            f"Was gibt es zu Verkehr in {zweiter}?",
            f"Welche Vorlagen betreffen Wohnungsbau in {stadt.name}?"]


def beispiel_plz(stadt: Stadt) -> str:
    mit = [code for code, e in sorted(stadt.plz.items()) if e.stadtteile]
    return (mit or sorted(stadt.plz) or [""])[0]


def _vorgaenge_je_quelle(sp: Speicher) -> dict[str, int]:
    return {r[0]: r[1] for r in sp.db.execute(
        "SELECT quelle_id, COUNT(*) FROM vorgang WHERE geloescht = 0 GROUP BY quelle_id")}


def uebersicht(sp: Speicher, staedte: dict[str, Stadt], *, standard: str, auf_abruf: bool,
               verfall_tage: int, max_staedte: int, sync_aktiv: bool, dauerhafte_ids: list[str],
               jetzt: datetime | None = None) -> dict:
    jetzt = jetzt or datetime.now(timezone.utc)
    zeilen = {r["id"]: r for r in sp.quellen()}
    vorgaenge = _vorgaenge_je_quelle(sp)
    vermerkt = bedarfe(sp)
    aktiv = set(aktive_staedte(sp, staedte, tage=verfall_tage, maximum=max_staedte, jetzt=jetzt)) if auf_abruf else set()

    eintraege = []
    for stadt in sortiert(staedte, standard):
        quellen = []
        for q in stadt.quellen:
            qid = stadt.quell_id(q)
            z = zeilen.get(qid)
            quellen.append({
                "id": qid, "name": q.name, "typ": q.typ, "typ_text": TYP_TEXT.get(q.typ, q.typ),
                "automatisch": q.automatisch and q.typ in CRAWLER,
                "im_abrufplan": qid in dauerhafte_ids or (stadt.schluessel in aktiv and q.automatisch),
                "pruefstand": q.pruefstand, "lizenz": q.lizenz,
                "status": z["status"] if z else "nie",
                "letzter_erfolg": z["letzter_erfolg"] if z else "",
                "fehlertext": z["fehlertext"] if z else "",
                "abkuehlung_bis": z["abkuehlung_bis"] if z else "",
                "vorgaenge": vorgaenge.get(qid, 0),
            })
        bestand = sum(q["vorgaenge"] for q in quellen)
        bedarf = vermerkt.get(stadt.schluessel)
        if not stadt.auf_abruf:
            zustand, text = ("aktiv", "Dauerhaft im Abrufplan") if sync_aktiv else (
                "aus", "Automatische Abrufe sind ausgeschaltet")
        elif not sync_aktiv:
            zustand, text = "aus", "Automatische Abrufe sind ausgeschaltet"
        elif not auf_abruf:
            zustand, text = "aus", "Laden auf Abruf ist ausgeschaltet (BEHOERDEN_AUF_ABRUF=false)"
        elif stadt.schluessel in aktiv:
            erfolg = any(q["letzter_erfolg"] for q in quellen)
            zustand = "aktiv" if erfolg else "laedt"
            text = (f"Angefordert – wird bis {bedarf_gueltig_bis(bedarf, verfall_tage)[:10]} aktuell gehalten"
                    if erfolg else "Angefordert – der erste Abruf läuft im Hintergrund")
        elif bedarf:
            zustand, text = "ruht", ("Ruht – letzte Anforderung abgelaufen oder andere Städte haben Vorrang; "
                                     "der Bestand bleibt durchsuchbar")
        else:
            zustand, text = "ruht", "Noch nicht angefordert – wird beim ersten Aufruf geladen"
        eintraege.append({
            "schluessel": stadt.schluessel, "name": stadt.name, "bundesland": stadt.bundesland,
            "ags": stadt.ags, "standard": stadt.schluessel == standard,
            "abruf": stadt.abruf, "abruf_text": ABRUF_TEXT[stadt.abruf],
            "zustand": zustand, "zustand_text": text,
            "angefordert": bedarf or None,
            "bestand_vorgaenge": bestand,
            "gliederung": gliederung(stadt),
            "quellen": quellen,
            "hinweis": stadt.hinweis,
            "beispiele": beispiele(stadt),
            "beispiel_plz": beispiel_plz(stadt),
            "themen": [{"schluessel": t.schluessel, "bezeichnung": t.bezeichnung} for t in stadt.themen],
        })
    return {
        "generiert": jetzt.isoformat(timespec="seconds"),
        "standard": standard,
        "laden_auf_abruf": {"aktiv": auf_abruf, "verfall_tage": verfall_tage, "max_staedte": max_staedte},
        "staedte": eintraege,
    }


def markdown(staedte: dict[str, Stadt], standard: str) -> str:
    """docs/staedte.md – nur aus der Konfiguration, damit die Datei reproduzierbar bleibt."""
    z = [
        "# Angebundene Städte",
        "",
        "<!-- Erzeugt mit `python -m behoerdenmaster staedte --markdown > docs/staedte.md`. "
        "Nicht von Hand ändern; ein Test prüft, dass die Datei zur Konfiguration passt. -->",
        "",
        "Alle Städte nutzen **dasselbe System**: dieselben Adapter, dieselbe Drossel je Host, robots.txt,",
        "Budgets, Abkühlzeiten und dieselbe lokale Suche. Eine neue Stadt ist eine neue Datei in",
        "`config/cities/`; die Ortsgliederung erzeugt `tools/stadt_aus_osm.py` aus dem OpenStreetMap-Auszug.",
        "",
        "**Abrufmodus.** *Dauerhaft* abgerufene Städte hält der Hintergrunddienst laufend aktuell.",
        "Städte *auf Abruf* sind vollständig angebunden, werden aber erst geladen, wenn jemand sie in",
        "der Oberfläche auswählt oder in ihr sucht – das ist nur ein Vermerk in der lokalen Datenbank;",
        "abgerufen wird ausschließlich im gedrosselten Hintergrundplan, nie während einer Suche.",
        "Den aktuellen Ladezustand zeigen die Oberfläche (Bereich „Angebundene Städte“),",
        "`GET /api/staedte` und `python -m behoerdenmaster staedte`.",
        "",
        "| Stadt | Abruf | Quellen | Ortsgliederung | Prüfstand |",
        "|---|---|---|---|---|",
    ]
    for stadt in sortiert(staedte, standard):
        g = gliederung(stadt)
        if g["stufe"] == "voll":
            geo = f"{g['bezirke']} Bezirke, {g['stadtteile']} Stadtteile, {g['plz']} PLZ, {g['strassen']} Straßen"
        elif g["stufe"] == "bezirke":
            geo = f"{g['bezirke']} Bezirke, {g['plz']} PLZ (keine Stadtteile)"
        else:
            geo = "–"
        quellen = ", ".join(TYP_TEXT.get(q.typ, q.typ) + ("" if q.automatisch else " (nur manuell)")
                            for q in stadt.quellen)
        ris = next((q for q in stadt.quellen if q.typ == "oparl"), stadt.quellen[0] if stadt.quellen else None)
        pruef = (ris.pruefstand if ris else "") or "–"
        name = f"**{stadt.name}**" + (" (Standard)" if stadt.schluessel == standard else "")
        abruf = "dauerhaft" if not stadt.auf_abruf else "auf Abruf"
        z.append(f"| {name} | {abruf} | {quellen} | {geo} | {pruef} |")
    z += [
        "",
        "## Je Stadt",
        "",
    ]
    for stadt in sortiert(staedte, standard):
        z.append(f"### {stadt.name} (`{stadt.schluessel}`)")
        z.append("")
        z.append(f"- Bundesland: {stadt.bundesland or '–'} · Amtlicher Gemeindeschlüssel: {stadt.ags or '–'}")
        z.append(f"- Abruf: {ABRUF_TEXT[stadt.abruf]}")
        z.append(f"- Ortsgliederung: {gliederung(stadt)['text']}"
                 + (f" – {stadt.gliederung_quelle}" if stadt.gliederung_quelle else ""))
        for q in stadt.quellen:
            z.append(f"- Quelle `{stadt.quell_id(q)}` – {q.name}: `{q.system_url}`")
            if q.pruefstand:
                z.append(f"  - Prüfstand: {q.pruefstand}")
            if q.lizenz:
                z.append(f"  - Lizenz: {q.lizenz}")
        z.append("")
    z += [
        "## Neue Stadt anbinden",
        "",
        "1. OParl-Endpunkt in der offiziellen Liste nachsehen (github.com/OParl/resources, `endpoints.yml`).",
        "2. `config/cities/<stadt>.toml` nach dem Vorbild von `bonn.toml` anlegen: `[stadt]` mit `ags`,",
        "   `abruf = \"auf_abruf\"`, eine `[[quellen]]` vom Typ `oparl` mit `koerperschaft`, die Themen und",
        "   `[erzeugung]` mit der Zuordnung der OSM-Bezirke.",
        "3. Ortsgliederung erzeugen: `python tools/stadt_aus_osm.py /tmp/streets.updated.csv config/cities/<stadt>.toml`.",
        "4. Auf einem Rechner mit Netz prüfen: `python -m behoerdenmaster --stadt <stadt> probe --quelle ris`,",
        "   danach `pruefstand` in der Konfiguration nachtragen.",
        "5. Diese Übersicht neu erzeugen: `python -m behoerdenmaster staedte --markdown > docs/staedte.md`.",
        "",
    ]
    return "\n".join(z)
