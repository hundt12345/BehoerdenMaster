"""Blueprint-Vertrag, CLI, echte Prozesssperren und konsistente WAL-Sicherungen."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest
import yaml

from behoerdenmaster.cli import baue_parser, main
from behoerdenmaster.config import PROJEKT_KONTAKT, PROJEKTWURZEL, lade_stadt
from behoerdenmaster.crawl_sperre import CrawlLaeuft, crawl_sperre
from behoerdenmaster.sicherung import sichere_datenbank
from behoerdenmaster.speicher import Speicher


LOCK_PROGRAMM = """
import sys
from behoerdenmaster.crawl_sperre import crawl_sperre, CrawlLaeuft
try:
    with crawl_sperre(sys.argv[1]):
        print('frei')
except CrawlLaeuft:
    print('gesperrt')
    sys.exit(3)
"""


def test_render_blueprint_passt_zum_sqlite_betrieb():
    daten = yaml.safe_load((PROJEKTWURZEL / "render.yaml").read_text())
    assert len(daten["services"]) == 1
    service = daten["services"][0]
    assert service["type"] == "web" and service["runtime"] == "python"
    assert service["region"] == "frankfurt" and service["plan"] == "free"
    assert service["numInstances"] == 1
    assert service["autoDeployTrigger"] == "off"  # String, nicht YAML-1.1-Boolean
    assert "branch" not in service  # folgt dem vom Nutzer ausgewählten Blueprint-Branch
    assert service["buildCommand"] == "python -m pip install -e ."
    assert service["startCommand"] == "python -m behoerdenmaster -v serve --host 0.0.0.0 --port $PORT"
    assert "preDeployCommand" not in service
    assert service["healthCheckPath"] == "/healthz" and service["maxShutdownDelaySeconds"] >= 60
    # Render Free: keine Disk; die SQLite-Datei liegt flüchtig in /tmp und wird nach jedem
    # Start vom Hintergrundabruf neu gefüllt. Deshalb ist das Erstabruf-Fenster begrenzt.
    assert "disk" not in service
    env = {e["key"]: e for e in service["envVars"]}
    assert Path(env["BEHOERDEN_DB"]["value"]).is_relative_to("/tmp")
    assert env["BEHOERDEN_SYNC_ENABLED"]["value"] == "true"
    # Die Ratsinformation steht bewusst am Ende: Die kleinen Quellen füllen den Bestand
    # zuerst, der große OParl-Erstabruf bekommt das restliche Zeitfenster der Etappe.
    assert env["BEHOERDEN_SYNC_QUELLEN"]["value"] == (
        "koeln:bekanntmachungen,koeln:beteiligung,koeln:opendata,koeln:ris-rss,koeln:presse-rss,koeln:ris")
    assert env["BEHOERDEN_SYNC_QUELLEN"]["value"].split(",")[-1] == "koeln:ris"
    assert env["BEHOERDEN_SYNC_ZEITBUDGET"]["value"].isdigit()
    # Ein rollierendes Fenster (Tage) statt eines festen Datums, das veralten würde.
    assert env["BEHOERDEN_SYNC_SEIT"]["value"].endswith("d") or env["BEHOERDEN_SYNC_SEIT"]["value"].isdigit()
    assert env["BEHOERDEN_DEMO"]["value"] == "false"
    assert env["WEB_CONCURRENCY"]["value"] == "1"
    # Der Kontakt steht bewusst im Blueprint (und im Code): Ein Deployment soll auch ohne
    # eigene Env-Werte abrufen statt wegen einer fehlenden Einstellung leer zu bleiben.
    assert env["BEHOERDEN_KONTAKT"]["value"] == PROJEKT_KONTAKT
    assert "sync" not in env["BEHOERDEN_KONTAKT"]
    assert env["BEHOERDEN_KONTAKT"]["value"].count("@") == 1
    assert all(isinstance(e["value"], str) for e in env.values() if "value" in e)
    assert (PROJEKTWURZEL / ".python-version").read_text().strip() == "3.13"


def test_cli_kontakt_faellt_auf_die_projektadresse_zurueck(capsys):
    from behoerdenmaster.cli import _kontakt

    args = baue_parser().parse_args(["probe", "--quelle", "ris"])
    assert _kontakt(args) == PROJEKT_KONTAKT
    assert "Projekt-Kontaktadresse" in capsys.readouterr().out


def test_cli_leer_gesetzter_kontakt_verzichtet_bewusst(monkeypatch):
    from behoerdenmaster.cli import _kontakt

    monkeypatch.setenv("BEHOERDEN_KONTAKT", "")
    args = baue_parser().parse_args(["crawl", "--quelle", "ris"])
    with pytest.raises(SystemExit, match="leer gesetzt"):
        _kontakt(args)


def test_port_env_und_explizite_port_option(monkeypatch):
    monkeypatch.delenv("PORT", raising=False)
    assert baue_parser().parse_args(["serve"]).port == 8000
    monkeypatch.setenv("PORT", "10000")
    assert baue_parser().parse_args(["serve"]).port == 10000
    assert baue_parser().parse_args(["serve", "--port", "8080"]).port == 8080
    monkeypatch.setenv("PORT", "keine-zahl")
    with pytest.raises(SystemExit):
        baue_parser().parse_args(["serve"])


def test_dateisperre_gilt_prozessuebergreifend_und_gibt_sich_selbst_frei(tmp_path):
    pfad = tmp_path / "disk" / "bestand.sqlite"

    def kind():
        return subprocess.run([sys.executable, "-c", LOCK_PROGRAMM, str(pfad)],
                              cwd=PROJEKTWURZEL, capture_output=True, text=True, timeout=10)

    with crawl_sperre(pfad):
        ergebnis = kind()
        assert ergebnis.returncode == 3 and ergebnis.stdout.strip() == "gesperrt", ergebnis.stderr
    assert pfad.with_name(pfad.name + ".crawl.lock").exists()  # kein Unlink beim Freigeben
    ergebnis = kind()
    assert ergebnis.returncode == 0 and ergebnis.stdout.strip() == "frei", ergebnis.stderr
    assert not pfad.exists()  # Lock allein erzeugt noch keine Datenbank


def test_lock_auch_bei_exception_frei_und_keine_memory_db(tmp_path):
    pfad = tmp_path / "bestand.sqlite"
    with pytest.raises(RuntimeError):
        with crawl_sperre(pfad):
            with pytest.raises(CrawlLaeuft):
                with crawl_sperre(pfad):
                    pytest.fail("Zweiter Lock darf nicht erworben werden")
            raise RuntimeError("Simulierter Abbruch")
    with crawl_sperre(pfad):
        pass
    with pytest.raises(ValueError, match=":memory:"):
        with crawl_sperre(":memory:"):
            pass


@pytest.mark.skipif(os.name == "nt", reason="Symlinks benötigen unter Windows zusätzliche Rechte")
def test_symlink_db_nutzt_dieselbe_sperre(tmp_path):
    pfad = tmp_path / "bestand.sqlite"
    pfad.touch()
    alias = tmp_path / "alias.sqlite"
    alias.symlink_to(pfad)
    with crawl_sperre(pfad):
        with pytest.raises(CrawlLaeuft):
            with crawl_sperre(alias):
                pass


@pytest.mark.parametrize("befehl", ["probe", "crawl", "demo"])
def test_cli_befehl_bei_laufendem_abruf_keine_http_anfrage(tmp_path, monkeypatch, capsys, befehl):
    pfad = tmp_path / "bestand.sqlite"

    def unerwartet(*args, **kwargs):
        pytest.fail("Bei aktiver Sperre darf kein HTTP-Client auf das Netz zugreifen")

    # Konstruktion von Clients ist beim Crawl erlaubt, echte HTTP-Aufrufe nicht.
    monkeypatch.setattr("httpx.Client.send", unerwartet)
    monkeypatch.setenv("BEHOERDEN_KONTAKT", "test@example.test")
    with crawl_sperre(pfad):
        assert main(["--db", str(pfad), befehl]) == 3
    ausgabe = capsys.readouterr()
    assert "läuft" in ausgabe.out + ausgabe.err
    assert not pfad.exists()


def test_backup_sichert_wal_und_ist_eigenstaendiger_snapshot(tmp_path):
    pfad = tmp_path / "laufend.sqlite"
    ziel = tmp_path / "backups" / "snapshot.sqlite"
    with Speicher(pfad) as sp:
        sp.betrieb_setzen("stand", "vorher")
        wal = pfad.with_name(pfad.name + "-wal")
        assert wal.exists() and wal.stat().st_size > 0
        assert sichere_datenbank(pfad, ziel) == ziel
        sp.betrieb_setzen("stand", "nachher")
        with closing(sqlite3.connect(ziel)) as backup:
            assert backup.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            assert backup.execute("SELECT wert FROM betrieb_meta WHERE schluessel='stand'").fetchone()[0] == "vorher"
        assert sp.betrieb_wert("stand") == "nachher"
    assert not list(ziel.parent.glob(".behoerdenmaster-backup-*"))


def test_backup_ueberschreibt_nie_dateien_oder_die_quelle(tmp_path):
    pfad = tmp_path / "bestand.sqlite"
    ziel = tmp_path / "schon-vorhanden.sqlite"
    ziel.write_bytes(b"unveraendert")
    with Speicher(pfad) as sp:
        sp.betrieb_setzen("wert", "erhalten")
        with pytest.raises(FileExistsError):
            sichere_datenbank(pfad, ziel)
        with pytest.raises(ValueError, match="laufende Datenbank"):
            sichere_datenbank(pfad, pfad)
        assert sp.betrieb_wert("wert") == "erhalten"
    assert ziel.read_bytes() == b"unveraendert"


def test_backup_legt_fehlende_quelle_nicht_an(tmp_path):
    with pytest.raises(ValueError, match="vorhandene SQLite"):
        sichere_datenbank(tmp_path / "fehlt.sqlite", tmp_path / "backups" / "ziel.sqlite")
    assert list(tmp_path.iterdir()) == []


def test_kaputte_db_veroeffentlicht_keine_unvollstaendige_sicherung(tmp_path):
    pfad = tmp_path / "kaputt.sqlite"
    pfad.write_bytes(b"keine SQLite-Datei")
    ziel = tmp_path / "ziel.sqlite"
    with pytest.raises(sqlite3.DatabaseError):
        sichere_datenbank(pfad, ziel)
    assert not ziel.exists() and not list(tmp_path.glob(".behoerdenmaster-backup-*"))


def test_cli_backup_erfolg_und_fehler_ohne_overwrite(tmp_path, capsys):
    pfad = tmp_path / "bestand.sqlite"
    ziel = tmp_path / "snapshot.sqlite"
    with Speicher(pfad) as sp:
        sp.betrieb_setzen("stand", "erhalten")
    assert main(["--db", str(pfad), "backup", str(ziel)]) == 0
    assert "Sicherung erstellt" in capsys.readouterr().out
    assert main(["--db", str(pfad), "backup", str(ziel)]) == 2
    assert "existiert bereits" in capsys.readouterr().err
    with Speicher(ziel) as sp:
        assert sp.betrieb_wert("stand") == "erhalten"


def test_blueprint_quellen_existieren_und_haben_einen_abrufer():
    """Ein Tippfehler im Blueprint darf nicht zu einem stummen Dienst führen.

    Der Sync-Dienst bricht bei unbekannten IDs bewusst mit einem Fehler ab. Dieser Test
    prüft dieselbe Zusage vorab: Jede im Blueprint aufgeführte Quelle existiert in der
    Stadtkonfiguration und hat dort einen implementierten Abrufer.
    """
    from behoerdenmaster.crawler import CRAWLER

    daten = yaml.safe_load((PROJEKTWURZEL / "render.yaml").read_text())
    env = {e["key"]: e["value"] for e in daten["services"][0]["envVars"]}
    stadt = lade_stadt("koeln")
    vorhanden = {stadt.quell_id(q): q for q in stadt.quellen}
    ids = env["BEHOERDEN_SYNC_QUELLEN"].split(",")
    assert ids and len(ids) >= 5
    for qid in ids:
        assert qid in vorhanden, f"Unbekannte Quelle im Blueprint: {qid}"
        assert vorhanden[qid].typ in CRAWLER, f"Kein Abrufer für {qid}"
