"""Öffentliche Bauleitverfahren der NRW-Plattform demosPlan.

Die öffentliche JSON-Suche wird mit orgaSlug auf eine Kommune begrenzt. Nur
external*-Felder werden verarbeitet, keine internen Daten oder Stellungnahmen.
PDF-Links stammen aus öffentlichen Detailseiten; die PDFs werden NICHT geladen.
Protokoll-/Server-Code des Anbieters wird nicht übernommen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit
from uuid import uuid4

from .abruf import (
    Abrufer, BudgetErschoepft, NichtErlaubt, ObjektNichtVorhanden,
    QuelleGesperrt, QuelleNichtErreichbar,
)
from .auswertung import BERLIN
from .config import Quelle, Stadt
from .crawler import LaufBericht, LaufOptionen, merke_server_pause, pruefe_abkuehlung
from .geo import Gazetteer
from .normalize import norm
from .plan_auswertung import beteiligungs_zeitpunkt
from .speicher import Speicher, jetzt_iso

PHASEN = {
    "earlyparticipation": "Frühzeitige Beteiligung Öffentlichkeit",
    "participation": "Beteiligung Öffentlichkeit",
    "analysis": "Auswertung",
    "evaluation": "Auswertung",
    "decision": "Beschlussfassung",
    "closed": "Abgeschlossen",
}
_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$")


class _Text(HTMLParser):
    """HTML → Klartext, ohne Skripte, Styles oder Formularinhalte auszuführen."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.teile: list[str] = []
        self.ignorieren = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.ignorieren += 1
        elif not self.ignorieren:
            self.teile.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.ignorieren = max(0, self.ignorieren - 1)
        elif not self.ignorieren:
            self.teile.append(" ")

    def handle_data(self, data):
        if not self.ignorieren:
            self.teile.append(data)


def klartext(html: str) -> str:
    parser = _Text()
    parser.feed(html)
    return " ".join("".join(parser.teile).split())


def _datum(wert) -> str:
    if wert in (None, ""):
        return ""
    if not isinstance(wert, str):
        raise ValueError("Beteiligungsdatum ist kein Text")
    try:
        datum = datetime.fromisoformat(wert.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Ungültiges Beteiligungsdatum: {wert}") from exc
    # demosPlan zeigt offensichtliche Platzhalterdaten ebenfalls nicht öffentlich an.
    return wert if datum.year >= 1971 else ""


@dataclass(frozen=True)
class Planverfahren:
    url: str
    titel: str
    phase: str
    beginn: str
    ende: str
    beschreibung: str
    organisation: str
    berechtigung: str

    def stand(self) -> dict:
        return {k: getattr(self, k) for k in (
            "phase", "beginn", "ende", "beschreibung", "organisation", "berechtigung")}

    def speichern(self, speicher: Speicher, stadt: Stadt, quelle_id: str, reihe: str,
                  festgestellt: str | None = None) -> bool:
        zeile = {
            "id": self.url, "quelle_id": quelle_id, "referenz": "", "titel": self.titel,
            "art": "Bauleitplanung / Beteiligungsverfahren",
            "datum": (beteiligungs_zeitpunkt(self.beginn).astimezone(BERLIN).date().isoformat() if self.beginn else ""),
            "geaendert": "",  # Abrufzeit ist KEIN amtlicher Änderungszeitpunkt.
            "web": self.url, "geloescht": 0,
            "suchtext": norm(f"{self.titel} Bauleitplanung {self.phase} {self.beschreibung}"),
        }
        gaz = Gazetteer(stadt)
        ortsbezuege = gaz.ortsbezuege_aus_text(self.titel, "betreff")
        # Keine Kontaktdaten aus der Detailseite: Amts-PLZ ist nicht die Lage des Vorhabens.
        ortsbezuege |= gaz.ortsbezuege_aus_text(self.beschreibung, "beschreibung")
        orte = {(o.typ, o.schluessel, o.quelle) for o in ortsbezuege}
        return speicher.planverfahren_speichern(zeile, self.stand(), orte, reihe, festgestellt)


def _textfeld(daten: dict, key: str) -> str:
    wert = daten.get(key)
    if wert is None:
        return ""
    if not isinstance(wert, str):
        raise ValueError(f"{key} ist kein Text")
    return klartext(wert)


def parse_liste(daten: dict, quelle: Quelle) -> tuple[list[Planverfahren], str | None]:
    """JSON:API-Collection prüfen. Eine unbekannte Antwort gilt nicht als leere Liste."""
    if not isinstance(daten, dict) or not isinstance(daten.get("data"), list):
        raise ValueError("Bauleitplanung: Antwort enthält keine öffentliche Verfahrensliste (data)")
    meta = daten.get("meta") or {}
    if not isinstance(meta, dict) or not isinstance(meta.get("messages") or {}, dict):
        raise ValueError("Bauleitplanung: ungültige Antwort-Metadaten")
    if daten.get("errors") or (meta.get("messages") or {}).get("error"):
        raise ValueError("Bauleitplanung: Schnittstelle meldet einen Fehler")
    if not quelle.web_vorlage:
        raise ValueError("Bauleitplanung benötigt eine web_vorlage für die Originalseite")

    ergebnis: dict[str, Planverfahren] = {}
    for obj in daten["data"]:
        if not isinstance(obj, dict) or obj.get("type") != "Procedure" or not isinstance(obj.get("attributes"), dict):
            raise ValueError("Bauleitplanung: unerwartetes Verfahrensobjekt")
        a = obj["attributes"]
        # Öffentlichkeits-Sichtbarkeit, nicht die interne Phase, entscheidet.
        erlaubnis = a.get("externalPhasePermissionset", "")
        if erlaubnis not in ("", "read", "write"):
            continue
        organisation = _textfeld(a, "owningOrganisationName")
        if quelle.organisation and not organisation:
            raise ValueError("Bauleitplanung: Organisationsfeld fehlt; Schema/Kommunenfilter prüfen")
        if quelle.organisation and norm(organisation) != norm(quelle.organisation):
            continue  # zusätzliche Absicherung gegen ungewollte Treffer anderer Kommunen
        ident = obj.get("id")
        if not isinstance(ident, str) or not _ID.fullmatch(ident):
            raise ValueError("Bauleitplanung: ungültige Verfahrens-ID")
        titel = _textfeld(a, "externalName")
        if not titel:
            raise ValueError("Bauleitplanung: öffentlicher Titel fehlt (interne Namen werden nicht verwendet)")
        phase = _textfeld(a, "externalPhaseDefinitionName")
        if not phase:
            key = _textfeld(a, "externalPhaseTranslationKey")
            phase = PHASEN.get(key.rsplit(".", 1)[-1], "Verfahrensphase unbekannt")
        url = quelle.web_vorlage.format(id=ident)
        beginn, ende = _datum(a.get("externalStartDate")), _datum(a.get("externalEndDate"))
        if beginn and ende and beteiligungs_zeitpunkt(ende, ende=True) < beteiligungs_zeitpunkt(beginn):
            raise ValueError("Bauleitplanung: Ende des Beteiligungszeitraums liegt vor dessen Beginn")
        ergebnis[url] = Planverfahren(
            url, titel, phase, beginn, ende, _textfeld(a, "externalDescription"), organisation, erlaubnis,
        )

    links = daten.get("links") or {}
    if not isinstance(links, dict):
        raise ValueError("Bauleitplanung: ungültige Paginierung")
    verweis = links.get("next")
    if isinstance(verweis, dict):
        if not isinstance(verweis.get("href"), str) or not verweis["href"]:
            raise ValueError("Bauleitplanung: ungültiger Link-Objekt-Folgelink")
        verweis = verweis["href"]
    return list(ergebnis.values()), _folgelink(verweis, quelle.system_url) if verweis not in (None, "") else None


def _folgelink(verweis: str, basis: str) -> str:
    if not isinstance(verweis, str):
        raise ValueError("Bauleitplanung: ungültiger Folgelink")
    ziel, original = urlsplit(urljoin(basis, verweis)), urlsplit(basis)
    if (ziel.scheme, ziel.netloc, ziel.path) != (original.scheme, original.netloc, original.path):
        raise ValueError("Bauleitplanung: Folgelink verlässt die konfigurierte öffentliche Suche")
    params = parse_qs(ziel.query)
    # Der Organisationsfilter darf beim Seitenwechsel weder verschwinden noch wechseln.
    for key, wert in parse_qs(original.query).items():
        if key in params and params[key] != wert:
            raise ValueError("Bauleitplanung: Folgelink ändert den Organisationsfilter")
        params[key] = wert
    return urlunsplit((ziel.scheme, ziel.netloc, ziel.path, urlencode(params, doseq=True), ""))


class _DokumentLinks(HTMLParser):
    def __init__(self, basis: str):
        super().__init__(convert_charrefs=True)
        self.basis = basis
        self.dokumente: dict[str, dict] = {}
        self.anker: dict | None = None
        self.oeffentliche_seite = False
        self.passwort = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get("id") in ("infolistContentMetaDescription", "helpPublicDetailTimelimit"):
            self.oeffentliche_seite = True
        if tag == "input" and a.get("type", "").lower() == "password":
            self.passwort = True
        if tag == "a":
            self.anker = {"href": a.get("href") or "", "title": a.get("title") or "", "text": []}

    def handle_data(self, data):
        if self.anker is not None:
            self.anker["text"].append(data)

    def handle_endtag(self, tag):
        if tag != "a" or self.anker is None:
            return
        anker, self.anker = self.anker, None
        url = urljoin(self.basis, anker["href"])
        ziel, basis = urlsplit(url), urlsplit(self.basis)
        if ((ziel.scheme, ziel.netloc) != (basis.scheme, basis.netloc)
                or not re.fullmatch(r"/file/[^/]+/[^/]+", ziel.path) or ziel.query or ziel.fragment):
            return
        text = " ".join(" ".join(anker["text"]).split())
        name = re.sub(r"^Herunterladen\s*", "", text, flags=re.I).strip()
        name = re.sub(r"\s*\((?:pdf|docx?|zip)[^)]*\)\s*$", "", name, flags=re.I).strip()
        name = name or anker["title"] or "Planungsdokument"
        if url in self.dokumente and name == "Planungsdokument":
            return
        self.dokumente[url] = {
            "id": url, "name": name, "dateiname": "", "mime": "application/pdf" if "pdf" in text.lower() else "",
            "datum": "", "url": url, "download_url": url, "rolle": "plandokument",
            "geaendert": "", "geloescht": 0,
        }


def parse_dokumente(html: str, basis: str) -> list[dict]:
    parser = _DokumentLinks(basis)
    parser.feed(html)
    if not parser.oeffentliche_seite:
        if parser.passwort:
            raise QuelleGesperrt("Detailseite verlangt Anmeldung; keine Umgehung")
        raise ValueError("Bauleitplanung: keine bekannte öffentliche Detailseite; Dokumentbestand unverändert")
    return list(parser.dokumente.values())


class BauleitplanungQuelle:
    def __init__(self, abrufer: Abrufer, quelle: Quelle):
        self.abrufer, self.quelle = abrufer, quelle

    def seite(self, url: str | None = None) -> tuple[list[Planverfahren], str | None]:
        return parse_liste(self.abrufer.get_json(url or self.quelle.system_url), self.quelle)


def crawle_bauleitplanung(speicher: Speicher, stadt: Stadt, quelle: Quelle,
                          abrufer: Abrufer, optionen: LaufOptionen) -> LaufBericht:
    """Komplette öffentliche Liste und Detail-Links, fortsetzbar in kleinen Etappen.

    Diese API hat keinen modified_since-Filter. Jeder abgeschlossene Folgelauf prüft
    die Liste erneut. Unveränderte Inhalte erzeugen keine zusätzlichen Planstände.
    """
    qid = stadt.quell_id(quelle)
    speicher.quelle_anlegen(qid, stadt.schluessel, quelle.schluessel, quelle.typ,
                            quelle.name, quelle.system_url)
    pruefe_abkuehlung(speicher, qid, optionen)
    bericht = LaufBericht(qid)
    lauf = speicher.lauf_beginnen(qid)
    speicher.quelle_status(qid, "laeuft")
    try:
        stand = speicher.stand_lesen(qid, "verfahren")
        fortsetzung = bool(stand and stand["reihe_beginn"])
        # Eindeutiger Zyklus: zwei vollständige Läufe innerhalb derselben Sekunde
        # dürfen fehlende Verfahren nicht fälschlich als wieder gesehen behandeln.
        reihe = stand["reihe_beginn"] if fortsetzung else f"{jetzt_iso()}#{uuid4().hex}"
        url = stand["naechste_url"] if fortsetzung else quelle.system_url
        modified = stand["modified_seit"] if stand else ""
        speicher.stand_schreiben(qid, "verfahren", modified, url, reihe)
        besucht: set[str] = set()
        adapter = BauleitplanungQuelle(abrufer, quelle)
        while url:
            # Auch gespeicherte Fortsetzungspunkte bleiben innerhalb der öffentlichen Suche.
            url = _folgelink(url, quelle.system_url)
            if url in besucht:
                raise ValueError("Bauleitplanung: Paginierungsschleife")
            besucht.add(url)
            verfahren, naechste = adapter.seite(url)
            if naechste in besucht:
                raise ValueError("Bauleitplanung: Paginierungsschleife")
            # Seite, Detail-Aufgaben und Fortsetzungspunkt werden atomar gespeichert.
            with speicher.transaktion():
                for plan in verfahren:
                    plan.speichern(speicher, stadt, qid, reihe)
                    speicher.aufgabe_anlegen(qid, plan.url, plan.url)
                    bericht.zaehle("verfahren:gespeichert")
                speicher.stand_schreiben(qid, "verfahren", modified, naechste or "", reihe)
            url = naechste

        speicher.plan_liste_abgeschlossen(qid, reihe)
        for aufgabe in speicher.aufgaben(qid):
            try:
                html = abrufer.get_text(aufgabe["url"])
                dokumente = parse_dokumente(html, aufgabe["url"])
                with speicher.transaktion():
                    speicher.plan_dokumente_speichern(aufgabe["vorgang_id"], dokumente, "ok")
                    speicher.aufgabe_erledigt(qid, aufgabe["vorgang_id"])
                bericht.zaehle("details:gespeichert")
            except ObjektNichtVorhanden:
                with speicher.transaktion():
                    speicher.plan_dokumente_speichern(aufgabe["vorgang_id"], [], "nicht verfügbar")
                    speicher.aufgabe_erledigt(qid, aufgabe["vorgang_id"])
                bericht.zaehle("details:nicht_verfuegbar")
        speicher.stand_schreiben(qid, "verfahren", jetzt_iso(), "", "")
        bericht.status = "ok"
    except BudgetErschoepft as exc:
        bericht.status, bericht.fehlertext = "teilweise", str(exc)
    except (QuelleGesperrt, NichtErlaubt) as exc:
        merke_server_pause(bericht, exc)
        bericht.status, bericht.fehlertext = "gesperrt", str(exc)
    except (QuelleNichtErreichbar, ObjektNichtVorhanden) as exc:
        merke_server_pause(bericht, exc)
        bericht.status, bericht.fehlertext = "nicht erreichbar", str(exc)
    except (ValueError, TypeError) as exc:
        bericht.status, bericht.fehlertext = "fehler", str(exc)
    except KeyboardInterrupt:
        bericht.status, bericht.fehlertext = "teilweise", "Manuell abgebrochen; der nächste Lauf setzt fort"
        raise
    finally:
        bericht.anfragen = abrufer.anfragen
        speicher.lauf_beenden(lauf, bericht.status, bericht.anfragen, bericht.zaehler, bericht.fehlertext)
        speicher.quelle_status(qid, bericht.status, bericht.fehlertext, erfolg=bericht.status == "ok",
                               abkuehlung_bis=bericht.abkuehlung_bis)
    return bericht
