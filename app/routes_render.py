"""Render routes — M3 (POD-10).

GET  /episodes/{id}/render            start-render screen or live progress
POST /episodes/{id}/render/start       create a render_job (may land awaiting
                                       cap confirmation instead of running)
POST /episodes/{id}/render/confirm     explicit confirm-to-proceed over the cap
POST /episodes/{id}/render/cancel      cancel the active job
GET  /api/episodes/{id}/render/status  JSON for the progress page's polling
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import episodes, render
from .web import templates

router = APIRouter()


def _err_redirect(path: str, message: str) -> RedirectResponse:
    return RedirectResponse(url=f"{path}?error={quote(message)}", status_code=303)


@router.get("/episodes/{episode_id}/render", response_class=HTMLResponse)
def render_screen(request: Request, episode_id: int):
    episode = episodes.get_episode(episode_id)
    if episode is None:
        return HTMLResponse(f"Episode {episode_id} not found.", status_code=404)

    job = render.get_latest_job_for_episode(episode_id)
    active_job_is_this_episode = job is not None and job["status"] in ("running", "awaiting_cap_confirmation")
    system_active = render.get_system_active_job()
    blocked_by_other = (
        system_active is not None
        and system_active["episode_id"] != episode_id
    )

    preview = None
    error = request.query_params.get("error")
    if job is None and not blocked_by_other and episode["status"] == "script_ready":
        try:
            preview = render.dry_run_projection(episode_id)
        except render.RenderError as exc:
            error = str(exc)

    return templates.TemplateResponse(
        request=request,
        name="episode_render.html",
        context={
            "episode": episode,
            "job": job,
            "active_job_is_this_episode": active_job_is_this_episode,
            "blocked_by_other": blocked_by_other,
            "system_active": system_active,
            "preview": preview,
            "error": error,
        },
    )


@router.post("/episodes/{episode_id}/render/start")
def start_render(episode_id: int):
    try:
        render.start_render_job(episode_id)
    except render.RenderError as exc:
        return _err_redirect(f"/episodes/{episode_id}/render", str(exc))
    return RedirectResponse(url=f"/episodes/{episode_id}/render", status_code=303)


@router.post("/episodes/{episode_id}/render/confirm")
def confirm_render(episode_id: int):
    job = render.get_latest_job_for_episode(episode_id)
    if job is None:
        return _err_redirect(f"/episodes/{episode_id}/render", "No render job to confirm.")
    try:
        render.confirm_render_job(job["id"])
    except render.RenderError as exc:
        return _err_redirect(f"/episodes/{episode_id}/render", str(exc))
    return RedirectResponse(url=f"/episodes/{episode_id}/render", status_code=303)


@router.post("/episodes/{episode_id}/render/cancel")
def cancel_render(episode_id: int):
    job = render.get_latest_job_for_episode(episode_id)
    if job is None:
        return _err_redirect(f"/episodes/{episode_id}/render", "No render job to cancel.")
    try:
        render.cancel_render_job(job["id"])
    except render.RenderError as exc:
        return _err_redirect(f"/episodes/{episode_id}/render", str(exc))
    return RedirectResponse(url=f"/episodes/{episode_id}/render", status_code=303)


@router.get("/api/episodes/{episode_id}/render/status")
def render_status(episode_id: int) -> JSONResponse:
    job = render.get_latest_job_for_episode(episode_id)
    if job is None:
        return JSONResponse({"job": None})
    progress = render.job_progress(job["id"])
    return JSONResponse(progress)
