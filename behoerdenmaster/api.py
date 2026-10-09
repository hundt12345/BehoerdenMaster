"""HTTP-API und Weboberfläche (FastAPI).

Alle Endpunkte lesen nur die lokale Datenbank. Die Stadt wird dabei nie abgerufen.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import __version__
from .auswertung import heute
from .config import PROJEKTWURZEL, Stadt, config_verzeichnis, lade_staedte
from .geo import Gazetteer
from .nlq import Verstaendnis, verstehe
from .speicher import Speicher
from .suche import Suchauftrag, suche, vorgang_detail

WEB_DATEI = Path(__file__).resolve().parent / "web" / "index.html"
STANDARD_DB = PROJEKTWURZEL / "data" / "behoerdenmaster.sqlite"


class FrageAnfrage(BaseModel):
    frage: str = Field(min_length=3, max_length=500)
    stadt: str | None = None
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


def _auftrag(v: Verstaendnis, stadt: Stadt, limit: int, offset: int) -> Suchauftrag:
    return Suchauftrag(
        stadt=stadt, orte=v.orte, plz=v.plz, themen=v.themen, freitext=v.freitext,
        von=v.von, bis=v.bis, offen=v.offen, limit=limit, offset=offset,
    )


def _stand(sp: Speicher) -> dict:
    return {
        "quellen": [
            {"id": r["id"], "name": r["name"], "demo": bool(r["demo"]), "status": r["status"],
             "letzter_erfolg": r["letzter_erfolg"]}
            for r in sp.quellen()
        ],
    }


def create_app(db_pfad: str | Path | None = None, config_dir: Path | None = None) -> FastAPI:
    db = str(db_pfad or os.environ.get("BEHOERDEN_DB") or STANDARD_DB)
    staedte = lade_staedte(config_dir or config_verzeichnis())
    gazetteers = {k: Gazetteer(s) for k, s in staedte.items()}

    app = FastAPI(title="BehoerdenMaster", version=__version__,
                  description="Kommunale Vorgänge durchsuchen: Fragen stellen oder nach PLZ suchen.")

    def stadt_waehlen(schluessel: str | None) -> Stadt:
        if schluessel is None:
            return next(iter(staedte.values()))
        if schluessel not in staedte:
            raise HTTPException(status_code=404, detail=f"Unbekannte Stadt: {schluessel}")
        return staedte[schluessel]

    @app.get("/", include_in_schema=False)
    def startseite():
        return FileResponse(WEB_DATEI)

    @app.get("/api/status")
    def status():
        with Speicher(db) as sp:
            daten = _stand(sp)
            daten["zaehler"] = sp.zaehle()
            daten["letzte_laeufe"] = [
                {"quelle_id": r["quelle_id"], "gestartet": r["gestartet"], "beendet": r["beendet"],
                 "status": r["status"], "anfragen": r["anfragen"], "fehlertext": r["fehlertext"]}
                for r in sp.letzte_laeufe(5)
            ]
        daten["version"] = __version__
        daten["staedte"] = [{"schluessel": s.schluessel, "name": s.name, "hinweis": s.hinweis}
                            for s in staedte.values()]
        return daten

    @app.post("/api/frage")
    def frage(anfrage: FrageAnfrage):
        stadt = stadt_waehlen(anfrage.stadt)
        v = verstehe(anfrage.frage, stadt, gazetteers[stadt.schluessel])
        with Speicher(db) as sp:
            ergebnis = suche(sp, _auftrag(v, stadt, anfrage.limit, anfrage.offset), heute())
        hinweise = list(v.hinweise)
        if ergebnis.get("freitext_oder"):
            hinweise.append("Keine Vorgänge mit allen Stichworten gefunden. Angezeigt werden Vorgänge mit mindestens einem Stichwort.")
        return {
            "verstanden": v.als_dict(),
            "hinweise": hinweise,
            "gesamt": ergebnis["gesamt"],
            "treffer": ergebnis["treffer"],
            "kandidaten_gekappt": ergebnis["kandidaten_gekappt"],
            "stadt": {"schluessel": stadt.schluessel, "name": stadt.name, "hinweis": stadt.hinweis},
        }

    @app.get("/api/plz/{plz}")
    def plz_suche(
        plz: str,
        thema: str | None = Query(default=None, description="Themen, kommagetrennt, z. B. bauen,verkehr"),
        stadt: str | None = None,
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ):
        if len(plz) != 5 or not plz.isdigit():
            raise HTTPException(status_code=422, detail="Eine PLZ besteht aus genau 5 Ziffern.")
        st = stadt_waehlen(stadt)
        themen = [t.strip() for t in (thema or "").split(",") if t.strip()]
        bekannte = {t.schluessel for t in st.themen}
        unbekannt = [t for t in themen if t not in bekannte]
        if unbekannt:
            raise HTTPException(status_code=422, detail=f"Unbekannte Themen: {', '.join(unbekannt)}")

        hinweise = []
        eintrag = st.plz.get(plz)
        if eintrag is None:
            hinweise.append(f"Die PLZ {plz} gehört nicht zum Gebiet von {st.name}. Es werden nur Vorgänge "
                            "mit ausdrücklicher PLZ-Nennung gefunden.")
        elif not eintrag.stadtteile:
            hinweise.append(f"Für die PLZ {plz} ist noch keine Stadtteil-Zuordnung hinterlegt. Es werden nur "
                            "Vorgänge mit ausdrücklicher PLZ-Nennung gefunden.")
        else:
            namen = ", ".join(st.stadtteile[s].name for s in eintrag.stadtteile)
            hinweis = f"Die PLZ {plz} wird den Stadtteilen {namen} zugeordnet."
            if eintrag.quelle:
                hinweis += f" Quelle der Zuordnung: {eintrag.quelle}. Die Zuordnung ist eine Näherung."
            hinweise.append(hinweis)

        auftrag = Suchauftrag(stadt=st, plz=plz, themen=themen, limit=limit, offset=offset)
        with Speicher(db) as sp:
            ergebnis = suche(sp, auftrag, heute())
        return {
            "plz": plz,
            "hinweise": hinweise,
            "gesamt": ergebnis["gesamt"],
            "treffer": ergebnis["treffer"],
            "kandidaten_gekappt": ergebnis["kandidaten_gekappt"],
            "stadt": {"schluessel": st.schluessel, "name": st.name, "hinweis": st.hinweis},
        }

    @app.get("/api/vorgang")
    def vorgang(id: str = Query(..., min_length=5, description="OParl-ID des Vorgangs")):
        st = stadt_waehlen(None)
        with Speicher(db) as sp:
            detail = vorgang_detail(sp, st, id, heute())
        if detail is None:
            raise HTTPException(status_code=404, detail="Vorgang nicht gefunden")
        return detail

    return app
