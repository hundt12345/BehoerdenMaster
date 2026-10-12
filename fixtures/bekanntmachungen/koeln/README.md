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
