"""Suche über die lokale Datenbank: Filter, Ranking und Aufbereitung der Ergebnisse.

Es werden ausschließlich gespeicherte Daten gelesen. Die Stadt wird bei einer
Nutzeranfrage nie abgerufen (das würde Behörden-Server unnötig belasten).
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .auswertung import bestimme_status, datum_de, ergebnis_kategorie, zusammenfassung
from .config import Quelle, Stadt
from .meldungen import status_aus_zeile as meldung_status
from .meldungen import verlauf as meldung_verlauf
from .meldungen import zusammenfassung as meldung_zusammenfassung
from .normalize import norm
from .plan_auswertung import plan_status, plan_verlauf, plan_zusammenfassung, zeit_de
from .speicher import Speicher

ROLLEN_TEXT = {
    "vorlage": "Vorlage",
    "plandokument": "Planungsdokument",
    "tagesordnung": "Tagesordnung",
    "einladung": "Einladung",
    "ergebnisprotokoll": "Ergebnisprotokoll",
    "wortprotokoll": "Wortprotokoll",
    "protokoll": "Protokoll",
    "bekanntmachung": "Amtliche Bekanntmachung",
    "datensatz": "Originaldatei des Datensatzes",
    "dokument": "Dokument",
}
ROLLEN_REIHENFOLGE = list(ROLLEN_TEXT)
KANDIDATEN_MAX = 3000
CHUNK = 400


@dataclass
class Suchauftrag:
    stadt: Stadt
    orte: list[dict] = field(default_factory=list)
    plz: str | None = None
    themen: list[str] = field(default_factory=list)
    freitext: list[str] = field(default_factory=list)
    von: str | None = None
    bis: str | None = None
    offen: bool = False
    limit: int = 20
    offset: int = 0


def kurz_id(oparl_id: str) -> str:
    """Letzter Pfadteil einer OParl-ID (z. B. .../papers/vo/101373 -> 101373)."""
    return oparl_id.rstrip("/").rsplit("/", 1)[-1]


def _platz(n: int) -> str:
    return ",".join("?" for _ in range(n))


_ABKUERZUNGEN = ("chen", "lein", "ern", "em", "er", "es", "en", "e", "s", "n")
_STAMM_MINDESTLAENGE = 5


def stamm(wort: str) -> str:
    """Suchstamm eines normalisierten deutschen Worts.

    Die Suche arbeitet mit Teilstringtreffern auf dem normalisierten Suchtext. Ohne Stamm
    findet die natürliche Frageform „Abendmärkten“ den gespeicherten Titel „Abendmärkte“
    nicht, obwohl es derselbe Begriff ist – der Bestand wirkt dann leer, obwohl er es nicht
    ist. Es wird höchstens eine Endung entfernt, und nur wenn der Stamm lang genug bleibt:
    Kurzwörter wie „Rat“ oder „Bau“ sollen nicht plötzlich beliebig viel treffen.
    """
    for endung in _ABKUERZUNGEN:
        if wort.endswith(endung) and len(wort) - len(endung) >= _STAMM_MINDESTLAENGE:
            return wort[: -len(endung)]
    return wort


_STRASSE_LANG = "strasse"
_STRASSE_KURZ = "str"


def _suchbegriffe(wort: str) -> list[str]:
    """Vollform, Stamm und – bei Straßennamen – die abgekürzte Form, ohne Doppelte.

    Quellen kürzen Straßen unterschiedlich ab: Der Bestand kann „Feldgärtenstr.“ enthalten,
    während die Frage „Feldgärtenstraße“ schreibt. Beides ist dieselbe Straße, deshalb wird
    zusätzlich die Kurzform gesucht („feldgaertenstr“). Sonst bleibt eine richtige Frage
    ohne Treffer, obwohl der Vorgang gespeichert ist.
    """
    n = norm(wort)
    if not n:
        return []
    varianten = [n]
    s = stamm(n)
    if s != n:
        varianten.append(s)
    for v in list(varianten):
        # Nur wenn vor dem Kürzel noch ein Name steht: „feldgaertenstrasse“ → „feldgaertenstr“.
        # Ein bloßes „strasse“ wird nicht zu „str“ – das würde auf fast jeden Text passen.
        if v.endswith(_STRASSE_LANG) and len(v) > len(_STRASSE_LANG):
            varianten.append(v[: -len(_STRASSE_LANG)] + _STRASSE_KURZ)
    return list(dict.fromkeys(varianten))


def _bulk(db, vorlage: str, ids: list[str]) -> list:
    """Führt eine SQL-Abfrage mit IN-Liste in Blöcken aus."""
    zeilen: list = []
    for i in range(0, len(ids), CHUNK):
        teil = ids[i:i + CHUNK]
        zeilen.extend(db.execute(vorlage.format(ph=_platz(len(teil))), teil).fetchall())
    return zeilen


def _quellen_cfg(stadt: Stadt) -> dict[str, Quelle]:
    return {stadt.quell_id(q): q for q in stadt.quellen}


def _cfg_fuer(quellen: dict[str, Quelle], quelle_id: str) -> Quelle | None:
    if quelle_id in quellen:
        return quellen[quelle_id]
    if quelle_id.endswith("-demo"):
        return quellen.get(quelle_id.removesuffix("-demo"))
    if any(quelle_id == qid.split(":", 1)[0] + ":demo" for qid in quellen):
        return next((q for q in quellen.values() if q.typ == "oparl"), None)
    return None


def _lade_verlauf(speicher: Speicher, stadt: Stadt, vorgang_ids: list[str], heute: date) -> dict[str, list[dict]]:
    db = speicher.db
    heute_iso = heute.isoformat()
    zeilen = _bulk(db, """
        SELECT b.id AS beratung_id, b.vorgang_id, b.sitzung_id, b.top_id, b.gremium_ids, b.rolle,
               b.federfuehrend, v.quelle_id AS quelle_id, s.name AS sitzung_name, s.datum AS sitzung_datum,
               s.abgesagt AS sitzung_abgesagt, s.gremium_ids AS sitzung_gremium_ids,
               t.nummer AS top_nummer, t.name AS top_name, t.ergebnis AS top_ergebnis,
               t.reihenfolge AS top_reihenfolge
        FROM beratung b
        JOIN vorgang v ON v.id = b.vorgang_id
        LEFT JOIN sitzung s ON s.id = b.sitzung_id AND s.geloescht = 0
        LEFT JOIN tagesordnungspunkt t ON t.id = b.top_id AND t.geloescht = 0
        WHERE b.geloescht = 0 AND b.vorgang_id IN ({ph})
    """, vorgang_ids)

    gremium_ids: set[str] = set()
    for z in zeilen:
        gremium_ids.update(json.loads(z["gremium_ids"] or "[]"))
        gremium_ids.update(json.loads(z["sitzung_gremium_ids"] or "[]"))
    namen = {}
    if gremium_ids:
        namen = {r["id"]: (r["name"] or r["id"]) for r in _bulk(
            db, "SELECT id, name FROM gremium WHERE id IN ({ph})", sorted(gremium_ids))}

    quellen = _quellen_cfg(stadt)
    verlauf: dict[str, list[dict]] = defaultdict(list)
    for z in zeilen:
        gremien = json.loads(z["gremium_ids"] or "[]") or json.loads(z["sitzung_gremium_ids"] or "[]")
        gremium_text = " / ".join(namen.get(g, f"Gremium {kurz_id(g)}") for g in gremien) or "Gremium unbekannt"
        datum = z["sitzung_datum"] or ""
        ergebnis = z["top_ergebnis"] or ""
        abgesagt = bool(z["sitzung_abgesagt"])
        cfg = _cfg_fuer(quellen, z["quelle_id"])
        sitzung_url = None
        if cfg and cfg.web_sitzung and z["sitzung_id"]:
            sitzung_url = cfg.web_sitzung.format(id=kurz_id(z["sitzung_id"]))
        verlauf[z["vorgang_id"]].append({
            "datum": datum,
            "datum_de": datum_de(datum),
            "gremium": gremium_text,
            "sitzung": {"id": z["sitzung_id"], "name": z["sitzung_name"] or "", "url": sitzung_url,
                        "abgesagt": abgesagt},
            "top": {"nummer": z["top_nummer"] or "", "name": z["top_name"] or "", "ergebnis": ergebnis},
            "rolle": z["rolle"] or "",
            "federfuehrend": bool(z["federfuehrend"]),
            "kategorie": ergebnis_kategorie(ergebnis),
            "anstehend": bool(datum) and datum >= heute_iso and not ergebnis and not abgesagt,
            "top_nummer": z["top_nummer"] or "",
            "_reihenfolge": z["top_reihenfolge"] or 0,
        })
    for liste in verlauf.values():
        liste.sort(key=lambda e: (e["datum"] or "9999", e["_reihenfolge"]))
        for e in liste:
            e.pop("_reihenfolge", None)
    return verlauf


def _orte_anzeige(stadt: Stadt, orte_rows: list) -> tuple[list[dict], list[str]]:
    stadtteile = sorted({r["schluessel"] for r in orte_rows if r["typ"] == "stadtteil"})
    bezirke = sorted({r["schluessel"] for r in orte_rows if r["typ"] == "bezirk"})
    plz = sorted({r["schluessel"] for r in orte_rows if r["typ"] == "plz"})
    herkunft = sorted({r["quelle"] for r in orte_rows})
    # Je Ort die Herkunft mitführen: „straße“ heißt, dass der Stadtteil aus einem
    # Straßennamen des Verzeichnisses abgeleitet wurde – eine Näherung, keine Angabe der Quelle.
    je_ort: dict[tuple[str, str], list[str]] = defaultdict(list)
    for r in orte_rows:
        if r["quelle"] not in je_ort[(r["typ"], r["schluessel"])]:
            je_ort[(r["typ"], r["schluessel"])].append(r["quelle"])

    def eintrag(typ: str, schluessel: str, name: str) -> dict:
        return {"typ": typ, "schluessel": schluessel, "name": name,
                "herkunft": sorted(je_ort.get((typ, schluessel), []))}

    anzeige = []
    for nr in stadtteile:
        if nr in stadt.stadtteile:
            anzeige.append(eintrag("stadtteil", nr, stadt.stadtteile[nr].name))
    for nr in bezirke:
        bezirk_hat_stadtteil = any(stadt.stadtteile[s].bezirk == nr for s in stadtteile if s in stadt.stadtteile)
        if not bezirk_hat_stadtteil and nr in stadt.bezirke:
            anzeige.append(eintrag("bezirk", nr, f"Stadtbezirk {stadt.bezirke[nr]}"))
    for p in plz:
        anzeige.append(eintrag("plz", p, f"PLZ {p}"))
    return anzeige, herkunft


def _bewerte(kand, orte_rows: list, auftrag: Suchauftrag, status: dict, heute: date,
             begriffe: list[str], gebiet: dict[str, frozenset[str]]) -> int:
    """Punkte für die Reihenfolge der Treffer – kein Ausschluss, nur Gewichtung."""
    stadtteile, bezirke, plz = gebiet["stadtteile"], gebiet["bezirke"], gebiet["plz"]
    punkte = 0
    for o in orte_rows:
        if o["typ"] == "stadtteil" and o["schluessel"] in stadtteile:
            # Ein im Betreff genannter Stadtteil ist die genaueste Angabe; ein aus einer
            # Straße abgeleiteter Stadtteil ist eine Näherung und zählt deshalb weniger.
            punkte += 5 if o["quelle"] == "betreff" else 3
        elif o["typ"] == "bezirk" and o["schluessel"] in bezirke:
            punkte += 2 if o["quelle"] == "betreff" else 1
        elif o["typ"] == "plz" and o["schluessel"] in plz:
            # Ausdrücklich gesuchte PLZ zählt am höchsten, eine nur mitgedachte PLZ des
            # Stadtteils darunter – sonst stünden PLZ-Nennungen vor Stadtteil-Treffern.
            punkte += 4 if o["schluessel"] == auftrag.plz else 2
    suchtext = kand["suchtext"] or ""
    titel = norm(kand["titel"])
    if begriffe:
        if any(b in titel for b in begriffe):
            punkte += 4
        elif any(b in suchtext for b in begriffe):
            punkte += 2
    for w in auftrag.freitext:
        if norm(w) in suchtext:
            punkte += 1
    if auftrag.offen and status["offen"]:
        punkte += 3
    if kand["datum"] and kand["datum"] >= (heute - timedelta(days=730)).isoformat():
        punkte += 1
    return punkte


def _dto(stadt: Stadt, kand, orte_rows: list, verlauf: list[dict], dokumente: list,
         status: dict, heute: date, quellen: dict[str, Quelle], quelle_meta: dict,
         plan: dict | None = None, staende: list[dict] | None = None,
         meldung: dict | None = None) -> dict:
    cfg = _cfg_fuer(quellen, kand["quelle_id"])
    vid = kand["id"]
    orte, herkunft = _orte_anzeige(stadt, orte_rows)
    dok_liste = []
    for d in sorted(dokumente, key=lambda d: (ROLLEN_REIHENFOLGE.index(d["rolle"])
                                               if d["rolle"] in ROLLEN_REIHENFOLGE else 99, d["name"])):
        dok_liste.append({
            "name": d["name"],
            "rolle": d["rolle"],
            "rolle_text": ROLLEN_TEXT.get(d["rolle"], "Dokument"),
            "url": d["url"] or None,
            "dateiname": d["dateiname"],
            "datum": d["datum"],
            "datum_de": datum_de(d["datum"]),
        })
    ortsnamen = [o["name"] for o in orte]
    text = zusammenfassung(kand["titel"], kand["art"], kand["referenz"], kand["datum"], status,
                           ortsnamen, len(dok_liste))
    meta = quelle_meta.get(kand["quelle_id"], {})
    typ = "bauleitplanung" if plan is not None else (cfg.typ if cfg else meta.get("typ", "unbekannt"))
    original = kand["web"] or (cfg.web_vorlage.format(id=kurz_id(vid)) if cfg and cfg.web_vorlage else None)
    hinweise = []
    if meldung is not None:
        text = meldung_zusammenfassung(kand["titel"], kand["art"], meldung)
        verlauf = meldung_verlauf(kand["datum"], meldung, original or vid, heute.isoformat())
        hinweise.append("Übernommen wurden nur Titel, Statusangabe, Kurztext und Link der Quelle; "
                        "Fristen, Inhalte und Unterlagen stehen im verlinkten Original.")
        if meldung["art_quelle"] == "Öffentliche Bekanntmachung von Bauleitplänen" or kand["art"] == "Bekanntmachung":
            hinweise.append("Der Inhalt der Bekanntmachung (PDF) wurde nicht geladen und nicht ausgewertet.")
            hinweise.append("Aus der Liste lässt sich kein Beteiligungszeitraum ablesen; maßgeblich ist die "
                            "Bekanntmachung selbst. Der Eintrag gilt deshalb nicht automatisch als offen.")
        if meldung["gesehen"]:
            hinweise.append("Statusangabe der Quelle, zuletzt abgerufen am "
                            + datum_de(meldung["gesehen"]) + ".")
        if not kand["datum"]:
            hinweise.append("Die Liste nennt für diesen Eintrag kein Datum; die Statuszeile der Quelle "
                            "steht oben, Einzelheiten auf der Projektseite.")
    if plan is not None:
        text = plan_zusammenfassung(kand["titel"], plan, status)
        verlauf = plan_verlauf(plan, staende or [], original or vid, heute)
        hinweise.append("Der Verlauf zeigt den aktuellen Beteiligungszeitraum und lokal festgestellte Änderungen, "
                        "nicht die vollständige amtliche Verfahrensgeschichte.")
        if plan["detail_status"] != "ok":
            hinweise.append("Dokumentlinks der Detailseite sind noch nicht vollständig erfasst bzw. erneut geprüft; siehe Originalverfahren.")
        if meta.get("demo"):
            hinweise.append("Offline-Demo: Beschreibung und Dokumentlinks sind ausgewählte Auszüge, kein vollständiger Bestand.")
        hinweise.append("Beteiligungsfristen aus der Quelle übernommen; für verbindliche Fristen die Originalseite prüfen.")
    return {
        "id": vid,
        "titel": kand["titel"],
        "referenz": kand["referenz"],
        "art": kand["art"],
        "datum": kand["datum"],
        "datum_de": datum_de(kand["datum"]),
        "status": {
            "kurz": status["kurz"],
            "text": status["text"],
            "offen": status["offen"],
        },
        "orte": orte,
        "orte_herkunft": herkunft,
        "zusammenfassung": text,
        "verlauf": [dict(e) for e in verlauf],
        "hinweise": hinweise,
        "beteiligung": ({"phase": plan["phase"], "beginn": plan["beginn"], "ende": plan["ende"],
                         "beginn_de": zeit_de(plan["beginn"]).split(",")[0], "ende_de": zeit_de(plan["ende"]).split(",")[0],
                         "beginn_zeit_de": zeit_de(plan["beginn"]), "ende_zeit_de": zeit_de(plan["ende"]),
                         "organisation": plan["organisation"], "beschreibung": plan["beschreibung"],
                         "zuletzt_gesehen": plan["gesehen"], "gelistet": bool(plan["gelistet"])}
                        if plan is not None else None),
        "dokumente": dok_liste,
        "meldung": ({
            "art_quelle": meldung["art_quelle"],
            "verfahrensschritt": meldung["verfahrensschritt"],
            "status_text": meldung["status_text"],
            "kurztext": meldung["kurztext"],
            "zusatz": meldung["zusatz"],
            "gesehen": meldung["gesehen"],
            "gesehen_de": datum_de(meldung["gesehen"]),
        } if meldung is not None else None),
        "links": {
            "original": original,
            "original_text": ("Verfahren in Bauleitplanung Online öffnen" if typ == "bauleitplanung"
                              else "Vorlage im Ratsinformationssystem öffnen" if typ == "oparl"
                              else "Bekanntmachung im Original öffnen" if typ == "bekanntmachungen"
                              else "Projektseite der Stadt öffnen" if typ == "beteiligung"
                              else "Originalverfahren öffnen"),
            "ratsinfo_vorlage": original if typ == "oparl" else None,
            "oparl": vid if typ == "oparl" else None,
        },
        "quelle": {"id": kand["quelle_id"], "name": meta.get("name", ""), "demo": bool(meta.get("demo")), "typ": typ},
    }


def _quelle_meta(speicher: Speicher) -> dict[str, dict]:
    return {r["id"]: {"name": r["name"], "demo": bool(r["demo"]), "typ": r["typ"], "status": r["status"],
                      "letzter_erfolg": r["letzter_erfolg"]} for r in speicher.quellen()}


def suche(speicher: Speicher, auftrag: Suchauftrag, heute: date, *, zeitpunkt: datetime | None = None) -> dict:
    """Sucht Vorgänge – Stichworte verengen immer, und niemand bleibt ohne Antwort.

    Die Stichworte gelten zunächst **zusätzlich** zu Ort und Thema (UND): Wer „Erlenweg
    Bickendorf“ sucht, will nicht alle Vorgänge Bickendorfs sehen. Findet das nichts, wird
    stufenweise erweitert – jede Stufe steht im Ergebnis:

    1. alle Bedingungen,
    2. ohne die Stichworte (nur sinnvoll, wenn Ort, Thema oder Zeitraum die Suche tragen),
    3. mindestens ein Stichwort (statt aller).

    Ohne diese Stufen war ein Tippfehler ein leeres Ergebnis, obwohl der Bestand den
    Vorgang kennt; ohne die UND-Verknüpfung war ein zusätzliches Wort wirkungslos.
    """
    ergebnis = _suche(speicher, auftrag, heute, freitext_modus="und", zeitpunkt=zeitpunkt)
    if ergebnis["gesamt"] == 0 and auftrag.freitext and (auftrag.orte or auftrag.plz or auftrag.themen):
        ergebnis = _suche(speicher, auftrag, heute, freitext_modus="ohne", zeitpunkt=zeitpunkt)
        ergebnis["freitext_verworfen"] = ergebnis["gesamt"] > 0
    if ergebnis["gesamt"] == 0 and len(auftrag.freitext) >= 2:
        ergebnis = _suche(speicher, auftrag, heute, freitext_modus="oder", zeitpunkt=zeitpunkt)
        ergebnis["freitext_oder"] = ergebnis["gesamt"] > 0
    return ergebnis


def _suche(speicher: Speicher, auftrag: Suchauftrag, heute: date, freitext_modus: str,
           zeitpunkt: datetime | None = None) -> dict:
    stadt = auftrag.stadt
    db = speicher.db
    bedingungen = ["v.geloescht = 0", "v.quelle_id IN (SELECT id FROM quelle WHERE stadt = ?)"]
    params: list = [stadt.schluessel]

    # Ortsangabe auf Stadtteil, Bezirk und PLZ ausdehnen: Ein Vorgang trägt seinen Ortsbezug
    # je Quelle nur auf einer Ebene (nur Stadtteil, nur Bezirk oder nur PLZ). Wird nicht
    # ausgedehnt, findet eine Suche nach dem Stadtteil jeden Vorgang mit bloßem PLZ-Bezug
    # nicht – genau das war der Grund, warum manche Treffer nur über die Straße zu finden waren.
    gebiet = stadt.ortsgebiet(
        stadtteile=[o["schluessel"] for o in auftrag.orte if o["typ"] == "stadtteil"],
        bezirke=[o["schluessel"] for o in auftrag.orte if o["typ"] == "bezirk"],
        plz=auftrag.plz,
    )
    ortsklauseln, ortsparams = [], []
    if gebiet["plz"]:
        ortsklauseln.append(f"(o.typ = 'plz' AND o.schluessel IN ({_platz(len(gebiet['plz']))}))")
        ortsparams += sorted(gebiet["plz"])
    if gebiet["stadtteile"]:
        ortsklauseln.append(f"(o.typ = 'stadtteil' AND o.schluessel IN ({_platz(len(gebiet['stadtteile']))}))")
        ortsparams += sorted(gebiet["stadtteile"])
    if gebiet["bezirke"]:
        ortsklauseln.append(f"(o.typ = 'bezirk' AND o.schluessel IN ({_platz(len(gebiet['bezirke']))}))")
        ortsparams += sorted(gebiet["bezirke"])
    if ortsklauseln:
        bedingungen.append(
            "EXISTS (SELECT 1 FROM ortsbezug o WHERE o.vorgang_id = v.id AND ("
            + " OR ".join(ortsklauseln) + "))"
        )
        params += ortsparams

    begriffe: list[str] = []
    for schluessel in auftrag.themen:
        thema = stadt.thema(schluessel)
        if thema:
            begriffe += list(thema.begriffe)
    if begriffe:
        bedingungen.append("(" + " OR ".join("instr(v.suchtext, ?) > 0" for _ in begriffe) + ")")
        params += begriffe

    if auftrag.freitext and freitext_modus != "ohne":
        if freitext_modus == "oder":
            woerter = [w for w in auftrag.freitext if len(norm(w)) >= 4] or list(auftrag.freitext)
            varianten = [b for w in woerter for b in _suchbegriffe(w)]
            if varianten:
                bedingungen.append("(" + " OR ".join("instr(v.suchtext, ?) > 0" for _ in varianten) + ")")
                params += varianten
        else:
            for w in auftrag.freitext:
                varianten = _suchbegriffe(w)
                if not varianten:
                    continue
                # Vollform ODER Stamm desselben Worts; über mehrere Wörter bleibt es UND.
                bedingungen.append("(" + " OR ".join("instr(v.suchtext, ?) > 0" for _ in varianten) + ")")
                params += varianten
    if auftrag.von:
        bedingungen.append("v.datum >= ?")
        params.append(auftrag.von)
    if auftrag.bis:
        bedingungen.append("v.datum < ?")
        params.append(auftrag.bis)

    sql = (
        "SELECT v.id, v.quelle_id, v.referenz, v.titel, v.art, v.datum, v.suchtext, v.web FROM vorgang v "
        f"WHERE {' AND '.join(bedingungen)} ORDER BY v.datum DESC LIMIT {KANDIDATEN_MAX}"
    )
    kandidaten = db.execute(sql, params).fetchall()
    gesamt_kandidaten = len(kandidaten)
    if not kandidaten:
        return {"gesamt": 0, "treffer": [], "kandidaten_gekappt": False,
                "freitext_oder": False, "freitext_verworfen": False}

    ids = [k["id"] for k in kandidaten]
    orte_map: dict[str, list] = defaultdict(list)
    for r in _bulk(db, "SELECT vorgang_id, typ, schluessel, quelle FROM ortsbezug WHERE vorgang_id IN ({ph})", ids):
        orte_map[r["vorgang_id"]].append(r)
    dok_map: dict[str, list[dict]] = defaultdict(list)
    for r in _bulk(db, "SELECT * FROM dokument WHERE geloescht = 0 AND vorgang_id IN ({ph})", ids):
        dok_map[r["vorgang_id"]].append(dict(r))
    verlauf_map = _lade_verlauf(speicher, stadt, ids, heute)
    plan_map = {r["vorgang_id"]: dict(r) for r in _bulk(
        db, "SELECT * FROM planverfahren WHERE vorgang_id IN ({ph})", ids)}
    meldung_map = speicher.meldungen(ids)
    status_map = {vid: (plan_status(plan_map[vid], heute, zeitpunkt) if vid in plan_map
                        else (meldung_status(meldung_map[vid]) if vid in meldung_map
                              else bestimme_status(verlauf_map.get(vid, [])))) for vid in ids}

    bewertet = [(
        _bewerte(k, orte_map.get(k["id"], []), auftrag, status_map[k["id"]], heute, begriffe, gebiet),
        k,
    ) for k in kandidaten]
    bewertet.sort(key=lambda t: t[1]["datum"] or "", reverse=True)
    bewertet.sort(key=lambda t: t[0], reverse=True)  # stabil: bei Gleichstand bleibt Datum absteigend

    seite = bewertet[auftrag.offset: auftrag.offset + auftrag.limit]
    quellen = _quellen_cfg(stadt)
    meta = _quelle_meta(speicher)
    staende_map: dict[str, list[dict]] = defaultdict(list)
    plan_ids = [k["id"] for _, k in seite if k["id"] in plan_map]
    for r in _bulk(db, "SELECT * FROM planstand WHERE vorgang_id IN ({ph}) ORDER BY id", plan_ids):
        staende_map[r["vorgang_id"]].append(dict(r))
    treffer = []
    for punkte, k in seite:
        vid = k["id"]
        dto = _dto(stadt, k, orte_map.get(vid, []), verlauf_map.get(vid, []), dok_map.get(vid, []),
                   status_map[vid], heute, quellen, meta, plan_map.get(vid), staende_map.get(vid, []),
                   meldung_map.get(vid))
        dto["punkte"] = punkte
        treffer.append(dto)
    return {"gesamt": gesamt_kandidaten, "treffer": treffer,
            "kandidaten_gekappt": gesamt_kandidaten >= KANDIDATEN_MAX,
            "freitext_oder": False, "freitext_verworfen": False}


def vorgang_detail(speicher: Speicher, stadt: Stadt, vorgang_id: str, heute: date, *,
                   zeitpunkt: datetime | None = None) -> dict | None:
    zeile = speicher.db.execute(
        "SELECT v.id, v.quelle_id, v.referenz, v.titel, v.art, v.datum, v.suchtext, v.web FROM vorgang v "
        "WHERE v.id = ? AND v.geloescht = 0", (vorgang_id,)
    ).fetchone()
    if zeile is None:
        return None
    quelle = speicher.db.execute("SELECT stadt FROM quelle WHERE id=?", (zeile["quelle_id"],)).fetchone()
    if not quelle or quelle["stadt"] != stadt.schluessel:
        return None
    orte_rows = speicher.db.execute(
        "SELECT vorgang_id, typ, schluessel, quelle FROM ortsbezug WHERE vorgang_id = ?", (vorgang_id,)
    ).fetchall()
    dokumente = [dict(r) for r in speicher.db.execute(
        "SELECT * FROM dokument WHERE geloescht = 0 AND vorgang_id = ?", (vorgang_id,)).fetchall()]
    verlauf = _lade_verlauf(speicher, stadt, [vorgang_id], heute).get(vorgang_id, [])
    plan_row = speicher.db.execute("SELECT * FROM planverfahren WHERE vorgang_id=?", (vorgang_id,)).fetchone()
    plan = dict(plan_row) if plan_row else None
    staende = [dict(r) for r in speicher.db.execute(
        "SELECT * FROM planstand WHERE vorgang_id=? ORDER BY id", (vorgang_id,)).fetchall()]
    meldung = speicher.meldungen([vorgang_id]).get(vorgang_id)
    status = (plan_status(plan, heute, zeitpunkt) if plan
              else meldung_status(meldung) if meldung
              else bestimme_status(verlauf))
    return _dto(stadt, zeile, orte_rows, verlauf, dokumente, status, heute,
                _quellen_cfg(stadt), _quelle_meta(speicher), plan, staende, meldung)
