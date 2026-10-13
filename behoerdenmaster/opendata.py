"""Offene Daten Köln (DKAN 2): Tabellendatensätze des städtischen Open-Data-Portals.

Was diese Quelle beiträgt: einzelne **Fachdatensätze der Stadt**, die als Tabelle
veröffentlicht sind – etwa genehmigte Baustellen mit Straßenbezug oder Aufstellungs-
beschlüsse. Jede Tabellenzeile wird ein durchsuchbarer Vorgang mit Titel, Datum, einer
Feldübersicht, dem Ortsbezug aus den Straßennamen der Zeile und einem Link auf die
Originaldatei der Quelle.

Der Weg einer Zeile: Katalog (Stichwort) → Datensatz-Metadaten samt Lizenz → Tabellen-
speicher („Datastore“) → Vorgang. Gelesen wird ausschließlich der Datastore (JSON);
PDF-, WMS- oder GIS-Ausgaben werden **nicht** geladen.

Grenzen, die in der Oberfläche sichtbar bleiben:

* Übernommen werden nur Datensätze unter einer offenen Lizenz: **DL-DE-Zero-2.0**
  (ohne Namensnennung) und **DL-DE-BY-2.0** (mit Namensnennung). Datensätze mit anderer,
  fehlender oder fremder Lizenz werden übersprungen und gezählt; es wird nichts
  umgedeutet. Bei BY-Datensätzen steht die Namensnennung am Vorgang.
* Ein Datensatz ist **kein Verfahren**: Die Zeilen sind Angaben der Quelle, ihre Felder
  stehen unverändert im Kurztext. Aus fehlenden Feldern wird nichts ergänzt; ein
  Verfahrensschritt wird nicht erfunden.
* Die Spaltennamen sind Maschinennamen der Quelle. Für Titel, Datum und Kennung werden
  passende Spalten gesucht; findet sich keine, wird die Zeile mit Datensatzname und
  Zeilennummer benannt – statt einen Titel zu erfinden.
* Der Katalog wechselt selten, die Tabellen ändern sich. Ein Lauf liest je Datensatz
  die ersten Seiten (Notbremse) und setzt nach einem Abbruch an derselben Stelle fort.
"""

from __future__ import annotations

import hashlib
import logging
import re
from urllib.parse import urlencode

from .abruf import (Abrufer, BudgetErschoepft, NichtErlaubt, ObjektNichtVorhanden,
                    QuelleGesperrt, QuelleNichtErreichbar)
from .auswertung import heute as heute_berlin
from .config import Quelle, Stadt
from .crawler import LaufBericht, LaufOptionen, merke_server_pause, pruefe_abkuehlung
from .geo import Gazetteer
from .meldungen import MAX_KURZTEXT, MeldungEintrag, datum_aus_wert, ortsbezuege
from .normalize import norm
from .speicher import Speicher, jetzt_iso

log = logging.getLogger("behoerdenmaster.opendata")

SEITE = 100        # Zeilen je Datastore-Abruf (die Quelle begrenzt selbst auf 100)
MAX_SEITEN = 20    # Notbremse je Datensatz und Lauf (höchstens 2.000 Zeilen)
MAX_FELDER = 8     # so viele Felder zeigt der Kurztext einer Zeile
MAX_ANTWORT_FELDER = 200  # Schutz gegen entgleiste Antworten

# Nur diese Lizenzen werden übernommen. Der erste Treffer in der Kennung entscheidet;
# unbekannte Angaben gelten als nicht übernehmbar (kein stilles Annehmen).
LIZENZ_ZERO = ("dl-zero-de", "dl-de/zero", "cc0", "dl zero de", "zero 2 0")
LIZENZ_BY = ("dl-by-de", "dl-de/by", "dl by de", "by 2 0", "namensnennung")
LIZENZ_TEXTE = {
    "zero": "Datenlizenz Deutschland – Zero – Version 2.0",
    "by": "Datenlizenz Deutschland – Namensnennung – Version 2.0",
}

# Spalten der Quelle sind Maschinennamen („strasse“, „beginn_datum“); verglichen wird
# über die normalisierten Wörter, damit auch „Straße“ oder „Beginn“ passen.
TITEL_MUSTER = ("titel", "title", "name", "bezeichnung", "massnahme", "massnahmen",
                "vorhaben", "objekt", "kurztext", "beschreibung", "zweck", "art",
                "strasse", "strassenname", "adresse", "standort", "ort", "lage")
DATUM_MUSTER = ("datum", "date", "beginn", "von", "start", "veroeffentlicht",
                "bekanntmachung", "aufstellungsbeschluss", "erstellt")
ENDE_MUSTER = ("ende", "bis", "fertigstellung", "abnahme", "endezeit", "laufzeit_ende")
KENNUNG_MUSTER = ("id", "nummer", "nr", "objektid", "objekt", "kennung", "key",
                  "schluessel", "identifikator", "vorgangsnummer", "aktenzeichen")

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


# --- Lizenz ---------------------------------------------------------------------

def lizenz_art(lizenz: str) -> str | None:
    """„zero“, „by“ oder None – welche Lizenz liegt vor?"""
    n = norm(lizenz)
    if not n:
        return None
    if any(kennung in n for kennung in LIZENZ_BY):
        return "by"
    if any(kennung in n for kennung in LIZENZ_ZERO):
        return "zero"
    return None


def datensatz_lizenz(datensatz: dict) -> str:
    """Lizenzangabe eines Datensatzes – auch dann, wenn sie nur an einer Verteilung steht."""
    kandidaten = [datensatz.get("license")]
    for verteilung in datensatz.get("distribution") or []:
        if isinstance(verteilung, dict):
            kandidaten.append(verteilung.get("license"))
            if isinstance(verteilung.get("data"), dict):
                kandidaten.append(verteilung["data"].get("license"))
    for wert in kandidaten:
        if str(wert or "").strip():
            return str(wert).strip()
    return ""


def namensnennung(datensatz: dict) -> str:
    """Namensnennung laut Metadaten: Herausgeber des Datensatzes (sonst die Stadt)."""
    herausgeber = datensatz.get("publisher") or {}
    name = (herausgeber.get("name") if isinstance(herausgeber, dict) else "") or ""
    return name.strip() or "Stadt Köln"


def lizenz_text(datensatz: dict) -> tuple[str, str]:
    """(Lizenzart, Text zur Anzeige). Leere Lizenz → (\"\", \"\")."""
    lizenz = datensatz_lizenz(datensatz)
    art = lizenz_art(lizenz)
    if not art:
        return "", ""
    text = LIZENZ_TEXTE[art]
    if art == "by":
        text += f" (Namensnennung: {namensnennung(datensatz)})"
    return art, text


# --- Katalog und Datensätze -----------------------------------------------------

def _url(quelle: Quelle, pfad: str, params: dict[str, str] | None = None) -> str:
    basis = quelle.system_url.rstrip("/")
    ziel = f"{basis}{pfad}"
    return f"{ziel}?{urlencode(params)}" if params else ziel


def katalog_treffer(abrufer: Abrufer, quelle: Quelle, stichwort: str) -> list[dict]:
    """Datensätze des Katalogs zu einem Stichwort (Volltextsuche des Portals)."""
    antwort = abrufer.get_json(_url(quelle, "/api/1/search", {"fulltext": stichwort, "page-size": "25"}))
    treffer = antwort.get("results") if isinstance(antwort, dict) else None
    return [d for d in (treffer or []) if isinstance(d, dict)]


def datensatz_holen(abrufer: Abrufer, quelle: Quelle, kennung: str) -> dict | None:
    """Metadaten eines Datensatzes; None, wenn die Kennung dort nicht existiert."""
    try:
        antwort = abrufer.get_json(_url(quelle, f"/api/1/metastore/schemas/dataset/items/{kennung}"))
    except ObjektNichtVorhanden:
        return None
    return antwort if isinstance(antwort, dict) else None


def passende_datensaetze(treffer: list[dict], stichwort: str) -> list[dict]:
    """Treffer, deren Titel das Stichwort enthält – in der Reihenfolge des Katalogs.

    Eine direkte Datensatz-Kennung (UUID) im Stichwort wird nicht als Titel verglichen.
    """
    if _UUID.match(stichwort.strip()):
        return [d for d in treffer if str(d.get("identifier", "")).lower() == stichwort.strip().lower()]
    gesucht = norm(stichwort)
    return [d for d in treffer if gesucht and gesucht in norm(str(d.get("title", "")))]


def unscharfe_datensaetze(treffer: list[dict], stichwort: str) -> list[dict]:
    """Katalogtreffer, die mindestens ein bedeutungstragendes Wort des Stichworts im Titel führen.

    Das ist der Rückfall, wenn der Titel des Datensatzes anders formuliert ist als das
    Stichwort („Baustellen Köln“ → „Baustellen im Stadtgebiet Köln“). Der Rückfall wird
    gezählt und im Ladebericht ausgewiesen – es wird kein beliebiger Treffer genommen.
    """
    if _UUID.match(stichwort.strip()):
        return []
    woerter = {w for w in norm(stichwort).split() if len(w) >= 4}
    if not woerter:
        return []
    return [d for d in treffer if woerter & set(norm(str(d.get("title", ""))).split())]


def datensaetze_finden(abrufer: Abrufer, quelle: Quelle, bericht: LaufBericht) -> list[dict]:
    """Die konfigurierten Datensätze auflösen: Stichwort im Katalog oder direkte Kennung."""
    gefunden: list[dict] = []
    kennungen: set[str] = set()
    for angabe in quelle.datensaetze:
        if _UUID.match(angabe.strip()):
            datensatz = datensatz_holen(abrufer, quelle, angabe.strip())
            bericht.zaehle("opendata:katalog")
            if datensatz is None:
                log.warning("%s: Datensatz %s nicht im Katalog", quelle.schluessel, angabe)
                bericht.zaehle("opendata:nicht_gefunden")
                continue
            kandidaten = [datensatz]
        else:
            treffer = katalog_treffer(abrufer, quelle, angabe)
            bericht.zaehle("opendata:katalog")
            kandidaten = passende_datensaetze(treffer, angabe)
            if not kandidaten:
                # Der Titel weicht vom Stichwort ab: Der erste Treffer, der ein Wort des
                # Stichworts führt, wird genommen – sichtbar als „unscharf“ gezählt.
                kandidaten = unscharfe_datensaetze(treffer, angabe)
                if kandidaten:
                    log.warning("%s: Datensatz zu „%s“ nur unscharf gefunden: %s", quelle.schluessel,
                                angabe, kandidaten[0].get("title"))
                    bericht.zaehle("opendata:unscharf")
            if not kandidaten:
                log.warning("%s: kein Datensatz zu „%s“ im Katalog", quelle.schluessel, angabe)
                bericht.zaehle("opendata:nicht_gefunden")
                continue
        for datensatz in kandidaten[:1]:
            kennung = str(datensatz.get("identifier", ""))
            if not kennung or kennung in kennungen:
                continue
            kennungen.add(kennung)
            gefunden.append(datensatz)
    return gefunden


def daten_verteilungen(datensatz: dict) -> list[tuple[int, dict]]:
    """Verteilungen, die im Tabellenspeicher liegen können, mit ihrer Position.

    Der Datastore entsteht aus CSV- und TSV-Dateien; andere Formate (PDF, WMS) gehören
    nicht hierher. Gibt es keine erkennbare Tabellendatei, bleibt die Liste leer.
    """
    ergebnis: list[tuple[int, dict]] = []
    for index, verteilung in enumerate(datensatz.get("distribution") or []):
        if not isinstance(verteilung, dict):
            continue
        daten = verteilung.get("data") if isinstance(verteilung.get("data"), dict) else {}
        quelle_url = str(daten.get("downloadURL") or daten.get("accessURL") or "")
        format_text = norm(f"{daten.get('format', '')} {daten.get('mediaType', '')}")
        if any(teil in format_text for teil in ("csv", "tsv", "tab")):
            ergebnis.append((index, verteilung))
        elif quelle_url.lower().endswith((".csv", ".tsv", ".txt")):
            ergebnis.append((index, verteilung))
    return ergebnis


# --- Zeilenabbildung ------------------------------------------------------------

def _spalte_finden(spalten: list[str], muster: tuple[str, ...]) -> str | None:
    """Erste Spalte, deren normalisierter Name zu einem Muster passt."""
    for name in spalten:
        woerter = norm(name).split()
        for m in muster:
            if m in woerter:
                return name
    return None


def _wert_text(wert) -> str:
    if wert is None or isinstance(wert, (dict, list)):
        return ""
    if isinstance(wert, bool):
        return "ja" if wert else "nein"
    return " ".join(str(wert).split())


def zeilen_kennung(zeile: dict, kennung_spalte: str | None) -> str:
    """Stabile Kennung einer Zeile: Wert der Kennungsspalte, sonst Kurz-Hash der Felder.

    Der Zeilenindex taugt nicht als Kennung: Bei einem Neuladen kann er sich verschieben,
    und derselbe Eintrag würde doppelt erscheinen.
    """
    if kennung_spalte:
        wert = _wert_text(zeile.get(kennung_spalte))
        if wert:
            return re.sub(r"\s+", "-", wert)[:80]
    roh = "|".join(f"{k}={_wert_text(v)}" for k, v in sorted(zeile.items()))
    return "z" + hashlib.sha1(roh.encode("utf-8")).hexdigest()[:12]


def zeile_zu_eintrag(datensatz: dict, zeile: dict, nummer: int, stand: str,
                     heute: str) -> MeldungEintrag:
    """Eine Datensatzzeile in das gemeinsame Modell der Listenquellen übersetzen."""
    kennung = str(datensatz.get("identifier", ""))
    titel_datensatz = _wert_text(datensatz.get("title")) or kennung
    spalten = [k for k in zeile if _wert_text(zeile.get(k))]
    titel_spalte = _spalte_finden(spalten, TITEL_MUSTER)
    datum_spalte = _spalte_finden(spalten, DATUM_MUSTER)
    ende_spalte = _spalte_finden([s for s in spalten if s != datum_spalte], ENDE_MUSTER)
    kennung_spalte = _spalte_finden(spalten, KENNUNG_MUSTER)

    titel = _wert_text(zeile.get(titel_spalte)) if titel_spalte else ""
    if not titel:
        titel = f"{titel_datensatz} – Zeile {nummer}"

    beginn = datum_aus_wert(_wert_text(zeile.get(datum_spalte))) if datum_spalte else ""
    ende = datum_aus_wert(_wert_text(zeile.get(ende_spalte))) if ende_spalte else ""
    felder = [s for s in spalten if s not in (titel_spalte, kennung_spalte)][:MAX_FELDER]
    uebersicht = " · ".join(f"{s.replace('_', ' ')}: {_wert_text(zeile[s])}" for s in felder)
    kurztext = uebersicht[:MAX_KURZTEXT]

    art = titel_datensatz[:120]
    _, lizenz = lizenz_text(datensatz)
    geaendert = str(datensatz.get("modified") or "")[:10]
    teile = [f"Eintrag aus dem offenen Datensatz „{titel_datensatz}“ (Zeile {nummer})."]
    if beginn or ende:
        zeitraum = " – ".join(x for x in (beginn, ende) if x) or beginn
        teile.append(f"Zeitraum laut Quelle: {zeitraum}.")
    if geaendert:
        teile.append(f"Datensatz zuletzt geändert: {geaendert}.")
    if lizenz:
        teile.append(f"Lizenz: {lizenz}.")

    return MeldungEintrag(
        vorgang_id=f"opendata:{kennung}:{zeilen_kennung(zeile, kennung_spalte)}",
        art=f"Datensatz: {art}",
        titel=titel,
        referenz=_wert_text(zeile.get(kennung_spalte)) if kennung_spalte else "",
        datum=beginn,
        web=_original_url(datensatz),
        kategorie=titel_datensatz,
        schritt="",
        status_kurz="Im offenen Datensatz geführt",
        status_text=" ".join(teile),
        offen=bool(ende) and ende >= heute,
        kurztext=kurztext,
        zusatz=(f"Auskunft des offenen Datensatzes der Stadt; keine amtliche Bekanntmachung. {lizenz}".strip()),
        frist=ende,
        suchtext_zusatz=" ".join(_wert_text(v) for v in zeile.values()),
    )


def _original_url(datensatz: dict) -> str:
    """Link auf die Originaldatei der ersten Tabellenverteilung (nur https)."""
    for _, verteilung in daten_verteilungen(datensatz):
        daten = verteilung.get("data") if isinstance(verteilung.get("data"), dict) else {}
        for schluessel in ("downloadURL", "accessURL"):
            ziel = str(daten.get(schluessel) or "")
            if ziel.startswith("https://"):
                return ziel
    return ""


# --- Abruf ----------------------------------------------------------------------

def _zeilen(antwort: dict) -> tuple[list[dict], int | None]:
    """Datenzeilen einer Datastore-Antwort, auch wenn nur Wertelisten geliefert werden."""
    roh = antwort.get("results")
    if not isinstance(roh, list):
        return [], None
    anzahl = antwort.get("count")
    anzahl = int(anzahl) if isinstance(anzahl, (int, float)) else None
    if roh and not isinstance(roh[0], dict):
        felder = _schema_felder(antwort.get("schema"))
        zeilen = []
        for eintrag in roh[:MAX_ANTWORT_FELDER]:
            if isinstance(eintrag, list):
                zeilen.append(dict(zip(felder, eintrag)))
            elif isinstance(eintrag, dict):
                zeilen.append(eintrag)
        return zeilen, anzahl
    return [z for z in roh[:MAX_ANTWORT_FELDER] if isinstance(z, dict)], anzahl


def _schema_felder(schema) -> list[str]:
    """Spaltennamen in Reihenfolge der Quelle (nur für Antworten ohne Feldnamen)."""
    if not isinstance(schema, dict):
        return []
    for eintrag in schema.values():
        felder = eintrag.get("fields") if isinstance(eintrag, dict) else None
        if isinstance(felder, dict) and felder:
            return list(felder)
    return []


def _speichere(speicher: Speicher, gaz: Gazetteer, quelle_id: str, datensatz: dict,
               zeile: dict, nummer: int, stand: str, heute: str, bericht: LaufBericht) -> None:
    eintrag = zeile_zu_eintrag(datensatz, zeile, nummer, stand, heute)
    text = f"{eintrag.titel} {eintrag.kurztext} {eintrag.suchtext_zusatz}"
    orte = ortsbezuege(gaz, text)
    neu = speicher.meldung_speichern(
        eintrag.vorgang_zeile(quelle_id), eintrag.meldung_zeile(stand), orte,
        eintrag.dokumente(name=f"Originaldatei: {eintrag.kategorie}", rolle="datensatz"),
    )
    bericht.zaehle("opendata:neu" if neu else "opendata:aktualisiert")
    if not eintrag.datum:
        bericht.zaehle("opendata:ohne_datum")
    if not orte:
        bericht.zaehle("opendata:ohne_ort")


def _verteilung_lesen(abrufer: Abrufer, speicher: Speicher, gaz: Gazetteer, quelle: Quelle,
                      quelle_id: str, datensatz: dict, index: int, stand: str, heute: str,
                      bericht: LaufBericht) -> None:
    """Eine Tabellenverteilung seitenweise lesen; je Verteilung ein Fortsetzungspunkt.

    Der Punkt ist die nächste Seitenadresse in ``crawl_stand``: Nach einem Abbruch
    (Budget, Netzfehler) beginnt der nächste Lauf genau dort wieder – auch mitten in
    einem Datensatz.
    """
    kennung = str(datensatz.get("identifier", ""))
    entitaet = f"datensatz:{kennung}:{index}"
    titel = _wert_text(datensatz.get("title")) or kennung
    cursor = speicher.stand_lesen(quelle_id, entitaet)
    if cursor and cursor["naechste_url"]:
        naechste, reihe = cursor["naechste_url"], cursor["reihe_beginn"] or stand
    else:
        naechste = _url(quelle, f"/api/1/datastore/query/{kennung}/{index}",
                        {"limit": str(SEITE), "offset": "0", "keys": "true"})
        reihe = stand
    gelesen, seiten = 0, 0
    try:
        while naechste and seiten < MAX_SEITEN:
            antwort = abrufer.get_json(naechste)
            zeilen, anzahl = _zeilen(antwort)
            seiten += 1
            bericht.zaehle("listen:seiten")
            if not zeilen:
                # Eine leere Seite beendet diese Verteilung; die übrigen Datensätze
                # werden weiter geprüft, der Bestand bleibt unverändert.
                log.warning("%s: „%s“ (Verteilung %d) lieferte eine leere Seite", quelle_id, titel, index)
                bericht.zaehle("opendata:leere_seite")
                speicher.stand_schreiben(quelle_id, entitaet, "", "", "")
                return
            for nummer, zeile in enumerate(zeilen, start=gelesen + 1):
                _speichere(speicher, gaz, quelle_id, datensatz, zeile, nummer, stand, heute, bericht)
            gelesen += len(zeilen)
            if (anzahl is not None and gelesen >= anzahl) or len(zeilen) < SEITE:
                naechste = ""
            else:
                naechste = _url(quelle, f"/api/1/datastore/query/{kennung}/{index}",
                                {"limit": str(SEITE), "offset": str(gelesen), "keys": "true"})
            speicher.stand_schreiben(quelle_id, entitaet, "", naechste, reihe if naechste else "")
    except ObjektNichtVorhanden:
        # Diese Verteilung liegt nicht im Tabellenspeicher (z. B. ein Karten-Export).
        log.info("%s: „%s“ (Verteilung %d) liegt nicht im Tabellenspeicher", quelle_id, titel, index)
        bericht.zaehle("opendata:ohne_tabelle")
        speicher.stand_schreiben(quelle_id, entitaet, "", "", "")
        return
    if seiten >= MAX_SEITEN and naechste:
        log.warning("%s: „%s“ (Verteilung %d) nach %d Seiten abgebrochen", quelle_id, titel, index, seiten)
    log.info("%s: „%s“ (Verteilung %d), %d Zeilen, %d Seiten, Anfragen bisher %d",
             quelle_id, titel, index, gelesen, seiten, abrufer.anfragen)


def _datensatz_lesen(abrufer: Abrufer, speicher: Speicher, gaz: Gazetteer, quelle: Quelle,
                     quelle_id: str, datensatz: dict, stand: str, heute: str,
                     bericht: LaufBericht) -> None:
    """Lizenz prüfen und die Tabellen eines Datensatzes lesen."""
    lizenz = datensatz_lizenz(datensatz)
    art = lizenz_art(lizenz)
    if art is None:
        log.warning("%s: Datensatz „%s“ hat keine übernehmbare Lizenz (%s)",
                    quelle_id, datensatz.get("title"), lizenz or "keine Angabe")
        bericht.zaehle("opendata:uebersprungen_lizenz")
        return
    verteilungen = daten_verteilungen(datensatz)
    if not verteilungen:
        log.warning("%s: Datensatz „%s“ hat keine Tabellenverteilung", quelle_id, datensatz.get("title"))
        bericht.zaehle("opendata:ohne_tabelle")
        return
    bericht.zaehle("opendata:datensaetze")
    if art == "by":
        bericht.zaehle("opendata:namensnennung")
    for index, _ in verteilungen:
        _verteilung_lesen(abrufer, speicher, gaz, quelle, quelle_id, datensatz, index, stand,
                          heute, bericht)


def crawle_opendata(speicher: Speicher, stadt: Stadt, quelle: Quelle, abrufer: Abrufer,
                    optionen: LaufOptionen) -> LaufBericht:
    """Datensätze des offenen Datenportals lesen und als Vorgänge speichern."""
    quelle_id = stadt.quell_id(quelle)
    gaz = Gazetteer(stadt)
    speicher.quelle_anlegen(quelle_id, stadt.schluessel, quelle.schluessel, quelle.typ,
                            quelle.name, quelle.system_url, demo=False)
    pruefe_abkuehlung(speicher, quelle_id, optionen)

    bericht = LaufBericht(quelle_id=quelle_id)
    lauf_id = speicher.lauf_beginnen(quelle_id)
    speicher.quelle_status(quelle_id, "laeuft")
    stand = jetzt_iso()
    heute = heute_berlin().isoformat()
    try:
        for datensatz in datensaetze_finden(abrufer, quelle, bericht):
            _datensatz_lesen(abrufer, speicher, gaz, quelle, quelle_id, datensatz, stand,
                             heute, bericht)
        bericht.status = "ok"
    except BudgetErschoepft as exc:
        bericht.status, bericht.fehlertext = "teilweise", str(exc)
    except (QuelleGesperrt, NichtErlaubt) as exc:
        merke_server_pause(bericht, exc)
        bericht.status, bericht.fehlertext = "gesperrt", str(exc)
    except QuelleNichtErreichbar as exc:
        merke_server_pause(bericht, exc)
        bericht.status, bericht.fehlertext = "nicht erreichbar", str(exc)
    except ObjektNichtVorhanden as exc:
        # Ein einzelner fehlender Datensatz ist kein Netzproblem: Der Lauf endet sauber,
        # der Bestand bleibt unverändert, der Hinweis steht im Laufprotokoll.
        bericht.zaehle("opendata:nicht_gefunden")
        bericht.status, bericht.fehlertext = "ok", str(exc)
    except ValueError as exc:
        bericht.status, bericht.fehlertext = "fehler", str(exc)
    except KeyboardInterrupt:
        bericht.status, bericht.fehlertext = "teilweise", "Manuell abgebrochen; der nächste Lauf setzt fort"
        raise
    except Exception as exc:
        bericht.status, bericht.fehlertext = "fehler", f"Unerwarteter Abruffehler ({type(exc).__name__})"
        raise
    finally:
        bericht.anfragen = abrufer.anfragen
        speicher.lauf_beenden(lauf_id, bericht.status, bericht.anfragen, bericht.zaehler, bericht.fehlertext)
        speicher.quelle_status(quelle_id, bericht.status, bericht.fehlertext, erfolg=bericht.status == "ok",
                               abkuehlung_bis=bericht.abkuehlung_bis)
    return bericht
