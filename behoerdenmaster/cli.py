"""Kommandozeile: Status, Probelauf, Crawl, Fragen, PLZ-Suche, Demo-Daten und Webserver."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from . import __version__
from .abruf import Abrufer, NichtErlaubt, QuelleGesperrt, QuelleNichtErreichbar
from .auswertung import heute
from .config import Quelle, Stadt, config_verzeichnis, lade_staedte
from .crawler import CRAWLER, ENTITAETEN, CrawlVerweigert, LaufOptionen
from .demo import demo_quell_id, lade_demo, loesche_demo
from .geo import Gazetteer
from .nlq import verstehe
from .oparl import OParlQuelle
from .speicher import Speicher
from .suche import Suchauftrag, suche

STANDARD_DB = Path(os.environ.get("BEHOERDEN_DB", Path(__file__).resolve().parent.parent / "data" / "behoerdenmaster.sqlite"))


def _db_pfad(args) -> str:
    return args.db or str(STANDARD_DB)


def _kontakt(args) -> str:
    kontakt = getattr(args, "kontakt", None) or os.environ.get("BEHOERDEN_KONTAKT")
    if not kontakt:
        raise SystemExit(
            "Bitte eine Kontaktadresse angeben (Umgebungsvariable BEHOERDEN_KONTAKT oder --kontakt).\n"
            "Die Stadt muss erkennen können, wer abruft und wen sie bei Problemen anschreiben kann."
        )
    return kontakt


def _stadt_und_quelle(args) -> tuple[Stadt, Quelle]:
    stadt = lade_staedte(config_verzeichnis())[args.stadt]
    if not stadt.quellen:
        raise SystemExit(f"Für {stadt.name} ist keine Quelle konfiguriert.")
    quelle = stadt.quellen[0]
    if getattr(args, "quelle", None):
        passende = [q for q in stadt.quellen if q.schluessel == args.quelle]
        if not passende:
            raise SystemExit(f"Unbekannte Quelle '{args.quelle}' für {stadt.name}.")
        quelle = passende[0]
    return stadt, quelle


# --- Befehle -------------------------------------------------------------------

def cmd_status(args) -> int:
    with Speicher(_db_pfad(args)) as sp:
        print(f"BehoerdenMaster {__version__} · Datenbank: {_db_pfad(args)}")
        for q in sp.quellen():
            tag = " (Beispieldaten)" if q["demo"] else ""
            print(f"- {q['id']}{tag}: Status {q['status']}, letzter Erfolg {q['letzter_erfolg'] or '–'}")
            if q["fehlertext"]:
                print(f"    Hinweis: {q['fehlertext']}")
        print("Bestand:", ", ".join(f"{k} {v}" for k, v in sp.zaehle().items()))
        for lauf in sp.letzte_laeufe(3):
            print(f"Lauf {lauf['id']} · {lauf['quelle_id']} · {lauf['gestartet']} · {lauf['status']} · {lauf['anfragen']} Anfragen")
    return 0


def cmd_probe(args) -> int:
    """Prüft Erreichbarkeit mit höchstens 5 Anfragen, ohne Daten zu speichern."""
    stadt, quelle = _stadt_und_quelle(args)
    # Ein Probelauf wiederholt nicht: Er soll nur zeigen, ob die Quelle jetzt antwortet.
    abrufer = Abrufer(kontakt=_kontakt(args), min_intervall=quelle.min_intervall_sekunden,
                      max_anfragen=5, wiederholungen=0)
    oparl = OParlQuelle(abrufer, quelle.system_url, quelle.body_id)
    try:
        system = oparl.system()
        print(f"System: {system.get('name', '?')} · OParl {system.get('oparlVersion', '?')} · "
              f"Hersteller {system.get('vendor', '?')}")
        body = oparl.body()
        print(f"Körperschaft: {body.get('name', '?')} ({body.get('id', '?')})")
        seite = next(oparl.seiten(body["paper"], params={"modified_since": "2025-11-01T00:00:00+01:00"}))
        print(f"Vorlagen (erste Seite, seit 01.11.2025): {len(seite.objekte)} Objekte, "
              f"weitere Seiten: {'ja' if seite.naechste else 'nein'}")
        for obj in seite.objekte[:3]:
            print(f"  - {obj.get('reference', '')}: {obj.get('name', '')[:90]}")
        print(f"Anfragen: {abrufer.anfragen}. Der Probelauf speichert nichts.")
        return 0
    except (QuelleGesperrt, NichtErlaubt, QuelleNichtErreichbar, ValueError) as exc:
        print(f"Probelauf fehlgeschlagen: {exc}")
        return 1


def cmd_crawl(args) -> int:
    stadt, quelle = _stadt_und_quelle(args)
    entitaeten = tuple(e.strip() for e in args.nur.split(",")) if args.nur else ENTITAETEN
    abrufer = Abrufer(kontakt=_kontakt(args), min_intervall=quelle.min_intervall_sekunden,
                      max_anfragen=args.max_anfragen)
    optionen = LaufOptionen(seit=args.seit, max_anfragen=args.max_anfragen,
                            entitaeten=entitaeten, trotz_sperre=args.trotz_sperre)
    if quelle.typ not in CRAWLER:
        raise SystemExit(f"Quelltyp '{quelle.typ}' ist noch nicht implementiert. Verfügbar: {', '.join(CRAWLER)}")
    try:
        with Speicher(_db_pfad(args)) as sp:
            bericht = CRAWLER[quelle.typ](sp, stadt, quelle, abrufer, optionen)
    except CrawlVerweigert as exc:
        print(exc)
        return 3
    finally:
        abrufer.schliessen()
    print(f"Lauf beendet: Status {bericht.status}, {bericht.anfragen} Anfragen, "
          + ", ".join(f"{k}: {v}" for k, v in sorted(bericht.zaehler.items())))
    if bericht.fehlertext:
        print(f"Hinweis: {bericht.fehlertext}")
    if bericht.status == "teilweise":
        print("Der Lauf ist unterbrochen (Budget erreicht). Ein erneuter Aufruf setzt fort.")
    return 0 if bericht.status in ("ok", "teilweise") else 2


def _drucke_treffer(ergebnis: dict, v=None) -> None:
    if v is not None:
        ortsnamen = ", ".join(
            f"{o['name']} (Stadtteil)" if o["typ"] == "stadtteil" else f"Stadtbezirk {o['name']}" for o in v.orte
        ) or "alle Orte"
        themen = ", ".join(v.themen) or "alle Themen"
        zeile = f"Verstanden: Ort: {ortsnamen} · Thema: {themen}"
        if v.plz:
            zeile += f" · PLZ {v.plz}"
        if v.freitext:
            zeile += " · Stichworte: " + ", ".join(v.freitext)
        if v.offen:
            zeile += " · geplant bzw. offen"
        print(zeile)
        for h in v.hinweise:
            print(f"Hinweis: {h}")
    anzahl = ergebnis["gesamt"]
    wort = "Vorgang" if anzahl == 1 else "Vorgänge"
    print(f"Gefunden: {anzahl} {wort}" + (" (Auswahl begrenzt)" if ergebnis.get("kandidaten_gekappt") else ""))
    for i, t in enumerate(ergebnis["treffer"], 1):
        print()
        print(f"{i}. „{t['titel']}“ · {t['art']} · {t['referenz']} · {t['datum_de']}")
        print(f"   Status: {t['status']['text']}")
        print(f"   {t['zusammenfassung']}")
        for e in t["verlauf"]:
            top = f" · TOP {e['top']['nummer']}" if e["top"]["nummer"] else ""
            ergebnis_txt = f" → {e['top']['ergebnis']}" if e["top"]["ergebnis"] else ""
            print(f"     · {e['datum_de'] or 'ohne Termin'} {e['gremium']}{top}{ergebnis_txt}")
        if t["links"]["ratsinfo_vorlage"]:
            print(f"   Link: {t['links']['ratsinfo_vorlage']}")
        if t["quelle"]["demo"]:
            print("   (Beispieldaten)")


def cmd_frage(args) -> int:
    stadt = lade_staedte(config_verzeichnis())[args.stadt]
    v = verstehe(" ".join(args.text), stadt, Gazetteer(stadt))
    with Speicher(_db_pfad(args)) as sp:
        ergebnis = suche(sp, Suchauftrag(stadt=stadt, orte=v.orte, plz=v.plz, themen=v.themen,
                                         freitext=v.freitext, von=v.von, bis=v.bis, offen=v.offen,
                                         limit=args.limit), heute())
    _drucke_treffer(ergebnis, v)
    return 0


def cmd_plz(args) -> int:
    if len(args.plz) != 5 or not args.plz.isdigit():
        print("Eine PLZ besteht aus genau 5 Ziffern.")
        return 2
    stadt = lade_staedte(config_verzeichnis())[args.stadt]
    eintrag = stadt.plz.get(args.plz)
    if eintrag is None:
        print(f"Die PLZ {args.plz} gehört nicht zu {stadt.name}.")
    elif not eintrag.stadtteile:
        print(f"Für PLZ {args.plz} ist noch keine Stadtteil-Zuordnung hinterlegt (nur ausdrückliche PLZ-Nennungen).")
    else:
        namen = ", ".join(stadt.stadtteile[s].name for s in eintrag.stadtteile)
        print(f"PLZ {args.plz} → Stadtteile: {namen} (Näherung, Quelle: {eintrag.quelle})")
    themen = [t.strip() for t in (args.thema or "").split(",") if t.strip()]
    with Speicher(_db_pfad(args)) as sp:
        ergebnis = suche(sp, Suchauftrag(stadt=stadt, plz=args.plz, themen=themen, limit=args.limit), heute())
    _drucke_treffer(ergebnis)
    return 0


def cmd_demo(args) -> int:
    stadt = lade_staedte(config_verzeichnis())[args.stadt]
    with Speicher(_db_pfad(args)) as sp:
        if args.loeschen:
            loesche_demo(sp, stadt)
            print("Beispieldaten entfernt.")
            return 0
        zaehler = lade_demo(sp, stadt)
    print(f"Beispieldaten geladen ({demo_quell_id(stadt)}): " + ", ".join(f"{k} {v}" for k, v in zaehler.items()))
    return 0


def cmd_serve(args) -> int:
    import uvicorn

    from .api import create_app

    os.environ["BEHOERDEN_DB"] = _db_pfad(args)
    app = create_app(_db_pfad(args))
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


# --- Parser --------------------------------------------------------------------

def baue_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="behoerdenmaster", description="Kommunale Vorgänge durchsuchen (Köln zuerst).")
    p.add_argument("--db", help="SQLite-Datei (Standard: data/behoerdenmaster.sqlite oder $BEHOERDEN_DB)")
    p.add_argument("--stadt", default="koeln", help="Stadt laut config/cities (Standard: koeln)")
    p.add_argument("-v", "--ausfuehrlich", action="store_true", help="Protokollierung auf INFO-Niveau")
    sub = p.add_subparsers(dest="befehl", required=True)

    sub.add_parser("status", help="Quellen, Bestand und letzte Läufe anzeigen")

    pr = sub.add_parser("probe", help="Erreichbarkeit der Quelle prüfen (höchstens 5 Anfragen)")
    pr.add_argument("--kontakt", help="Kontaktadresse (sonst $BEHOERDEN_KONTAKT)")
    pr.add_argument("--quelle", help="Quelle laut Konfiguration")

    pc = sub.add_parser("crawl", help="Daten von der Quelle abrufen und lokal speichern")
    pc.add_argument("--kontakt", help="Kontaktadresse (sonst $BEHOERDEN_KONTAKT)")
    pc.add_argument("--quelle", help="Quelle laut Konfiguration")
    pc.add_argument("--seit", help="Ersterlauf: Objekte seit diesem Datum (YYYY-MM-DD)")
    pc.add_argument("--max-anfragen", type=int, help="Anfragebudget für diesen Lauf (Abrufe in kleinen Schritten)")
    pc.add_argument("--nur", help=f"Nur bestimmte Entitäten: {', '.join(ENTITAETEN)}")
    pc.add_argument("--trotz-sperre", action="store_true", help="Abkühlzeit nach einer Sperre ignorieren (nicht empfohlen)")

    pf = sub.add_parser("frage", help="Frage in natürlicher Sprache beantworten")
    pf.add_argument("text", nargs="+", help="z. B. 'Welche Bauvorhaben sind in Köln Ehrenfeld geplant?'")
    pf.add_argument("--limit", type=int, default=10)

    pp = sub.add_parser("plz", help="Alle Vorgänge zu einer PLZ anzeigen")
    pp.add_argument("plz")
    pp.add_argument("--thema", help="Themen, kommagetrennt (z. B. bauen)")
    pp.add_argument("--limit", type=int, default=10)

    pd = sub.add_parser("demo", help="Beispieldaten laden (offline ausprobieren)")
    pd.add_argument("--loeschen", action="store_true", help="Beispieldaten wieder entfernen")

    ps = sub.add_parser("serve", help="Weboberfläche und API starten")
    ps.add_argument("--host", default="127.0.0.1", help="Standard 127.0.0.1; 0.0.0.0 für Netzwerkzugriff")
    ps.add_argument("--port", type=int, default=8000)
    return p


def main(argv: list[str] | None = None) -> int:
    args = baue_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.ausfuehrlich else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    befehle = {
        "status": cmd_status, "probe": cmd_probe, "crawl": cmd_crawl, "frage": cmd_frage,
        "plz": cmd_plz, "demo": cmd_demo, "serve": cmd_serve,
    }
    try:
        return befehle[args.befehl](args)
    except KeyboardInterrupt:
        print("Abgebrochen. Der Fortschritt bleibt gespeichert; ein erneuter Lauf setzt fort.", file=sys.stderr)
        return 130
