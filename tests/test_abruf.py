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
