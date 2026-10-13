"""Research routes — M7.

GET  /episodes/{id}/research            topic form, progress, and the latest report
POST /episodes/{id}/research/run        start search + report generation (background)
GET  /episodes/{id}/research/report.md  download the report (+ source list) for /upload
GET  /api/episodes/{id}/research        JSON status for polling
"""
from __future__ import annotations

import re
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse

from . import episodes, research
from .config import config
from .web import templates

router = APIRouter()


@router.get("/episodes/{episode_id}/research", response_class=HTMLResponse)
def episode_research(request: Request, episode_id: int):
    episode = episodes.get_episode(episode_id)
    if episode is None:
        return HTMLResponse(f"Episode {episode_id} not found.", status_code=404)
    return templates.TemplateResponse(
        request=request,
        name="episode_research.html",
        context={
            "episode": episode,
            "status": research.get_status(episode_id),
            "lm_studio_url": config.lm_studio_url,
            "error": request.query_params.get("error"),
        },
    )


@router.post("/episodes/{episode_id}/research/run")
def run_research(episode_id: int, query: str = Form("")):
    episode = episodes.get_episode(episode_id)
    if episode is None:
        return HTMLResponse(f"Episode {episode_id} not found.", status_code=404)
    try:
        research.start(episode_id, query.strip() or episode["topic_or_query"])
    except research.ResearchError as exc:
        return RedirectResponse(
            url=f"/episodes/{episode_id}/research?error={quote(str(exc))}", status_code=303
        )
    # Already running is fine: the page shows that run's progress.
    return RedirectResponse(url=f"/episodes/{episode_id}/research", status_code=303)


@router.get("/episodes/{episode_id}/research/report.md")
def download_report(episode_id: int):
    episode = episodes.get_episode(episode_id)
    status = research.get_status(episode_id) if episode else None
    if not status or not status["report"]:
        return PlainTextResponse("No research report for this episode.", status_code=404)
    body = status["report"].rstrip() + "\n"
    if status["sources"]:
        # Kept out of the narrated body: Upload Script reads everything, so
        # trim this section before uploading if you don't want it voiced.
        body += "\n---\n\n## Sources\n\n" + "".join(
            f"- {s['title']} — {s['url']}\n" for s in status["sources"]
        )
    slug = re.sub(r"[^a-z0-9]+", "-", episode["title"].lower()).strip("-")[:60] or f"episode-{episode_id}"
    return PlainTextResponse(
        body,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{slug}-research.md"'},
    )


@router.get("/api/episodes/{episode_id}/research")
def research_status(episode_id: int) -> JSONResponse:
    if episodes.get_episode(episode_id) is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    status = research.get_status(episode_id)
    return JSONResponse({
        "in_progress": status["in_progress"],
        "report_available": bool(status["report"]),
        "word_count": status["word_count"],
        "error": status["error"],
    })
