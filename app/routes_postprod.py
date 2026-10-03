"""Mastering/export + QA routes — M4 (POD-11).

POST /episodes/{id}/postprod/run     master the episode's succeeded render
                                      (concatenate, speed, loudnorm, export)
POST /episodes/{id}/qa/run           run the QA checks against the master
POST /episodes/{id}/qa/retry         re-render only the chunks the last QA
                                      run flagged, then resume
GET  /episodes/{id}/download/{kind}  download wav_master | archive_mp3 | email_mp3

All mutating routes redirect back to the episode detail page with the exact
rejection message in ?error= on failure, same pattern as routes_episodes.py
and routes_render.py — there is no flash/session mechanism in this app.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter
from fastapi.responses import FileResponse, RedirectResponse

from . import episodes, postprod, qa

router = APIRouter()

_DOWNLOAD_COLUMNS = {
    "wav_master": ("wav_master_path", "audio/wav"),
    "archive_mp3": ("archive_mp3_path", "audio/mpeg"),
    "email_mp3": ("email_mp3_path", "audio/mpeg"),
}


def _err_redirect(episode_id: int, message: str) -> RedirectResponse:
    return RedirectResponse(url=f"/episodes/{episode_id}?error={quote(message)}", status_code=303)


@router.post("/episodes/{episode_id}/postprod/run")
def run_postprod(episode_id: int):
    try:
        postprod.run_postproduction(episode_id)
    except postprod.PostprodError as exc:
        return _err_redirect(episode_id, str(exc))
    return RedirectResponse(url=f"/episodes/{episode_id}", status_code=303)


@router.post("/episodes/{episode_id}/qa/run")
def run_qa_route(episode_id: int):
    try:
        qa.run_qa(episode_id)
    except (qa.QAError, postprod.PostprodError) as exc:
        return _err_redirect(episode_id, str(exc))
    return RedirectResponse(url=f"/episodes/{episode_id}", status_code=303)


@router.post("/episodes/{episode_id}/qa/retry")
def retry_qa(episode_id: int):
    try:
        qa.retry_failing_chunks(episode_id)
    except qa.QAError as exc:
        return _err_redirect(episode_id, str(exc))
    return RedirectResponse(url=f"/episodes/{episode_id}/render", status_code=303)


@router.get("/episodes/{episode_id}/download/{kind}")
def download(episode_id: int, kind: str):
    if kind not in _DOWNLOAD_COLUMNS:
        return _err_redirect(episode_id, f"Unknown download kind '{kind}'.")
    episode = episodes.get_episode(episode_id)
    if episode is None:
        return _err_redirect(episode_id, f"Episode {episode_id} not found.")
    column, media_type = _DOWNLOAD_COLUMNS[kind]
    path = episode.get(column)
    if not path or not Path(path).exists():
        return _err_redirect(episode_id, f"No {kind} file is available for this episode yet.")
    return FileResponse(path, media_type=media_type, filename=Path(path).name)
