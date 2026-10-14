"""Render routes — M3 (POD-10).

GET  /rendering                       jump to whichever render matters now
GET  /episodes/{id}/render            start-render screen or live progress
POST /episodes/{id}/render/start       create a render_job (may land awaiting
                                       cap confirmation instead of running)
POST /episodes/{id}/render/confirm     explicit confirm-to-proceed over the cap
POST /episodes/{id}/render/cancel      cancel the active job
POST /episodes/{id}/render/hold        pause the running job (finished chunks kept)
POST /episodes/{id}/render/resume      continue a held job
POST /episodes/{id}/render/discard     stop the job and delete its saved chunk audio
GET  /api/episodes/{id}/render/status  JSON for the progress page's polling
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import autopilot, episodes, render
from .web import templates

router = APIRouter()


def _err_redirect(path: str, message: str) -> RedirectResponse:
    return RedirectResponse(url=f"{path}?error={quote(message)}", status_code=303)


def _back(request: Request, episode_id: int) -> str:
    """Where a render control returns to: the page it was pressed on (the
    queued episode's render page shows the active job's controls too)."""
    back = request.query_params.get("back", "")
    if (back == "/episodes" or back.startswith(("/episodes/", "/episodes?"))) and "//" not in back:
        return back
    return f"/episodes/{episode_id}/render"


def _job_action(request: Request, episode_id: int, action, missing: str) -> RedirectResponse:
    back = _back(request, episode_id)
    job = render.get_latest_job_for_episode(episode_id)
    if job is None:
        return _err_redirect(back, missing)
    try:
        action(job["id"])
    except render.RenderError as exc:
        return _err_redirect(back, str(exc))
    return RedirectResponse(url=back, status_code=303)


def rendering_target_episode_id() -> int | None:
    """The episode the Rendering button should open: the render running
    right now, else the next upload waiting in the queue, else the most
    recent render of any kind."""
    active = render.get_system_active_job()
    if active is not None:
        return int(active["episode_id"])
    queue = autopilot.waiting_queue()
    if queue:
        return queue[0]
    latest = render.get_latest_job()
    return int(latest["episode_id"]) if latest else None


@router.get("/rendering")
def rendering_shortcut():
    episode_id = rendering_target_episode_id()
    if episode_id is None:
        return RedirectResponse(url="/upload", status_code=303)
    return RedirectResponse(url=f"/episodes/{episode_id}/render", status_code=303)


@router.get("/episodes/{episode_id}/render", response_class=HTMLResponse)
def render_screen(request: Request, episode_id: int):
    episode = episodes.get_episode(episode_id)
    if episode is None:
        return HTMLResponse(f"Episode {episode_id} not found.", status_code=404)

    job = render.get_latest_job_for_episode(episode_id)
    active_job_is_this_episode = job is not None and job["status"] in ("running", "awaiting_cap_confirmation", "held")
    system_active = render.get_system_active_job()
    blocked_by_other = (
        system_active is not None
        and system_active["episode_id"] != episode_id
    )

    queue_position = None
    active_episode = None
    if blocked_by_other:
        active_episode = episodes.get_episode(system_active["episode_id"])
        queue = autopilot.waiting_queue()
        if episode_id in queue:
            queue_position = queue.index(episode_id) + 1

    preview = None
    error = request.query_params.get("error")
    if job is not None and job["status"] == "discarded":
        job = None  # its audio is gone; the episode starts over
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
            "active_episode": active_episode,
            "queue_position": queue_position,
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
def cancel_render(request: Request, episode_id: int):
    return _job_action(request, episode_id, render.cancel_render_job, "No render job to cancel.")


@router.post("/episodes/{episode_id}/render/hold")
def hold_render(request: Request, episode_id: int):
    return _job_action(request, episode_id, render.hold_render_job, "No render job to put on hold.")


@router.post("/episodes/{episode_id}/render/resume")
def resume_render(request: Request, episode_id: int):
    return _job_action(request, episode_id, render.resume_held_job, "No render job to resume.")


@router.post("/episodes/{episode_id}/render/discard")
def discard_render(request: Request, episode_id: int):
    return _job_action(request, episode_id, render.discard_render_job, "No render job to discard.")


@router.get("/api/episodes/{episode_id}/render/status")
def render_status(episode_id: int) -> JSONResponse:
    job = render.get_latest_job_for_episode(episode_id)
    if job is None:
        return JSONResponse({"job": None})
    progress = render.job_progress(job["id"])
    episode = episodes.get_episode(episode_id)
    progress["episode"] = {"id": episode_id, "title": episode["title"] if episode else ""}
    return JSONResponse(progress)
