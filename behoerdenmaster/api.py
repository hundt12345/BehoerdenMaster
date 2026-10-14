"""HTTP-API und Weboberfläche (FastAPI).

Alle Endpunkte lesen nur die lokale Datenbank. Die Stadt wird dabei nie abgerufen.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
from contextlib import asynccontextmanager, closing
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from . import __version__
from .auswertung import heute, jetzt
from .bedarf import bedarf_melden
from .betrieb import Betrieb, SyncDienst, initialisiere
from .config import PROJEKTWURZEL, Stadt, config_verzeichnis, lade_staedte, standard_stadt
from .geo import Gazetteer
from .ladebericht import ladebericht
from .nlq import Verstaendnis, verstehe
from .normalize import norm
from .rechtliches import Betreiber, datenschutz, impressum
from .speicher import Speicher
from .staedte import sortiert, uebersicht
from .suche import Suchauftrag, suche, vorgang_detail

WEB_DATEI = Path(__file__).resolve().parent / "web" / "index.html"
STANDARD_DB = PROJEKTWURZEL / "data" / "behoerdenmaster.sqlite"


class FrageAnfrage(BaseModel):
    frage: str = Field(min_length=3, max_length=500)
    stadt: str | None = None
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


def stadt_aus_frage(frage: str, staedte: dict[str, Stadt], standard: Stadt) -> Stadt | None:
    """Eine andere Stadt, wenn die Frage genau sie (und nicht die Standardstadt) nennt.

    „Was plant Düsseldorf in Bilk?“ soll nicht in Köln suchen, nur weil kein `stadt`
    mitgeschickt wurde. Nennt die Frage mehrere Städte, bleibt es bei der Standardstadt.
    """
    text = f" {norm(frage)} "
    treffer = [st for st in staedte.values()
               if any(f" {a} " in text for a in {norm(x) for x in (st.name, *st.aliase)} - {""})]
    if len(treffer) == 1 and treffer[0].schluessel != standard.schluessel:
        return treffer[0]
    return None


def _auftrag(v: Verstaendnis, stadt: Stadt, limit: int, offset: int) -> Suchauftrag:
    return Suchauftrag(
        stadt=stadt, orte=v.orte, plz=v.plz, themen=v.themen, freitext=v.freitext,
        von=v.von, bis=v.bis, offen=v.offen, limit=limit, offset=offset,
    )


def _stand(sp: Speicher) -> dict:
    return {
        "quellen": [
            {"id": r["id"], "name": r["name"], "demo": bool(r["demo"]), "status": r["status"],
             "letzter_erfolg": r["letzter_erfolg"], "abkuehlung_bis": r["abkuehlung_bis"],
             "fehlertext": r["fehlertext"]}
            for r in sp.quellen()
        ],
    }


def _hinweise(zaehler: dict[str, int], quellen: list[dict], laeufe: list[dict],
              einstellungen: Betrieb, demo_status: str, sync_ids: list[str]) -> list[str]:
    """Erklärt einen leeren Bestand, statt den Nutzer mit einer trefferlosen Suche zurückzulassen.

    Die Hinweise beschreiben nur den lokalen Zustand und die dafür zuständigen
    Einstellungen; sie enthalten keine Pfade, Kontaktadressen oder sonstige Env-Werte.
    """
    hinweise: list[str] = []
    echt = [q for q in quellen if not q["demo"]]
    # Nur die Quellen nennen, für die der Hintergrunddienst tatsächlich eingerichtet ist.
    geplant = [q for q in echt if q["id"] in sync_ids] or echt
    if zaehler.get("vorgang", 0) > 0:
        return hinweise

    hinweise.append("Der Datenbestand ist leer: Es ist noch kein Vorgang abgerufen worden, "
                    "deshalb liefert jede Suche null Treffer.")
    for q in echt:
        if q["fehlertext"]:
            hinweise.append(f"{q['name']}: letzter Abruf mit „{q['status']}“ – {q['fehlertext']}")
        elif q["abkuehlung_bis"]:
            hinweise.append(f"{q['name']}: Derzeit in Abkühlzeit bis {q['abkuehlung_bis'][:16].replace('T', ' ')} UTC.")

    if not einstellungen.sync_aktiv:
        if any(w.startswith("BEHOERDEN_SYNC_ENABLED") for w in einstellungen.warnungen):
            hinweise.extend(einstellungen.warnungen)
        else:
            hinweise.append("Automatische Abrufe sind ausgeschaltet (BEHOERDEN_SYNC_ENABLED=false oder "
                            "BEHOERDEN_KONTAKT bewusst leer gesetzt). Ohne Abruf füllt sich die Datenbank "
                            "nicht von selbst.")
            hinweise.append("Zum Aktivieren BEHOERDEN_SYNC_ENABLED=true setzen; als Kontaktadresse dient "
                            "BEHOERDEN_KONTAKT, sonst die im Repository hinterlegte Projekt-Kontaktadresse. "
                            "Der Abruf startet danach automatisch im Hintergrund.")
    elif not laeufe:
        hinweise.append(f"Automatische Abrufe sind aktiv. Der Hintergrunddienst startet "
                        f"{einstellungen.sync_startverzoegerung} s nach dem Hochfahren und ruft dann "
                        f"{', '.join(q['name'] for q in geplant)} ab. "
                        "Bis dahin bleibt die Suche leer; diese Seite lädt den Status beim Öffnen neu.")
    if demo_status == "deaktiviert":
        hinweise.append("Beispieldaten sind ausgeschaltet (BEHOERDEN_DEMO=false). Sie würden nur "
                        "offline und klar markiert angezeigt, aber keine echten Abrufe ersetzen.")
    elif demo_status == "uebersprungen_import_laeuft":
        hinweise.append("Die einmalige Demo wurde übersprungen, weil beim Start bereits ein "
                        "Import/Crawl dieselbe Datenbank benutzt hat.")
    return hinweise


def create_app(db_pfad: str | Path | None = None, config_dir: Path | None = None,
               *, betrieb: Betrieb | None = None) -> FastAPI:
    db = str(db_pfad or os.environ.get("BEHOERDEN_DB") or STANDARD_DB)
    staedte = lade_staedte(config_dir or config_verzeichnis())
    gazetteers = {k: Gazetteer(s) for k, s in staedte.items()}
    # streng=False: Ein falsch gesetzter Umgebungswert darf die Seite nicht unbeantwortbar machen.
    einstellungen = betrieb or Betrieb.aus_umgebung(streng=False)
    standard = standard_stadt(staedte)
    betreiber = Betreiber.aus_umgebung()
    # Ohne ausdrückliche Liste: alle automatischen Quellen der dauerhaft abgerufenen Städte.
    # Auf-Abruf-Städte kommen erst nach einer Anforderung hinzu (siehe /api/staedte).
    dauerhafte_ids = [s.quell_id(q) for s in staedte.values() if not s.auf_abruf
                      for q in s.quellen if q.automatisch]
    sync_quellen_effektiv = list(einstellungen.sync_quellen) or dauerhafte_ids

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Render mountet die Disk nur zur Laufzeit, nicht während Build/Pre-Deploy.
        await asyncio.to_thread(initialisiere, db, staedte, einstellungen)
        dienst = SyncDienst(db, staedte, einstellungen) if einstellungen.sync_aktiv else None
        app.state.sync_dienst = dienst
        if dienst:
            dienst.starten()
        try:
            yield
        finally:
            if dienst:
                dienst.stoppen()
                await asyncio.to_thread(dienst.warten)

    app = FastAPI(title="BehoerdenMaster", version=__version__, lifespan=lifespan,
                  description="Kommunale Vorgänge durchsuchen: Fragen stellen oder nach PLZ suchen.")
    app.state.sync_dienst = None
    app.state.betrieb = einstellungen

    def stadt_waehlen(schluessel: str | None) -> Stadt:
        if schluessel is None:
            return standard
        if schluessel not in staedte:
            raise HTTPException(status_code=404, detail=f"Unbekannte Stadt: {schluessel}")
        return staedte[schluessel]

    def bestand_der_stadt(sp: Speicher, stadt: Stadt) -> int:
        return sp.db.execute(
            "SELECT COUNT(*) FROM vorgang WHERE geloescht = 0 AND quelle_id IN "
            "(SELECT id FROM quelle WHERE stadt = ?)", (stadt.schluessel,)).fetchone()[0]

    def anfordern(stadt: Stadt) -> list[str]:
        """Bedarf an einer Auf-Abruf-Stadt vermerken und Hinweise für die Antwort liefern.

        Kein Behördenabruf: Es entsteht nur ein Vermerk; der Hintergrunddienst holt die
        Stadt in seinem normalen, gedrosselten Plan.
        """
        if not stadt.auf_abruf:
            return []
        with Speicher(db) as sp:
            neu = einstellungen.auf_abruf and einstellungen.sync_aktiv and bedarf_melden(
                sp, stadt, datetime.now(timezone.utc))
            bestand = bestand_der_stadt(sp, stadt)
        if neu and app.state.sync_dienst:
            app.state.sync_dienst.wecken()
        if bestand:
            return []
        if not einstellungen.sync_aktiv:
            return [f"{stadt.name} ist auf Abruf angebunden, automatische Abrufe sind auf diesem Server "
                    "aber ausgeschaltet. Daten kommen nur über einen manuellen Lauf hinzu."]
        if not einstellungen.auf_abruf:
            return [f"{stadt.name} ist auf Abruf angebunden, das Laden auf Abruf ist auf diesem Server "
                    "aber ausgeschaltet (BEHOERDEN_AUF_ABRUF=false)."]
        return [f"{stadt.name} wird auf Abruf geladen: Die Anforderung ist vermerkt, der erste Abruf läuft "
                "im Hintergrund – gedrosselt, in der Regel einige Minuten. Bis dahin gibt es hier noch "
                "keine Vorgänge; die Seite zeigt den Ladezustand unter „Angebundene Städte“."]

    @app.get("/impressum", include_in_schema=False, response_class=HTMLResponse)
    def impressum_seite():
        return HTMLResponse(impressum(betreiber))

    @app.get("/datenschutz", include_in_schema=False, response_class=HTMLResponse)
    def datenschutz_seite():
        return HTMLResponse(datenschutz(betreiber))

    @app.get("/api/staedte")
    def staedte_endpunkt():
        """Angebundene Städte mit Abrufmodus, Ladezustand und Bestand – nur lokale Daten."""
        with Speicher(db) as sp:
            return uebersicht(sp, staedte, standard=standard.schluessel,
                              auf_abruf=einstellungen.auf_abruf, verfall_tage=einstellungen.auf_abruf_tage,
                              max_staedte=einstellungen.auf_abruf_max, sync_aktiv=einstellungen.sync_aktiv,
                              dauerhafte_ids=sync_quellen_effektiv)

    @app.post("/api/staedte/{schluessel}/anfordern")
    def stadt_anfordern(schluessel: str):
        """Eine Auf-Abruf-Stadt anfordern. Vermerkt nur den Bedarf; löst selbst keinen Abruf aus."""
        stadt = stadt_waehlen(schluessel)
        hinweise = anfordern(stadt)
        eintrag = next(e for e in staedte_endpunkt()["staedte"] if e["schluessel"] == stadt.schluessel)
        return {"stadt": eintrag, "hinweise": hinweise}

    @app.get("/", include_in_schema=False)
    def startseite():
        return FileResponse(WEB_DATEI)

    @app.api_route("/healthz", methods=["GET", "HEAD"], include_in_schema=False)
    def healthcheck():
        # Read-only, kurzer Timeout, keine DB-Initialisierung und keine Live-Abfragen.
        uri = Path(db).expanduser().resolve().as_uri() + "?mode=ro"
        try:
            with closing(sqlite3.connect(uri, uri=True, timeout=2)) as verbindung:
                verbindung.execute("SELECT id FROM quelle LIMIT 1").fetchone()
        except (sqlite3.Error, OSError) as exc:
            raise HTTPException(status_code=503, detail="Lokale Datenbank nicht erreichbar") from exc
        return {"status": "ok", "version": __version__}

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
            demo_status = sp.betrieb_wert("demo_status")
        daten["version"] = __version__
        daten["betrieb"] = einstellungen.oeffentlicher_status() | {
            "sync_dienst_laeuft": bool(app.state.sync_dienst and app.state.sync_dienst.laeuft),
            "quellen_effektiv": sync_quellen_effektiv,
            "demo_modus": einstellungen.demo,
            "demo_status": demo_status,
        }
        daten["bestand_leer"] = daten["zaehler"].get("vorgang", 0) == 0
        daten["hinweise"] = _hinweise(daten["zaehler"], daten["quellen"], daten["letzte_laeufe"],
                                      einstellungen, demo_status, sync_quellen_effektiv)
        daten["staedte"] = [{"schluessel": s.schluessel, "name": s.name, "hinweis": s.hinweis,
                             "abruf": s.abruf, "standard": s.schluessel == standard.schluessel}
                            for s in sortiert(staedte, standard.schluessel)]
        daten["rechtliches"] = {"impressum_vollstaendig": betreiber.vollstaendig}
        return daten

    @app.get("/api/ladebericht")
    def ladebericht_endpunkt(
        stadt: str | None = None,
        laeufe: int = Query(default=8, ge=1, le=50, description="protokollierte Läufe je Quelle"),
    ):
        """Was aus welcher Quelle geladen wurde – rein lokal, ohne Behördenabruf."""
        st = stadt_waehlen(stadt)
        with Speicher(db) as sp:
            daten = ladebericht(
                sp, st,
                intervall_sekunden=einstellungen.sync_intervall,
                sync_aktiv=einstellungen.sync_aktiv,
                laeufe_pro_quelle=laeufe,
            )
        daten["betrieb"] = {
            "automatische_abrufe": einstellungen.sync_aktiv,
            "intervall_sekunden": einstellungen.sync_intervall,
            "max_anfragen_pro_quelle": einstellungen.sync_budget,
            "parallel_quellen": einstellungen.sync_parallel,
            "sync_dienst_laeuft": bool(app.state.sync_dienst and app.state.sync_dienst.laeuft),
            "warnungen": list(einstellungen.warnungen),
        }
        return daten

    @app.post("/api/frage")
    def frage(anfrage: FrageAnfrage):
        gewaehlt = stadt_waehlen(anfrage.stadt)
        genannt = stadt_aus_frage(anfrage.frage, staedte, gewaehlt)
        # Ohne gewählte Stadt folgt die Suche der Frage; mit gewählter Stadt bleibt es bei der
        # Wahl und die genannte Stadt wird nur vorgeschlagen („Bonner Münster“ ≠ Münster).
        erkannt = genannt if anfrage.stadt is None else None
        vorschlag = genannt if anfrage.stadt is not None else None
        stadt = erkannt or gewaehlt
        v = verstehe(anfrage.frage, stadt, gazetteers[stadt.schluessel])
        abruf_hinweise = anfordern(stadt)
        with Speicher(db) as sp:
            ergebnis = suche(sp, _auftrag(v, stadt, anfrage.limit, anfrage.offset), heute(), zeitpunkt=jetzt())
        hinweise = ([f"Stadt aus der Frage erkannt: {stadt.name}."] if erkannt else []) + abruf_hinweise
        hinweise += list(v.hinweise)
        if ergebnis.get("freitext_verworfen"):
            hinweise.append("Zu den Stichworten wurde im gefragten Ort bzw. Thema nichts gefunden. "
                            "Angezeigt werden die Vorgänge des Orts und Themas ohne die Stichworte – "
                            "bitte prüfen, ob das Gesuchte dabei ist.")
        if ergebnis.get("freitext_oder"):
            hinweise.append("Keine Vorgänge mit allen Stichworten gefunden. Angezeigt werden Vorgänge mit mindestens einem Stichwort.")
        return {
            "verstanden": v.als_dict(),
            "hinweise": hinweise,
            "gesamt": ergebnis["gesamt"],
            "treffer": ergebnis["treffer"],
            "kandidaten_gekappt": ergebnis["kandidaten_gekappt"],
            "stadt": {"schluessel": stadt.schluessel, "name": stadt.name, "hinweis": stadt.hinweis},
            "stadt_vorschlag": ({"schluessel": vorschlag.schluessel, "name": vorschlag.name}
                                if vorschlag else None),
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
        abruf_hinweise = anfordern(st)
        themen = [t.strip() for t in (thema or "").split(",") if t.strip()]
        bekannte = {t.schluessel for t in st.themen}
        unbekannt = [t for t in themen if t not in bekannte]
        if unbekannt:
            raise HTTPException(status_code=422, detail=f"Unbekannte Themen: {', '.join(unbekannt)}")

        hinweise = list(abruf_hinweise)
        eintrag = st.plz.get(plz)
        if eintrag is None and not st.plz:
            hinweise.append(f"Für {st.name} ist noch keine PLZ-Zuordnung hinterlegt. Es werden nur Vorgänge "
                            "mit ausdrücklicher PLZ-Nennung gefunden.")
        elif eintrag is None:
            hinweise.append(f"Die PLZ {plz} gehört nicht zum Gebiet von {st.name}. Es werden nur Vorgänge "
                            "mit ausdrücklicher PLZ-Nennung gefunden.")
        elif not eintrag.stadtteile:
            hinweise.append(f"Für die PLZ {plz} ließ sich aus dem Straßenverzeichnis kein Stadtteil ableiten. "
                            "Es werden nur Vorgänge mit ausdrücklicher PLZ-Nennung gefunden.")
            if eintrag.quelle:
                hinweise.append(f"Grund: {eintrag.quelle}")
        else:
            namen = ", ".join(st.stadtteile[s].name for s in eintrag.stadtteile)
            hinweis = f"Die PLZ {plz} wird den Stadtteilen {namen} zugeordnet."
            if eintrag.quelle:
                hinweis += f" Quelle der Zuordnung: {eintrag.quelle}."
            hinweis += " Die Zuordnung ist eine Näherung; eine Straße im Suchfeld ist genauer."
            hinweise.append(hinweis)
            if st.strassen:
                hinweise.append(f"Straßenverzeichnis: {len(st.strassen)} Straßennamen für {st.name} "
                                f"(Stand {st.strassen_stand or 'unbekannt'}), Herkunft: {st.strassen_quelle} "
                                f"– Lizenz {st.strassen_lizenz}.")

        auftrag = Suchauftrag(stadt=st, plz=plz, themen=themen, limit=limit, offset=offset)
        with Speicher(db) as sp:
            ergebnis = suche(sp, auftrag, heute(), zeitpunkt=jetzt())
        return {
            "plz": plz,
            "hinweise": hinweise,
            "gesamt": ergebnis["gesamt"],
            "treffer": ergebnis["treffer"],
            "kandidaten_gekappt": ergebnis["kandidaten_gekappt"],
            "stadt": {"schluessel": st.schluessel, "name": st.name, "hinweis": st.hinweis},
        }

    @app.get("/api/vorgang")
    def vorgang(id: str = Query(..., min_length=5, description="Quell-URL/ID des Vorgangs"), stadt: str | None = None):
        st = stadt_waehlen(stadt)
        with Speicher(db) as sp:
            detail = vorgang_detail(sp, st, id, heute(), zeitpunkt=jetzt())
        if detail is None:
            raise HTTPException(status_code=404, detail="Vorgang nicht gefunden")
        return detail

    return app
