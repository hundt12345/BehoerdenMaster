# Temporäre Sondierung

`ausloeser.txt` löst den Arbeitsablauf `.github/workflows/sonde.yml` aus: Er führt den
Live-Probelauf der beiden neuen Listenquellen (öffentliche Bekanntmachungen und
Bürgerbeteiligung „Meinung für Köln“) aus einem Netz mit Internetzugang aus und legt das
Protokoll unter `sonde-raw/` ab. Nach dem Probelauf werden `sonde/`, `sonde-raw/` und der
Arbeitsablauf wieder entfernt; die Zahlen stehen in den Fixture-READMEs.
