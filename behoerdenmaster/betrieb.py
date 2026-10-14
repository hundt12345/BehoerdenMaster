"""Startinitialisierung und optionaler Hintergrundabruf im Web-Service.

Ein Render-Service mit SQLite-Disk kann die Disk nicht mit einem Cron-Service
teilen. Daher läuft genau ein stoppbarer Hintergrund-Thread neben der API.
Die fälligen Quellen einer Etappe laufen **parallel** (bis `sync_parallel`,
Voreinstellung 3); pro Host sorgt eine gemeinsame Drossel weiterhin für den
Mindestabstand zwischen Anfragen. Der Zeitpunkt des letzten Versuchs bleibt in
SQLite erhalten, sodass ein Redeploy keine erneute Abrufserie auslöst.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from .abruf import Abrufer, BudgetErschoepft, Drossel
from .bedarf import aktive_staedte
from .config import PROJEKT_KONTAKT, Quelle, Stadt
from .crawl_sperre import CrawlLaeuft, crawl_sperre
from .crawler import CRAWLER, CrawlVerweigert, LaufBericht, LaufOptionen
from .sicherung import sichere_datenbank
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
    # Gleichzeitige Quellen je Etappe. Unterschiedliche Hosts laufen parallel; Quellen
    # auf demselben Host teilen sich weiterhin ihre Drossel und starten Anfragen
    # nacheinander. 1 schaltet bewusst auf die alte, strikt sequenzielle Reihenfolge.
    sync_parallel: int = 3
    # Zeitfenster je Quelle und Etappe. Quellen laufen nacheinander; ohne Fenster kann eine
    # einzige große Quelle (Erstabruf der Ratsinformation) alle anderen so lange blockieren,
    # dass deren Daten erst Stunden später erscheinen. Nach dem Fenster wird die Quelle
    # unterbrochen und beim nächsten Mal an ihrem gespeicherten Cursor fortgesetzt.
    sync_zeitbudget: int = 240
    sync_startverzoegerung: int = 30
    sync_seit: str = ""  # leer = `seit` aus der Stadtkonfiguration; "90d" = rollierendes Fenster
    # Laden auf Abruf (bedarf.py): Städte mit `abruf = "auf_abruf"` erst nach Anforderung abrufen.
    auf_abruf: bool = True
    auf_abruf_tage: int = 14  # ohne neue Anforderung ruht die Stadt danach wieder
    auf_abruf_max: int = 3  # höchstens so viele Auf-Abruf-Städte gleichzeitig aktuell halten
    # Automatische Sicherung (nur mit persistenter Disk sinnvoll); leer = aus.
    backup_verzeichnis: str = field(default="", repr=False)
    backup_intervall: int = 86400
    backup_behalten: int = 7
    # Konfigurationsprobleme, die den Dienst bewusst nicht stoppen (z. B. fehlende Kontaktadresse).
    warnungen: tuple[str, ...] = ()

    @classmethod
    def aus_umgebung(cls, env: Mapping[str, str] | None = None, *, streng: bool = True) -> "Betrieb":
        """Umgebung auswerten.

        `streng=True` (CLI) bricht bei ungültigen Werten ab. `streng=False` (Web-Service)
        hält die Seite erreichbar: Ein einzelner falsch gesetzter Umgebungswert darf nicht
        dazu führen, dass gar nichts mehr ausgeliefert wird. Das Problem wird stattdessen
        in `warnungen` und über `/api/status` sichtbar.

        Voreinstellung der Umgebung: **Abrufe sind aktiviert** und als Kontaktadresse dient
        `PROJEKT_KONTAKT`, solange `BEHOERDEN_KONTAKT` fehlt. Ein Deployment soll sich
        also von selbst füllen, statt wegen einer fehlenden Einstellung dauerhaft leer zu
        bleiben. `BEHOERDEN_SYNC_ENABLED=false` schaltet bewusst ab; ein leer gesetztes
        `BEHOERDEN_KONTAKT` verzichtet bewusst auf die Voreinstellung.
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

        aktiv = flag("BEHOERDEN_SYNC_ENABLED", default=True)
        # Nicht gesetzt > im Repository hinterlegte Projektadresse; leer gesetzt > bewusst keine.
        roh_kontakt = env.get("BEHOERDEN_KONTAKT")
        voreinstellung = roh_kontakt is None
        kontakt = (PROJEKT_KONTAKT if voreinstellung else roh_kontakt).strip()
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
        elif voreinstellung and aktiv:
            warnungen.append(
                "BEHOERDEN_KONTAKT ist nicht gesetzt; die im Repository hinterlegte Projekt-Kontaktadresse "
                "wird verwendet. Eigene Deployments sollten eine eigene erreichbare Adresse setzen."
            )
        quellen = tuple(dict.fromkeys(q.strip() for q in env.get("BEHOERDEN_SYNC_QUELLEN", "").split(",") if q.strip()))
        return cls(
            demo=flag("BEHOERDEN_DEMO"), sync_aktiv=aktiv, kontakt=kontakt,
            sync_quellen=quellen,
            sync_intervall=zahl("BEHOERDEN_SYNC_INTERVALL", 21600, 300),
            sync_budget=zahl("BEHOERDEN_SYNC_MAX_ANFRAGEN", 100, 1),
            sync_parallel=zahl("BEHOERDEN_SYNC_PARALLEL", 3, 1),
            sync_zeitbudget=zahl("BEHOERDEN_SYNC_ZEITBUDGET", 240, 30),
            sync_startverzoegerung=zahl("BEHOERDEN_SYNC_STARTVERZOEGERUNG", 30, 0),
            sync_seit=seit(),
            auf_abruf=flag("BEHOERDEN_AUF_ABRUF", default=True),
            auf_abruf_tage=zahl("BEHOERDEN_AUF_ABRUF_TAGE", 14, 1),
            auf_abruf_max=zahl("BEHOERDEN_AUF_ABRUF_MAX", 3, 0),
            backup_verzeichnis=env.get("BEHOERDEN_BACKUP_VERZEICHNIS", "").strip(),
            backup_intervall=zahl("BEHOERDEN_BACKUP_INTERVALL", 86400, 3600),
            backup_behalten=zahl("BEHOERDEN_BACKUP_BEHALTEN", 7, 1),
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
            "kontakt_hinterlegt": bool(self.kontakt),
            "intervall_sekunden": self.sync_intervall,
            "max_anfragen_pro_quelle": self.sync_budget,
            "parallel_quellen": self.sync_parallel,
            "zeitbudget_pro_quelle_sekunden": self.sync_zeitbudget,
            "startverzoegerung_sekunden": self.sync_startverzoegerung,
            "erstabruf_seit": self.sync_seit,
            "quellen": list(self.sync_quellen),
            "laden_auf_abruf": {
                "aktiv": self.auf_abruf,
                "verfall_tage": self.auf_abruf_tage,
                "max_staedte": self.auf_abruf_max,
            },
            # Ob gesichert wird – nie der Pfad.
            "sicherung": {
                "aktiv": bool(self.backup_verzeichnis),
                "intervall_sekunden": self.backup_intervall,
                "behalten": self.backup_behalten,
            },
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
        # Weckt die Schleife vorzeitig, z. B. wenn eine Auf-Abruf-Stadt angefordert wurde.
        self._wecker = threading.Event()
        self._thread: threading.Thread | None = None
        self.staedte = staedte
        alle = {s.quell_id(q): (s, q) for s in staedte.values() for q in s.quellen}
        # Ohne ausdrückliche Liste: alle automatischen Quellen der dauerhaft abgerufenen Städte.
        auswahl = betrieb.sync_quellen or tuple(
            qid for qid, (s, q) in alle.items() if not s.auf_abruf and q.automatisch)
        unbekannte = [qid for qid in auswahl if qid not in alle]
        if unbekannte:
            raise ValueError("Unbekannte BEHOERDEN_SYNC_QUELLEN: " + ", ".join(unbekannte))
        self.quellen: dict[str, tuple[Stadt, Quelle]] = {qid: alle[qid] for qid in auswahl}
        if any(q.typ not in CRAWLER for _, q in self.quellen.values()):
            raise ValueError("Automatischer Abruf für einen nicht implementierten Quelltyp konfiguriert.")
        # Auf-Abruf-Städte: ihre Quellen kommen erst nach einer Anforderung in die Etappe.
        self.abruf_quellen: dict[str, dict[str, tuple[Stadt, Quelle]]] = {
            s.schluessel: {s.quell_id(q): (s, q) for q in s.quellen
                           if q.automatisch and q.typ in CRAWLER and s.quell_id(q) not in self.quellen}
            for s in staedte.values() if s.auf_abruf
        }

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
        log.info("Hintergrundabruf aktiv für %s", ", ".join(self.quellen))

    def stoppen(self) -> None:
        self._stop.set()
        self._wecker.set()

    def wecken(self) -> None:
        """Nächste Etappe vorziehen (z. B. nach einer Anforderung); ändert keine Drossel."""
        self._wecker.set()

    def geplante_quellen(self, sp: Speicher) -> dict[str, tuple[Stadt, Quelle]]:
        """Quellen dieser Etappe: dauerhafte plus die der aktuell angeforderten Städte."""
        geplant = dict(self.quellen)
        if self.betrieb.auf_abruf:
            for schluessel in aktive_staedte(sp, self.staedte, tage=self.betrieb.auf_abruf_tage,
                                             maximum=self.betrieb.auf_abruf_max, jetzt=self.clock()):
                geplant.update(self.abruf_quellen.get(schluessel, {}))
        return geplant

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
            try:
                self.sicherung()
            except Exception:
                log.exception("Automatische Sicherung fehlgeschlagen")
            self._wecker.wait(60)
            self._wecker.clear()
            if self._stop.is_set():
                return

    def sicherung(self) -> Path | None:
        """Fällige automatische Sicherung anlegen und alte Sicherungen ausdünnen.

        Nur aktiv mit `BEHOERDEN_BACKUP_VERZEICHNIS`. Der Zeitpunkt steht in SQLite, damit
        ein Neustart keine zusätzliche Sicherung erzwingt. Gelöscht werden ausschließlich
        Dateien nach dem eigenen Namensmuster – nie andere Dateien im Verzeichnis.
        """
        if not self.betrieb.backup_verzeichnis:
            return None
        jetzt = self.clock()
        with Speicher(self.db_pfad) as sp:
            letzte = sp.betrieb_wert("sicherung_zuletzt")
            if letzte and jetzt < _utc(letzte) + timedelta(seconds=self.betrieb.backup_intervall):
                return None
            verzeichnis = Path(self.betrieb.backup_verzeichnis).expanduser()
            ziel = verzeichnis / f"behoerdenmaster-{jetzt.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}.sqlite"
            try:
                sichere_datenbank(self.db_pfad, ziel)
            except (OSError, ValueError) as exc:
                sp.betrieb_setzen("sicherung_fehler", f"{jetzt.isoformat(timespec='seconds')}: {type(exc).__name__}")
                raise
            sp.betrieb_setzen("sicherung_zuletzt", jetzt.isoformat(timespec="seconds"))
            sp.betrieb_setzen("sicherung_fehler", "")
        vorhandene = sorted(verzeichnis.glob("behoerdenmaster-*Z.sqlite"))
        for alt in vorhandene[:-self.betrieb.backup_behalten]:
            alt.unlink(missing_ok=True)
        log.info("Automatische Sicherung angelegt; %d Sicherungen vorhanden",
                 min(len(vorhandene), self.betrieb.backup_behalten))
        return ziel

    def etappe(self) -> list[LaufBericht]:
        """Nur fällige Quellen, begrenzt und – bis `sync_parallel` – gleichzeitig.

        Parallele Quellen bedienen in der Regel verschiedene Hosts und dürfen
        gemeinsam laufen; für jeden Host sorgt eine geteilte Drossel weiterhin für
        den Mindestabstand zwischen Anfragen. Jede Quelle schreibt über eine eigene
        SQLite-Verbindung (WAL), die Rückgabe bleibt in Konfigurationsreihenfolge.
        Auch direkt offline testbar.
        """
        if not self.betrieb.sync_aktiv or self._stop.is_set():
            return []
        berichte: dict[str, LaufBericht] = {}
        reihenfolge: list[str] = []
        try:
            with crawl_sperre(self.db_pfad), Speicher(self.db_pfad) as sp:
                faellig = []
                for qid, (stadt, quelle) in self.geplante_quellen(sp).items():
                    if self._stop.is_set():
                        break
                    zeile = sp.db.execute("SELECT * FROM quelle WHERE id=?", (qid,)).fetchone()
                    fruehestens = naechster_abruf(zeile, self.betrieb.sync_intervall)
                    if fruehestens and self.clock() < fruehestens:
                        continue
                    faellig.append((qid, stadt, quelle))
                reihenfolge = [qid for qid, _, _ in faellig]
                if not faellig:
                    return []
                # Drosseln und Verbindungen vor dem Start nacheinander aufbauen: eine
                # Drossel je Host (geteilt), eine SQLite-Verbindung je Quelle (pro Thread).
                drosseln: dict[str, Drossel] = {}
                verbindungen: dict[str, Speicher] = {}
                for qid, _, quelle in faellig:
                    host = urlsplit(quelle.system_url).netloc.lower()
                    if host not in drosseln:
                        drosseln[host] = Drossel()
                    verbindungen[qid] = Speicher(self.db_pfad)
                try:
                    def job(qid: str, stadt: Stadt, quelle: Quelle) -> LaufBericht | None:
                        try:
                            return self._quelle_laden(verbindungen[qid], stadt, quelle,
                                                      drosseln[urlsplit(quelle.system_url).netloc.lower()])
                        finally:
                            verbindungen[qid].schliessen()

                    with ThreadPoolExecutor(max_workers=min(self.betrieb.sync_parallel, len(faellig))) as pool:
                        zukuenfte = {qid: pool.submit(job, qid, stadt, quelle)
                                     for qid, stadt, quelle in faellig}
                        # Ergebnisse in Konfigurationsreihenfolge einsammeln, nicht in
                        # Zufallsreihenfolge der Fertigstellung.
                        for qid in reihenfolge:
                            bericht = zukuenfte[qid].result()
                            if bericht is not None:
                                berichte[qid] = bericht
                finally:
                    for verbindung in verbindungen.values():
                        verbindung.schliessen()
        except CrawlLaeuft:
            log.info("Automatischer Abruf übersprungen: Ein anderer Crawl/Import läuft")
        return [berichte[qid] for qid in reihenfolge if qid in berichte]

    def _quelle_laden(self, sp: Speicher, stadt: Stadt, quelle: Quelle,
                      drossel: Drossel) -> LaufBericht | None:
        """Eine Quelle einer Etappe; Fehler bleiben auf diese Quelle begrenzt."""
        qid = stadt.quell_id(quelle)
        frist = time.monotonic() + self.betrieb.sync_zeitbudget
        abrufer = self.abrufer_factory(
            kontakt=self.betrieb.kontakt, min_intervall=quelle.min_intervall_sekunden,
            max_anfragen=self.betrieb.sync_budget, sleep=self._pause,
            soll_abbrechen=lambda frist=frist: self._stop.is_set() or time.monotonic() >= frist,
            drossel=drossel,
        )
        try:
            log.info("Automatischer Abruf %s, Budget %d Anfragen, Zeitfenster %d s",
                     qid, self.betrieb.sync_budget, self.betrieb.sync_zeitbudget)
            bericht = CRAWLER[quelle.typ](sp, stadt, quelle, abrufer, LaufOptionen(
                seit=self.betrieb.seit_datum(), max_anfragen=self.betrieb.sync_budget))
            log.info("Abruf %s: %s (%d Anfragen)", qid, bericht.status, bericht.anfragen)
            return bericht
        except CrawlVerweigert as exc:
            log.info("Abruf %s pausiert: %s", qid, exc)
        except Exception:
            # Quellen getrennt behandeln: ein Schema-/Netzfehler blockiert nicht die andere Quelle.
            log.exception("Unerwarteter Fehler beim Abruf %s", qid)
            sp.quelle_status(qid, "fehler", "Unerwarteter Abruffehler; siehe Service-Logs")
        finally:
            abrufer.schliessen()
        return None
