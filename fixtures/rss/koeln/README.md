# Offline-Fixtures: RSS-Feeds der Stadt Köln

**Stand: 12. Oktober 2026. Rekonstruiert, keine Rohantwort.**

Die Abrufe vom 12.10.2026 (Sitzungs-Feed `rssfeed.asp?id=1&filter=snvf` und Presse-Feed
`externe-dienste/rss/pressemeldungen.xml`) wurden als Text-Auszug gelesen, nicht als
unveränderte XML-Datei gespeichert. Titel, Daten, Links und Sitzungsnummern stammen aus
diesen Auszügen; Kopf und Element-Anordnung sind eine Rekonstruktion in RSS 2.0.

- `sitzungen.xml`: drei Sitzungseinträge, ein Vorlagen-Eintrag (wird verworfen) und ein
  Eintrag mit fremdem Host (wird verworfen).
- `presse.xml`: zwei Pressemeldungen.

Die Tests in `tests/test_rss.py` prüfen das Parsen gegen diese Dateien. Vor einer
Entscheidung über einen automatischen Dauerabruf das Rohformat der echten Feeds mit
`python -m behoerdenmaster probe --quelle ris-rss` prüfen.
