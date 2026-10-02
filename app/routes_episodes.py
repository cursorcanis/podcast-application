"""Routes for episode intake, the Board source gate, and script review (M2).

Every mutating route (create episode, add/decide a source, close the gate,
save/ready the script) validates through app/episodes.py and, on rejection,
redirects back to the originating page with the exact validation message in
an `error` query parameter rather than a generic failure — there is no
session/flash mechanism in this app, so the message rides in the redirect
URL and the GET handler below renders it if present.
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import episodes
from .config import config
from .web import templates

router = APIRouter()


def _err_redirect(path: str, message: str) -> RedirectResponse:
    return RedirectResponse(url=f"{path}?error={quote(message)}", status_code=303)


@router.get("/episodes", response_class=HTMLResponse)
def episodes_library(request: Request):
    status_filter = request.query_params.get("status") or None
    return templates.TemplateResponse(
        request=request,
        name="episodes_list.html",
        context={
            "episodes": episodes.list_episodes(status_filter),
            "status_filter": status_filter,
        },
    )


@router.get("/episodes/new", response_class=HTMLResponse)
def new_episode_form(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="episode_new.html",
        context={
            "error": request.query_params.get("error"),
            "default_recipients": config.default_recipients,
            "allowed_recipients": episodes.allowed_recipient_emails(),
            "min_speed": episodes.MIN_SPEED,
            "max_speed": episodes.MAX_SPEED,
            "default_speed": episodes.DEFAULT_SPEED,
            "default_length": episodes.DEFAULT_LENGTH_MINUTES,
            "min_length": episodes.MIN_LENGTH_MINUTES,
            "max_length": episodes.MAX_LENGTH_MINUTES,
            "valid_formats": episodes.VALID_FORMATS,
            "valid_audience_levels": episodes.VALID_AUDIENCE_LEVELS,
        },
    )


@router.post("/episodes")
def create_episode(
    title: str = Form(...),
    topic_or_query: str = Form(...),
    format: str = Form(episodes.DEFAULT_FORMAT),
    tone: str = Form(""),
    speed: str = Form(""),
    cadence: str = Form(""),
    audience_level: str = Form(""),
    length_minutes_target: str = Form(""),
    voice_1: str = Form(""),
    voice_2: str = Form(""),
    recipient_emails: list[str] = Form([]),
):
    voices = [v for v in (voice_1, voice_2) if v and v.strip()]
    try:
        episode_id = episodes.create_episode(
            episodes.EpisodeInput(
                title=title,
                topic_or_query=topic_or_query,
                format=format,
                tone=tone,
                speed=speed,
                cadence=cadence,
                audience_level=audience_level,
                length_minutes_target=length_minutes_target,
                voices=voices,
                recipient_emails=recipient_emails,
            )
        )
    except episodes.ValidationError as exc:
        return _err_redirect("/episodes/new", str(exc))
    return RedirectResponse(url=f"/episodes/{episode_id}/sources", status_code=303)


@router.get("/episodes/{episode_id}", response_class=HTMLResponse)
def episode_detail(request: Request, episode_id: int):
    episode = episodes.get_episode(episode_id)
    if episode is None:
        return HTMLResponse(f"Episode {episode_id} not found.", status_code=404)
    sources = episodes.list_sources(episode_id)
    docs = episodes.latest_episode_documents(episode_id)
    return templates.TemplateResponse(
        request=request,
        name="episode_detail.html",
        context={
            "episode": episode,
            "sources": sources,
            "gate": episodes.gate_status(episode_id, sources),
            "docs": docs,
        },
    )


@router.get("/episodes/{episode_id}/sources", response_class=HTMLResponse)
def episode_sources(request: Request, episode_id: int):
    episode = episodes.get_episode(episode_id)
    if episode is None:
        return HTMLResponse(f"Episode {episode_id} not found.", status_code=404)
    sources = episodes.list_sources(episode_id)
    return templates.TemplateResponse(
        request=request,
        name="episode_sources.html",
        context={
            "episode": episode,
            "sources": sources,
            "gate": episodes.gate_status(episode_id, sources),
            "gate_open": episode["status"] == "sources_pending_approval",
            "error": request.query_params.get("error"),
        },
    )


@router.post("/episodes/{episode_id}/sources/add")
def add_source(
    episode_id: int,
    title: str = Form(...),
    publication: str = Form(""),
    published_at: str = Form(""),
    url: str = Form(""),
    summary: str = Form(""),
    credibility_note: str = Form(""),
):
    try:
        episodes.add_source(
            episode_id,
            {
                "title": title,
                "publication": publication,
                "published_at": published_at,
                "url": url,
                "summary": summary,
                "credibility_note": credibility_note,
            },
        )
    except episodes.ValidationError as exc:
        return _err_redirect(f"/episodes/{episode_id}/sources", str(exc))
    return RedirectResponse(url=f"/episodes/{episode_id}/sources", status_code=303)


@router.post("/episodes/{episode_id}/sources/{source_id}/decide")
def decide_source(episode_id: int, source_id: int, decision: str = Form(...)):
    try:
        episodes.decide_source(episode_id, source_id, decision)
    except episodes.ValidationError as exc:
        return _err_redirect(f"/episodes/{episode_id}/sources", str(exc))
    return RedirectResponse(url=f"/episodes/{episode_id}/sources", status_code=303)


@router.post("/episodes/{episode_id}/sources/close")
def close_source_gate(episode_id: int):
    try:
        episodes.close_source_gate(episode_id)
    except episodes.ValidationError as exc:
        return _err_redirect(f"/episodes/{episode_id}/sources", str(exc))
    return RedirectResponse(url=f"/episodes/{episode_id}/script", status_code=303)


@router.get("/episodes/{episode_id}/script", response_class=HTMLResponse)
def episode_script(request: Request, episode_id: int):
    episode = episodes.get_episode(episode_id)
    if episode is None:
        return HTMLResponse(f"Episode {episode_id} not found.", status_code=404)
    gate_closed = episode["status"] in ("sources_approved", "script_ready")
    return templates.TemplateResponse(
        request=request,
        name="episode_script.html",
        context={
            "episode": episode,
            "gate_closed": gate_closed,
            "docs": episodes.latest_episode_documents(episode_id),
            "error": request.query_params.get("error"),
        },
    )


@router.post("/episodes/{episode_id}/script/save")
def save_script(
    episode_id: int,
    outline: str = Form(""),
    script: str = Form(""),
    citation_map: str = Form(""),
):
    try:
        episodes.save_script_documents(episode_id, outline, script, citation_map)
    except episodes.ValidationError as exc:
        return _err_redirect(f"/episodes/{episode_id}/script", str(exc))
    return RedirectResponse(url=f"/episodes/{episode_id}/script", status_code=303)


@router.post("/episodes/{episode_id}/script/ready")
def ready_script(episode_id: int):
    try:
        episodes.mark_script_ready(episode_id)
    except episodes.ValidationError as exc:
        return _err_redirect(f"/episodes/{episode_id}/script", str(exc))
    return RedirectResponse(url=f"/episodes/{episode_id}", status_code=303)
