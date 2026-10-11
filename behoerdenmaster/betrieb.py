"""Startinitialisierung und optionaler Hintergrundabruf im Web-Service.

Ein Render-Service mit SQLite-Disk kann die Disk nicht mit einem Cron-Service
teilen. Daher läuft genau ein stoppbarer Hintergrund-Thread neben der API.
Alle Quellen laufen sequenziell; der Zeitpunkt des letzten Versuchs bleibt in
SQLite erhalten, sodass ein Redeploy keine erneute Abrufserie auslöst.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from .abruf import Abrufer, BudgetErschoepft
from .config import Quelle, Stadt
from .crawl_sperre import CrawlLaeuft, crawl_sperre
from .crawler import CRAWLER, CrawlVerweigert, LaufBericht, LaufOptionen
from .speicher import Speicher, jetzt_iso

log = logging.getLogger("behoerdenmaster.betrieb")


def _bool(env: Mapping[str, str], key: str, default: bool = False) -> bool:
    wert = env.get(key, str(default)).strip().lower()
    if wert in ("true", "1", "yes", "on"):
        return True
    if wert in ("false", "0", "no", "off"):
        return False
    raise ValueError(f"{key} muss true oder false sein.")


def _zahl(env: Mapping[str, str], key: str, default: int, minimum: int) -> int:
    try:
        wert = int(env.get(key, str(default)))
    except ValueError as exc:
        raise ValueError(f"{key} muss eine ganze Zahl sein.") from exc
    if wert < minimum:
        raise ValueError(f"{key} muss mindestens {minimum} sein.")
    return wert


def pruefe_seit(wert: str) -> str:
    """`BEHOERDEN_SYNC_SEIT`: entweder `YYYY-MM-DD` oder eine Tagesanzahl (`90`, `90d`).

    Eine Tagesanzahl ist ein **rollierendes Fenster** und damit für Instanzen ohne
    persistente Disk geeignet: Der Erstabruf bleibt klein, obwohl das Datum nicht
    nachgepflegt werden muss. Der leere Wert übernimmt `seit` aus der Stadtkonfiguration.
    """
    roh = (wert or "").strip()
    if not roh:
        return ""
    kandidaten = roh[:-1] if roh[-1] in "dD" else roh
    if kandidaten.isdigit():
        if int(kandidaten) < 1:
            raise ValueError("BEHOERDEN_SYNC_SEIT muss als Tagesanzahl mindestens 1 sein.")
        return f"{int(kandidaten)}d"
    try:
        date.fromisoformat(roh)
    except ValueError as exc:
        raise ValueError(
            "BEHOERDEN_SYNC_SEIT muss ein Datum (YYYY-MM-DD) oder eine Tagesanzahl (z. B. 90) sein."
        ) from exc
    return roh


@dataclass(frozen=True)
class Betrieb:
    demo: bool = False
    sync_aktiv: bool = False
    kontakt: str = field(default="", repr=False)
    sync_quellen: tuple[str, ...] = ()  # vollständige IDs, z. B. koeln:bauleitplanung
    sync_intervall: int = 21600  # sechs Stunden pro Quelle; auch nach Neustarts
    sync_budget: int = 100  # pro Quelle und Etappe, einschließlich robots.txt
    sync_startverzoegerung: int = 30
    sync_seit: str = ""  # leer = `seit` aus der Stadtkonfiguration; "90d" = rollierendes Fenster
    # Konfigurationsprobleme, die den Dienst bewusst nicht stoppen (z. B. fehlende Kontaktadresse).
    warnungen: tuple[str, ...] = ()

    @classmethod
    def aus_umgebung(cls, env: Mapping[str, str] | None = None, *, streng: bool = True) -> "Betrieb":
        """Umgebung auswerten.

        `streng=True` (CLI) bricht bei ungültigen Werten ab. `streng=False` (Web-Service)
        hält die Seite erreichbar: Ein einzelner falsch gesetzter Umgebungswert darf nicht
        dazu führen, dass gar nichts mehr ausgeliefert wird. Das Problem wird stattdessen
        in `warnungen` und über `/api/status` sichtbar.
        """
        env = os.environ if env is None else env
        warnungen: list[str] = []

        def flag(key: str, default: bool = False) -> bool:
            try:
                return _bool(env, key, default)
            except ValueError as exc:
                if streng:
                    raise
                warnungen.append(f"{exc} Der Standardwert {'true' if default else 'false'} wird verwendet.")
                return default

        def zahl(key: str, default: int, minimum: int) -> int:
            try:
                return _zahl(env, key, default, minimum)
            except ValueError as exc:
                if streng:
                    raise
                warnungen.append(f"{exc} Der Standardwert {default} wird verwendet.")
                return default

        def seit() -> str:
            try:
                return pruefe_seit(env.get("BEHOERDEN_SYNC_SEIT", ""))
            except ValueError as exc:
                if streng:
                    raise
                warnungen.append(f"{exc} Das Datum aus der Stadtkonfiguration wird verwendet.")
                return ""

        aktiv = flag("BEHOERDEN_SYNC_ENABLED")
        kontakt = env.get("BEHOERDEN_KONTAKT", "").strip()
        if "\n" in kontakt or "\r" in kontakt:
            raise ValueError("BEHOERDEN_KONTAKT darf keine Zeilenumbrüche enthalten.")
        if aktiv and not kontakt:
            if streng:
                raise ValueError("BEHOERDEN_KONTAKT ist für automatische Abrufe erforderlich.")
            warnungen.append(
                "BEHOERDEN_SYNC_ENABLED ist aktiviert, aber BEHOERDEN_KONTAKT fehlt. Automatische Abrufe "
                "bleiben ausgeschaltet, damit die Behörde nicht von einem nicht identifizierbaren Client "
                "abgerufen wird. Bitte eine erreichbare Kontaktadresse als Umgebungswert setzen."
            )
            aktiv = False
        quellen = tuple(dict.fromkeys(q.strip() for q in env.get("BEHOERDEN_SYNC_QUELLEN", "").split(",") if q.strip()))
        return cls(
            demo=flag("BEHOERDEN_DEMO"), sync_aktiv=aktiv, kontakt=kontakt,
            sync_quellen=quellen,
            sync_intervall=zahl("BEHOERDEN_SYNC_INTERVALL", 21600, 300),
            sync_budget=zahl("BEHOERDEN_SYNC_MAX_ANFRAGEN", 100, 1),
            sync_startverzoegerung=zahl("BEHOERDEN_SYNC_STARTVERZOEGERUNG", 30, 0),
            sync_seit=seit(),
            warnungen=tuple(warnungen),
        )

    def seit_datum(self, referenz: date | None = None) -> str | None:
        """Aufgelöstes `--seit` für den Erstabruf; None überlässt der Konfiguration den Vorrang."""
        if not self.sync_seit:
            return None
        if self.sync_seit.endswith("d"):
            tage = int(self.sync_seit[:-1])
            tag = (referenz or datetime.now(timezone.utc).date()) - timedelta(days=tage)
            return tag.isoformat()
        return self.sync_seit

    def oeffentlicher_status(self) -> dict:
        # Insbesondere keine Kontaktadresse, Dateipfade oder sonstigen Env-Werte veröffentlichen.
        return {
            "automatische_abrufe": self.sync_aktiv,
            "intervall_sekunden": self.sync_intervall,
            "max_anfragen_pro_quelle": self.sync_budget,
            "startverzoegerung_sekunden": self.sync_startverzoegerung,
            "erstabruf_seit": self.sync_seit,
            "quellen": list(self.sync_quellen),
            "warnungen": list(self.warnungen),
        }


def initialisiere(db_pfad: str | Path, staedte: dict[str, Stadt], betrieb: Betrieb) -> None:
    """Disk/Schema erst zur Laufzeit öffnen; optional einmalige, klar markierte Demo.

    Der Marker verhindert, dass gelöschte Demo-Daten beim Redeploy wiederkommen.
    Bereits vorhandene Vorgänge werden nie durch automatisches Seeding ergänzt
    oder überschrieben. Manuelles `demo` bleibt unabhängig davon möglich.

    Der Grund für eine nicht geladene Demo wird in `betrieb_meta` festgehalten, damit
    `/api/status` eine leere Startseite erklären kann statt nur nichts anzuzeigen.
    """
    with Speicher(db_pfad) as sp:
        if betrieb.demo:
            try:
                with crawl_sperre(db_pfad):
                    with sp.transaktion():
                        if not sp.betrieb_wert("demo_initialisiert"):
                            tabellen = ("gremium", "sitzung", "tagesordnungspunkt", "vorgang", "beratung", "dokument")
                            leer = not any(sp.db.execute(f"SELECT 1 FROM {t} LIMIT 1").fetchone() for t in tabellen)
                            if leer and "koeln" in staedte:
                                from .demo import lade_demo

                                lade_demo(sp, staedte["koeln"])
                                sp.betrieb_setzen("demo_status", "geladen")
                                log.info("Offline-Demo einmalig geladen; keine Live-Daten")
                            else:
                                sp.betrieb_setzen("demo_status", "bestand_vorhanden" if not leer else "keine_stadt_koeln")
                            sp.betrieb_setzen("demo_initialisiert", jetzt_iso())
                        else:
                            sp.betrieb_setzen("demo_status", sp.betrieb_wert("demo_status") or "bereits_initialisiert")
            except CrawlLaeuft:
                # Ein manueller Import ist kein Grund, den Web-Service zu blockieren.
                sp.betrieb_setzen("demo_status", "uebersprungen_import_laeuft")
                log.info("Demo-Initialisierung übersprungen: Ein Import/Crawl läuft bereits")
        else:
            sp.betrieb_setzen("demo_status", "deaktiviert")
        for stadt in staedte.values():
            for quelle in stadt.quellen:
                sp.quelle_anlegen(stadt.quell_id(quelle), stadt.schluessel, quelle.schluessel,
                                   quelle.typ, quelle.name, quelle.system_url)


def _utc(wert: str) -> datetime:
    moment = datetime.fromisoformat(wert)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def naechster_abruf(zeile: Mapping | None, intervall: int) -> datetime | None:
    """Persistierter Zeitplan aus dem letzten Versuch; Server-Pause hat Vorrang."""
    if zeile is None:
        return None
    termine = []
    if zeile["letzter_versuch"]:
        termine.append(_utc(zeile["letzter_versuch"]) + timedelta(seconds=intervall))
    if zeile["abkuehlung_bis"]:
        termine.append(_utc(zeile["abkuehlung_bis"]))
    return max(termine) if termine else None


class SyncDienst:
    """Ein einzelner Thread; SQL-Verbindungen gehören jeweils dem ausführenden Thread."""

    def __init__(self, db_pfad: str | Path, staedte: dict[str, Stadt], betrieb: Betrieb,
                 abrufer_factory: Callable = Abrufer,
                 clock: Callable[[], datetime] | None = None):
        self.db_pfad = str(db_pfad)
        self.betrieb = betrieb
        self.abrufer_factory = abrufer_factory
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        alle = {s.quell_id(q): (s, q) for s in staedte.values() for q in s.quellen}
        auswahl = betrieb.sync_quellen or tuple(alle)
        unbekannte = [qid for qid in auswahl if qid not in alle]
        if unbekannte:
            raise ValueError("Unbekannte BEHOERDEN_SYNC_QUELLEN: " + ", ".join(unbekannte))
        self.quellen: dict[str, tuple[Stadt, Quelle]] = {qid: alle[qid] for qid in auswahl}
        if any(q.typ not in CRAWLER for _, q in self.quellen.values()):
            raise ValueError("Automatischer Abruf für einen nicht implementierten Quelltyp konfiguriert.")

    @property
    def laeuft(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def starten(self) -> None:
        if not self.betrieb.sync_aktiv:
            return
        if not self.betrieb.kontakt:
            raise ValueError("Eine Kontaktadresse ist für automatische Abrufe erforderlich.")
        if self._thread is not None:
            raise RuntimeError("Dieser Sync-Dienst wurde bereits gestartet.")
        self._thread = threading.Thread(target=self._schleife, name="behoerdenmaster-sync", daemon=True)
        self._thread.start()

    def stoppen(self) -> None:
        self._stop.set()

    def warten(self, timeout: float = 40) -> None:
        if self._thread is not None:
            self._thread.join(timeout)
            if self._thread.is_alive():
                log.warning("Sync-Thread beendet sich noch; vorhandene Checkpoints bleiben erhalten")

    def _pause(self, sekunden: float) -> None:
        if self._stop.wait(sekunden):
            raise BudgetErschoepft("Service wird beendet; der nächste Abruf setzt fort")

    def _schleife(self) -> None:
        if self._stop.wait(self.betrieb.sync_startverzoegerung):
            return
        while not self._stop.is_set():
            try:
                self.etappe()
            except Exception:
                # Ein unerwarteter Fehler darf den Web-Service nicht beenden.
                log.exception("Hintergrundabruf fehlgeschlagen; keine sofortige Wiederholung")
            if self._stop.wait(60):
                return

    def etappe(self) -> list[LaufBericht]:
        """Nur fällige Quellen, sequenziell und begrenzt. Auch direkt offline testbar."""
        if not self.betrieb.sync_aktiv or self._stop.is_set():
            return []
        berichte = []
        try:
            with crawl_sperre(self.db_pfad), Speicher(self.db_pfad) as sp:
                for qid, (stadt, quelle) in self.quellen.items():
                    if self._stop.is_set():
                        break
                    zeile = sp.db.execute("SELECT * FROM quelle WHERE id=?", (qid,)).fetchone()
                    fruehestens = naechster_abruf(zeile, self.betrieb.sync_intervall)
                    if fruehestens and self.clock() < fruehestens:
                        continue
                    abrufer = self.abrufer_factory(
                        kontakt=self.betrieb.kontakt, min_intervall=quelle.min_intervall_sekunden,
                        max_anfragen=self.betrieb.sync_budget, sleep=self._pause,
                        soll_abbrechen=self._stop.is_set,
                    )
                    try:
                        log.info("Automatischer Abruf %s, Budget %d", qid, self.betrieb.sync_budget)
                        bericht = CRAWLER[quelle.typ](sp, stadt, quelle, abrufer, LaufOptionen(
                            seit=self.betrieb.seit_datum(), max_anfragen=self.betrieb.sync_budget))
                        berichte.append(bericht)
                        log.info("Abruf %s: %s (%d Anfragen)", qid, bericht.status, bericht.anfragen)
                    except CrawlVerweigert as exc:
                        log.info("Abruf %s pausiert: %s", qid, exc)
                    except Exception:
                        # Quellen getrennt behandeln: ein Schema-/Netzfehler blockiert nicht die andere Quelle.
                        log.exception("Unerwarteter Fehler beim Abruf %s", qid)
                        sp.quelle_status(qid, "fehler", "Unerwarteter Abruffehler; siehe Service-Logs")
                    finally:
                        abrufer.schliessen()
        except CrawlLaeuft:
            log.info("Automatischer Abruf übersprungen: Ein anderer Crawl/Import läuft")
        return berichte
