"""OParl-Adapter (Versionen 1.0 und 1.1): Listen mit Paginierung abrufen und Objekte abbilden.

Die Abbildung ist tolerant: Fehlende Felder sind erlaubt, und Verweise dürfen
als Objekt oder nur als URL vorliegen (OParl erlaubt beides je nach Implementierung).
Eingebettete Objekte (Beratungsfolge, TOPs, Dateien) werden direkt übernommen.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterator
from urllib.parse import urljoin

from .abruf import Abrufer
from .normalize import norm


@dataclass
class Seite:
    url: str
    objekte: list[dict]
    naechste: str | None


class OParlQuelle:
    def __init__(self, abrufer: Abrufer, system_url: str, body_id: str | None = None):
        self.abrufer = abrufer
        self.system_url = system_url
        self.body_id = body_id

    def seiten(self, url: str, params: dict[str, str] | None = None, start: str | None = None) -> Iterator[Seite]:
        """Alle Seiten einer OParl-Liste. Folgt ``links.next`` bis zum Ende.

        Mit ``start`` wird ein früher gespeicherter Fortsetzungspunkt genutzt; dessen
        Parameter (z. B. modified_since) stehen bereits in der URL.
        """
        aktuell = start or url
        parameter = None if start else params
        besucht: set[str] = set()
        while aktuell:
            if aktuell in besucht:
                raise ValueError(f"Paginierungsschleife erkannt bei {aktuell}")
            besucht.add(aktuell)
            daten = self.abrufer.get_json(aktuell, params=parameter)
            parameter = None
            objekte = [o for o in daten.get("data", []) if isinstance(o, dict)]
            verweis = (daten.get("links") or {}).get("next")
            naechste = urljoin(aktuell, verweis) if verweis else None
            yield Seite(url=aktuell, objekte=objekte, naechste=naechste)
            aktuell = naechste

    def system(self) -> dict:
        return self.abrufer.get_json(self.system_url)

    def body(self) -> dict:
        system = self.system()
        bodies_url = system.get("body")
        if not bodies_url:
            raise ValueError("System-Objekt enthält keinen 'body'-Verweis")
        for seite in self.seiten(bodies_url):
            for body in seite.objekte:
                if self.body_id is None or str(body.get("id", "")).endswith("/" + self.body_id):
                    return body
        raise ValueError(f"Körperschaft '{self.body_id or '(erste)'}' nicht gefunden")


# --- Abbildung auf Speicherzeilen -------------------------------------------

def _str(wert) -> str:
    return "" if wert is None else str(wert)


def _ist_deleted(obj: dict) -> bool:
    return bool(obj.get("deleted"))


def rolle_aus_name(name: str) -> str:
    n = norm(name)
    if "tagesordnung" in n:
        return "tagesordnung"
    if "einladung" in n:
        return "einladung"
    if "niederschrift" in n or "protokoll" in n:
        return "protokoll"
    return "dokument"


def _datei(f: dict, rolle: str) -> dict:
    return {
        "id": f["id"],
        "name": _str(f.get("name")) or _str(f.get("fileName")) or "Dokument",
        "dateiname": _str(f.get("fileName")),
        "mime": _str(f.get("mimeType")),
        "datum": _str(f.get("date")),
        "url": _str(f.get("accessUrl")) or _str(f.get("downloadUrl")),
        "download_url": _str(f.get("downloadUrl")),
        "rolle": rolle,
        "geaendert": _str(f.get("modified")),
        "geloescht": 1 if _ist_deleted(f) else 0,
    }


def _dateien_ohne_doppelte(paare: list[tuple[dict, str]]) -> list[dict]:
    gesehen: set[str] = set()
    ergebnis = []
    for f, rolle in paare:
        if not isinstance(f, dict) or "id" not in f or f["id"] in gesehen:
            continue
        gesehen.add(f["id"])
        ergebnis.append(_datei(f, rolle))
    return ergebnis


def map_gremium(obj: dict, quelle_id: str) -> dict:
    return {
        "id": obj["id"],
        "quelle_id": quelle_id,
        "name": _str(obj.get("name")),
        "kurzname": _str(obj.get("shortName")),
        "typ": _str(obj.get("organizationType")),
        "beginn": _str(obj.get("startDate")),
        "ende": _str(obj.get("endDate")),
        "web": _str(obj.get("web")),
        "geaendert": _str(obj.get("modified")),
        "geloescht": 0,
    }


def map_sitzung(obj: dict, quelle_id: str) -> tuple[dict, list[dict], list[dict]]:
    ort = obj.get("location") if isinstance(obj.get("location"), dict) else {}
    ort_teile = [
        _str(ort.get("room")),
        _str(ort.get("streetAddress")),
        " ".join(x for x in (_str(ort.get("postalCode")), _str(ort.get("locality"))) if x),
    ]
    beginn = _str(obj.get("start"))
    zeile = {
        "id": obj["id"],
        "quelle_id": quelle_id,
        "name": _str(obj.get("name")),
        "beginn": beginn,
        "ende": _str(obj.get("end")),
        "datum": beginn[:10],
        "ort": ", ".join(t for t in ort_teile if t),
        "abgesagt": 1 if obj.get("cancelled") else 0,
        "gremium_ids": json.dumps(_liste(obj.get("organization")), ensure_ascii=False),
        "web": _str(obj.get("web")),
        "geaendert": _str(obj.get("modified")),
        "geloescht": 0,
    }

    paare: list[tuple[dict, str]] = []
    for schluessel, rolle in (("invitation", "einladung"), ("resultsProtocol", "ergebnisprotokoll"),
                              ("verbatimProtocol", "wortprotokoll")):
        if isinstance(obj.get(schluessel), dict):
            paare.append((obj[schluessel], rolle))
    for f in _liste(obj.get("auxiliaryFile")):
        if isinstance(f, dict):
            paare.append((f, rolle_aus_name(_str(f.get("name")) or _str(f.get("fileName")))))
    dokumente = _dateien_ohne_doppelte(paare)

    tops = []
    for a in _liste(obj.get("agendaItem")):
        if isinstance(a, dict) and "id" in a:
            tops.append(map_top(a, obj["id"]))
    return zeile, tops, dokumente


def map_top(a: dict, sitzung_id: str) -> dict:
    beratung = a.get("consultation")
    return {
        "id": a["id"],
        "sitzung_id": sitzung_id,
        "nummer": _str(a.get("number")),
        "reihenfolge": int(a.get("order") or 0),
        "name": _str(a.get("name")),
        "ergebnis": _str(a.get("result")),
        "beratung_id": _id_von(beratung),
        "oeffentlich": 0 if a.get("public") is False else 1,
        "geaendert": _str(a.get("modified")),
        "geloescht": 1 if _ist_deleted(a) else 0,
    }


def map_vorgang(obj: dict, quelle_id: str) -> tuple[dict, list[dict], list[dict], list[tuple[str, str]], list[tuple[str, str]]]:
    """Gibt zurück: Vorgangszeile, Beratungsfolge, Dokumente, Beziehungen, Ortsangaben (typ, wert)."""
    zeile = {
        "id": obj["id"],
        "quelle_id": quelle_id,
        "referenz": _str(obj.get("reference")),
        "titel": _str(obj.get("name")),
        "art": _str(obj.get("paperType")),
        "datum": _str(obj.get("date")),
        "geaendert": _str(obj.get("modified")),
        "web": _str(obj.get("web")),
        "suchtext": "",
        "geloescht": 0,
    }

    beratungen = []
    for c in _liste(obj.get("consultation")):
        if isinstance(c, str):
            beratungen.append(_beratung_leer(c, obj["id"]))
            continue
        if not isinstance(c, dict) or "id" not in c:
            continue
        beratungen.append({
            "id": c["id"],
            "vorgang_id": obj["id"],
            "sitzung_id": _str(c.get("meeting")),
            "top_id": _str(c.get("agendaItem")),
            "gremium_ids": json.dumps(_liste(c.get("organization")), ensure_ascii=False),
            "rolle": _str(c.get("role")),
            "federfuehrend": 1 if c.get("authoritative") else 0,
            "geaendert": _str(c.get("modified")),
            "geloescht": 1 if _ist_deleted(c) else 0,
        })

    paare: list[tuple[dict, str]] = []
    if isinstance(obj.get("mainFile"), dict):
        paare.append((obj["mainFile"], "vorlage"))
    for f in _liste(obj.get("auxiliaryFile")):
        if isinstance(f, dict):
            paare.append((f, rolle_aus_name(_str(f.get("name")) or _str(f.get("fileName")))))
    dokumente = _dateien_ohne_doppelte(paare)

    beziehungen: list[tuple[str, str]] = []
    for schluessel, art in (("relatedPaper", "verwandt"), ("superordinatedPaper", "uebergeordnet"),
                            ("subordinatedPaper", "untergeordnet")):
        for ziel in _liste(obj.get(schluessel)):
            if isinstance(ziel, str):
                beziehungen.append((ziel, art))
            elif isinstance(ziel, dict) and "id" in ziel:
                beziehungen.append((ziel["id"], art))

    ortsangaben: list[tuple[str, str]] = []
    for ort in _liste(obj.get("location")):
        if not isinstance(ort, dict):
            continue
        if ort.get("postalCode"):
            ortsangaben.append(("plz", _str(ort["postalCode"])))
        if ort.get("subLocality"):
            ortsangaben.append(("stadtteil_name", _str(ort["subLocality"])))
    return zeile, beratungen, dokumente, beziehungen, ortsangaben


def _beratung_leer(consultation_id: str, vorgang_id: str) -> dict:
    return {
        "id": consultation_id, "vorgang_id": vorgang_id, "sitzung_id": "", "top_id": "",
        "gremium_ids": "[]", "rolle": "", "federfuehrend": 0, "geaendert": "", "geloescht": 0,
    }


def _id_von(verweis) -> str:
    """OParl-Verweise können URL oder eingebettetes Objekt sein."""
    if isinstance(verweis, str):
        return verweis
    if isinstance(verweis, dict):
        return _str(verweis.get("id"))
    return ""


def _liste(wert) -> list:
    if wert is None:
        return []
    if isinstance(wert, list):
        return wert
    return [wert]
