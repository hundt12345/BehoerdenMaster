"""Lädt Stadtkonfigurationen (TOML) aus config/cities.

Jede Stadt beschreibt ihre Quellen, Bezirke, Stadtteile, PLZ-Zuordnung und
Themenbegriffe. Eine neue Stadt entsteht durch eine neue TOML-Datei; Code
muss dafür nicht geändert werden (sofern ein bekannter Quelltyp genügt).
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .normalize import norm

PROJEKTWURZEL = Path(__file__).resolve().parent.parent


def config_verzeichnis() -> Path:
    return Path(os.environ.get("BEHOERDEN_CONFIG_DIR", PROJEKTWURZEL / "config" / "cities"))


@dataclass(frozen=True)
class Stadtteil:
    nr: str
    name: str
    bezirk: str
    aliase: tuple[str, ...] = ()


@dataclass(frozen=True)
class Thema:
    schluessel: str
    bezeichnung: str
    begriffe: tuple[str, ...]  # normalisiert


@dataclass(frozen=True)
class PlzEintrag:
    plz: str
    stadtteile: tuple[str, ...]
    quelle: str = ""


@dataclass(frozen=True)
class Quelle:
    schluessel: str
    typ: str
    name: str
    system_url: str
    body_id: str | None
    seit: str | None
    min_intervall_sekunden: float
    web_vorlage: str | None
    web_sitzung: str | None
    web_gremium: str | None
    lizenz: str


@dataclass(frozen=True)
class Stadt:
    schluessel: str
    name: str
    aliase: tuple[str, ...]
    bezirke: dict[str, str]
    stadtteile: dict[str, Stadtteil]
    plz: dict[str, PlzEintrag]
    themen: tuple[Thema, ...]
    quellen: tuple[Quelle, ...]
    hinweis: str = ""

    def quell_id(self, quelle: Quelle) -> str:
        return f"{self.schluessel}:{quelle.schluessel}"

    def bezirk_fuer(self, stadtteil_nr: str) -> str | None:
        st = self.stadtteile.get(stadtteil_nr)
        return st.bezirk if st else None

    def thema(self, schluessel: str) -> Thema | None:
        return next((t for t in self.themen if t.schluessel == schluessel), None)


def _lade_datei(pfad: Path) -> Stadt:
    with pfad.open("rb") as fh:
        daten = tomllib.load(fh)

    s = daten["stadt"]
    bezirke = {str(k): str(v) for k, v in daten.get("bezirke", {}).items()}

    stadtteile: dict[str, Stadtteil] = {}
    for nr, eintrag in daten.get("stadtteile", {}).items():
        if eintrag["bezirk"] not in bezirke:
            raise ValueError(f"{pfad.name}: Stadtteil {nr} verweist auf unbekannten Bezirk {eintrag['bezirk']}")
        stadtteile[str(nr)] = Stadtteil(
            nr=str(nr),
            name=eintrag["name"],
            bezirk=str(eintrag["bezirk"]),
            aliase=tuple(eintrag.get("aliase", [])),
        )

    plz: dict[str, PlzEintrag] = {}
    for code, eintrag in daten.get("plz", {}).items():
        for st in eintrag.get("stadtteile", []):
            if st not in stadtteile:
                raise ValueError(f"{pfad.name}: PLZ {code} verweist auf unbekannten Stadtteil {st}")
        plz[str(code)] = PlzEintrag(
            plz=str(code),
            stadtteile=tuple(str(x) for x in eintrag.get("stadtteile", [])),
            quelle=eintrag.get("quelle", ""),
        )

    themen = tuple(
        Thema(
            schluessel=t["schluessel"],
            bezeichnung=t["bezeichnung"],
            begriffe=tuple(sorted({norm(b) for b in t["begriffe"] if norm(b)})),
        )
        for t in daten.get("themen", [])
    )

    quellen = tuple(
        Quelle(
            schluessel=q["schluessel"],
            typ=q["typ"],
            name=q["name"],
            system_url=q["system_url"],
            body_id=q.get("body_id"),
            seit=q.get("seit"),
            min_intervall_sekunden=float(q.get("min_intervall_sekunden", 3.0)),
            web_vorlage=q.get("web_vorlage"),
            web_sitzung=q.get("web_sitzung"),
            web_gremium=q.get("web_gremium"),
            lizenz=q.get("lizenz", ""),
        )
        for q in daten.get("quellen", [])
    )

    return Stadt(
        schluessel=s["schluessel"],
        name=s["name"],
        aliase=tuple(s.get("aliase", [])),
        bezirke=bezirke,
        stadtteile=stadtteile,
        plz=plz,
        themen=themen,
        quellen=quellen,
        hinweis=s.get("hinweis", ""),
    )


def lade_staedte(verzeichnis: Path | None = None) -> dict[str, Stadt]:
    """Alle Städte aus dem Konfigurationsverzeichnis, nach Schlüssel."""
    verzeichnis = verzeichnis or config_verzeichnis()
    staedte = {}
    for pfad in sorted(verzeichnis.glob("*.toml")):
        stadt = _lade_datei(pfad)
        staedte[stadt.schluessel] = stadt
    if not staedte:
        raise FileNotFoundError(f"Keine Stadtkonfiguration in {verzeichnis} gefunden")
    return staedte


def lade_stadt(schluessel: str | None = None, verzeichnis: Path | None = None) -> Stadt:
    staedte = lade_staedte(verzeichnis)
    if schluessel is None:
        return next(iter(staedte.values()))
    if schluessel not in staedte:
        raise KeyError(f"Unbekannte Stadt '{schluessel}'. Verfügbar: {', '.join(staedte)}")
    return staedte[schluessel]
