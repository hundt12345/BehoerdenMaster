"""Impressum und Datenschutzhinweise als eigene Seiten (/impressum, /datenschutz).

Vorbereitung für einen öffentlichen bzw. kommerziellen Betrieb: Die Seiten existieren schon,
die **Angaben zum Betreiber kommen aus der Umgebung** und stehen nie im Repository:

* ``BEHOERDEN_BETREIBER_NAME`` – Name bzw. Firma (Pflicht für ein vollständiges Impressum)
* ``BEHOERDEN_BETREIBER_ANSCHRIFT`` – ladungsfähige Anschrift; Zeilen mit ``|`` trennen (Pflicht)
* ``BEHOERDEN_BETREIBER_EMAIL`` – Kontakt-E-Mail (Pflicht)
* ``BEHOERDEN_BETREIBER_TELEFON`` – optional
* ``BEHOERDEN_BETREIBER_VERANTWORTLICH`` – optional, inhaltlich Verantwortliche/r

Fehlen Pflichtangaben, sagt die Seite das offen, und ``/api/status`` meldet
``rechtliches.impressum_vollstaendig = false``. Der Datenschutztext beschreibt nur, was
diese Anwendung tatsächlich tut. **Beides sind Vorlagen und ersetzen keine Rechtsberatung.**
"""

from __future__ import annotations

import html
import os
from collections.abc import Mapping
from dataclasses import dataclass

PFLICHT = ("BEHOERDEN_BETREIBER_NAME", "BEHOERDEN_BETREIBER_ANSCHRIFT", "BEHOERDEN_BETREIBER_EMAIL")


@dataclass(frozen=True)
class Betreiber:
    name: str = ""
    anschrift: tuple[str, ...] = ()
    email: str = ""
    telefon: str = ""
    verantwortlich: str = ""

    @classmethod
    def aus_umgebung(cls, env: Mapping[str, str] | None = None) -> "Betreiber":
        env = os.environ if env is None else env

        def wert(key: str) -> str:
            return " ".join(env.get(key, "").split())

        anschrift = tuple(z.strip() for z in env.get("BEHOERDEN_BETREIBER_ANSCHRIFT", "").split("|") if z.strip())
        return cls(name=wert("BEHOERDEN_BETREIBER_NAME"), anschrift=anschrift,
                   email=wert("BEHOERDEN_BETREIBER_EMAIL"), telefon=wert("BEHOERDEN_BETREIBER_TELEFON"),
                   verantwortlich=wert("BEHOERDEN_BETREIBER_VERANTWORTLICH"))

    @property
    def vollstaendig(self) -> bool:
        return bool(self.name and self.anschrift and self.email)

    @property
    def fehlend(self) -> list[str]:
        return [k for k, v in zip(PFLICHT, (self.name, self.anschrift, self.email)) if not v]


_SEITE = """<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{titel} – BehoerdenMaster</title>
  <style>
    body {{ margin:0; font:15px/1.6 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; color:#1d2433; background:#f6f8fb; }}
    main {{ max-width:760px; margin:0 auto; padding:24px 16px 60px; }}
    h1 {{ font-size:24px; }} h2 {{ font-size:18px; margin-top:28px; }}
    .hinweis {{ background:#fff7e0; border:1px solid #f1d58a; border-radius:8px; padding:10px 14px; color:#6b4b00; }}
    .fehlt {{ background:#fdf1f1; border:1px solid #eec3c3; border-radius:8px; padding:10px 14px; color:#7d2b2b; }}
    a {{ color:#1f5fbf; }}
  </style>
</head>
<body><main>
<p><a href="/">← zurück zur Suche</a></p>
<h1>{titel}</h1>
{inhalt}
</main></body></html>
"""


def _e(text: str) -> str:
    return html.escape(text, quote=True)


def _kontakt(b: Betreiber) -> str:
    teile = [f"<p><strong>{_e(b.name)}</strong><br>" + "<br>".join(_e(z) for z in b.anschrift) + "</p>"]
    zeilen = []
    if b.email:
        zeilen.append(f'E-Mail: <a href="mailto:{_e(b.email)}">{_e(b.email)}</a>')
    if b.telefon:
        zeilen.append(f"Telefon: {_e(b.telefon)}")
    if zeilen:
        teile.append("<p>" + "<br>".join(zeilen) + "</p>")
    return "\n".join(teile)


def impressum(b: Betreiber) -> str:
    if not b.vollstaendig:
        inhalt = (
            '<div class="fehlt"><strong>Die Angaben zum Betreiber sind noch nicht hinterlegt.</strong> '
            "Für einen öffentlichen Betrieb müssen sie vor dem Start ergänzt werden (Umgebungswerte "
            f"{', '.join(_e(k) for k in b.fehlend)}).</div>"
        )
        if b.name or b.email:
            inhalt += _kontakt(b)
    else:
        inhalt = "<h2>Angaben zum Betreiber</h2>\n" + _kontakt(b)
        if b.verantwortlich:
            inhalt += f"<h2>Inhaltlich verantwortlich</h2><p>{_e(b.verantwortlich)}</p>"
    inhalt += (
        "<h2>Inhalte und Quellen</h2>"
        "<p>BehoerdenMaster durchsucht öffentlich zugängliche Informationen von Kommunen "
        "(Ratsinformationssysteme, Bauleitplanung, Bekanntmachungen) und verlinkt die Originale. "
        "Zusammenfassungen und Statusangaben werden automatisch abgeleitet und sind ohne Gewähr; "
        "maßgeblich sind die amtlichen Originale. Für Inhalte verlinkter Seiten sind deren Betreiber "
        "verantwortlich.</p>"
        "<p>Ortsbezüge nutzen Daten von © OpenStreetMap-Mitwirkende (ODbL 1.0).</p>"
        '<p class="hinweis">Vorlage – vor einem öffentlichen oder kommerziellen Betrieb rechtlich prüfen lassen.</p>'
    )
    return _SEITE.format(titel="Impressum", inhalt=inhalt)


def datenschutz(b: Betreiber) -> str:
    verantwortlich = _kontakt(b) if b.vollstaendig else (
        '<div class="fehlt">Die Angaben zur verantwortlichen Stelle sind noch nicht hinterlegt '
        "(siehe Impressum).</div>")
    inhalt = f"""
<p class="hinweis">Diese Hinweise beschreiben, was die Anwendung technisch tut. Sie sind eine
Vorlage und ersetzen keine Rechtsberatung.</p>
<h2>Verantwortliche Stelle</h2>
{verantwortlich}
<h2>Keine Konten, keine Cookies, kein Tracking</h2>
<p>Die Suche funktioniert ohne Anmeldung. Die Anwendung setzt keine Cookies, bindet keine
Analyse- oder Werbedienste ein und lädt keine Inhalte von Drittservern (keine externen
Schriftarten oder Skripte).</p>
<p>Im Browser wird lediglich die zuletzt gewählte Stadt im lokalen Speicher (<code>localStorage</code>)
abgelegt, damit sie beim nächsten Besuch vorausgewählt ist. Diese Angabe verlässt das Gerät nicht
und kann über die Browsereinstellungen gelöscht werden.</p>
<h2>Suchanfragen</h2>
<p>Suchanfragen werden verarbeitet, um Ergebnisse aus der lokalen Datenbank zu liefern. Die
Anwendung speichert weder Suchbegriffe noch IP-Adressen. Wird eine Stadt angefordert, die erst auf
Abruf geladen wird, speichert sie nur den Namen der Stadt, den Zeitpunkt und die Anzahl der
Anforderungen – keine Angaben zur anfragenden Person.</p>
<h2>Server-Protokolle des Hosters</h2>
<p>Beim Aufruf verarbeitet der Hosting-Anbieter technisch notwendige Verbindungsdaten (z. B.
IP-Adresse, Zeitpunkt, aufgerufene Adresse) in eigenen Protokollen, um den Betrieb und die
Sicherheit zu gewährleisten (Art. 6 Abs. 1 lit. f DSGVO). Umfang und Speicherdauer richten sich nach
dem eingesetzten Anbieter und sind hier vor dem Start zu ergänzen.</p>
<h2>Inhalte aus öffentlichen Quellen</h2>
<p>Angezeigt werden öffentliche Informationen der Kommunen. Sie können Namen enthalten, etwa von
Antragstellenden oder Mitgliedern von Gremien. Beiträge und Kommentare Dritter aus
Beteiligungsplattformen werden nicht übernommen. Wer eine Angabe entfernt haben möchte, wendet
sich an die verantwortliche Stelle; maßgeblich bleibt die Veröffentlichung der Kommune.</p>
<h2>Ihre Rechte</h2>
<p>Sie haben das Recht auf Auskunft, Berichtigung, Löschung, Einschränkung der Verarbeitung und
Widerspruch (Art. 15–21 DSGVO) sowie auf Beschwerde bei einer Datenschutz-Aufsichtsbehörde.</p>
"""
    return _SEITE.format(titel="Datenschutz", inhalt=inhalt)
