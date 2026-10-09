"""Upload Script routes — the one-step path.

GET  /upload                              the upload form
POST /upload                              create an autopilot episode from the
                                          uploaded file and start it
POST /episodes/{id}/autopilot/resume      turn autopilot back on after it stopped
GET  /api/episodes/{id}/autopilot         JSON status line for polling
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import autopilot, episodes, render, script_import
from .config import config
from .web import templates

router = APIRouter()


@router.get("/upload", response_class=HTMLResponse)
def upload_form(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="upload.html",
        context={
            "error": request.query_params.get("error"),
            "narrators": autopilot.narrator_choices(),
            "voice_mapping": config.voice_mapping,
            "allowed_recipients": episodes.allowed_recipient_emails(),
            "default_recipients": config.default_recipients,
            "min_speed": episodes.MIN_SPEED,
            "max_speed": episodes.MAX_SPEED,
            "extensions": ", ".join(script_import.SUPPORTED_EXTENSIONS),
            "email_paused": config.email_paused,
            "share_location": config.share_location,
            "active_job": render.get_system_active_job(),
        },
    )


@router.post("/upload")
async def upload_script(
    script_file: UploadFile = File(...),
    title: str = Form(""),
    narrator: str = Form(""),
    speed: str = Form(""),
    recipient_emails: list[str] = Form([]),
):
    data = await script_file.read(script_import.MAX_UPLOAD_BYTES + 1)
    try:
        episode_id = autopilot.create_from_upload(
            filename=script_file.filename or "",
            data=data,
            title=title,
            narrator=narrator,
            speed=speed,
            recipient_emails=recipient_emails,
        )
    except (script_import.ScriptImportError, episodes.ValidationError) as exc:
        return RedirectResponse(url=f"/upload?error={quote(str(exc))}", status_code=303)
    autopilot.start(episode_id)
    return RedirectResponse(url=f"/episodes/{episode_id}", status_code=303)


@router.post("/episodes/{episode_id}/autopilot/resume")
def resume_autopilot(episode_id: int):
    if episodes.get_episode(episode_id) is None:
        return HTMLResponse(f"Episode {episode_id} not found.", status_code=404)
    autopilot.restart(episode_id)
    return RedirectResponse(url=f"/episodes/{episode_id}", status_code=303)


@router.get("/api/episodes/{episode_id}/autopilot")
def autopilot_status(episode_id: int) -> JSONResponse:
    ep = episodes.get_episode(episode_id)
    if ep is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse({
        "status": ep["status"],
        "autopilot": bool(ep.get("autopilot")),
        "note": ep.get("autopilot_note"),
    })
