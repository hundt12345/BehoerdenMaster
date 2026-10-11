import httpx
import pytest

from behoerdenmaster.abruf import (
    Abrufer, BudgetErschoepft, NichtErlaubt, ObjektNichtVorhanden, QuelleGesperrt,
    QuelleNichtErreichbar, user_agent,
)

URL = "https://example.test/oparl/papers"


def _abrufer(uhr, handler, **kw):
    kw.setdefault("min_intervall", 3.0)
    return Abrufer(kontakt="test@example.test", sleep=uhr.schlaf, clock=uhr,
                   client=httpx.Client(transport=httpx.MockTransport(handler)), **kw)


def test_user_agent_nennt_kontakt():
    ua = user_agent("it@stadt.example")
    assert ua.startswith("BehoerdenMaster/") and "it@stadt.example" in ua and "github.com" in ua


def test_mindestabstand_zwischen_anfragen(uhr):
    seen = []
    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={"data": []})
    a = _abrufer(uhr, handler)
    a.get_json(URL)
    a.get_json(URL)
    # Erster Abruf ohne Wartezeit (robots + Liste), danach jeweils mindestens 3 s Abstand.
    assert uhr.schlaefe, "es muss gewartet worden sein"
    assert all(s >= 0 for s in uhr.schlaefe)
    assert max(uhr.schlaefe) <= 3.0 + 1e-9


def test_retry_after_wird_beachtet(uhr):
    antworten = [httpx.Response(503, headers={"Retry-After": "120"}), httpx.Response(200, json={"data": []})]
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return antworten.pop(0)
    a = _abrufer(uhr, handler)
    assert a.get_json(URL) == {"data": []}
    assert 120.0 in uhr.schlaefe


def test_403_fuehrt_sofort_zum_abbruch_ohne_wiederholung(uhr):
    anfragen = []
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        anfragen.append(req)
        return httpx.Response(403)
    a = _abrufer(uhr, handler)
    with pytest.raises(QuelleGesperrt):
        a.get_json(URL)
    assert len(anfragen) == 1


def test_429_nach_wiederholungen_ist_sperre(uhr):
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(429)
    a = _abrufer(uhr, handler, wiederholungen=2, wartezeiten=(10.0, 20.0))
    with pytest.raises(QuelleGesperrt):
        a.get_json(URL)
    assert 10.0 in uhr.schlaefe and 20.0 in uhr.schlaefe


def test_5xx_nach_wiederholungen_nicht_erreichbar(uhr):
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(502)
    a = _abrufer(uhr, handler, wiederholungen=1, wartezeiten=(5.0,))
    with pytest.raises(QuelleNichtErreichbar):
        a.get_json(URL)


def test_404_ist_objekt_nicht_vorhanden(uhr):
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(404)
    with pytest.raises(ObjektNichtVorhanden):
        _abrufer(uhr, handler).get_json(URL)


def test_robots_txt_verbot_wird_respektiert(uhr):
    anfragen = []
    def handler(req):
        anfragen.append(req.url.path)
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /oparl/\n")
        return httpx.Response(200, json={"data": []})
    with pytest.raises(NichtErlaubt):
        _abrufer(uhr, handler).get_json(URL)
    assert "/oparl/papers" not in anfragen


def test_robots_crawl_delay_erhoeht_abstand(uhr):
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nCrawl-delay: 20\n")
        return httpx.Response(200, json={"data": []})
    a = _abrufer(uhr, handler)
    a.get_json(URL)
    a.get_json(URL)
    assert 20.0 in uhr.schlaefe


def test_budget_begrenzt_anfragen(uhr):
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, json={"data": []})
    a = _abrufer(uhr, handler, max_anfragen=2)
    a.get_json(URL)  # robots + Abruf = 2 Anfragen
    with pytest.raises(BudgetErschoepft):
        a.get_json(URL)


def test_ungueltiges_json_ist_fehler(uhr):
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text="<html>kein JSON</html>")
    with pytest.raises(QuelleNichtErreichbar):
        _abrufer(uhr, handler).get_json(URL)


def test_auch_robots_und_html_tragen_die_kontaktkennung(uhr):
    seen = []
    def handler(req):
        seen.append(req)
        return httpx.Response(200, text="<h1>Detail</h1>" if req.url.path != "/robots.txt" else "User-agent: *\n")
    assert _abrufer(uhr, handler).get_text(URL) == "<h1>Detail</h1>"
    assert all("test@example.test" in req.headers["User-Agent"] for req in seen)


@pytest.mark.parametrize("location,exc", [("/dplan/login", QuelleGesperrt), ("/dplan/%6Cogin", QuelleGesperrt),
                                         ("https://anderer-host.test/detail", QuelleNichtErreichbar)])
def test_login_und_hostwechsel_werden_nicht_verfolgt(uhr, location, exc):
    seen = []
    def handler(req):
        seen.append(req.url.path)
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(302, headers={"Location": location})
    with pytest.raises(exc):
        _abrufer(uhr, handler).get_text(URL)
    assert seen == ["/robots.txt", "/oparl/papers"]


def test_erlaubte_weiterleitung_zaehlt_zum_budget(uhr):
    seen = []
    def handler(req):
        seen.append(req.url.path)
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        if req.url.path == "/oparl/papers":
            return httpx.Response(302, headers={"Location": "/detail"})
        return httpx.Response(200, text="public detail")
    a = _abrufer(uhr, handler)
    assert a.get_text(URL) == "public detail" and a.anfragen == 3
    assert seen == ["/robots.txt", "/oparl/papers", "/detail"]
    assert uhr.schlaefe == [3.0, 3.0]
    with pytest.raises(BudgetErschoepft):
        _abrufer(uhr, handler, max_anfragen=2).get_text(URL)


def test_weitergeleitetes_ziel_wird_erneut_gegen_robots_geprueft(uhr):
    seen = []
    def handler(req):
        seen.append(req.url.path)
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /verboten\n")
        return httpx.Response(302, headers={"Location": "/verboten"})
    with pytest.raises(NichtErlaubt):
        _abrufer(uhr, handler).get_text(URL)
    assert "/verboten" not in seen


@pytest.mark.parametrize("code", [401, 403, 429])
def test_robots_sperre_stoppt_auch_inhaltsabruf(uhr, code):
    seen = []
    def handler(req):
        seen.append(req.url.path)
        return httpx.Response(code)
    with pytest.raises(QuelleGesperrt):
        _abrufer(uhr, handler).get_text(URL)
    assert seen == ["/robots.txt"]


def test_robots_redirect_wird_nicht_ungeprueft_verfolgt(uhr):
    seen = []
    def handler(req):
        seen.append(str(req.url))
        return httpx.Response(302, headers={"Location": "https://fremd.test/robots.txt"})
    with pytest.raises(QuelleNichtErreichbar):
        _abrufer(uhr, handler).get_text(URL)
    assert seen == ["https://example.test/robots.txt"]


def test_retry_after_http_datum_wird_beachtet(uhr, monkeypatch):
    from datetime import datetime, timezone
    import behoerdenmaster.abruf as modul

    class FestesDatum(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(modul, "datetime", FestesDatum)
    antworten = [httpx.Response(503, headers={"Retry-After": "Fri, 09 Oct 2026 12:05:00 GMT"}),
                 httpx.Response(200, json={"data": []})]
    def handler(req):
        return httpx.Response(404) if req.url.path == "/robots.txt" else antworten.pop(0)
    assert _abrufer(uhr, handler).get_json(URL) == {"data": []}
    assert 300.0 in uhr.schlaefe


def test_sehr_langes_retry_after_wird_nicht_auf_sechs_stunden_verkuerzt(uhr):
    def handler(req):
        return (httpx.Response(404) if req.url.path == "/robots.txt" else
                httpx.Response(503, headers={"Retry-After": "86400"}))
    with pytest.raises(QuelleNichtErreichbar, match="Pause"):
        _abrufer(uhr, handler).get_text(URL)
    assert max(uhr.schlaefe) <= 3
