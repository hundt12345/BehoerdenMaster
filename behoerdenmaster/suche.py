"""Suche über die lokale Datenbank: Filter, Ranking und Aufbereitung der Ergebnisse.

Es werden ausschließlich gespeicherte Daten gelesen. Die Stadt wird bei einer
Nutzeranfrage nie abgerufen (das würde Behörden-Server unnötig belasten).
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from .auswertung import bestimme_status, datum_de, ergebnis_kategorie, zusammenfassung
from .config import Quelle, Stadt
from .normalize import norm
from .speicher import Speicher

ROLLEN_TEXT = {
    "vorlage": "Vorlage",
    "tagesordnung": "Tagesordnung",
    "einladung": "Einladung",
    "ergebnisprotokoll": "Ergebnisprotokoll",
    "wortprotokoll": "Wortprotokoll",
    "protokoll": "Protokoll",
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
    """Konfiguration einer Quelle für Web-Links. Beispieldaten nutzen die Vorlagen der Stadt,
    weil sie aus deren Ratsinformationssystem stammen."""
    if quelle_id in quellen:
        return quellen[quelle_id]
    return next((q for q in quellen.values() if q.web_vorlage or q.web_sitzung), None)


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
    anzeige = []
    for nr in stadtteile:
        if nr in stadt.stadtteile:
            anzeige.append({"typ": "stadtteil", "schluessel": nr, "name": stadt.stadtteile[nr].name})
    for nr in bezirke:
        bezirk_hat_stadtteil = any(stadt.stadtteile[s].bezirk == nr for s in stadtteile if s in stadt.stadtteile)
        if not bezirk_hat_stadtteil and nr in stadt.bezirke:
            anzeige.append({"typ": "bezirk", "schluessel": nr, "name": f"Stadtbezirk {stadt.bezirke[nr]}"})
    for p in plz:
        anzeige.append({"typ": "plz", "schluessel": p, "name": f"PLZ {p}"})
    return anzeige, herkunft


def _bewerte(kand, orte_rows: list, auftrag: Suchauftrag, status: dict, heute: date,
             begriffe: list[str]) -> int:
    stadtteile = {o["schluessel"] for o in auftrag.orte if o["typ"] == "stadtteil"}
    bezirke = {o["schluessel"] for o in auftrag.orte if o["typ"] == "bezirk"}
    if auftrag.plz and auftrag.plz in auftrag.stadt.plz:
        stadtteile |= set(auftrag.stadt.plz[auftrag.plz].stadtteile)
    punkte = 0
    for o in orte_rows:
        if o["typ"] == "stadtteil" and o["schluessel"] in stadtteile:
            punkte += 5 if o["quelle"] == "betreff" else 3
        elif o["typ"] == "bezirk" and o["schluessel"] in bezirke:
            punkte += 2 if o["quelle"] == "betreff" else 1
        elif o["typ"] == "plz" and o["schluessel"] == auftrag.plz:
            punkte += 4
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
         status: dict, heute: date, quellen: dict[str, Quelle], quelle_meta: dict) -> dict:
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
        "dokumente": dok_liste,
        "links": {
            "ratsinfo_vorlage": cfg.web_vorlage.format(id=kurz_id(vid)) if cfg and cfg.web_vorlage else None,
            "oparl": vid,
        },
        "quelle": {"id": kand["quelle_id"], "name": meta.get("name", ""), "demo": bool(meta.get("demo"))},
    }


def _quelle_meta(speicher: Speicher) -> dict[str, dict]:
    return {r["id"]: {"name": r["name"], "demo": bool(r["demo"]), "status": r["status"],
                      "letzter_erfolg": r["letzter_erfolg"]} for r in speicher.quellen()}


def suche(speicher: Speicher, auftrag: Suchauftrag, heute: date) -> dict:
    """Sucht Vorgänge. Ohne Ort- und Themenfilter müssen zunächst alle Stichworte vorkommen.
    Findet das nichts, gelten mindestens ein Stichwort (Rückfall, ist im Ergebnis gekennzeichnet)."""
    ergebnis = _suche(speicher, auftrag, heute, freitext_oder=False)
    if (ergebnis["gesamt"] == 0 and len(auftrag.freitext) >= 2
            and not (auftrag.orte or auftrag.plz or auftrag.themen)):
        ergebnis = _suche(speicher, auftrag, heute, freitext_oder=True)
        ergebnis["freitext_oder"] = ergebnis["gesamt"] > 0
    return ergebnis


def _suche(speicher: Speicher, auftrag: Suchauftrag, heute: date, freitext_oder: bool) -> dict:
    stadt = auftrag.stadt
    db = speicher.db
    bedingungen = ["v.geloescht = 0", "v.quelle_id IN (SELECT id FROM quelle WHERE stadt = ?)"]
    params: list = [stadt.schluessel]

    stadtteile = [o["schluessel"] for o in auftrag.orte if o["typ"] == "stadtteil"]
    bezirke = [o["schluessel"] for o in auftrag.orte if o["typ"] == "bezirk"]
    if auftrag.plz:
        if auftrag.plz in stadt.plz:
            stadtteile += list(stadt.plz[auftrag.plz].stadtteile)
    ortsklauseln, ortsparams = [], []
    if auftrag.plz:
        ortsklauseln.append("(o.typ = 'plz' AND o.schluessel = ?)")
        ortsparams.append(auftrag.plz)
    if stadtteile:
        ortsklauseln.append(f"(o.typ = 'stadtteil' AND o.schluessel IN ({_platz(len(stadtteile))}))")
        ortsparams += stadtteile
    if bezirke:
        ortsklauseln.append(f"(o.typ = 'bezirk' AND o.schluessel IN ({_platz(len(bezirke))}))")
        ortsparams += bezirke
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

    if auftrag.freitext and not (ortsklauseln or begriffe):
        if freitext_oder:
            woerter = [norm(w) for w in auftrag.freitext if len(norm(w)) >= 4] or [norm(w) for w in auftrag.freitext]
            bedingungen.append("(" + " OR ".join("instr(v.suchtext, ?) > 0" for _ in woerter) + ")")
            params += woerter
        else:
            for w in auftrag.freitext:
                bedingungen.append("instr(v.suchtext, ?) > 0")
                params.append(norm(w))
    if auftrag.von:
        bedingungen.append("v.datum >= ?")
        params.append(auftrag.von)
    if auftrag.bis:
        bedingungen.append("v.datum < ?")
        params.append(auftrag.bis)

    sql = (
        "SELECT v.id, v.quelle_id, v.referenz, v.titel, v.art, v.datum, v.suchtext FROM vorgang v "
        f"WHERE {' AND '.join(bedingungen)} ORDER BY v.datum DESC LIMIT {KANDIDATEN_MAX}"
    )
    kandidaten = db.execute(sql, params).fetchall()
    gesamt_kandidaten = len(kandidaten)
    if not kandidaten:
        return {"gesamt": 0, "treffer": [], "kandidaten_gekappt": False, "freitext_oder": False}

    ids = [k["id"] for k in kandidaten]
    orte_map: dict[str, list] = defaultdict(list)
    for r in _bulk(db, "SELECT vorgang_id, typ, schluessel, quelle FROM ortsbezug WHERE vorgang_id IN ({ph})", ids):
        orte_map[r["vorgang_id"]].append(r)
    dok_map: dict[str, list[dict]] = defaultdict(list)
    for r in _bulk(db, "SELECT * FROM dokument WHERE geloescht = 0 AND vorgang_id IN ({ph})", ids):
        dok_map[r["vorgang_id"]].append(dict(r))
    verlauf_map = _lade_verlauf(speicher, stadt, ids, heute)
    status_map = {vid: bestimme_status(verlauf_map.get(vid, [])) for vid in ids}

    bewertet = [(
        _bewerte(k, orte_map.get(k["id"], []), auftrag, status_map[k["id"]], heute, begriffe),
        k,
    ) for k in kandidaten]
    bewertet.sort(key=lambda t: t[1]["datum"] or "", reverse=True)
    bewertet.sort(key=lambda t: t[0], reverse=True)  # stabil: bei Gleichstand bleibt Datum absteigend

    seite = bewertet[auftrag.offset: auftrag.offset + auftrag.limit]
    quellen = _quellen_cfg(stadt)
    meta = _quelle_meta(speicher)
    treffer = []
    for punkte, k in seite:
        vid = k["id"]
        dto = _dto(stadt, k, orte_map.get(vid, []), verlauf_map.get(vid, []), dok_map.get(vid, []),
                   status_map[vid], heute, quellen, meta)
        dto["punkte"] = punkte
        treffer.append(dto)
    return {"gesamt": gesamt_kandidaten, "treffer": treffer,
            "kandidaten_gekappt": gesamt_kandidaten >= KANDIDATEN_MAX, "freitext_oder": False}


def vorgang_detail(speicher: Speicher, stadt: Stadt, vorgang_id: str, heute: date) -> dict | None:
    zeile = speicher.db.execute(
        "SELECT v.id, v.quelle_id, v.referenz, v.titel, v.art, v.datum, v.suchtext FROM vorgang v "
        "WHERE v.id = ? AND v.geloescht = 0", (vorgang_id,)
    ).fetchone()
    if zeile is None:
        return None
    orte_rows = speicher.db.execute(
        "SELECT vorgang_id, typ, schluessel, quelle FROM ortsbezug WHERE vorgang_id = ?", (vorgang_id,)
    ).fetchall()
    dokumente = [dict(r) for r in speicher.db.execute(
        "SELECT * FROM dokument WHERE geloescht = 0 AND vorgang_id = ?", (vorgang_id,)).fetchall()]
    verlauf = _lade_verlauf(speicher, stadt, [vorgang_id], heute).get(vorgang_id, [])
    status = bestimme_status(verlauf)
    return _dto(stadt, zeile, orte_rows, verlauf, dokumente, status, heute,
                _quellen_cfg(stadt), _quelle_meta(speicher))
