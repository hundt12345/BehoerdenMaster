"""Höflicher HTTP-Abruf für Behördenquellen.

Leitplanken (bewusst streng, weil Behörden-Server Clients mit zu vielen
Anfragen sperren können):

* identifizierbarer User-Agent mit Kontaktadresse,
* robots.txt nach RFC 9309 wird vor dem Abruf geprüft (inkl. Crawl-delay),
* Mindestabstand zwischen Anfragen an dieselbe Quelle (Standard 3 s),
* bei 429/5xx: wachsende Wartezeiten, Retry-After wird beachtet,
* bei 401/403 sofort Abbruch – es wird nicht versucht, die Sperre zu umgehen,
* ein Anfragebudget pro Lauf, damit große Abrufe in kleinen Schritten erfolgen.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable
from urllib.parse import unquote, urljoin, urlsplit

import httpx
from protego import Protego


class AbrufFehler(Exception):
    def __init__(self, meldung: str, *, retry_after: float | None = None):
        super().__init__(meldung)
        self.retry_after = retry_after


class QuelleGesperrt(AbrufFehler):
    """Die Quelle lehnt Abrufe ab (401/403 oder 429 trotz Wartezeiten). Lauf sofort beenden."""


class QuelleNichtErreichbar(AbrufFehler):
    """Zeitüberschreitung oder Serverfehler trotz Wiederholungen."""


class NichtErlaubt(Exception):
    """robots.txt untersagt diesen Abruf."""


class BudgetErschoepft(Exception):
    """Das Anfragebudget dieses Laufs ist aufgebraucht; der Fortschritt ist gespeichert."""


class ObjektNichtVorhanden(Exception):
    """404/410: Objekt existiert nicht (mehr). Kein Fehler des Laufs."""


PRODUKT_TOKEN = "BehoerdenMaster"


def user_agent(kontakt: str) -> str:
    return f"{PRODUKT_TOKEN}/0.1 (+https://github.com/hundt12345/BehoerdenMaster; Kontakt: {kontakt})"


def _retry_after(resp: httpx.Response) -> float | None:
    wert = resp.headers.get("Retry-After")
    if not wert:
        return None
    if wert.isdigit():
        return float(wert)
    try:
        datum = parsedate_to_datetime(wert)
        if datum.tzinfo is None:
            datum = datum.replace(tzinfo=timezone.utc)
        return max(0.0, (datum - datetime.now(timezone.utc)).total_seconds())
    except (ValueError, TypeError, OverflowError):
        return None


@dataclass
class Abrufer:
    kontakt: str
    min_intervall: float = 3.0
    timeout: float = 30.0
    wiederholungen: int = 2
    wartezeiten: tuple[float, ...] = (60.0, 180.0)
    max_anfragen: int | None = None
    client: httpx.Client | None = None
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    anfragen: int = 0
    _letzte: float = field(default=-1e9, init=False)
    _robots: dict[str, Protego] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        if self.client is None:
            self.client = httpx.Client(
                timeout=self.timeout,
                follow_redirects=True,
                headers={
                    "User-Agent": user_agent(self.kontakt),
                    "Accept": "application/json;q=1.0, text/plain;q=0.5, */*;q=0.1",
                },
            )

    # --- Drosselung -------------------------------------------------------

    def _warte(self, mindestens: float) -> None:
        frist = self._letzte + max(self.min_intervall, mindestens)
        jetzt = self.clock()
        if jetzt < frist:
            self.sleep(frist - jetzt)
        self._letzte = self.clock()

    def _zaehle(self) -> None:
        if self.max_anfragen is not None and self.anfragen >= self.max_anfragen:
            raise BudgetErschoepft(f"Anfragebudget von {self.max_anfragen} erreicht")
        self.anfragen += 1

    # --- robots.txt -------------------------------------------------------

    def _regeln(self, url: str) -> Protego:
        teile = urlsplit(url)
        origin = f"{teile.scheme}://{teile.netloc}"
        if origin not in self._robots:
            try:
                self._zaehle()
                self._warte(0.0)
                resp = self.client.get(urljoin(origin, "/robots.txt"), follow_redirects=False,
                                       headers={"User-Agent": user_agent(self.kontakt)})
            except httpx.TransportError as exc:
                raise QuelleNichtErreichbar(f"robots.txt nicht abrufbar: {exc}") from exc
            if resp.status_code in (401, 403, 429):
                raise QuelleGesperrt(f"robots.txt liefert HTTP {resp.status_code}: Lauf beendet",
                                    retry_after=_retry_after(resp))
            if resp.status_code == 200:
                text = resp.text
            elif 400 <= resp.status_code < 500:
                text = ""  # RFC 9309: 4xx bedeutet keine Einschränkungen
            else:
                raise QuelleNichtErreichbar(f"robots.txt liefert HTTP {resp.status_code}")
            self._robots[origin] = Protego.parse(text)
        return self._robots[origin]

    def _pruefe_robots(self, url: str) -> float:
        regeln = self._regeln(url)
        if not regeln.can_fetch(url, PRODUKT_TOKEN):
            raise NichtErlaubt(f"robots.txt untersagt den Abruf von {url}")
        delay = regeln.crawl_delay(PRODUKT_TOKEN) or regeln.crawl_delay("*") or 0.0
        return float(delay)

    # --- Abruf ------------------------------------------------------------

    def get_json(self, url: str, params: dict[str, str] | None = None) -> dict:
        crawl_delay = self._pruefe_robots(url)
        resp = self._mit_wiederholung(url, params, crawl_delay)
        try:
            return resp.json()
        except ValueError as exc:
            raise QuelleNichtErreichbar(f"Antwort von {url} ist kein gültiges JSON") from exc

    def get_text(self, url: str) -> str:
        """Öffentliche HTML-Detailseite; dieselben Schutzregeln wie für JSON."""
        delay = self._pruefe_robots(url)
        return self._mit_wiederholung(url, None, delay).text

    def _mit_wiederholung(self, url: str, params: dict[str, str] | None, crawl_delay: float) -> httpx.Response:
        versuch = 0
        redirects = 0
        while True:
            self._zaehle()
            self._warte(crawl_delay)
            try:
                # Jede Weiterleitung wird einzeln geprüft, gedrosselt und gezählt.
                resp = self.client.get(url, params=params, follow_redirects=False,
                                       headers={"User-Agent": user_agent(self.kontakt)})
            except httpx.TransportError as exc:
                if versuch < self.wiederholungen:
                    self.sleep(self.wartezeiten[min(versuch, len(self.wartezeiten) - 1)])
                    versuch += 1
                    continue
                raise QuelleNichtErreichbar(f"Keine Antwort von {url}: {exc}") from exc

            code = resp.status_code
            if code in (301, 302, 303, 307, 308):
                ziel = urljoin(str(resp.url), resp.headers.get("Location", ""))
                teile = urlsplit(ziel)
                if any(p in unquote(teile.path).lower().split("/") for p in ("login", "anmelden", "signin")):
                    raise QuelleGesperrt("Weiterleitung zur Anmeldung: keine weiteren Abrufe")
                if (teile.scheme, teile.netloc) != (urlsplit(url).scheme, urlsplit(url).netloc):
                    raise QuelleNichtErreichbar("Weiterleitung auf einen anderen Host wird nicht verfolgt")
                redirects += 1
                if redirects > 5 or not resp.headers.get("Location"):
                    raise QuelleNichtErreichbar("Ungültige oder zu viele Weiterleitungen")
                url, params = ziel, None
                crawl_delay = self._pruefe_robots(url)
                continue
            if code == 200:
                return resp
            if code in (404, 410):
                raise ObjektNichtVorhanden(f"HTTP {code} für {url}")
            if code in (401, 403):
                raise QuelleGesperrt(f"HTTP {code} für {url}: Abruf wird nicht fortgesetzt",
                                    retry_after=_retry_after(resp))
            if code in (429, 500, 502, 503, 504):
                if versuch < self.wiederholungen:
                    warte = max(_retry_after(resp) or 0.0, self.wartezeiten[min(versuch, len(self.wartezeiten) - 1)])
                    if warte > 6 * 3600:
                        raise QuelleNichtErreichbar(f"Quelle verlangt eine Pause von {warte:.0f} s; Lauf beendet",
                                                    retry_after=warte)
                    self.sleep(warte)
                    versuch += 1
                    continue
                if code == 429:
                    raise QuelleGesperrt(f"HTTP 429 (zu viele Anfragen) trotz Wartezeiten für {url}",
                                        retry_after=_retry_after(resp))
                raise QuelleNichtErreichbar(f"HTTP {code} trotz Wiederholungen für {url}",
                                            retry_after=_retry_after(resp))
            raise QuelleNichtErreichbar(f"Unerwartete Antwort HTTP {code} für {url}")

    def schliessen(self) -> None:
        if self.client is not None:
            self.client.close()
