"""Listenbasierte Meldungsquellen: amtliche Bekanntmachungen und Bürgerbeteiligung.

Beide Quellen sind **Listen ohne Änderungsfilter**: ein Lauf liest die öffentliche
Liste, speichert Titel, Datum, Statusangabe und Link – und nichts weiter. Es werden
keine PDF-Dateien geladen und keine Beiträge Dritter übernommen.

Gemeinsam genutzt werden:

* das Erkennen der Einträge aus dem öffentlichen HTML (``parse_*``),
* die Ableitung von Verfahrensschritt und Status **ausschließlich aus Formulierungen
  der Quelle** (kein erfundener Paragraf, keine erfundene Frist),
* Ortsbezug über den Gazetteer der Stadt (mit „Köln-Stadtteil“-Schreibweise),
* Aufbereitung für die Suche (Status, Zusammenfassung, Verlauf).

Die Bezeichnung „Meldung“ ist bewusst gewählt: Eine Bekanntmachung ist ein amtlicher
Veröffentlichungsschritt, ein Beteiligungsangebot ein Angebot der Stadt – beides ist
kein Ratsbeschluss und wird nicht als solcher dargestellt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .bauleitplanung import klartext
from .geo import Gazetteer, OrtsBezug
from .normalize import norm

MAX_KURZTEXT = 600

# --- Bekanntmachungen ---------------------------------------------------------

# Nur diese öffentliche Route ist eine Bekanntmachung; dieselbe Prüfung wie beim Abruf.
BEKANNTMACHUNG_PFAD = "/mediaasset/content/bekanntmachungen/"
_ANKER = re.compile(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.I | re.S)
_TIME = re.compile(r'<time\b[^>]*datetime="(?P<wert>[^"]+)"', re.I)
_FIGURE = re.compile(r"<figure[^>]*>(?P<inhalt>.*?)</figure>", re.I | re.S)
_FIGCAPTION = re.compile(r"<figcaption.*?</figcaption>", re.I | re.S)
_ZEITSTEMPEL_TAG = re.compile(r"^(?P<tag>\d{4}-\d{2}-\d{2})")
_DE_DATUM = re.compile(r"\b(?P<tag>\d{1,2})\.(?P<monat>\d{1,2})\.(?P<jahr>\d{4})\b")
# Interne Nummer der Bekanntmachung, z. B. „0191-01“; sie steht im Dateinamen der Quelle.
_NUMMER = re.compile(r"(?<!\d)(?P<nr>\d{3,4}-\d{2})(?!\d)")

KATEGORIE_PRAEFIXE = (
    "öffentliche bekanntmachung von bauleitplänen",
)

# Reihum geprüft; die längste passende Formulierung gewinnt. Die Beschriftung ist eine
# **Kurzform der Formulierung der Quelle**, kein zusätzlicher Paragraf.
VERFAHRENSSCHRITTE: tuple[tuple[str, str], ...] = (
    ("frühzeitige beteiligung der öffentlichkeit", "Frühzeitige Beteiligung der Öffentlichkeit"),
    ("beteiligung der öffentlichkeit", "Beteiligung der Öffentlichkeit"),
    ("veröffentlichung eines flächennutzungsplan-entwurfs", "Veröffentlichung eines FNP-Entwurfs"),
    ("veröffentlichung eines bebauungsplan-entwurfs", "Veröffentlichung eines B-Plan-Entwurfs"),
    ("inkrafttreten eines bebauungsplans", "Inkrafttreten eines Bebauungsplans"),
    ("aufstellungsbeschluss", "Aufstellungsbeschluss"),
    ("satzungsbeschluss", "Satzungsbeschluss"),
    ("widmung", "Widmung einer Straße"),
    ("unterrichtung der öffentlichkeit", "Unterrichtung der Öffentlichkeit"),
)

# Die Tabellen stehen lesbar mit Umlauten und werden für den Vergleich normalisiert
# (norm schreibt Umlaute aus: „Öffentlichkeit“ → „oeffentlichkeit“).
_VERFAHRENSSCHRITTE_NORM = tuple((norm(phrase), beschriftung) for phrase, beschriftung in VERFAHRENSSCHRITTE)
_KATEGORIE_PRAEFIXE_NORM = tuple(norm(praefix) for praefix in KATEGORIE_PRAEFIXE)

# --- Bürgerbeteiligung --------------------------------------------------------

_KARTE = re.compile(r"<article\b[^>]*group--teaser[^>]*>(?P<inhalt>.*?)</article>", re.I | re.S)
_COUNTDOWN = re.compile(r'<p class="participation-countdown(?P<klasse>[^"]*)">(?P<text>.*?)</p>', re.I | re.S)
_TITEL_LINK = re.compile(
    r'<h[0-9]\b[^>]*class="title"[^>]*>\s*<a\b[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<titel>.*?)</a>',
    re.I | re.S,
)
_BEITRAEGE = re.compile(r'<span class="countValue">(?P<anzahl>\d+)</span>\s*(?P<wort>[^<]*)', re.I)
_BESCHREIBUNG = re.compile(r"field--field_description[^>]*>(?P<inhalt>.*?)</div>", re.I | re.S)

# Dieselben Pfade schließt die robots.txt der Plattform aus; sie sind keine Projekte.
_NICHT_PROJEKT = ("/search/", "/admin/", "/user/", "/comment/")


def _ohne_mehrfach(text: str) -> str:
    return " ".join(text.split())


def datum_aus_wert(wert: str) -> str:
    """ISO-Zeitstempel oder deutsches Datum → ISO-Tag; sonst leer."""
    treffer = _ZEITSTEMPEL_TAG.match(wert.strip())
    if treffer:
        return treffer.group("tag")
    treffer = _DE_DATUM.search(wert)
    if treffer:
        return f"{treffer.group('jahr')}-{treffer.group('monat').zfill(2)}-{treffer.group('tag').zfill(2)}"
    return ""


def _link_pruefen(link: str, seite_url: str, pfad: str | None = None) -> str:
    """Nur https-Links auf demselben Host; optional begrenzt auf einen Pfadbereich."""
    from urllib.parse import urljoin, urlsplit

    ziel, basis = urlsplit(urljoin(seite_url, link)), urlsplit(seite_url)
    if ziel.scheme != "https" or ziel.netloc != basis.netloc:
        return ""
    if not ziel.path:
        return ""
    if pfad and not ziel.path.startswith(pfad):
        return ""
    return urljoin(seite_url, link).split("#", 1)[0]


def verfahrensschritt(text: str) -> str:
    """Kurzform des in der Quelle genannten Verfahrensformulierung; sonst leer.

    Es wird nur erkannt, was die Quelle selbst schreibt – der Paragraf bleibt der
    Wortlaut der Bekanntmachung und wird hier nicht ergänzt.
    """
    n = norm(text)
    for phrase, beschriftung in _VERFAHRENSSCHRITTE_NORM:
        if phrase in n:
            return beschriftung
    return ""


def kategorie(text: str) -> str:
    """Erkannte Überschriftengruppe der Quelle (leer, wenn keine bekannt ist)."""
    n = norm(text)
    for praefix in _KATEGORIE_PRAEFIXE_NORM:
        if n.startswith(praefix):
            return "Öffentliche Bekanntmachung von Bauleitplänen"
    return ""


@dataclass(frozen=True)
class MeldungEintrag:
    """Ein Eintrag einer Listenquelle, wie er in der Datenbank landet."""

    vorgang_id: str
    art: str
    titel: str
    referenz: str
    datum: str
    web: str
    kategorie: str
    schritt: str
    status_kurz: str
    status_text: str
    offen: bool
    kurztext: str
    zusatz: str
    frist: str

    def suchtext(self) -> str:
        return norm(" ".join(x for x in (self.titel, self.referenz, self.art,
                                         self.kategorie, self.schritt, self.kurztext) if x))

    def vorgang_zeile(self, quelle_id: str) -> dict:
        return {
            "id": self.vorgang_id, "quelle_id": quelle_id, "referenz": self.referenz,
            "titel": self.titel, "art": self.art, "datum": self.datum, "geaendert": "",
            "web": self.web, "suchtext": self.suchtext(), "geloescht": 0,
        }

    def meldung_zeile(self, gesehen: str) -> dict:
        return {
            "vorgang_id": self.vorgang_id, "art_quelle": self.kategorie, "verfahrensschritt": self.schritt,
            "status_kurz": self.status_kurz, "status_text": self.status_text, "offen": int(self.offen),
            "kurztext": self.kurztext, "zusatz": self.zusatz, "frist": self.frist, "gesehen": gesehen,
        }

    def dokumente(self, name: str, rolle: str) -> list[dict]:
        """Das Original bleibt verlinkt; es wird keine Datei geladen."""
        if not self.web:
            return []
        return [{
            "id": self.vorgang_id, "name": name, "dateiname": self.web.rsplit("/", 1)[-1],
            "mime": "", "datum": self.datum, "url": self.web, "download_url": "", "rolle": rolle,
            "geaendert": "", "geloescht": 0,
        }]


def parse_bekanntmachungen(html: str, seite_url: str) -> tuple[list[MeldungEintrag], str, str]:
    """Öffentliche Bekanntmachungen einer Listenseite lesen.

    Rückgabe: (Einträge, Link zur nächsten Seite oder leer, Trefferangabe der Quelle).
    Einträge ohne erkennbaren Titel oder mit fremdem/ungültigem Link werden verworfen.
    """
    eintraege: list[MeldungEintrag] = []
    for treffer in _ANKER.finditer(html):
        href, inhalt = treffer.group(1), treffer.group(2)
        link = _link_pruefen(href, seite_url, BEKANNTMACHUNG_PFAD)
        if not link:
            continue
        titel = _titel_aus_anker(inhalt)
        if not titel:
            continue
        datum = _datum_aus_anker(inhalt)
        zusatz = _hinweisveroeffentlichung(inhalt)
        eintraege.append(MeldungEintrag(
            vorgang_id=f"bekanntmachung:{link.rsplit('/', 1)[-1].removesuffix('.pdf')}",
            art="Bekanntmachung",
            titel=titel,
            referenz=_nummer(link),
            datum=datum,
            web=link,
            kategorie=kategorie(titel),
            schritt=verfahrensschritt(titel),
            status_kurz="amtlich bekannt gemacht",
            status_text=_bekanntmachung_text(titel, datum, zusatz),
            offen=False,
            kurztext="",
            zusatz=zusatz,
            frist="",
        ))
    return eintraege, _naechste_seite(html, seite_url), _trefferangabe(html)


def _titel_aus_anker(inhalt: str) -> str:
    """Betreffzeile des Eintrags: Text der Abbildung ohne die Dateiangabe."""
    figure = _FIGURE.search(inhalt)
    roh = _FIGCAPTION.sub(" ", figure.group("inhalt")) if figure else _FIGCAPTION.sub(" ", inhalt)
    return _ohne_mehrfach(klartext(roh)).strip(" –-")


def _datum_aus_anker(inhalt: str) -> str:
    zeit = _TIME.search(inhalt)
    if zeit:
        datum = datum_aus_wert(zeit.group("wert"))
        if datum:
            return datum
    return ""


def _hinweisveroeffentlichung(inhalt: str) -> str:
    text = _ohne_mehrfach(klartext(inhalt))
    for treffer in re.finditer(r"Hinweisver\w*", text, re.I):
        rest = text[treffer.end(): treffer.end() + 40]
        datum = datum_aus_wert(rest)
        if datum:
            tag, monat, jahr = datum[8:10], datum[5:7], datum[:4]
            return f"Hinweisveröffentlichung {tag}.{monat}.{jahr}"
    return ""


def _nummer(link: str) -> str:
    treffer = _NUMMER.search(link.rsplit("/", 1)[-1])
    return treffer.group("nr") if treffer else ""


def _bekanntmachung_text(titel: str, datum: str, zusatz: str) -> str:
    teile = ["Amtliche Bekanntmachung der Stadt Köln"]
    if datum:
        teile.append(f"veröffentlicht am {datum_anzeige(datum)}")
    text = ", ".join(teile) + "."
    schritt = verfahrensschritt(titel)
    if schritt:
        text += f" Verfahrensschritt laut Quelle: {schritt}."
    if zusatz:
        text += f" {zusatz}."
    text += " Maßgeblich ist die verlinkte Bekanntmachung; hier werden nur Titel, Datum und Link übernommen."
    return text


def datum_anzeige(iso: str) -> str:
    return f"{iso[8:10]}.{iso[5:7]}.{iso[:4]}" if len(iso) >= 10 and iso[4] == "-" else ""


# Weiter-Link der Quelle: <a href="…?page=2#listtop" … rel="next">. Die Reihenfolge der
# Attribute ist nicht zugesichert, deshalb Lookahead auf rel="next" im selben Tag.
_WEITER_LINK = re.compile(r'<a\b(?=[^>]*rel="next")[^>]*>', re.I)
_HREF = re.compile(r'href="([^"]+)"', re.I)


def _naechste_seite(html: str, seite_url: str) -> str:
    """Folgeseite der Liste: zuerst der Weiter-Link der Quelle, sonst die Seitennummer.

    Die Quelle zählt die Seiten **1-basiert**: Ohne Parameter steht man auf Seite 1, und der
    Weiter-Link führt auf ``?page=2``. Wird stattdessen nur gerechnet, muss die Seite ohne
    Parameter deshalb als Seite 1 gelten – sonst liest ein Lauf die erste Seite erneut.
    """
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    for tag in _WEITER_LINK.finditer(html):
        href = _HREF.search(tag.group(0))
        if href:
            ziel = _link_pruefen(href.group(1), seite_url)
            if ziel:
                return ziel

    nummern = [int(m.group(1)) for m in re.finditer(r"[?&]page=(\d+)", html)]
    aktuell = dict(parse_qsl(urlsplit(seite_url).query)).get("page")
    aktuell = int(aktuell) if aktuell and aktuell.isdigit() else 1
    kandidaten = sorted({n for n in nummern if n > aktuell})
    if not kandidaten:
        return ""
    teile = urlsplit(seite_url)
    query = dict(parse_qsl(teile.query))
    query["page"] = str(kandidaten[0])
    return urlunsplit((teile.scheme, teile.netloc, teile.path, urlencode(query), ""))


def _trefferangabe(html: str) -> str:
    treffer = re.search(r"Treffer\s*([\d.,–\-]+)\s*von\s*([\d.,]+)", _ohne_mehrfach(klartext(html)), re.I)
    return f"Treffer {treffer.group(1).strip()} von {treffer.group(2)}" if treffer else ""


def parse_beteiligungen(html: str, seite_url: str) -> tuple[list[MeldungEintrag], int]:
    """Projektkarten der Beteiligungsplattform lesen: Titel, Statuszeile, Link, Kurztext.

    Übernommen wird nur, was die Stadt selbst veröffentlicht (Projekttitel, Träger,
    Beschreibung, Statuszeile). Beiträge Dritter, Kommentare und Bilder werden nicht
    gelesen. Rückgabe: (Einträge, Anzahl verworfener Karten).
    """
    eintraege: list[MeldungEintrag] = []
    verworfen = 0
    for treffer in _KARTE.finditer(html):
        karte = treffer.group("inhalt")
        kopf = _TITEL_LINK.search(karte)
        if not kopf:
            verworfen += 1
            continue
        link = _link_pruefen(kopf.group("href"), seite_url)
        titel = _ohne_mehrfach(klartext(kopf.group("titel")))
        if not link or not titel or any(t in link.lower() for t in _NICHT_PROJEKT):
            verworfen += 1
            continue
        statuszeile = _ohne_mehrfach(klartext(_COUNTDOWN.search(karte).group("text"))) if _COUNTDOWN.search(karte) else ""
        kurztext = _ohne_mehrfach(klartext(_BESCHREIBUNG.search(karte).group("inhalt"))) if _BESCHREIBUNG.search(karte) else ""
        if len(kurztext) > MAX_KURZTEXT:
            kurztext = kurztext[: MAX_KURZTEXT - 1].rstrip() + "…"
        kurz, text, offen = beteiligungs_status(statuszeile)
        eintraege.append(MeldungEintrag(
            vorgang_id=f"beteiligung:{_kennung(link)}",
            art="Beteiligungsangebot",
            titel=titel,
            referenz="",
            datum="",
            web=link,
            kategorie="Bürgerbeteiligung Meinung für Köln",
            schritt="",
            status_kurz=kurz,
            status_text=text,
            offen=offen,
            kurztext=kurztext,
            zusatz=_beitraege(karte),
            frist="",
        ))
    return eintraege, verworfen


def _kennung(link: str) -> str:
    from urllib.parse import urlsplit

    pfad = urlsplit(link).path.strip("/")
    return pfad.replace("/", "-") or "projekt"


def _beitraege(karte: str) -> str:
    treffer = _BEITRAEGE.search(karte)
    if not treffer:
        return ""
    wort = _ohne_mehrfach(treffer.group("wort")) or "Beiträge"
    return f"{treffer.group('anzahl')} {wort} laut Plattform"


def beteiligungs_status(zeile: str) -> tuple[str, str, bool]:
    """Statuszeile der Plattform in Kurzstatus, Text und „offen“ übersetzen.

    Es wird nur die Zeile ausgewertet, die die Plattform selbst anzeigt. Fehlt sie oder
    ist sie unbekannt, bleibt der Wortlaut stehen – es wird nichts dazuerfunden.
    """
    n = norm(zeile)
    if not n:
        return ("ohne Statusangabe",
                "Die Liste nennt für dieses Angebot keine Statuszeile; der Stand ist dort nicht ablesbar.",
                False)
    if "geschlossen" in n or "beendet" in n or "abgeschlossen" in n:
        return ("Beteiligung geschlossen",
                f"Auskunft der Plattform: {zeile}. Der genaue Beteiligungszeitraum steht auf der Projektseite.", False)
    if "startet" in n or "ab dem" in n or "vom" in n and "bis" in n:
        return ("Beteiligung angekündigt",
                f"Auskunft der Plattform: {zeile}. Beginn und Frist stehen auf der Projektseite.", True)
    if "laeuft" in n or "noch" in n or "tage" in n:
        return ("Beteiligung läuft",
                f"Auskunft der Plattform: {zeile}. Frist und Umfang stehen auf der Projektseite.", True)
    return (zeile[:80], f"Auskunft der Plattform: {zeile}.", False)


# --- Ortsbezug ----------------------------------------------------------------


def ortsbezuege(gaz: Gazetteer, text: str, quelle: str = "betreff") -> set[tuple[str, str, str]]:
    """Ortsbezug aus einem Titel oder einer Kurzbeschreibung ableiten.

    Zusätzlich zur normalen Wortsuche wird die Kölner Schreibweise „Köln-<Stadtteil>“
    aufgelöst: In „Arbeitstitel: ‚Feldgärtenstraße in Köln-Niehl‘“ erkennt der Gazetteer
    den Stadtteil sonst nicht, weil Bindestrich-Verbindungen als ein Wort gelten.
    """
    treffer: set[OrtsBezug] = set(gaz.ortsbezuege_aus_text(_koeln_marke(text), quelle))
    return {(o.typ, o.schluessel, o.quelle) for o in treffer}


def _koeln_marke(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\b(?:Köln|Koeln|koeln)\s*-\s*", "Köln ", text)


# --- Aufbereitung für die Suche ----------------------------------------------


def status_aus_zeile(zeile) -> dict:
    """Status eines gespeicherten Meldungseintrags – der Stand des letzten Abrufs."""
    stand = (zeile["gesehen"] or "")[:10]
    text = zeile["status_text"]
    if stand:
        text += f" (Stand des lokalen Abrufs: {datum_anzeige(stand)})"
    return {"kurz": zeile["status_kurz"], "text": text, "offen": bool(zeile["offen"])}


def zusammenfassung(titel: str, art: str, zeile) -> str:
    """Kurzbeschreibung aus den Feldern der Quelle – keine Ergänzung aus dem Nichts."""
    # Viele Bekanntmachungstitel tragen bereits Anführungszeichen („Arbeitstitel: …“);
    # dann wird der Titel nicht noch einmal in Anführungszeichen gesetzt.
    teile = [titel + "."] if ("„" in titel or "“" in titel) else [f"„{titel}“."]
    if zeile["kurztext"]:
        teile.append(zeile["kurztext"])
    teile.append(zeile["status_text"])
    if zeile["zusatz"]:
        teile.append(zeile["zusatz"] + ".")
    return " ".join(teile)


def verlauf(datum: str, zeile, url: str, heute: str) -> list[dict]:
    """Verlauf eines Meldungseintrags: ein Quellereignis und die Abrufbeobachtung.

    Der Verlauf behauptet **keine** Ratsberatung und keinen amtlichen Beginn, den die
    Liste nicht nennt. Er zeigt, was die Quelle veröffentlicht hat und wann wir es
    zuletzt gesehen haben.
    """
    ereignisse: list[dict] = []

    def eintrag(typ: str, tag: str, ereignis: str, beschreibung: str) -> dict:
        return {
            "typ": typ, "datum": tag, "datum_de": datum_anzeige(tag),
            "ereignis": ereignis, "beschreibung": beschreibung, "url": url,
            "gremium": zeile["art_quelle"] or "", "rolle": zeile["verfahrensschritt"] or "",
            "anstehend": bool(tag) and tag > heute,
        }

    if datum:
        ereignis = ("Amtliche Bekanntmachung veröffentlicht" if zeile["art_quelle"] != ""
                    else "Eintrag der Quelle veröffentlicht")
        ereignisse.append(eintrag("meldung", datum, ereignis, zeile["status_text"]))
    elif zeile["kurztext"]:
        ereignisse.append(eintrag("meldung", "", "Angaben der Quelle", zeile["kurztext"]))
    stand = (zeile["gesehen"] or "")[:10]
    if stand:
        beschreibung = "Abrufbeobachtung, kein amtlicher Verfahrensschritt."
        if zeile["status_text"]:
            beschreibung += " " + zeile["status_text"]
        ereignisse.append(eintrag("beobachtung", stand, "Zuletzt im lokalen Bestand erfasst", beschreibung))
    return ereignisse


def filtere_bekanntmachungen(eintraege: Iterable[MeldungEintrag]) -> list[MeldungEintrag]:
    """Doppelte Kennungen (z. B. dasselbe Dokument in zwei Listen) zusammenfassen."""
    gesehen: dict[str, MeldungEintrag] = {}
    for eintrag in eintraege:
        gesehen.setdefault(eintrag.vorgang_id, eintrag)
    return list(gesehen.values())
