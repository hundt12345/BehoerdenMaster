"""RSS-Hinweise der Stadt Köln: Sitzungstermine (SessionNet) und Pressemeldungen.

Jede Quelle ist genau ein Feed-Dokument pro Lauf. Gespeichert werden nur Titel,
Datum, Uhrzeit, Ort, Link und ein gekürzter Kurztext. Ein Feed-Eintrag ist bewusst
KEIN Vorgang: er erscheint nicht in der Suche und ersetzt nicht den OParl-Abruf.

Regeln wie bei allen Quellen: Abruf nur über `Abrufer` (robots.txt, Mindestabstand,
Anfragebudget, keine Umgehung von Sperren). Links werden nur akzeptiert, wenn sie auf
demselben Host wie der Feed liegen. Fehlende Einträge werden nie gelöscht.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urlsplit

from .abruf import Abrufer, BudgetErschoepft, NichtErlaubt, ObjektNichtVorhanden, QuelleGesperrt, QuelleNichtErreichbar
from .bauleitplanung import klartext
from .config import Quelle, Stadt
from .crawler import LaufBericht, LaufOptionen, merke_server_pause, pruefe_abkuehlung
from .speicher import Speicher, jetzt_iso

ART_JE_TYP = {"ris-rss": "sitzungen", "presse-rss": "presse"}
MAX_BYTES = 2_000_000
MAX_KURZTEXT = 600

# Titel der SessionNet-Einträge: „Gremium | 12.10.2026 - 17:00 (Sitzung AVR/0008/2026)“
_SITZUNG_TITEL = re.compile(
    r"^(?P<gremium>.+?)\s*\|\s*(?P<datum>\d{2}\.\d{2}\.\d{4})\s*-\s*(?P<zeit>\d{1,2}:\d{2})\s*"
    r"\(Sitzung\s+(?P<nr>[^)]+?)\s*\)"
)
_OPARL_ID = re.compile(r"[?&]si(\d+)\b")  # rssgo.asp?si34706 entspricht OParl meetings/34706
_XML_KOPF = re.compile(r"^\s*<\?xml[^>]*\?>")


class RssFehler(ValueError):
    """Der Feed hat nicht das erwartete Format. Der Bestand bleibt unverändert."""


@dataclass(frozen=True)
class Hinweis:
    id: str
    art: str
    titel: str
    datum: str
    zeit: str
    ort: str
    gremium: str
    web: str
    kurztext: str
    bezug: str

    def zeile(self, abgerufen: str) -> dict:
        return {
            "id": self.id, "art": self.art, "titel": self.titel, "datum": self.datum, "zeit": self.zeit,
            "ort": self.ort, "gremium": self.gremium, "web": self.web, "kurztext": self.kurztext,
            "bezug": self.bezug, "abgerufen": abgerufen,
        }


class _Tabelle(HTMLParser):
    """Zellenpaare „Ort:“/„Gremium:“ aus der HTML-Beschreibung eines Eintrags lesen."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.zellen: list[str] = []
        self._in_td = False
        self.paare: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        if tag == "td":
            self._in_td = True
            self.zellen.append("")
        elif tag == "tr":
            self.zellen = []

    def handle_endtag(self, tag):
        if tag == "td":
            self._in_td = False
        elif tag == "tr" and len(self.zellen) >= 2:
            schluessel = " ".join(self.zellen[0].split()).rstrip(":").strip()
            if schluessel:
                self.paare[schluessel] = " ".join(self.zellen[1].split())

    def handle_data(self, data):
        if self._in_td and self.zellen:
            self.zellen[-1] += data


def _iso_datum(tt_mm_jjjj: str) -> str:
    try:
        return datetime.strptime(tt_mm_jjjj, "%d.%m.%Y").date().isoformat()
    except ValueError as exc:
        raise RssFehler(f"Ungültiges Datum im Feed: {tt_mm_jjjj}") from exc


def _kurz(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= MAX_KURZTEXT else text[: MAX_KURZTEXT - 1].rstrip() + "…"


def _eigener_link(link: str, basis: str) -> str | None:
    """Nur https-Links auf demselben Host wie der Feed übernehmen."""
    ziel, quelle = urlsplit(link), urlsplit(basis)
    if ziel.scheme != "https" or ziel.netloc != quelle.netloc or not ziel.path:
        return None
    return link


def _kind_text(item: ET.Element, name: str) -> str:
    kind = item.find(name)
    return (kind.text or "").strip() if kind is not None else ""


def parse_feed(text: str, art: str, basis: str) -> tuple[list[Hinweis], int]:
    """RSS 2.0 lesen. Gibt (übernommene Hinweise, Anzahl verworfener Einträge) zurück."""
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise RssFehler("RSS-Feed ist ungewöhnlich groß; Abbruch")
    try:
        # Ein Encoding-Kopf im Text ist für fromstring() unzulässig; der Text liegt bereits decodiert vor.
        wurzel = ET.fromstring(_XML_KOPF.sub("", text, count=1).encode("utf-8"))
    except ET.ParseError as exc:
        raise RssFehler(f"RSS-Feed ist kein gültiges XML: {exc}") from exc
    channel = wurzel.find("channel") if wurzel.tag == "rss" else None
    if channel is None or not _kind_text(channel, "title"):
        raise RssFehler("RSS-Feed enthält keinen Kanal (rss/channel/title)")

    treffer: list[Hinweis] = []
    verworfen = 0
    for item in channel.findall("item"):
        link = _eigener_link(_kind_text(item, "link"), basis)
        titel = _kind_text(item, "title")
        beschreibung = item.find("description")
        roh = (beschreibung.text or "") if beschreibung is not None else ""
        if not link or not titel:
            verworfen += 1
            continue
        if art == "sitzungen":
            hinweis = _sitzung(titel, roh, link)
        else:
            hinweis = _presse(item, titel, roh, link)
        if hinweis is None:
            verworfen += 1
        else:
            treffer.append(hinweis)
    return treffer, verworfen


def _sitzung(titel: str, roh: str, link: str) -> Hinweis | None:
    # Vorlagen und Niederschriften im selben Feed haben andere Titel; sie werden verworfen.
    m = _SITZUNG_TITEL.match(" ".join(titel.split()))
    if not m:
        return None
    tabelle = _Tabelle()
    tabelle.feed(roh)
    nr = m.group("nr").strip()
    oparl = _OPARL_ID.search(link)
    # Text vor der HTML-Tabelle ist der Kurztext des Eintrags (z. B. „Sondersitzung …“).
    vorspann = _kurz(klartext(roh.split("<", 1)[0]))
    return Hinweis(
        id=f"sitzung:{nr}",
        art="Sitzungstermin",
        titel=f"{m.group('gremium').strip()} – Sitzung {nr}",
        datum=_iso_datum(m.group("datum")),
        zeit=m.group("zeit"),
        ort=tabelle.paare.get("Ort", ""),
        gremium=tabelle.paare.get("Gremium", m.group("gremium").strip()),
        web=link,
        kurztext=vorspann,
        bezug=f"meetings/{oparl.group(1)}" if oparl else "",
    )


def _presse(item: ET.Element, titel: str, roh: str, link: str) -> Hinweis | None:
    try:
        datum = parsedate_to_datetime(_kind_text(item, "pubDate")).date().isoformat()
    except (TypeError, ValueError):
        return None  # ohne gültiges Datum kein sinnvoller Hinweis; der Eintrag wird verworfen
    ident = _kind_text(item, "guid") or link
    return Hinweis(
        id=ident[:300], art="Pressemitteilung", titel=" ".join(titel.split())[:300], datum=datum,
        zeit="", ort="", gremium="", web=link, kurztext=_kurz(klartext(roh)), bezug="",
    )


def crawle_rss(speicher: Speicher, stadt: Stadt, quelle: Quelle, abrufer: Abrufer,
               optionen: LaufOptionen) -> LaufBericht:
    """Ein Feed-Abruf pro Lauf. Ergebnisse landen in der Tabelle `hinweis`."""
    art = ART_JE_TYP[quelle.typ]
    qid = stadt.quell_id(quelle)
    speicher.quelle_anlegen(qid, stadt.schluessel, quelle.schluessel, quelle.typ,
                            quelle.name, quelle.system_url, demo=False)
    pruefe_abkuehlung(speicher, qid, optionen)
    bericht = LaufBericht(quelle_id=qid)
    lauf = speicher.lauf_beginnen(qid)
    speicher.quelle_status(qid, "laeuft")
    try:
        feed = abrufer.get_text(quelle.system_url)
        treffer, verworfen = parse_feed(feed, art, quelle.system_url)
        speicher.hinweise_speichern(qid, [h.zeile(jetzt_iso()) for h in treffer])
        for _ in treffer:
            bericht.zaehle(f"hinweis:{art}")
        for _ in range(verworfen):
            bericht.zaehle("hinweis:verworfen")
        bericht.status = "ok"
    except BudgetErschoepft as exc:
        bericht.status, bericht.fehlertext = "teilweise", str(exc)
    except (QuelleGesperrt, NichtErlaubt) as exc:
        merke_server_pause(bericht, exc)
        bericht.status, bericht.fehlertext = "gesperrt", str(exc)
    except (QuelleNichtErreichbar, ObjektNichtVorhanden) as exc:
        merke_server_pause(bericht, exc)
        bericht.status, bericht.fehlertext = "nicht erreichbar", str(exc)
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
        speicher.lauf_beenden(lauf, bericht.status, bericht.anfragen, bericht.zaehler, bericht.fehlertext)
        speicher.quelle_status(qid, bericht.status, bericht.fehlertext, erfolg=bericht.status == "ok",
                               abkuehlung_bis=bericht.abkuehlung_bis)
    return bericht
