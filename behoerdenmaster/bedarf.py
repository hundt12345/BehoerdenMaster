"""Laden auf Abruf: Welche „auf_abruf“-Städte gerade gebraucht werden.

Eine Stadt mit ``[stadt] abruf = "auf_abruf"`` ist vollständig konfiguriert, wird aber nicht
dauerhaft abgerufen. Erst wenn jemand sie **anfordert** – Auswahl in der Oberfläche oder
eine Suche in dieser Stadt –, vermerkt die Anwendung den Bedarf in der lokalen Datenbank.
Der Hintergrunddienst nimmt die Quellen dieser Stadt dann in seinen normalen, gedrosselten
Abrufplan auf.

Was dabei bewusst **nicht** passiert:

* Eine Suche löst keinen Behördenabruf aus. Sie schreibt höchstens einen Vermerk
  (Stadt + Zeitpunkt), keine IP-Adresse und keine Suchbegriffe.
* Die Zahl der Abrufe wächst nicht mit der Zahl der Anfragen: Je Stadt gibt es einen
  Vermerk, und der Abruf folgt demselben Intervall, denselben Budgets und derselben
  Drossel wie bei Köln.
* Unbekannte Städte können nicht angefordert werden – nur, was in ``config/cities`` steht.

Ein Bedarf **verfällt** nach ``BEHOERDEN_AUF_ABRUF_TAGE`` ohne neue Anforderung; die Stadt
ruht dann wieder (ihr Bestand bleibt erhalten und durchsuchbar). Höchstens
``BEHOERDEN_AUF_ABRUF_MAX`` Städte werden gleichzeitig auf Abruf aktuell gehalten – die
zuletzt angeforderten zuerst.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .config import Stadt
from .speicher import Speicher

# Eine erneute Anforderung derselben Stadt wird höchstens so oft geschrieben. Das hält
# Suchanfragen schreibarm, ohne dass ein Bedarf bei laufender Nutzung verfällt.
SCHREIB_ABSTAND = timedelta(minutes=10)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _utc(wert: str) -> datetime:
    moment = datetime.fromisoformat(wert)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def bedarf_melden(sp: Speicher, stadt: Stadt, jetzt: datetime | None = None) -> bool:
    """Bedarf an einer Auf-Abruf-Stadt vermerken. True, wenn der Bedarf neu oder erneuert ist.

    Für dauerhaft abgerufene Städte passiert nichts: Sie sind ohnehin im Abrufplan.
    """
    if not stadt.auf_abruf:
        return False
    jetzt = jetzt or datetime.now(timezone.utc)
    zeile = sp.db.execute("SELECT zuletzt FROM stadt_bedarf WHERE stadt = ?", (stadt.schluessel,)).fetchone()
    if zeile and zeile["zuletzt"] and jetzt - _utc(zeile["zuletzt"]) < SCHREIB_ABSTAND:
        return False
    with sp.transaktion():
        sp.db.execute(
            """INSERT INTO stadt_bedarf (stadt, erstmals, zuletzt, anforderungen) VALUES (?, ?, ?, 1)
               ON CONFLICT(stadt) DO UPDATE SET zuletzt = excluded.zuletzt,
                                                anforderungen = stadt_bedarf.anforderungen + 1""",
            (stadt.schluessel, _iso(jetzt), _iso(jetzt)),
        )
    return True


def bedarfe(sp: Speicher) -> dict[str, dict]:
    """Alle vermerkten Bedarfe: Stadt → {erstmals, zuletzt, anforderungen}."""
    return {r["stadt"]: {"erstmals": r["erstmals"], "zuletzt": r["zuletzt"], "anforderungen": r["anforderungen"]}
            for r in sp.db.execute("SELECT * FROM stadt_bedarf")}


def aktive_staedte(sp: Speicher, staedte: dict[str, Stadt], *, tage: int, maximum: int,
                   jetzt: datetime | None = None) -> list[str]:
    """Auf-Abruf-Städte mit gültigem Bedarf, zuletzt angeforderte zuerst, höchstens ``maximum``."""
    if maximum < 1:
        return []
    jetzt = jetzt or datetime.now(timezone.utc)
    grenze = jetzt - timedelta(days=tage)
    kandidaten = []
    for schluessel, eintrag in bedarfe(sp).items():
        stadt = staedte.get(schluessel)
        if stadt is None or not stadt.auf_abruf or not eintrag["zuletzt"]:
            continue
        zuletzt = _utc(eintrag["zuletzt"])
        if zuletzt >= grenze:
            kandidaten.append((zuletzt, schluessel))
    kandidaten.sort(reverse=True)
    return [schluessel for _, schluessel in kandidaten[:maximum]]


def bedarf_gueltig_bis(eintrag: dict | None, tage: int) -> str:
    if not eintrag or not eintrag.get("zuletzt"):
        return ""
    return _iso(_utc(eintrag["zuletzt"]) + timedelta(days=tage))
