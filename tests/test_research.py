"""M7 research: search parsing, report generation, run state, and routes.

All network access is stubbed: `research._http_get` for the searches and
page fetches, `httpx.get`/`httpx.post` inside research for LM Studio. The
DB is the suite-wide scratch file from conftest.py.
"""
from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

import app as app_module
from app import episodes, research
from app.config import config

DDG_PAGE = """
<div class="result"><a rel="nofollow" class="result__a"
  href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fstory&amp;rut=abc">An <b>example</b> story</a>
  <a class="result__snippet" href="x">Snippet &amp; more</a></div>
<div class="result"><a rel="nofollow" class="result__a"
  href="https://duckduckgo.com/y.js?ad_provider=x">Sponsored</a>
  <a class="result__snippet" href="x">Buy now</a></div>
<div class="result"><a rel="nofollow" class="result__a"
  href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fnews.example.com%2Fa%3Fid%3D1">Second</a></div>
"""

WIKI_SEARCH = {"query": {"search": [{"title": "Example topic", "snippet": "<span>About</span> it"}]}}
WIKI_EXTRACT = {"query": {"pages": {"1": {"extract": "Full plain-text article."}}}}
ARXIV_FEED = """<feed><entry><id>http://arxiv.org/abs/2401.00001v1</id>
<title>A  Paper &amp; Title</title><summary>  The abstract
text.  </summary></entry></feed>"""
ARTICLE_HTML = (
    "<html><head><style>body{}</style><script>var x=1;</script></head><body>"
    "<nav>Home | About | Contact | Subscribe to our newsletter today</nav>"
    "<p>This is the first real paragraph of the article, long enough to keep.</p>"
    "<p>Short.</p></body></html>"
)


def _resp(url, *, text=None, json_body=None, status=200, ctype="text/html"):
    req = httpx.Request("GET", url)
    if json_body is not None:
        return httpx.Response(status, json=json_body, request=req)
    return httpx.Response(status, text=text or "", headers={"content-type": ctype}, request=req)


def fake_web(url, params=None, api=False):
    params = params or {}
    if "duckduckgo" in url:
        return _resp(url, text=DDG_PAGE)
    if "wikipedia" in url:
        return _resp(url, json_body=WIKI_EXTRACT if params.get("prop") == "extracts" else WIKI_SEARCH)
    if "arxiv" in url:
        return _resp(url, text=ARXIV_FEED, ctype="application/atom+xml")
    return _resp(url, text=ARTICLE_HTML)


class FakeLM:
    """Stands in for LM Studio's /models and /chat/completions."""

    def __init__(self, content="<think>plan</think>## Summary\n\nThe report body."):
        self.content = content
        self.prompts: list[str] = []

    def get(self, url, **kw):
        return _resp(url, json_body={"data": [{"id": "qwen-test"}]})

    def post(self, url, json=None, **kw):
        assert json["model"] == "qwen-test"
        self.prompts.append(json["messages"][-1]["content"])
        return _resp(url, json_body={"choices": [{"message": {"content": self.content}}]})


@pytest.fixture()
def web(monkeypatch):
    monkeypatch.setattr(research, "_http_get", fake_web)


@pytest.fixture()
def lm(monkeypatch):
    fake = FakeLM()
    monkeypatch.setattr(research.httpx, "get", fake.get)
    monkeypatch.setattr(research.httpx, "post", fake.post)
    monkeypatch.setitem(config.values, "LM_STUDIO_MODEL", "")
    return fake


def _episode() -> int:
    return episodes.create_episode(episodes.EpisodeInput(
        title="Research Test", topic_or_query="example topic",
        recipient_emails=[config.default_recipients[0]],
    ))


def test_duckduckgo_unwraps_redirects_and_skips_ads(web):
    results = research._duckduckgo_search("q")
    assert [r.url for r in results] == ["https://example.org/story", "https://news.example.com/a?id=1"]
    assert results[0].title == "An example story"
    assert results[0].snippet == "Snippet & more"
    assert results[1].snippet == ""  # no snippet: doesn't borrow the next result's


def test_wikipedia_and_arxiv_parse(web):
    wiki = research._wikipedia_search("q")
    assert wiki[0].url == "https://en.wikipedia.org/wiki/Example_topic"
    assert wiki[0].content == "Full plain-text article."
    paper = research._arxiv_search("q")[0]
    assert paper.title == "A Paper & Title"
    assert paper.url == "http://arxiv.org/abs/2401.00001v1"
    assert paper.content == "[Abstract] The abstract text."


def test_html_to_text_drops_chrome():
    text = research.html_to_text(ARTICLE_HTML)
    assert text == "This is the first real paragraph of the article, long enough to keep."


def test_search_merges_ranks_and_scrapes(web):
    results = research.search("q")
    assert [r.source for r in results] == ["wikipedia", "duckduckgo", "duckduckgo", "arxiv"]
    assert results[1].content.startswith("This is the first real paragraph")


def test_search_failure_is_empty_not_crash(monkeypatch):
    def down(url, params=None, api=False):
        raise httpx.ConnectError("offline")
    monkeypatch.setattr(research, "_http_get", down)
    assert research.search("q") == []


def test_generate_report_strips_reasoning_and_cites_sources(web, lm):
    report = research.generate_report("example topic", research.search("example topic"))
    assert report.report_text == "## Summary\n\nThe report body."
    assert "Full plain-text article." in lm.prompts[0]
    assert {s["source"] for s in report.sources_used} == {"arxiv", "wikipedia", "duckduckgo"}


def test_generate_report_refuses_without_sources(lm):
    with pytest.raises(research.ResearchError, match="No sources"):
        research.generate_report("q", [])
    assert lm.prompts == []


def test_lm_studio_down_is_a_clear_error(monkeypatch):
    monkeypatch.setitem(config.values, "LM_STUDIO_MODEL", "")
    def refused(url, **kw):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(research.httpx, "get", refused)
    result = research.SearchResult("t", "u", "s", "duckduckgo")
    with pytest.raises(research.ResearchError, match="LM Studio isn't reachable"):
        research.generate_report("q", [result])


def test_run_success_stores_report_and_leaves_status_alone(web, lm):
    ep = _episode()
    before = episodes.get_episode(ep)["status"]
    research.run(ep, "example topic")
    status = research.get_status(ep)
    assert status["in_progress"] is False
    assert status["report"] == "## Summary\n\nThe report body."
    assert status["error"] is None
    assert len(status["sources"]) == 4
    assert episodes.get_episode(ep)["status"] == before


def test_run_failure_records_error_and_clears_marker(monkeypatch):
    ep = _episode()
    monkeypatch.setattr(research, "search", lambda q, n_sources=15: [])
    research._write(ep, "research_in_progress", "{}")
    research.run(ep, "example topic")
    status = research.get_status(ep)
    assert status["in_progress"] is False
    assert "No sources" in status["error"]


def test_newer_report_hides_older_error(web, lm):
    ep = _episode()
    research._write(ep, "research_error", "old failure")
    research.run(ep, "example topic")
    assert research.get_status(ep)["error"] is None


def test_stale_marker_does_not_block(monkeypatch):
    ep = _episode()
    conn = research.get_connection()
    conn.execute(
        "INSERT INTO episode_documents (episode_id, kind, body, created_at) VALUES (?, 'research_in_progress', '{}', '2020-01-01T00:00:00+00:00')",
        (ep,),
    )
    conn.commit()
    conn.close()
    assert research.get_status(ep)["in_progress"] is False

    started = []
    monkeypatch.setattr(research.threading, "Thread", lambda target, args, daemon: type(
        "T", (), {"start": lambda self: started.append(args)})())
    assert research.start(ep, "topic") is True
    assert research.get_status(ep)["in_progress"] is True
    assert research.start(ep, "topic") is False  # second click while running
    assert started == [(ep, "topic")]


def test_routes_page_run_status_download(web, lm, monkeypatch):
    # Run synchronously so the redirect lands on a finished report.
    monkeypatch.setattr(research.threading, "Thread", lambda target, args, daemon: type(
        "T", (), {"start": lambda self: target(*args)})())
    ep = _episode()
    with TestClient(app_module.app) as client:
        page = client.get(f"/episodes/{ep}/research")
        assert page.status_code == 200 and "Run research" in page.text
        assert f'href="/episodes/{ep}/research"' in client.get(f"/episodes/{ep}").text

        assert client.get(f"/episodes/{ep}/research/report.md").status_code == 404

        resp = client.post(f"/episodes/{ep}/research/run", data={"query": "example topic"})
        assert resp.status_code == 200 and "The report body." in resp.text

        api = client.get(f"/api/episodes/{ep}/research").json()
        assert api == {"in_progress": False, "report_available": True, "word_count": 5, "error": None}

        md = client.get(f"/episodes/{ep}/research/report.md")
        assert md.status_code == 200
        assert 'filename="research-test-research.md"' in md.headers["content-disposition"]
        assert "## Sources" in md.text and "https://example.org/story" in md.text

        blank = client.post(f"/episodes/{ep}/research/run", data={"query": "   "}, follow_redirects=False)
        assert blank.status_code == 303  # falls back to the episode topic

        assert client.get("/episodes/9999/research").status_code == 404
