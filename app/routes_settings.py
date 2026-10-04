"""Routes for the Settings page — M6 (POD-13): voice profiles (with the 30s
sample render), tone/cadence presets, and saved recipient lists.

Every mutating route validates through app/settings.py and redirects back to
/settings with the exact validation message in an `error` query parameter
(same pattern as routes_episodes.py — no session/flash mechanism).
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import settings
from .config import config
from .web import templates

router = APIRouter()


def _err_redirect(message: str) -> RedirectResponse:
    return RedirectResponse(url=f"/settings?error={quote(message)}", status_code=303)


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "error": request.query_params.get("error"),
            "voice_profiles": settings.list_voice_profiles(),
            "tone_presets": settings.list_presets("tone"),
            "cadence_presets": settings.list_presets("cadence"),
            "recipient_lists": settings.list_recipient_lists(),
            "default_recipients": config.default_recipients,
            # Read-only transparency: the Audio Engineer's measured mapping
            # (Board gate) shown so the Board can cross-check a voice
            # profile's comfyui_voice_ref against what a render would use.
            "voice_mapping": config.voice_mapping,
            "sample_states": {
                p["id"]: settings.sample_status(p["id"]) for p in settings.list_voice_profiles()
            },
        },
    )


# --- Voice profiles ------------------------------------------------------------

@router.post("/settings/voice-profiles")
def create_voice_profile(
    name: str = Form(...),
    comfyui_voice_ref: str = Form(""),
    reference_clip_path: str = Form(""),
    notes: str = Form(""),
):
    try:
        settings.create_voice_profile(name, comfyui_voice_ref, reference_clip_path, notes)
    except settings.SettingsError as exc:
        return _err_redirect(str(exc))
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/settings/voice-profiles/{profile_id}/delete")
def delete_voice_profile(profile_id: int):
    try:
        settings.delete_voice_profile(profile_id)
    except settings.SettingsError as exc:
        return _err_redirect(str(exc))
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/settings/voice-profiles/{profile_id}/sample")
def start_voice_sample(profile_id: int):
    try:
        settings.start_sample_render(profile_id)
    except settings.SettingsError as exc:
        return _err_redirect(str(exc))
    return RedirectResponse(url="/settings", status_code=303)


@router.get("/settings/voice-profiles/{profile_id}/sample/status")
def voice_sample_status(profile_id: int) -> JSONResponse:
    return JSONResponse(settings.sample_status(profile_id))


# --- Presets -------------------------------------------------------------------

@router.post("/settings/presets")
def create_preset(kind: str = Form(...), name: str = Form(...), params: str = Form(...)):
    try:
        settings.create_preset(kind, name, params)
    except settings.SettingsError as exc:
        return _err_redirect(str(exc))
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/settings/presets/{preset_id}/delete")
def delete_preset(preset_id: int):
    try:
        settings.delete_preset(preset_id)
    except settings.SettingsError as exc:
        return _err_redirect(str(exc))
    return RedirectResponse(url="/settings", status_code=303)


# --- Saved recipient lists -----------------------------------------------------

@router.post("/settings/recipient-lists")
def create_recipient_list(name: str = Form(...), emails: str = Form(...)):
    # One address per line in the form textarea.
    split = [line for line in emails.replace(",", "\n").splitlines()]
    try:
        settings.create_recipient_list(name, split)
    except settings.SettingsError as exc:
        return _err_redirect(str(exc))
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/settings/recipient-lists/{list_id}/delete")
def delete_recipient_list(list_id: int):
    try:
        settings.delete_recipient_list(list_id)
    except settings.SettingsError as exc:
        return _err_redirect(str(exc))
    return RedirectResponse(url="/settings", status_code=303)