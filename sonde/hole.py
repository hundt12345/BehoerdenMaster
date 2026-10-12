"""Einmalige Sondierung: prüft Kandidaten-Endpunkte und sammelt Nachweise (temporär).

Zweck und Stand: siehe Commit-Historie der Sondierung (wird nach dem Live-Probelauf entfernt).

Wird nur vom Arbeitsablauf `.github/workflows/sonde.yml` ausgeführt, schreibt die Rohantworten
nach `sonde-raw/` und druckt eine kurze Diagnose. Nach der Umsetzung entfernt.
"""

from __future__ import annotations

import hashlib
import pathlib
import re
import time

import httpx

UA = ("BehoerdenMaster/0.1 (+https://github.com/hundt12345/BehoerdenMaster; "
      "Kontakt: behordenkontakt123@posteo.de)")

STADT = "https://www.stadt-koeln.de/politik-und-verwaltung/bekanntmachungen"

# (Name, Adresse, Mindestabstand in Sekunden, Rohantwort ablegen?)
ZIELE = [
    ("robots-stadt-koeln", "https://www.stadt-koeln.de/robots.txt", 3, False),
    ("robots-meinungfuer", "https://meinungfuer.koeln/robots.txt", 5, False),
    ("bekanntmachungen-2026", f"{STADT}/oeffentliche-bekanntmachungen-2026", 3, True),
    ("bekanntmachungen-2026-page2", f"{STADT}/oeffentliche-bekanntmachungen-2026?page=2", 3, True),
    ("bekanntmachungen-2026-letter-u", f"{STADT}/oeffentliche-bekanntmachungen-2026?letter=U", 3, True),
    ("bekanntmachungen-2025", f"{STADT}/oeffentliche-bekanntmachungen-2025", 3, True),
    ("meinungfuer-projekte", "https://meinungfuer.koeln/mitmachen/alle-projekte", 5, True),
    ("probe-meinungfuer-rss", "https://meinungfuer.koeln/rss", 5, False),
    ("probe-meinungfuer-feed", "https://meinungfuer.koeln/feed", 5, False),
    ("probe-meinungfuer-archiv", "https://meinungfuer.koeln/archiv/index.html", 5, False),
    ("probe-opendata-katalog",
     "https://www.offenedaten-koeln.de/api/1/metastore/schemas/dataset/items?page-size=5", 3, False),
]

ABLAGE = pathlib.Path("sonde-raw")


def hole(name: str, url: str, pause: int, ablegen: bool) -> dict:
    with httpx.Client(headers={"User-Agent": UA}, timeout=45.0, follow_redirects=True) as client:
        antwort = client.get(url)
    text = antwort.text
    zeile = {
        "name": name,
        "url": url,
        "status": antwort.status_code,
        "typ": antwort.headers.get("content-type", ""),
        "bytes": len(antwort.content),
        "sha256": hashlib.sha256(antwort.content).hexdigest()[:16],
        "artikel": len(re.findall(r"<article\b", text, re.I)),
        "anker": len(re.findall(r"<a\b", text, re.I)),
        "pdf_links": len(re.findall(r'href="[^"]*\.pdf"', text, re.I)),
        "time_tags": len(re.findall(r"<time\b", text, re.I)),
    }
    if ablegen:
        ABLAGE.mkdir(exist_ok=True)
        (ABLAGE / f"{name}.roh").write_bytes(antwort.content)
    if name.startswith("probe-"):
        zeile["anfang"] = re.sub(r"\s+", " ", text[:300])
    if "opendata" in name or name == "probe-opendata-katalog":
        for titel in re.findall(r'"title":"(.*?)"', text)[:8]:
            zeile.setdefault("titel", []).append(titel)
        for url_ in re.findall(r'"downloadURL":"(.*?)"', text)[:8]:
            zeile.setdefault("downloads", []).append(url_)
        for lizenz in re.findall(r'"license":"(.*?)"', text)[:8]:
            zeile.setdefault("lizenzen", []).append(lizenz)
    time.sleep(pause)
    return zeile


def main() -> None:
    # Wir lesen zuerst beide robots-Dateien und halten fest, was dort steht.
    ergebnisse = []
    for name, url, pause, ablegen in ZIELE:
        try:
            ergebnisse.append(hole(name, url, pause, ablegen))
        except Exception as exc:  # noqa: BLE001 – bei einer Sondierung ist jeder Fehler ein Ergebnis
            ergebnisse.append({"name": name, "url": url, "fehler": f"{type(exc).__name__}: {exc}"})
            time.sleep(pause)
    for d in ergebnisse:
        print(f"{d['name']}: status={d.get('status')} typ={d.get('typ')} "
              f"bytes={d.get('bytes')} artikel={d.get('artikel')} anker={d.get('anker')} "
              f"pdf={d.get('pdf_links')} time={d.get('time_tags')} fehler={d.get('fehler', '')}")
        for schluessel in ("anfang", "titel", "downloads", "lizenzen"):
            for wert in d.get(schluessel, []) or []:
                print(f"   {schluessel}: {wert[:200]}")


if __name__ == "__main__":
    main()
