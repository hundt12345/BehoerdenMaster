"""SQLite-Speicher: Schema, Schreibvorgänge des Crawlers und Lesezugriffe.

Alle Schreibvorgänge sind Upserts. Kinder (Beratungsfolge, Dokumente, TOPs,
Ortsbezug) werden beim Speichern des Elternobjekts vollständig ersetzt. Das
hält die Daten konsistent, auch wenn die Quelle Objekte nachträglich ändert.
Gelöschte Objekte werden weich markiert (geloescht = 1), damit Verweise nicht
ins Leere laufen.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS quelle (
    id TEXT PRIMARY KEY,
    stadt TEXT NOT NULL,
    schluessel TEXT NOT NULL,
    typ TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    basis_url TEXT NOT NULL DEFAULT '',
    demo INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'nie',
    letzter_erfolg TEXT NOT NULL DEFAULT '',
    letzter_versuch TEXT NOT NULL DEFAULT '',
    fehlertext TEXT NOT NULL DEFAULT '',
    abkuehlung_bis TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS crawl_stand (
    quelle_id TEXT NOT NULL,
    entitaet TEXT NOT NULL,
    modified_seit TEXT NOT NULL DEFAULT '',
    naechste_url TEXT NOT NULL DEFAULT '',
    reihe_beginn TEXT NOT NULL DEFAULT '',
    aktualisiert TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (quelle_id, entitaet)
);
CREATE TABLE IF NOT EXISTS crawl_lauf (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quelle_id TEXT NOT NULL,
    gestartet TEXT NOT NULL,
    beendet TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'laeuft',
    anfragen INTEGER NOT NULL DEFAULT 0,
    statistik TEXT NOT NULL DEFAULT '{}',
    fehlertext TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS gremium (
    id TEXT PRIMARY KEY,
    quelle_id TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    kurzname TEXT NOT NULL DEFAULT '',
    typ TEXT NOT NULL DEFAULT '',
    beginn TEXT NOT NULL DEFAULT '',
    ende TEXT NOT NULL DEFAULT '',
    web TEXT NOT NULL DEFAULT '',
    geaendert TEXT NOT NULL DEFAULT '',
    geloescht INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sitzung (
    id TEXT PRIMARY KEY,
    quelle_id TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    beginn TEXT NOT NULL DEFAULT '',
    ende TEXT NOT NULL DEFAULT '',
    datum TEXT NOT NULL DEFAULT '',
    ort TEXT NOT NULL DEFAULT '',
    abgesagt INTEGER NOT NULL DEFAULT 0,
    gremium_ids TEXT NOT NULL DEFAULT '[]',
    web TEXT NOT NULL DEFAULT '',
    geaendert TEXT NOT NULL DEFAULT '',
    geloescht INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS tagesordnungspunkt (
    id TEXT PRIMARY KEY,
    sitzung_id TEXT NOT NULL,
    nummer TEXT NOT NULL DEFAULT '',
    reihenfolge INTEGER NOT NULL DEFAULT 0,
    name TEXT NOT NULL DEFAULT '',
    ergebnis TEXT NOT NULL DEFAULT '',
    beratung_id TEXT NOT NULL DEFAULT '',
    oeffentlich INTEGER NOT NULL DEFAULT 1,
    geaendert TEXT NOT NULL DEFAULT '',
    geloescht INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS vorgang (
    id TEXT PRIMARY KEY,
    quelle_id TEXT NOT NULL,
    referenz TEXT NOT NULL DEFAULT '',
    titel TEXT NOT NULL DEFAULT '',
    art TEXT NOT NULL DEFAULT '',
    datum TEXT NOT NULL DEFAULT '',
    geaendert TEXT NOT NULL DEFAULT '',
    web TEXT NOT NULL DEFAULT '',
    suchtext TEXT NOT NULL DEFAULT '',
    geloescht INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS beratung (
    id TEXT PRIMARY KEY,
    vorgang_id TEXT NOT NULL,
    sitzung_id TEXT NOT NULL DEFAULT '',
    top_id TEXT NOT NULL DEFAULT '',
    gremium_ids TEXT NOT NULL DEFAULT '[]',
    rolle TEXT NOT NULL DEFAULT '',
    federfuehrend INTEGER NOT NULL DEFAULT 0,
    geaendert TEXT NOT NULL DEFAULT '',
    geloescht INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS dokument (
    id TEXT NOT NULL,
    vorgang_id TEXT NOT NULL DEFAULT '',
    sitzung_id TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL DEFAULT '',
    dateiname TEXT NOT NULL DEFAULT '',
    mime TEXT NOT NULL DEFAULT '',
    datum TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    download_url TEXT NOT NULL DEFAULT '',
    rolle TEXT NOT NULL DEFAULT 'dokument',
    geaendert TEXT NOT NULL DEFAULT '',
    geloescht INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (id, vorgang_id, sitzung_id)
);
CREATE TABLE IF NOT EXISTS vorgang_beziehung (
    vorgang_id TEXT NOT NULL,
    bezogen_id TEXT NOT NULL,
    art TEXT NOT NULL,
    PRIMARY KEY (vorgang_id, bezogen_id, art)
);
CREATE TABLE IF NOT EXISTS ortsbezug (
    vorgang_id TEXT NOT NULL,
    typ TEXT NOT NULL,
    schluessel TEXT NOT NULL,
    quelle TEXT NOT NULL,
    PRIMARY KEY (vorgang_id, typ, schluessel, quelle)
);
-- Ergänzungen für öffentliche Bauleitverfahren; bestehende SQLite-Dateien bleiben nutzbar.
CREATE TABLE IF NOT EXISTS planverfahren (
    vorgang_id TEXT PRIMARY KEY,
    phase TEXT NOT NULL DEFAULT '',
    beginn TEXT NOT NULL DEFAULT '',
    ende TEXT NOT NULL DEFAULT '',
    beschreibung TEXT NOT NULL DEFAULT '',
    organisation TEXT NOT NULL DEFAULT '',
    berechtigung TEXT NOT NULL DEFAULT '',
    gesehen TEXT NOT NULL,
    liste_reihe TEXT NOT NULL,
    gelistet INTEGER NOT NULL DEFAULT 1,
    detail_status TEXT NOT NULL DEFAULT 'offen'
);
CREATE TABLE IF NOT EXISTS planstand (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vorgang_id TEXT NOT NULL,
    festgestellt TEXT NOT NULL,
    inhalt_hash TEXT NOT NULL,
    inhalt TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS crawl_aufgabe (
    quelle_id TEXT NOT NULL,
    vorgang_id TEXT NOT NULL,
    url TEXT NOT NULL,
    PRIMARY KEY (quelle_id, vorgang_id)
);
CREATE INDEX IF NOT EXISTS ix_planstand_vorgang ON planstand (vorgang_id, id);
CREATE INDEX IF NOT EXISTS ix_ortsbezug ON ortsbezug (typ, schluessel);
CREATE INDEX IF NOT EXISTS ix_vorgang_datum ON vorgang (datum);
CREATE INDEX IF NOT EXISTS ix_beratung_vorgang ON beratung (vorgang_id);
CREATE INDEX IF NOT EXISTS ix_top_sitzung ON tagesordnungspunkt (sitzung_id);
CREATE INDEX IF NOT EXISTS ix_top_beratung ON tagesordnungspunkt (beratung_id);
CREATE INDEX IF NOT EXISTS ix_sitzung_datum ON sitzung (datum);
CREATE INDEX IF NOT EXISTS ix_dokument_sitzung ON dokument (sitzung_id);
"""


def jetzt_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Speicher:
    """Dünne Schicht über einer SQLite-Datei. Pro Thread/Request eine Instanz verwenden."""

    def __init__(self, pfad: str | Path):
        self.pfad = str(pfad)
        if self.pfad != ":memory:":
            Path(self.pfad).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.pfad, timeout=30, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self._transaktions_nr = 0
        self.db.execute("PRAGMA foreign_keys = ON")
        if self.pfad != ":memory:":
            self.db.execute("PRAGMA journal_mode = WAL")
        self.db.executescript(SCHEMA)
        # CREATE TABLE IF NOT EXISTS ergänzt keine Spalten alter SQLite-Dateien.
        if "abkuehlung_bis" not in {z["name"] for z in self.db.execute("PRAGMA table_info(quelle)")}:
            # Mehrere API-Requests können dieselbe alte Datei gleichzeitig öffnen.
            self.db.execute("BEGIN IMMEDIATE")
            try:
                if "abkuehlung_bis" not in {z["name"] for z in self.db.execute("PRAGMA table_info(quelle)")}:
                    self.db.execute("ALTER TABLE quelle ADD COLUMN abkuehlung_bis TEXT NOT NULL DEFAULT ''")
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise

    def schliessen(self) -> None:
        self.db.close()

    def __enter__(self) -> "Speicher":
        return self

    def __exit__(self, *exc) -> None:
        self.schliessen()

    # --- generische Helfer -------------------------------------------------

    @contextmanager
    def transaktion(self):
        """Verschachtelbare SQLite-Savepoints statt vorzeitig commitender Connection-Kontexte.

        Ein äußerer Aufruf bündelt eine Listen-Seite mit Aufgaben und Cursor atomar.
        Einzelne Speicher-Aufrufe funktionieren weiter als eigene Transaktion.
        Auch KeyboardInterrupt rollt nur die unvollständige Einheit zurück.
        """
        self._transaktions_nr += 1
        name = f"bm_{self._transaktions_nr}"
        self.db.execute(f"SAVEPOINT {name}")
        try:
            yield
        except BaseException:
            self.db.execute(f"ROLLBACK TO SAVEPOINT {name}")
            self.db.execute(f"RELEASE SAVEPOINT {name}")
            raise
        else:
            self.db.execute(f"RELEASE SAVEPOINT {name}")

    def _upsert(self, tabelle: str, zeile: dict, pk: tuple[str, ...] = ("id",)) -> None:
        spalten = list(zeile)
        platz = ",".join("?" for _ in spalten)
        setz = ",".join(f"{c}=excluded.{c}" for c in spalten if c not in pk)
        sql = (
            f"INSERT INTO {tabelle} ({','.join(spalten)}) VALUES ({platz}) "
            f"ON CONFLICT({','.join(pk)}) DO UPDATE SET {setz}"
        )
        self.db.execute(sql, [zeile[c] for c in spalten])

    # --- Quellen und Läufe -------------------------------------------------

    def quelle_anlegen(self, quelle_id: str, stadt: str, schluessel: str, typ: str,
                       name: str, basis_url: str, demo: bool = False) -> None:
        with self.transaktion():
            self._upsert("quelle", {
                "id": quelle_id, "stadt": stadt, "schluessel": schluessel, "typ": typ,
                "name": name, "basis_url": basis_url, "demo": int(demo),
            })

    def quelle_status(self, quelle_id: str, status: str, fehlertext: str = "", erfolg: bool = False,
                      abkuehlung_bis: str = "") -> None:
        jetzt = jetzt_iso()
        with self.transaktion():
            if erfolg:
                self.db.execute(
                    "UPDATE quelle SET status=?, fehlertext=?, letzter_versuch=?, letzter_erfolg=?, abkuehlung_bis=? WHERE id=?",
                    (status, fehlertext, jetzt, jetzt, abkuehlung_bis, quelle_id),
                )
            else:
                self.db.execute(
                    "UPDATE quelle SET status=?, fehlertext=?, letzter_versuch=?, abkuehlung_bis=? WHERE id=?",
                    (status, fehlertext, jetzt, abkuehlung_bis, quelle_id),
                )

    def quellen(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM quelle ORDER BY demo, id").fetchall()

    def lauf_beginnen(self, quelle_id: str) -> int:
        with self.transaktion():
            cur = self.db.execute(
                "INSERT INTO crawl_lauf (quelle_id, gestartet) VALUES (?, ?)", (quelle_id, jetzt_iso())
            )
        return int(cur.lastrowid)

    def lauf_beenden(self, lauf_id: int, status: str, anfragen: int,
                     statistik: dict | None = None, fehlertext: str = "") -> None:
        with self.transaktion():
            self.db.execute(
                "UPDATE crawl_lauf SET beendet=?, status=?, anfragen=?, statistik=?, fehlertext=? WHERE id=?",
                (jetzt_iso(), status, anfragen, json.dumps(statistik or {}, ensure_ascii=False), fehlertext, lauf_id),
            )

    def letzte_laeufe(self, limit: int = 5) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM crawl_lauf ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

    def fehlschlaege_in_folge(self, quelle_id: str) -> int:
        """Anzahl aufeinanderfolgender Läufe, die gesperrt waren oder die Quelle nicht erreichten."""
        zeilen = self.db.execute(
            "SELECT status FROM crawl_lauf WHERE quelle_id = ? ORDER BY id DESC LIMIT 20", (quelle_id,)
        ).fetchall()
        n = 0
        for z in zeilen:
            if z["status"] not in ("gesperrt", "nicht erreichbar"):
                break
            n += 1
        return n

    def letzter_fehlschlag(self, quelle_id: str) -> str:
        """Zeitpunkt des letzten Abbruchs wegen Sperre/Nichterreichbarkeit, sonst ''."""
        zeile = self.db.execute(
            "SELECT letzter_versuch, status FROM quelle WHERE id=?", (quelle_id,)
        ).fetchone()
        if zeile and zeile["status"] in ("gesperrt", "nicht erreichbar") and zeile["letzter_versuch"]:
            return zeile["letzter_versuch"]
        return ""

    # --- Crawl-Stand ---------------------------------------------------------

    def stand_lesen(self, quelle_id: str, entitaet: str) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT * FROM crawl_stand WHERE quelle_id=? AND entitaet=?", (quelle_id, entitaet)
        ).fetchone()

    def stand_schreiben(self, quelle_id: str, entitaet: str, modified_seit: str,
                        naechste_url: str, reihe_beginn: str = "") -> None:
        """Speichert den Fortschritt einer Entität.

        modified_seit: Grenze für ``modified_since`` des nächsten vollständigen Laufs.
        naechste_url:  Fortsetzungspunkt innerhalb einer unvollständigen Abrufreihe.
        reihe_beginn:  Startzeitpunkt der offenen Abrufreihe (leer, wenn keine offen ist).
        """
        with self.transaktion():
            self._upsert("crawl_stand", {
                "quelle_id": quelle_id, "entitaet": entitaet, "modified_seit": modified_seit,
                "naechste_url": naechste_url, "reihe_beginn": reihe_beginn, "aktualisiert": jetzt_iso(),
            }, pk=("quelle_id", "entitaet"))

    # --- Objekte schreiben ---------------------------------------------------

    def gremium_speichern(self, zeile: dict) -> None:
        with self.transaktion():
            self._upsert("gremium", zeile)

    def gremium_namen(self, ids: Iterable[str]) -> dict[str, str]:
        ids = list(dict.fromkeys(i for i in ids if i))
        if not ids:
            return {}
        platz = ",".join("?" for _ in ids)
        zeilen = self.db.execute(f"SELECT id, name FROM gremium WHERE id IN ({platz})", ids).fetchall()
        return {z["id"]: z["name"] for z in zeilen}

    def sitzung_speichern(self, zeile: dict, tops: list[dict], dokumente: list[dict]) -> None:
        with self.transaktion():
            self._upsert("sitzung", zeile)
            self.db.execute("DELETE FROM tagesordnungspunkt WHERE sitzung_id=?", (zeile["id"],))
            for top in tops:
                self._upsert("tagesordnungspunkt", top)
            self.db.execute("DELETE FROM dokument WHERE sitzung_id=? AND vorgang_id=''", (zeile["id"],))
            for dok in dokumente:
                self._upsert("dokument", {**dok, "vorgang_id": "", "sitzung_id": zeile["id"]},
                             pk=("id", "vorgang_id", "sitzung_id"))

    def vorgang_speichern(self, zeile: dict, beratungen: list[dict], dokumente: list[dict],
                          beziehungen: list[tuple[str, str]], orte: set[tuple[str, str, str]]) -> None:
        vid = zeile["id"]
        with self.transaktion():
            self._upsert("vorgang", zeile)
            self.db.execute("DELETE FROM beratung WHERE vorgang_id=?", (vid,))
            for b in beratungen:
                self._upsert("beratung", b)
            self.db.execute("DELETE FROM dokument WHERE vorgang_id=? AND sitzung_id=''", (vid,))
            for dok in dokumente:
                self._upsert("dokument", {**dok, "vorgang_id": vid, "sitzung_id": ""},
                             pk=("id", "vorgang_id", "sitzung_id"))
            self.db.execute("DELETE FROM vorgang_beziehung WHERE vorgang_id=?", (vid,))
            for ziel, art in beziehungen:
                self.db.execute("INSERT OR IGNORE INTO vorgang_beziehung VALUES (?,?,?)", (vid, ziel, art))
            self.db.execute("DELETE FROM ortsbezug WHERE vorgang_id=?", (vid,))
            for typ, schluessel, quelle in orte:
                self.db.execute("INSERT OR IGNORE INTO ortsbezug VALUES (?,?,?,?)", (vid, typ, schluessel, quelle))

    # --- Bauleitverfahren und beobachtete Änderungen -----------------------------

    def planverfahren_speichern(self, zeile: dict, stand: dict,
                               orte: set[tuple[str, str, str]], reihe: str,
                               festgestellt: str | None = None) -> bool:
        """Speichert den öffentlichen Stand, ohne Dokumente vor dem Detailabruf zu löschen.

        Rückgabe: Inhalt geändert? Ein unveränderter Abruf erzeugt KEINEN Verlaufsschritt.
        Nicht mehr gelistete Verfahren bleiben im Bestand (kein erfundener Abschluss).
        """
        import hashlib

        vid = zeile["id"]
        festgestellt = festgestellt or jetzt_iso()
        inhalt = json.dumps({"titel": zeile["titel"], **stand}, ensure_ascii=False, sort_keys=True)
        fingerprint = hashlib.sha256(inhalt.encode("utf-8")).hexdigest()
        with self.transaktion():
            # Ein echter Abruf ersetzt ggf. eine Demo, aber übernimmt keine Demo-Historie.
            bisher = self.db.execute(
                "SELECT v.quelle_id, q.demo FROM vorgang v JOIN quelle q ON q.id=v.quelle_id WHERE v.id=?",
                (vid,),
            ).fetchone()
            if bisher and bisher["demo"] and bisher["quelle_id"] != zeile["quelle_id"]:
                self.db.execute("DELETE FROM planstand WHERE vorgang_id=?", (vid,))
                self.db.execute("DELETE FROM crawl_aufgabe WHERE vorgang_id=?", (vid,))
                self.db.execute("DELETE FROM dokument WHERE vorgang_id=?", (vid,))
                self.db.execute("UPDATE planverfahren SET detail_status='offen' WHERE vorgang_id=?", (vid,))
            letzter = self.db.execute(
                "SELECT inhalt_hash FROM planstand WHERE vorgang_id=? ORDER BY id DESC LIMIT 1", (vid,)
            ).fetchone()
            geaendert = not letzter or letzter["inhalt_hash"] != fingerprint
            self._upsert("vorgang", zeile)
            self._upsert("planverfahren", {
                "vorgang_id": vid, **stand, "gesehen": festgestellt,
                "liste_reihe": reihe, "gelistet": 1, "detail_status": "offen",
            }, pk=("vorgang_id",))
            if geaendert:
                self.db.execute(
                    "INSERT INTO planstand (vorgang_id, festgestellt, inhalt_hash, inhalt) VALUES (?,?,?,?)",
                    (vid, festgestellt, fingerprint, inhalt),
                )
            self.db.execute("DELETE FROM ortsbezug WHERE vorgang_id=?", (vid,))
            self.db.executemany("INSERT INTO ortsbezug VALUES (?,?,?,?)",
                                [(vid, typ, nr, herkunft) for typ, nr, herkunft in orte])
        return geaendert

    def plan_liste_abgeschlossen(self, quelle_id: str, reihe: str) -> None:
        """Erst nach vollständiger Liste die Sichtbarkeit aktualisieren, niemals löschen."""
        with self.transaktion():
            self.db.execute(
                "UPDATE planverfahren SET gelistet=0 WHERE liste_reihe<>? AND vorgang_id IN "
                "(SELECT id FROM vorgang WHERE quelle_id=?)", (reihe, quelle_id),
            )

    def plan_dokumente_speichern(self, vorgang_id: str, dokumente: list[dict], status: str) -> None:
        with self.transaktion():
            if status == "ok":
                self.db.execute("DELETE FROM dokument WHERE vorgang_id=?", (vorgang_id,))
                for dok in dokumente:
                    self._upsert("dokument", {**dok, "vorgang_id": vorgang_id, "sitzung_id": ""},
                                 pk=("id", "vorgang_id", "sitzung_id"))
            # Login/404/fehlende Detailansicht dürfen vorhandene Dokumentlinks nicht entfernen.
            self.db.execute("UPDATE planverfahren SET detail_status=? WHERE vorgang_id=?", (status, vorgang_id))

    def aufgabe_anlegen(self, quelle_id: str, vorgang_id: str, url: str) -> None:
        with self.transaktion():
            self._upsert("crawl_aufgabe", {"quelle_id": quelle_id, "vorgang_id": vorgang_id, "url": url},
                         pk=("quelle_id", "vorgang_id"))

    def aufgaben(self, quelle_id: str) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM crawl_aufgabe WHERE quelle_id=? ORDER BY vorgang_id", (quelle_id,)
        ).fetchall()

    def aufgabe_erledigt(self, quelle_id: str, vorgang_id: str) -> None:
        with self.transaktion():
            self.db.execute("DELETE FROM crawl_aufgabe WHERE quelle_id=? AND vorgang_id=?", (quelle_id, vorgang_id))

    def markiere_geloescht(self, typ: str, objekt_id: str) -> None:
        """Weiches Löschen (OParl: deleted=true). Kinder werden mit markiert."""
        with self.transaktion():
            if typ == "gremium":
                self.db.execute("UPDATE gremium SET geloescht=1 WHERE id=?", (objekt_id,))
            elif typ == "sitzung":
                self.db.execute("UPDATE sitzung SET geloescht=1 WHERE id=?", (objekt_id,))
                self.db.execute("UPDATE tagesordnungspunkt SET geloescht=1 WHERE sitzung_id=?", (objekt_id,))
                self.db.execute("UPDATE dokument SET geloescht=1 WHERE sitzung_id=?", (objekt_id,))
            elif typ == "vorgang":
                self.db.execute("UPDATE vorgang SET geloescht=1 WHERE id=?", (objekt_id,))
                self.db.execute("UPDATE beratung SET geloescht=1 WHERE vorgang_id=?", (objekt_id,))
                self.db.execute("UPDATE dokument SET geloescht=1 WHERE vorgang_id=?", (objekt_id,))
                self.db.execute("DELETE FROM ortsbezug WHERE vorgang_id=?", (objekt_id,))
            else:
                raise ValueError(f"Unbekannter Objekttyp: {typ}")

    def zaehle(self) -> dict[str, int]:
        ergebnis = {}
        for tabelle in ("gremium", "sitzung", "tagesordnungspunkt", "vorgang", "beratung", "dokument"):
            ergebnis[tabelle] = self.db.execute(
                f"SELECT COUNT(*) FROM {tabelle} WHERE geloescht=0"
            ).fetchone()[0]
        return ergebnis

    def loesche_quelle_daten(self, quelle_id: str) -> None:
        """Entfernt alle Daten einer Quelle (z. B. Demo-Daten vor einem echten Abruf)."""
        with self.transaktion():
            vorgaenge = [r[0] for r in self.db.execute("SELECT id FROM vorgang WHERE quelle_id=?", (quelle_id,))]
            sitzungen = [r[0] for r in self.db.execute("SELECT id FROM sitzung WHERE quelle_id=?", (quelle_id,))]
            self.db.execute("DELETE FROM crawl_aufgabe WHERE quelle_id=?", (quelle_id,))
            for vid in vorgaenge:
                for tab in ("planverfahren", "planstand"):
                    self.db.execute(f"DELETE FROM {tab} WHERE vorgang_id=?", (vid,))
                for tab in ("beratung", "dokument", "vorgang_beziehung", "ortsbezug"):
                    col = "vorgang_id"
                    self.db.execute(f"DELETE FROM {tab} WHERE {col}=?", (vid,))
                self.db.execute("DELETE FROM vorgang_beziehung WHERE bezogen_id=?", (vid,))
            for sid in sitzungen:
                self.db.execute("DELETE FROM tagesordnungspunkt WHERE sitzung_id=?", (sid,))
                self.db.execute("DELETE FROM dokument WHERE sitzung_id=?", (sid,))
            self.db.execute("DELETE FROM vorgang WHERE quelle_id=?", (quelle_id,))
            self.db.execute("DELETE FROM sitzung WHERE quelle_id=?", (quelle_id,))
            self.db.execute("DELETE FROM gremium WHERE quelle_id=?", (quelle_id,))
            self.db.execute("DELETE FROM crawl_stand WHERE quelle_id=?", (quelle_id,))
