# Fixture: Öffentliche Bekanntmachungen der Stadt Köln

**Herkunft:** öffentliche Jahresliste der Stadt Köln,
`https://www.stadt-koeln.de/politik-und-verwaltung/bekanntmachungen/oeffentliche-bekanntmachungen-2026`,
abgerufen am **12.10.2026** mit dem regulären Crawler-User-Agent (`BehoerdenMaster/0.1 (+…; Kontakt: …)`)
nach Prüfung der `robots.txt` (keine Einschränkung für diese Route).

**Was in `liste.html` steht:** die **ersten drei Listeneinträge unverändert** samt Markup
(`<li>` → `<a>` → `<time itemprop="startDate">` → `<figure>` → Hinweisveröffentlichung), dazu der
Weiter-Link (`rel="next"`) und die Trefferangabe der Paginierung. Die übrigen 12 Einträge der Seite
und der Seitenrahmen wurden entfernt; am Abruftag hatte die Liste **191 Einträge** (15 je Seite).

**Was die Datei nicht enthält:** keine PDF-Datei und keinen Text aus einer Bekanntmachung. Der
Adapter übernimmt ausschließlich Titel, Datum, Hinweisveröffentlichung, interne Nummer und Link;
das verlinkte PDF wird nie geladen.

**Wofür die Fixture gut ist:** Sie hält die tatsächlich beobachtete Struktur der Quelle fest
(auch die Besonderheit `<figcaption class="downloadbeschreibung"<abbr …>` und Dateinamen ohne
Jahresordner), damit Parser und Tests nicht gegen eine erfundene Struktur prüfen. Ein Live-Lauf
bleibt trotzdem nötig: Die Stadt kann Markup und Seitennummerierung jederzeit ändern.

## Live-Probelauf (12.10.2026)

Derselbe Auszug stammt aus einem Probelauf mit dem regulären Adapter, ausgeführt aus einem
Rechenzentrums-Netz (GitHub-Actions-Runner) und **nicht** aus der gesperrten Entwicklungssandbox:

```text
probe --quelle bekanntmachungen
  → 15 Bekanntmachungen auf der ersten Seite (weitere Seiten vorhanden; Treffer 1–15 von 191), 2 Anfragen

crawl --quelle bekanntmachungen --max-anfragen 10   (leere Datenbank)
  → status=teilweise, 9 Listenseiten gelesen, 135 Bekanntmachungen neu, 0 aktualisiert
    (9 Seiten × 15 = 135: keine Seite wurde doppelt gelesen)
  → Cursor danach: …/oeffentliche-bekanntmachungen-2026?page=10  (Seitennummerierung 1-basiert)
```

Damit sind die Seitennummerierung (`rel="next"`, 1-basiert), das Fortsetzen am Cursor und die
Übernahme von Titel, Datum, Hinweisveröffentlichung und Link gegen die **echte** Quelle belegt.
Ein Zwischenstand mit falscher Seitenrechnung hatte dieselbe erste Seite zweimal gelesen
(105 Zeilen, davon 15 Aktualisierungen); der Vergleich mit den echten Rohantworten hat den Fehler
gefunden, die Tests halten ihn seither fest.
Ein Eintrag ohne Jahresordner im Dateinamen (`0176-02_vertretungsberechtigten_gebäudewirtschaft.pdf`,
Seite 2) wurde dabei korrekt als Eintrag mit Datum `2026-09-15` erfasst – das Datum stammt aus
`<time itemprop="startDate">`, nicht aus dem Dateinamen. Nicht geprüft ist ein Abruf aus dem
Render-Netz; die Stadt kann Markup und Seitennummerierung jederzeit ändern.
