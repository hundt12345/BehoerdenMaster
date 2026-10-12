# Fixture: Bürgerbeteiligung „Meinung für Köln“

**Herkunft:** öffentliche Projektliste der Beteiligungsplattform,
`https://meinungfuer.koeln/mitmachen/alle-projekte`, abgerufen am **12.10.2026** mit dem regulären
Crawler-User-Agent. Die `robots.txt` der Plattform erlaubt unbekannten Clients den Abruf
(`Crawl-delay: 5`, ausgenommen sind u. a. `/search/`, `/admin/`, `/user/`).

**Was in `projekte.html` steht:** die **drei ersten Projektkarten unverändert** samt Markup
(`<article class="group--teaser …">` mit Statuszeile `<p class="participation-countdown…">`,
Titel-Link, Beitragszahl und Kurzbeschreibung). Am Abruftag hatte die Liste **56 Karten**, davon
45 mit einer Statuszeile „… geschlossen“ und 11 ohne Statuszeile; diese drei Fälle sind hier
vertreten. Bild- und CSS-Pfade stehen wie in der Quelle, die Bilder werden nicht geladen.

**Was der Adapter nicht ausliest:** Beiträge, Kommentare, Namen, Fotos und Anhänge Dritter.
Übernommen werden Projekttitel, Statuszeile, Kurztext, Beitragszahl und der Link; es wird nur
diese eine Liste aufgerufen, keine Detailseite und kein `/jsonapi`-Endpunkt.

**Wofür die Fixture gut ist:** Sie hält die real beobachteten Varianten der Statuszeile fest
(Jahresangabe in Tagen, ohne Tagesangabe, ganz ohne Statuszeile), damit die Statusableitung nicht
gegen erfundene Texte getestet wird.

## Live-Probelauf (12.10.2026)

```text
probe --quelle beteiligung
  → 56 Beteiligungsangebote übernommen, 0 verworfen, 2 Anfragen (robots.txt + Liste)
     Beispiele: „Beteiligung geschlossen: GRÜNZUG Flittard-Stammheim“,
                „ohne Statusangabe: Qualifizierungsverfahren Neubrücker Ring in Köln-Neubrück“

crawl --quelle beteiligung --max-anfragen 3   (leere Datenbank)
  → status=ok, 56 Beteiligungen neu, 0 aktualisiert, 2 Anfragen
    (robots.txt + Liste; die Suche nach „Flittard“ findet „GRÜNZUG Flittard-Stammheim“
     mit den Stadtteilen Flittard und Stammheim)
```

Der Abruf lief mit dem regulären Adapter aus einem Rechenzentrums-Netz (GitHub-Actions-Runner),
nicht aus der gesperrten Entwicklungssandbox. Die Plattform antwortete auch einer
Rechenzentrums-IP; der Abstand von 5 Sekunden (`Crawl-delay: 5` der Plattform) wurde eingehalten.
Ein Abruf aus dem Render-Netz steht aus. Gelesen wurde ausschließlich diese eine Liste –
keine Detailseite, kein `/jsonapi`, keine Beiträge Dritter.
