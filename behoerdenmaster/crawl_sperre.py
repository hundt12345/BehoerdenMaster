"""Prozessübergreifende Abrufsperre für dieselbe lokale SQLite-Datei.

Render-Webprozess und manuelle Shell-Crawls teilen sich diese Sperre. Ein zweiter
Crawler wartet nicht und stellt keine HTTP-Anfrage. Betriebssystem-Dateisperren
werden beim Prozessende automatisch freigegeben; eine übrig gebliebene Lock-Datei
ist daher keine verwaiste Sperre. Nicht als verteilter Lock für mehrere Disks gedacht.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path


class CrawlLaeuft(Exception):
    """Ein anderer Prozess ruft bereits Daten in dieselbe Datenbank ab."""


@contextmanager
def crawl_sperre(db_pfad: str | Path):
    if str(db_pfad) == ":memory:":
        raise ValueError("Die Abrufsperre benötigt eine SQLite-Datei, nicht :memory:")
    pfad = Path(db_pfad).expanduser().resolve()
    lock = pfad.with_name(pfad.name + ".crawl.lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+b") as datei:
        if os.name == "nt":
            import msvcrt

            if lock.stat().st_size == 0:
                datei.write(b"0")
                datei.flush()
            datei.seek(0)
            try:
                msvcrt.locking(datei.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise CrawlLaeuft("Ein anderer Crawl/Demo-Import läuft; bitte später erneut versuchen.") from exc
            try:
                yield
            finally:
                datei.seek(0)
                msvcrt.locking(datei.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            try:
                fcntl.flock(datei.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise CrawlLaeuft("Ein anderer Crawl/Demo-Import läuft; bitte später erneut versuchen.") from exc
            try:
                yield
            finally:
                fcntl.flock(datei.fileno(), fcntl.LOCK_UN)
