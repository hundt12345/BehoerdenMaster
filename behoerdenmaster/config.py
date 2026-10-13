"""Lädt Stadtkonfigurationen (TOML) aus config/cities.

Jede Stadt beschreibt ihre Quellen, Bezirke, Stadtteile, PLZ-Zuordnung und
Themenbegriffe. Eine neue Stadt entsteht durch eine neue TOML-Datei; Code
muss dafür nicht geändert werden (sofern ein bekannter Quelltyp genügt).
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .normalize import norm
from .presse import PresseVerweis

PROJEKTWURZEL = Path(__file__).resolve().parent.parent

# Erreichbare Projekt-Kontaktadresse für automatische Abrufe.
#
# Sie wird genau dann verwendet, wenn `BEHOERDEN_KONTAKT` nicht gesetzt ist, und landet
# im User-Agent jedes Abrufers („Kontakt: …“). Die Adresse steht deshalb bewusst
# öffentlich im Repository: Ein Deployment soll auch ohne eigene Umgebungswerte
# identifizierbar abrufen können, statt wegen einer fehlenden Einstellung dauerhaft
# leer zu bleiben. Eigene Instanzen sollten sie per `BEHOERDEN_KONTAKT` durch eine
# eigene, erreichbare Adresse ersetzen; ein leer gesetzter Wert verzichtet bewusst
# darauf und schaltet den automatischen Abruf ab.
PROJEKT_KONTAKT = "behordenkontakt123@posteo.de"


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
class Strasse:
    """Ein Straßenname aus dem Straßenverzeichnis der Stadt.

    ``stadtteile`` zählt, wie viele Straßenobjekte des Verzeichnisses diesen Namen in
    welchem Stadtteil tragen. Ein Name mit genau einem Stadtteil gilt als **eindeutig**
    und darf einen Ortsbezug erzeugen; bei mehreren Stadtteilen wird nichts geraten
    (die Kandidaten dienen nur dem Verständnis einer Suchanfrage).
    """

    name: str  # kanonisch normalisiert (siehe normalize.strassen_kanonisch)
    stadtteile: tuple[tuple[str, int], ...]
    plz: tuple[str, ...] = ()

    @property
    def stadtteil(self) -> str | None:
        """Stadtteilnummer, wenn der Name nur in einem Stadtteil vorkommt."""
        return self.stadtteile[0][0] if len(self.stadtteile) == 1 else None

    @property
    def kandidaten(self) -> tuple[str, ...]:
        return tuple(nr for nr, _ in self.stadtteile)

    @property
    def mehrdeutig(self) -> bool:
        return len(self.stadtteile) > 1


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
    organisation: str = ""  # zusätzliche Prüfung des Köln-Filters bei Bauleitplanung
    # Listenquelle mit Jahresarchiv (öffentliche Bekanntmachungen): `listen_vorlage`
    # enthält den Platzhalter `{jahr}`, `start_jahr` den ältesten zu prüfenden Jahrgang.
    listen_vorlage: str | None = None
    start_jahr: int = 0
    # Open-Data-Portal (DKAN 2): gefragte Datensätze – Katalogstichworte (Titel des
    # Datensatzes) oder eine direkte Datensatz-Kennung (UUID). Je Eintrag wird höchstens
    # ein Datensatz übernommen, der zur Angabe passt.
    datensaetze: tuple[str, ...] = ()


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
    # Straßenverzeichnis (optional, aus <stadt>.strassen.tsv neben der Konfiguration):
    # Name → Strasse, dazu Herkunft und Lizenz für die Anzeige.
    strassen: dict[str, Strasse] = field(default_factory=dict)
    strassen_quelle: str = ""
    strassen_lizenz: str = ""
    strassen_stand: str = ""
    strassen_max_gram: int = 1
    # Redaktionell gepflegte Zeitungsverweise (optional, aus `[[presse]]`): werden
    # passenden Vorgängen als bloße Verweise zugeordnet (siehe presse.py).
    presse: tuple[PresseVerweis, ...] = ()
    # Umkehrschlüssel für die Suche: Stadtteil → PLZ, in denen er vorkommt.
    plz_je_stadtteil: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def quell_id(self, quelle: Quelle) -> str:
        return f"{self.schluessel}:{quelle.schluessel}"

    def bezirk_fuer(self, stadtteil_nr: str) -> str | None:
        st = self.stadtteile.get(stadtteil_nr)
        return st.bezirk if st else None

    def thema(self, schluessel: str) -> Thema | None:
        return next((t for t in self.themen if t.schluessel == schluessel), None)

    def strasse(self, name: str) -> Strasse | None:
        """Straßeneintrag zu einem (rohen) Namen; ``None``, wenn unbekannt."""
        from .normalize import strassen_kanonisch

        return self.strassen.get(strassen_kanonisch(name))

    def plz_fuer_stadtteil(self, stadtteil_nr: str) -> tuple[str, ...]:
        """PLZ, in denen der Stadtteil vorkommt (Näherung aus der PLZ-Zuordnung)."""
        return self.plz_je_stadtteil.get(stadtteil_nr, ())

    def ortsgebiet(self, stadtteile: list[str] | tuple[str, ...] = (), bezirke: list[str] | tuple[str, ...] = (),
                   plz: str | None = None) -> dict[str, frozenset[str]]:
        """Eine Ortsangabe auf die drei Ebenen Stadtteil, Bezirk und PLZ ausdehnen.

        Warum das nötig ist: Ein Vorgang trägt seinen Ortsbezug je nach Quelle nur auf einer
        Ebene – die eine Vorlage nennt einen Stadtteil, die nächste nur eine PLZ, eine
        Bekanntmachung nur eine Straße. Die Suche darf daran nicht scheitern. Deshalb:

        * PLZ → ihre Stadtteile,
        * Bezirk → alle seine Stadtteile (und deren PLZ),
        * Stadtteil → die PLZ, in denen er liegt.

        Was bewusst **nicht** gilt:

        * Stadtteil → dessen Stadtbezirk. Ein Bezirk umfasst bis zu 16 Stadtteile; wer
          „Niehl“ sucht, meint nicht den ganzen Bezirk Nippes.
        * Stadtteil → die übrigen Stadtteile seiner PLZ. Wer Bickendorf sucht, meint nicht
          ganz 50827 samt Ossendorf.

        Die Ausdehnung wirkt also nur so großzügig, dass Vorgänge mit bloßem PLZ-Ortsbezug
        gefunden werden, und bleibt sonst eng.
        """
        st = {nr for nr in stadtteile if nr in self.stadtteile}
        bz = {nr for nr in bezirke if nr in self.bezirke}
        pz: set[str] = set()
        if plz:
            pz.add(plz)
            eintrag = self.plz.get(plz)
            if eintrag:
                st.update(eintrag.stadtteile)
        if bz:
            st.update(nr for nr, teil in self.stadtteile.items() if teil.bezirk in bz)
        for nr in st:
            pz.update(self.plz_fuer_stadtteil(nr))
        return {"stadtteile": frozenset(st), "bezirke": frozenset(bz), "plz": frozenset(pz)}


def _zaehler_feld(text: str) -> tuple[tuple[str, int], ...]:
    """``"403:60,406:44"`` → ``(("403", 60), ("406", 44))``."""
    eintraege = []
    for teil in text.split(","):
        teil = teil.strip()
        if not teil:
            continue
        nr, _, zahl = teil.partition(":")
        eintraege.append((nr.strip(), int(zahl) if zahl.strip().isdigit() else 1))
    return tuple(eintraege)


def _lade_strassen(pfad: Path, stadtteile: dict[str, Stadtteil]) -> tuple[
        dict[str, Strasse], dict[str, str]]:
    """Straßenverzeichnis neben der Stadtkonfiguration lesen.

    Fehlt die Datei, bleibt das Merkmal einfach aus (die Suche arbeitet dann ohne
    Straßenauflösung). Ist sie vorhanden, wird sie streng geprüft: Eine Zeile, die auf
    einen unbekannten Stadtteil verweist, ist ein Fehler und keine stille Lücke.
    """
    kopf: dict[str, str] = {}
    strassen: dict[str, Strasse] = {}
    for nummer, zeile in enumerate(pfad.read_text(encoding="utf-8").splitlines(), start=1):
        if not zeile.strip():
            continue
        if zeile.startswith("#"):
            for schluessel in ("Quelle", "Lizenz", "Stand"):
                if zeile.startswith(f"# {schluessel}:"):
                    kopf[schluessel] = zeile.split(":", 1)[1].strip()
            continue
        spalten = zeile.split("\t")
        if len(spalten) < 2:
            raise ValueError(f"{pfad.name}: Zeile {nummer} hat kein Tabellenformat")
        name = spalten[0].strip()
        stadtteile_des_namens = _zaehler_feld(spalten[1]) if len(spalten) > 1 else ()
        if not name or not stadtteile_des_namens:
            raise ValueError(f"{pfad.name}: Zeile {nummer} nennt keinen Stadtteil")
        for nr, _ in stadtteile_des_namens:
            if nr not in stadtteile:
                raise ValueError(f"{pfad.name}: Straße „{name}“ verweist auf unbekannten Stadtteil {nr}")
        plz = tuple(p for p in (spalten[2].split(",") if len(spalten) > 2 else []) if p.strip())
        strassen[name] = Strasse(name=name, stadtteile=stadtteile_des_namens, plz=plz)
    return strassen, kopf


def _lade_presse(eintraege: list, dateiname: str) -> tuple[PresseVerweis, ...]:
    """`[[presse]]` streng prüfen: Jeder Verweis braucht Medium, Titel, URL und ein Kriterium.

    Wie beim Straßenverzeichnis ist eine fehlerhafte Zeile ein Fehler und keine
    stille Lücke – sonst fehlt einem Vorgang unbemerkt sein Zeitungsverweis.
    """
    presse: list[PresseVerweis] = []
    for nr, eintrag in enumerate(eintraege, start=1):
        medium = str(eintrag.get("medium", "")).strip()
        titel = str(eintrag.get("titel", "")).strip()
        url = str(eintrag.get("url", "")).strip()
        if not medium or not titel or not url:
            raise ValueError(f"{dateiname}: Presse-Eintrag {nr} braucht medium, titel und url")
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"{dateiname}: Presse-Eintrag {nr} braucht eine http(s)-URL")
        datum = str(eintrag.get("datum", "")).strip()
        if datum:
            try:
                date.fromisoformat(datum)
            except ValueError as exc:
                raise ValueError(
                    f"{dateiname}: Presse-Eintrag {nr} hat kein Datum im Format YYYY-MM-DD"
                ) from exc
        stichworte = tuple(sorted({norm(s) for s in eintrag.get("stichworte", []) if norm(s)}))
        referenzen = tuple(sorted({norm(r) for r in eintrag.get("referenzen", []) if norm(r)}))
        if not stichworte and not referenzen:
            raise ValueError(
                f"{dateiname}: Presse-Eintrag {nr} braucht stichworte oder referenzen zur Zuordnung"
            )
        presse.append(PresseVerweis(medium=medium, titel=titel, url=url, datum=datum,
                                    stichworte=stichworte, referenzen=referenzen))
    return tuple(presse)


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
            organisation=q.get("organisation", ""),
            listen_vorlage=q.get("listen_vorlage"),
            start_jahr=int(q.get("start_jahr", 0) or 0),
            datensaetze=tuple(str(d).strip() for d in q.get("datensaetze", ()) if str(d).strip()),
        )
        for q in daten.get("quellen", [])
    )

    presse = _lade_presse(daten.get("presse", []), pfad.name)

    strassen: dict[str, Strasse] = {}
    strassen_kopf: dict[str, str] = {}
    strassen_pfad = pfad.parent / f"{pfad.stem}.strassen.tsv"
    if strassen_pfad.is_file():
        strassen, strassen_kopf = _lade_strassen(strassen_pfad, stadtteile)

    # Umkehrschlüssel PLZ ↔ Stadtteil: Beide Richtungen werden gebraucht. Die Vorwärtsrichtung
    # steht in der Konfiguration, die Rückrichtung wird hier einmal berechnet.
    plz_je_stadtteil: dict[str, list[str]] = {}
    for code, eintrag in plz.items():
        for stadtteil_nr in eintrag.stadtteile:
            plz_je_stadtteil.setdefault(stadtteil_nr, []).append(code)

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
        strassen=strassen,
        strassen_quelle=strassen_kopf.get("Quelle", ""),
        strassen_lizenz=strassen_kopf.get("Lizenz", ""),
        strassen_stand=strassen_kopf.get("Stand", ""),
        strassen_max_gram=max((len(name.split()) for name in strassen), default=1),
        presse=presse,
        plz_je_stadtteil={nr: tuple(sorted(codes)) for nr, codes in plz_je_stadtteil.items()},
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
