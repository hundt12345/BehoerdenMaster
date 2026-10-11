# BehoerdenMaster auf Render veröffentlichen

Das Repository enthält eine fertige [`render.yaml`](../render.yaml) für **einen nativen Python-Web-Service in Frankfurt im kostenlosen Tarif (Render Free)**. Es ist kein Docker, separater Worker, externer Sprachmodellschlüssel oder PostgreSQL erforderlich.

**Wichtig: Render Free hat kein persistentes Dateisystem.** Die SQLite-Datenbank liegt in `/tmp/behoerdenmaster/` und geht bei Neustart, Redeploy oder nach dem Schlafmodus verloren. Der Dienst wird dann mit der Offline-Demo neu aufgebaut, das ist für eine Homepage-Demo ausreichend. Eigene Daten, die bleiben sollen, brauchen einen bezahlten Tarif mit Disk: dann `plan` auf z. B. `0.5c-512mb` setzen, einen `disk`-Block mit `mountPath: /var/data` ergänzen und `BEHOERDEN_DB` auf `/var/data/behoerdenmaster.sqlite` setzen. Das Einchecken der Datenbank ersetzt keine Disk.

Der erste Deploy zeigt ausschließlich die **klar markierte Offline-Demo vom 09.10.2026**. Automatische Behördenabrufe sind standardmäßig **ausgeschaltet**. Es wurde kein Service in deinem Konto angelegt; die Veröffentlichung führst du mit den folgenden Schritten selbst aus.

## 1. Blueprint anlegen

1. Bei Render anmelden und dem Render-GitHub-Zugriff dieses Repository freigeben (besonders bei einem privaten Repository).
2. Für den aktuellen, noch nicht gemergten Pull Request diesen Link öffnen:

   **[Auf Render bereitstellen](https://render.com/deploy?repo=https://github.com/hundt12345/BehoerdenMaster/tree/arena/027b2661-behoerdenmaster)**

   **Wichtig:** Ein manuell angelegter Web-Service muss **Runtime = Python 3** haben. Steht dort `Node` und Start `npm start`, meldet Render den Fehler `Could not read package.json`, weil dieses Repository kein Node-Projekt ist. Den Service dann mit dem Blueprint neu anlegen oder Runtime/Build-/Start-Befehl wie in Schritt 4 setzen.

   Alternativ im Render-Dashboard **New → Blueprint**, Repository `hundt12345/BehoerdenMaster`, Branch **`arena/027b2661-behoerdenmaster`**, Blueprint-Pfad **`render.yaml`** auswählen. Nach dem Merge kannst du stattdessen `main` verwenden. Die YAML legt keinen Branch fest und folgt dem beim Blueprint ausgewählten Branch.
3. Namen, **Region Frankfurt** und den Tarif **Free** kontrollieren. Bei `BEHOERDEN_KONTAKT` eine **eigene erreichbare Kontaktadresse** eingeben; sie wird für den identifizierbaren Crawler-User-Agent benötigt. Keine GitHub-/Render-Zugangsdaten eingeben! Der Kontakt steht nicht im Repository und wird nicht über `/api/status` ausgegeben.
4. Render baut mit `python -m pip install -e .` und startet den Web-Service mit:

   ```bash
   python -m behoerdenmaster -v serve --host 0.0.0.0 --port $PORT
   ```

5. Nach erfolgreichem Deploy die angezeigte `https://…onrender.com`-Adresse öffnen. Frage „Welche Bauvorhaben sind in Köln Ehrenfeld geplant?“ und PLZ `50827` ausprobieren. Demo-Markierungen und Quellen-/Datenstand beachten. `/healthz` muss HTTP 200 liefern, `/api/status` den lokalen Bestand.

**Warum `pip install -e .`?** Die Stadtkonfigurationen und Demo-Fixtures liegen im Repository, nicht im Python-Wheel. Der editable Install verwendet den ausgecheckten Code samt diesen Dateien. `.python-version` wählt Python **3.13**; ein abweichendes `PYTHON_VERSION` im Render-Dashboard würde diese Datei übersteuern.

**Nicht in Build oder Pre-Deploy initialisieren:** Dort ist die Disk nicht verfügbar. Die Anwendung erstellt/ergänzt ihr SQLite-Schema und lädt ggf. die einmalige Demo erst im FastAPI-Lifespan zur Laufzeit. Bestehende Daten werden nicht zurückgesetzt. Ein persistierter Marker verhindert, dass `demo --loeschen` durch den nächsten Redeploy rückgängig gemacht wird; ein bereits befüllter Bestand wird auch beim ersten Start nicht automatisch mit Demo ergänzt.

## 2. Einstellungen

Unter **Service → Environment** verwalten; Änderungen anschließend deployen. `.env.example` ist lediglich eine Referenz und wird nicht automatisch geladen.

| Variable | Blueprint-Wert / Zweck |
|---|---|
| `BEHOERDEN_DB` | `/tmp/behoerdenmaster/behoerdenmaster.sqlite` (Free, flüchtig). Mit bezahlter Disk: `/var/data/behoerdenmaster.sqlite` |
| `BEHOERDEN_CONFIG_DIR` | `/opt/render/project/src/config/cities` |
| `BEHOERDEN_DEMO` | `true`: einmalige Demo nur bei leerem Bestand; `false` für einen leeren produktiven Erststart |
| `BEHOERDEN_KONTAKT` | eigene erreichbare Adresse; für Live-Abrufe Pflicht |
| `BEHOERDEN_SYNC_ENABLED` | **`false`**; `true` schaltet Hintergrundabrufe ein |
| `BEHOERDEN_SYNC_QUELLEN` | `koeln:bauleitplanung,koeln:ris`; kommagetrennte vollständige Quell-IDs. Leer = alle konfigurierten Quellen. |
| `BEHOERDEN_SYNC_INTERVALL` | `21600` Sekunden = **6 Stunden pro Quelle**; mindestens 300 Sekunden |
| `BEHOERDEN_SYNC_MAX_ANFRAGEN` | `100` **pro Quelle und Etappe**, einschließlich robots und Weiterleitungen; mindestens 1 |
| `BEHOERDEN_SYNC_STARTVERZOEGERUNG` | `30` Sekunden nach Start, mindestens 0; keine Abrufe im Healthcheck |
| `WEB_CONCURRENCY` | **`1`**, keine mehreren Worker/Instanzen für diesen Aufbau |
| `PYTHONUNBUFFERED` | `1`, zeitnahe Service-Logs |
| `FORWARDED_ALLOW_IPS` | `*`, HTTPS-Proxy von Render vertrauen; nur für diesen hinter Render betriebenen Service |
| `PORT` | von Render gesetzt; nicht hart auf eine andere Nummer festlegen |

Ungültige Werte bzw. aktivierter Sync ohne Kontakt stoppen den Start mit einer verständlichen Fehlermeldung. Nicht unterstützte oder unbekannte Sync-Quellen werden nicht stillschweigend abgerufen.

## 3. Echte Daten zunächst manuell prüfen

Unter **Service → Shell** arbeiten. Die Shell des Web-Service sieht dieselbe Disk und seine Environment-Werte. **Keinen separaten Render-Cron-Job oder One-off-Job** für diese Datenbank anlegen: diese können die Disk des Web-Service nicht benutzen.

```bash
# Keine DB-Schreibvorgänge, höchstens 5 HTTP-Anfragen, keine Wiederholungen.
python -m behoerdenmaster probe --quelle bauleitplanung

# Kleine, fortsetzbare Abrufetappe; Status/Datenstand danach kontrollieren.
python -m behoerdenmaster crawl --quelle bauleitplanung --max-anfragen 15
python -m behoerdenmaster status

# Erst danach separat Ratsinformation prüfen und klein anfangen.
python -m behoerdenmaster probe --quelle ris
python -m behoerdenmaster crawl --quelle ris --max-anfragen 30
python -m behoerdenmaster status
```

Bei Status `teilweise` wurde nur das Budget erreicht; ein neuer Aufruf setzt den gespeicherten Cursor bzw. die Detail-Warteschlange fort. Bei `gesperrt`, 401/403/429, robots-Verbot oder Netzfehler **nicht in einer Schleife wiederholen**. Server-Pause/`Retry-After` und Cooldown respektieren; kein `--trotz-sperre` im automatischen Betrieb. Vor einem größeren RIS-Erstabruf Abrufgrenzen mit der Stadt abstimmen. Siehe [README: echte Daten abrufen](../README.md#echte-daten-abrufen).

**Kein erfolgreicher End-to-End-Live-Crawl aus der Entwicklungs-Sandbox wurde bestätigt.** Die Offline-Tests beweisen Adapter-/Betriebsverhalten, nicht die Erreichbarkeit oder Freigabe vom Render-Netz aus. Ein Probelauf auf Render bleibt erforderlich.

## 4. Optionale automatische Aktualisierung

Erst nach erfolgreichen Probeläufen und geklärten Nutzungs-/Abrufgrenzen unter **Environment** setzen, z. B. zunächst nur Bauleitplanung:

```text
BEHOERDEN_SYNC_ENABLED=true
BEHOERDEN_KONTAKT=<deine eigene erreichbare Kontaktadresse>
BEHOERDEN_SYNC_QUELLEN=koeln:bauleitplanung
BEHOERDEN_SYNC_INTERVALL=21600
BEHOERDEN_SYNC_MAX_ANFRAGEN=100
```

Nach Deploy startet genau ein Hintergrund-Thread im Web-Service; Quellen laufen **nacheinander**, nie parallel. Bei mehreren Quellen die IDs kommagetrennt setzen. Fälligkeit basiert auf `letzter_versuch` in SQLite; ein Restart/Redeploy startet keine neue Abrufserie innerhalb des Intervalls. Längere persistierte Server-Pausen haben Vorrang. Ein budgetbegrenzter Teilabruf wird frühestens nach dem Intervall fortgesetzt, nicht sofort in einer Endlosschleife.

Der Thread teilt sich eine nicht blockierende **Dateisperre** mit `crawl` und `demo` aus der Shell. Ist ein Import/Abruf schon aktiv, beginnt kein zweiter Crawl. Lock-Dateien nicht löschen, um eine Sperre „freizugeben“; das Betriebssystem gibt den Lock beim Prozessende frei. Ein Dateilock ist kein verteilter Lock für andere Disks/Instanzen.

Mindestabstand, robots, Budgets, Backoff, sichere Weiterleitungen und Sperrabbruch bleiben unverändert. Beim Service-Shutdown werden Warteschleifen unterbrochen und vorhandene Checkpoints behalten; eine bereits laufende HTTP-Anfrage darf noch innerhalb ihres Timeouts enden. `/api/status` meldet die Betriebsoptionen, den Thread-Status und die letzten Abrufläufe. Die Weboberfläche zeigt, ob automatische Aktualisierung an oder aus ist. **Fragen, PLZ-Suche und `/healthz` lösen selbst niemals externe Abrufe aus. Es gibt keinen öffentlich aufrufbaren Crawl-/Admin-Endpunkt.**

Zum Abschalten `BEHOERDEN_SYNC_ENABLED=false` setzen und neu deployen. Manuelle, budgetbegrenzte Crawls in der Service-Shell bleiben möglich.

## 5. Demo entfernen, Daten sichern, Redeploys

Sobald echte Daten vorhanden sind, die verbliebenen Beispiele entfernen:

```bash
python -m behoerdenmaster demo --loeschen
```

Das entfernt nur Demo-Quellen. Danach zusätzlich `BEHOERDEN_DEMO=false` setzen. Bereits live erfasste Objekte und Checkpoints bleiben bestehen.

Vor riskanten Änderungen einen konsistenten Snapshot erstellen:

```bash
python -m behoerdenmaster backup "/var/data/backups/behoerdenmaster-$(date -u +%Y%m%dT%H%M%SZ).sqlite"
```

Der Befehl verwendet die **SQLite-Backup-API einschließlich WAL**, prüft die Zieldatei und überschreibt niemals vorhandene Sicherungen. **Nicht einfach die aktive Hauptdatei mit `cp` kopieren:** ungesicherte Seiten können in `-wal` liegen. Die Sicherung danach mit den von Render angebotenen Datei-/Shell-Transfermöglichkeiten herunterladen und an einem unabhängigen Ort aufbewahren. Eine Kopie auf derselben Disk schützt nicht vor Verlust oder Löschen des Service. Speicherverbrauch überwachen; alte Sicherungen nach festgelegter Aufbewahrung entfernen. Zugriff beschränken, da öffentliche Behördeninformationen trotzdem personenbezogene Daten enthalten können.

Die Disk behält Daten nur unter **`/var/data`** über Neustarts/Redeploys. Eine Disk gehört zu genau einer Service-Instanz, kann nicht an einen zweiten Worker/Cron-Service angehängt werden und verhindert horizontale Skalierung dieses SQLite-Aufbaus. Disk-basierte Deploys können kurzzeitig nicht erreichbar sein; keine Zero-Downtime-Zusage. Datenbankdateien bei einer Wiederherstellung **nie im laufenden Web-/Sync-Prozess austauschen**; vorher alle Zugriffe beenden und eine getrennte Sicherung behalten. Render-Disk-Snapshots nicht als einzige Datenbank-Sicherungsstrategie behandeln.

Automatische Deploys sind im Blueprint mit `autoDeployTrigger: off` absichtlich deaktiviert: neue Commits/PR-Änderungen veröffentlichen sich nicht ungeprüft. Neue Versionen im Dashboard **Manual Deploy → Deploy latest commit** auslösen. Nach dem Merge gegebenenfalls den Service-/Blueprint-Branch auf `main` umstellen. Auf Wunsch Auto-Deploy erst nach erfolgreichen Checks bewusst aktivieren.

## Fehlerbehebung

- **Healthcheck schlägt fehl:** Service-Logs lesen; Disk-Mount, Schreibrechte und `BEHOERDEN_DB` prüfen. `/healthz` prüft ausschließlich, ob die vorhandene SQLite-Datei samt Quell-Tabelle lesbar ist; Fehler liefern 503, keine Pfad-/Kontaktdaten. Es ist kein Nachweis aktueller oder vollständiger Behördeninformationen.
- **Leerer Bestand nach Redeploy:** Meist DB außerhalb der Disk oder Service ohne Disk. Richtigen Pfad wiederherstellen; nicht zur „Reparatur“ bestehende Dateien löschen.
- **Start scheitert mit Sync-Konfiguration:** tatsächliche Kontaktadresse, boolesche Flags, numerische Grenzen und Quellen-IDs prüfen. Für einen sicheren Demo-Start Sync ausschalten.
- **Shell-Crawl gibt Exitcode 3 zurück:** Ein anderer Crawl/Demo-Import läuft oder eine Cooldown-Sperre ist aktiv. Nicht durch parallele Prozesse bzw. Lock-Dateilöschung umgehen.
- **Suchtreffer fehlen:** Quellenstatus/letzte Läufe kontrollieren, noch nicht abgeschlossene Erstabrufe fortsetzen. PLZ-Mapping und Ortsbezug sind ausdrücklich unvollständig/heuristisch; keine Vollständigkeit versprechen.
- **Bauleitquelle geändert/gesperrt:** automatischen Abruf ausschalten, Betreiber/Schema prüfen. Keine Logins, privaten Stellungnahmen oder Sperrumgehung implementieren.

Vor öffentlichem Betrieb zusätzlich Nutzungsrechte, Datenschutz, Aufbewahrung/Löschverfahren und die noch offene Code-Lizenzentscheidung klären.

## Render-Referenzen

- [Blueprint-Spezifikation](https://render.com/docs/blueprint-spec)
- [Persistente Disks und ihre Einschränkungen](https://render.com/docs/disks)
- [Python-Version konfigurieren](https://render.com/docs/python-version)
- [FastAPI bereitstellen](https://render.com/docs/deploy-fastapi)
- [Deploy-to-Render-Button und Branch-Auswahl](https://render.com/docs/deploy-to-render)
