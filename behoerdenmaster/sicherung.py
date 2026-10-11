"""Konsistente SQLite-Sicherung, auch mit aktivem WAL und laufenden Lesern/Schreibern.

Nicht einfach die Hauptdatei kopieren: aktuelle Seiten können noch im WAL liegen.
Die SQLite-Backup-API erzeugt einen eigenständigen Snapshot. Erst nach Prüfung
wird er unter dem Zielnamen veröffentlicht; vorhandene Dateien bleiben unangetastet.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path


def sichere_datenbank(db_pfad: str | Path, ziel: str | Path) -> Path:
    quelle = Path(db_pfad).expanduser().resolve()
    ziel = Path(ziel).expanduser().resolve()
    if not quelle.is_file():
        raise ValueError("Keine vorhandene SQLite-Datenbank zum Sichern gefunden.")
    if quelle == ziel:
        raise ValueError("Sicherungsziel darf nicht die laufende Datenbank sein.")
    if ziel.exists():
        raise FileExistsError("Sicherungsziel existiert bereits; bitte einen neuen Namen wählen.")
    ziel.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".behoerdenmaster-backup-", suffix=".tmp", dir=ziel.parent)
    os.close(fd)
    temp = Path(temp_name)
    try:
        with closing(sqlite3.connect(quelle.as_uri() + "?mode=ro", uri=True, timeout=30)) as src:
            with closing(sqlite3.connect(temp)) as dst:
                src.backup(dst)
                if dst.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ValueError("SQLite-Prüfung der Sicherung fehlgeschlagen.")
        # Atomar und ohne Overwrite, auch wenn ein zweiter Backup-Aufruf dasselbe Ziel nutzt.
        os.link(temp, ziel)
        return ziel
    finally:
        temp.unlink(missing_ok=True)
