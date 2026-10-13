"""Research module — web search, scraping, and report generation (M7).

Takes an episode's topic, searches DuckDuckGo / Wikipedia / arXiv (public
endpoints, no API keys), pulls readable text from the results, and has the
model loaded in LM Studio write a 4000–6000 word report suitable for
narration. The report is stored on the episode as an `episode_documents`
row (kind `research_report`); it never changes the episode's status. To
voice it, download the report and feed it to Upload Script (/upload).

LM Studio is reached through its OpenAI-compatible API at `LM_STUDIO_URL`
(default `http://127.0.0.1:1234/v1`); `LM_STUDIO_MODEL` picks a model, empty
means whichever one is loaded.

State lives in `episode_documents` under three kinds:
  research_in_progress  marker row while a run is going (deleted when it ends)
  research_report       the report text (latest row wins)
  research_sources      JSON list of the sources the report was built from
  research_error        the error text from a failed run
"""
from __future__ import annotations

import html as html_lib
import json
import re
import threading
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

from .config import config
from .db import get_connection

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PodcastFoundry/1.0"
# Wikimedia 403s browser-like agents without contact info (w.wiki/4wJS);
# the APIs get an honest one.
API_USER_AGENT = "PodcastFoundry/0.7 (https://github.com/cursorcanis/podcast-application)"
SEARCH_TIMEOUT_S = 15
REPORT_TIMEOUT_S = 2700  # ~8k tokens at ~6 tok/s (35B local model) is ~25 min
SCRAPE_TOP_N = 6  # web pages fetched in full; the rest keep their snippet
SOURCE_CHARS = 4000  # per-source cap in the prompt
# A marker older than this is a run that died with the process (app restart).
STALE_AFTER = timedelta(minutes=30)

_RUN_LOCK = threading.Lock()


class ResearchError(Exception):
    """A run can't proceed; the message is shown to the user as-is."""


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class SearchResult:
    """A single search result with its extracted content."""
    title: str
    url: str
    snippet: str
    source: str  # 'duckduckgo', 'wikipedia', 'arxiv'
    content: str = ""  # readable text, when fetched
    relevance_score: float = 0.0


@dataclass
class ResearchReport:
    """The final research report."""
    query: str
    report_text: str
    sources_used: list[dict]
    word_count: int
    generated_at: str


# ---------------------------------------------------------------------------
# HTTP + text helpers
# ---------------------------------------------------------------------------

def _http_get(url: str, params: dict | None = None, *, api: bool = False) -> httpx.Response:
    """Single choke point for outbound GETs (tests monkeypatch this)."""
    return httpx.get(
        url,
        params=params,
        headers={"User-Agent": API_USER_AGENT if api else USER_AGENT},
        timeout=SEARCH_TIMEOUT_S,
        follow_redirects=True,
    )


def _strip_tags(markup: str) -> str:
    text = re.sub(r"<[^>]+>", "", markup)
    return re.sub(r"\s+", " ", html_lib.unescape(text)).strip()


def html_to_text(markup: str) -> str:
    """Readable text from an HTML page: drop script/style/nav chrome, keep
    paragraph breaks, unescape entities."""
    markup = re.sub(
        r"(?is)<(script|style|noscript|svg|nav|header|footer|form)\b.*?</\1>", " ", markup
    )
    markup = re.sub(r"(?i)<(br|/p|/div|/li|/h[1-6]|/tr)\b[^>]*>", "\n", markup)
    text = html_lib.unescape(re.sub(r"<[^>]+>", " ", markup))
    lines = (re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in text.split("\n"))
    # Keep lines that look like prose; menus and buttons are short.
    return "\n".join(line for line in lines if len(line) >= 40)


def _ddg_target(href: str) -> str | None:
    """DDG's HTML results link through //duckduckgo.com/l/?uddg=<real url>."""
    href = html_lib.unescape(href)
    if href.startswith("//"):
        href = "https:" + href
    parsed = urllib.parse.urlparse(href)
    if parsed.netloc.endswith("duckduckgo.com"):
        if parsed.path.startswith("/y.js"):
            return None  # sponsored result
        target = urllib.parse.parse_qs(parsed.query).get("uddg", [None])[0]
        return target if target and target.startswith("http") else None
    return href if href.startswith("http") else None


# ---------------------------------------------------------------------------
# Search functions (all free, no API keys needed)
# ---------------------------------------------------------------------------

def _duckduckgo_search(query: str, max_results: int = 15) -> list[SearchResult]:
    """Search DuckDuckGo's HTML endpoint. Best-effort: [] on any failure."""
    try:
        resp = _http_get("https://html.duckduckgo.com/html/", params={"q": query})
        resp.raise_for_status()
        page = resp.text
    except httpx.HTTPError:
        return []

    links = list(re.finditer(r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', page, re.S))
    results: list[SearchResult] = []
    for i, match in enumerate(links):
        url = _ddg_target(match.group(1))
        if not url:
            continue
        # The snippet sits between this title link and the next one.
        end = links[i + 1].start() if i + 1 < len(links) else len(page)
        snip = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', page[match.end():end], re.S)
        results.append(SearchResult(
            title=_strip_tags(match.group(2)) or url,
            url=url,
            snippet=_strip_tags(snip.group(1)) if snip else "",
            source="duckduckgo",
        ))
        if len(results) >= max_results:
            break
    return results


def _wikipedia_search(query: str, max_results: int = 5) -> list[SearchResult]:
    """Search Wikipedia and pull each article's plain-text extract."""
    api = "https://en.wikipedia.org/w/api.php"
    try:
        resp = _http_get(api, params={
            "action": "query", "list": "search", "srsearch": query,
            "format": "json", "srlimit": max_results,
        }, api=True)
        resp.raise_for_status()
        hits = resp.json().get("query", {}).get("search", [])
    except (httpx.HTTPError, ValueError):
        return []

    results: list[SearchResult] = []
    for item in hits:
        title = item["title"]
        snippet = _strip_tags(item.get("snippet", ""))
        content = snippet
        try:
            page = _http_get(api, params={
                "action": "query", "prop": "extracts", "explaintext": 1,
                "titles": title, "format": "json",
            }, api=True)
            page.raise_for_status()
            pages = page.json().get("query", {}).get("pages", {})
            extract = next(iter(pages.values()), {}).get("extract") or ""
            content = extract or snippet
        except (httpx.HTTPError, ValueError):
            pass
        results.append(SearchResult(
            title=title,
            url="https://en.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_")),
            snippet=snippet,
            source="wikipedia",
            content=content[:8000],
        ))
    return results


def _arxiv_search(query: str, max_results: int = 5) -> list[SearchResult]:
    """Search arXiv (Atom feed) for papers; the abstract is the content."""
    try:
        resp = _http_get("https://export.arxiv.org/api/query", params={
            "search_query": f"all:{query}", "max_results": max_results,
        }, api=True)
        resp.raise_for_status()
        feed = resp.text
    except httpx.HTTPError:
        return []

    results: list[SearchResult] = []
    for entry in re.finditer(r"<entry>(.*?)</entry>", feed, re.S):
        body = entry.group(1)
        title = re.search(r"<title>(.*?)</title>", body, re.S)
        summary = re.search(r"<summary>(.*?)</summary>", body, re.S)
        link = re.search(r"<id>(https?://arxiv\.org/abs/\S+?)</id>", body)
        if not (title and link):
            continue
        abstract = _strip_tags(summary.group(1)) if summary else ""
        results.append(SearchResult(
            title=_strip_tags(title.group(1)),
            url=link.group(1),
            snippet=abstract[:500],
            source="arxiv",
            content=f"[Abstract] {abstract}",
        ))
    return results


def _fetch_page_text(result: SearchResult) -> None:
    """Fill `content` from the result's page; leave it empty on failure."""
    try:
        resp = _http_get(result.url)
        resp.raise_for_status()
    except httpx.HTTPError:
        return
    if "html" not in resp.headers.get("content-type", "html"):
        return  # PDFs and other binaries: keep the snippet
    result.content = html_to_text(resp.text)[:8000]


def search(query: str, n_sources: int = 15) -> list[SearchResult]:
    """Run all searches, fetch the top web pages, and merge the results,
    deduplicated by URL. Wikipedia ranks first, then the web; arXiv last,
    since its keyword search often returns off-topic papers for general
    (non-academic) topics."""
    weighted = (
        (_wikipedia_search(query, max_results=5), 2.5),
        (_duckduckgo_search(query, max_results=n_sources), 1.5),
        (_arxiv_search(query, max_results=3), 1.0),
    )
    merged: list[SearchResult] = []
    seen: set[str] = set()
    for results, score in weighted:
        for r in results:
            if r.url in seen:
                continue
            seen.add(r.url)
            r.relevance_score += score
            merged.append(r)

    for r in [r for r in merged if r.source == "duckduckgo"][:SCRAPE_TOP_N]:
        _fetch_page_text(r)

    merged.sort(key=lambda r: (-r.relevance_score, -len(r.content or r.snippet)))
    return merged[:n_sources]


# ---------------------------------------------------------------------------
# Report generation via LM Studio
# ---------------------------------------------------------------------------

def build_prompt(query: str, results: list[SearchResult]) -> str:
    sources_text = ""
    for i, r in enumerate(results[:12], 1):  # top 12 keeps a local model's context sane
        text = (r.content or r.snippet)[:SOURCE_CHARS]
        sources_text += f"\n--- Source {i}: {r.title} ({r.source}) ---\n{text}\n"

    return f"""**Topic:** {query}

**Instructions:**
- Write a detailed, well-structured research report of approximately 4000–6000 words.
- Organize it with clear sections using markdown headings (## and ###).
- Open with an executive summary (~300-500 words).
- Build the analysis from the sources below, citing them naturally in the text ("according to ...").
- Use only facts the sources support; if they disagree or are thin on a point, say so rather than inventing detail.
- Write in a conversational but authoritative tone suited to being read aloud — no tables, no bullet-point walls, no jargon without explanation.
- End with a "Key Findings" section (~200 words).
- Do NOT put URLs in the report body (sources are listed separately).

**Sources:**
{sources_text}

Write the report now."""


def _strip_reasoning(text: str) -> str:
    """Qwen-style reasoning models wrap their thinking in <think>...</think>."""
    return re.sub(r"(?s)<think>.*?</think>", "", text).strip()


def _lm_model() -> str:
    if config.lm_studio_model:
        return config.lm_studio_model
    try:
        resp = httpx.get(f"{config.lm_studio_url}/models", timeout=5)
        resp.raise_for_status()
        models = resp.json().get("data", [])
    except (httpx.HTTPError, ValueError) as exc:
        raise ResearchError(
            f"LM Studio isn't reachable at {config.lm_studio_url} ({exc}). "
            "Start LM Studio's local server (Developer tab) and load a model."
        ) from exc
    if not models:
        raise ResearchError("LM Studio is running but has no model loaded.")
    return models[0].get("id", "")


def generate_report(query: str, results: list[SearchResult]) -> ResearchReport:
    """Ask the LM Studio model for the report. Raises ResearchError."""
    if not results:
        raise ResearchError(
            "No sources found for this topic (DuckDuckGo, Wikipedia and arXiv all "
            "returned nothing — check the internet connection or reword the topic)."
        )
    model = _lm_model()
    try:
        resp = httpx.post(
            f"{config.lm_studio_url}/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": "You are a research assistant writing thorough, accurate reports that will be narrated as a podcast."},
                    {"role": "user", "content": build_prompt(query, results)},
                ],
                "temperature": 0.7,
                "max_tokens": 10000,
            },
            timeout=REPORT_TIMEOUT_S,
        )
    except httpx.HTTPError as exc:
        raise ResearchError(f"LM Studio request failed: {exc}") from exc
    if resp.status_code != 200:
        raise ResearchError(f"LM Studio returned {resp.status_code}: {resp.text[:500]}")

    try:
        report_text = _strip_reasoning(resp.json()["choices"][0]["message"]["content"] or "")
    except (ValueError, KeyError, IndexError) as exc:
        raise ResearchError(f"LM Studio sent an unexpected response: {resp.text[:300]}") from exc
    if not report_text:
        raise ResearchError("LM Studio returned an empty report.")

    return ResearchReport(
        query=query,
        report_text=report_text,
        sources_used=[{"title": r.title, "url": r.url, "source": r.source} for r in results],
        word_count=len(report_text.split()),
        generated_at=_now(),
    )


# ---------------------------------------------------------------------------
# Run state (episode_documents rows) + background runner
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _latest(conn, episode_id: int, kind: str):
    return conn.execute(
        "SELECT * FROM episode_documents WHERE episode_id = ? AND kind = ? ORDER BY id DESC LIMIT 1",
        (episode_id, kind),
    ).fetchone()


def _marker_is_live(row) -> bool:
    try:
        started = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
    except ValueError:
        return False
    if started.tzinfo is None:  # SQLite datetime('now') default: naive UTC
        started = started.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - started < STALE_AFTER


def get_status(episode_id: int) -> dict:
    """What the research page needs: running?, latest report/sources, and
    an error only if it's newer than the latest report."""
    conn = get_connection()
    try:
        marker = _latest(conn, episode_id, "research_in_progress")
        report = _latest(conn, episode_id, "research_report")
        sources = _latest(conn, episode_id, "research_sources")
        error = _latest(conn, episode_id, "research_error")
    finally:
        conn.close()
    in_progress = bool(marker) and _marker_is_live(marker)
    if error and report and report["id"] > error["id"]:
        error = None
    if error and marker and marker["id"] > error["id"]:
        error = None
    return {
        "in_progress": in_progress,
        "report": report["body"] if report else None,
        "report_created_at": report["created_at"] if report else None,
        "word_count": len(report["body"].split()) if report else 0,
        "sources": json.loads(sources["body"]) if sources else [],
        "error": error["body"] if error else None,
    }


def _write(episode_id: int, kind: str, body: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO episode_documents (episode_id, kind, body, created_at) VALUES (?, ?, ?, ?)",
            (episode_id, kind, body, _now()),
        )
        conn.commit()
    finally:
        conn.close()


def _clear_marker(episode_id: int) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM episode_documents WHERE episode_id = ? AND kind = 'research_in_progress'",
            (episode_id,),
        )
        conn.commit()
    finally:
        conn.close()


def run(episode_id: int, query: str, n_sources: int = 15) -> None:
    """The whole pipeline, synchronously. Always clears the marker."""
    try:
        results = search(query, n_sources=n_sources)
        report = generate_report(query, results)
        _write(episode_id, "research_sources", json.dumps(report.sources_used))
        _write(episode_id, "research_report", report.report_text)
    except ResearchError as exc:
        _write(episode_id, "research_error", str(exc))
    except Exception as exc:  # a background thread has nowhere else to report
        _write(episode_id, "research_error", f"Research failed unexpectedly: {exc!r}")
    finally:
        _clear_marker(episode_id)


def start(episode_id: int, query: str) -> bool:
    """Start a background run. False if one is already running for this
    episode (double-click, second tab)."""
    query = (query or "").strip()
    if not query:
        raise ResearchError("Enter a research topic first.")
    with _RUN_LOCK:
        if get_status(episode_id)["in_progress"]:
            return False
        _clear_marker(episode_id)  # drop a stale one left by a dead process
        _write(episode_id, "research_in_progress", json.dumps({"query": query}))
    threading.Thread(target=run, args=(episode_id, query), daemon=True).start()
    return True
