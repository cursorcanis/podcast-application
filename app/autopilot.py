"""Upload-a-script autopilot: one file in, finished episode out.

`create_from_upload` turns an uploaded script document into an episode that
is already `script_ready` (there is no research step to gate — the person
uploading wrote or chose the script), flagged `autopilot = 1`. From there a
background thread per episode walks it through the same stages a person
would click through on the episode page, using the same functions:

    script_ready -> render (app/render.py) -> mastering (app/postprod.py)
                 -> QA (app/qa.py) -> delivery (app/delivery.py)

Nothing here bypasses a stage's own checks; it only presses the buttons.

Behaviour that a person would otherwise have to babysit:
  * Queueing: only one render runs at a time (one GPU). An uploaded episode
    waits its turn while another render is active, then starts by itself.
  * ComfyUI offline: the render does not start until ComfyUI answers, so an
    upload made before ComfyUI is launched simply waits.
  * Render-time cap: uploading the script is the go-ahead, so a projection
    over MAX_RENDER_HOURS is confirmed automatically (and noted).
  * A render that fails part-way is resumed — keeping every finished chunk —
    up to MAX_RENDER_RETRIES times.
  * QA flagging specific chunks (clipping / wrong voice) re-renders just
    those chunks once, then masters and re-checks.
  * If QA still fails, or email can't be sent, the finished files are copied
    to SHARE_LOCATION anyway and the note says exactly why — the audio is
    never stranded.
  * Restart-safe: every step is decided from the database, and
    `resume_autopilots` (app startup) restarts the thread for any episode
    still in progress.

Everything is free/local: ComfyUI + Chatterbox on this machine, ffmpeg, and
(optionally) Gmail SMTP. No paid API and no agent is involved.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import comfyui, delivery, episodes, postprod, qa, render, script_import
from .config import config
from .db import get_connection

POLL_SECONDS = 15.0
MAX_RENDER_RETRIES = 2
MAX_QA_RETRIES = 1
# QA's duration check is a floor. Chatterbox's real pace varies from the
# 150 wpm estimate, so an uploaded script's floor is set well under the
# estimate — it catches a truncated render, not a brisk narrator.
DURATION_FLOOR_FRACTION = 0.6

DONE_STATUSES = ("delivered", "delivered_paused")

_threads_lock = threading.Lock()
_threads: dict[int, threading.Thread] = {}


@dataclass(frozen=True)
class Step:
    keep_going: bool
    wait_seconds: float = 0.0


def _now() -> str:
    return episodes.utcnow_iso()


def set_note(episode_id: int, note: str, *, stop: bool = False) -> None:
    conn = get_connection()
    try:
        if stop:
            conn.execute(
                "UPDATE episodes SET autopilot_note = ?, autopilot = 0, updated_at = ? WHERE id = ?",
                (note, _now(), episode_id),
            )
        else:
            conn.execute(
                "UPDATE episodes SET autopilot_note = ?, updated_at = ? WHERE id = ?",
                (note, _now(), episode_id),
            )
        conn.commit()
    finally:
        conn.close()


def _bump(episode_id: int, column: str) -> None:
    assert column in ("autopilot_render_retries", "autopilot_qa_retries")
    conn = get_connection()
    try:
        conn.execute(f"UPDATE episodes SET {column} = {column} + 1 WHERE id = ?", (episode_id,))
        conn.commit()
    finally:
        conn.close()


def _load(episode_id: int) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM episodes WHERE id = ?", (episode_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# --- Intake -------------------------------------------------------------------

def narrator_choices() -> list[str]:
    """Speaker tags that have a voice reference configured — the only voices
    a script can actually be rendered in."""
    return list(config.voice_mapping.keys())


def create_from_upload(
    *,
    filename: str,
    data: bytes,
    title: str = "",
    narrator: str = "",
    speed: str | float = "",
    recipient_emails: list[str] | None = None,
) -> int:
    """Validate an uploaded script and create its autopilot episode. Raises
    script_import.ScriptImportError or episodes.ValidationError with a message
    meant for the person uploading; nothing is written on rejection."""
    voices = config.voice_mapping
    narrator = (narrator or next(iter(voices), "HOST_A")).strip().upper()
    if voices and narrator not in voices:
        raise episodes.ValidationError(
            f"Narrator voice '{narrator}' has no voice reference configured. "
            f"Choose one of: {', '.join(voices)}."
        )
    raw = script_import.decode_upload(filename, data)
    script = script_import.prepare_script(raw, narrator=narrator)
    unmapped = [s for s in script.speakers if voices and s not in voices]
    if unmapped:
        raise episodes.ValidationError(
            f"The script uses speaker tag(s) {', '.join('[' + s + ']' for s in unmapped)} "
            f"but only {', '.join('[' + s + ']' for s in voices)} have a voice configured. "
            "Retag those lines, or add the speaker to VOICE_MAPPING in config/app_config.json."
        )

    speed_value = episodes._validate_speed(speed if speed not in (None, "") else episodes.DEFAULT_SPEED)
    title = (title or "").strip() or script_import.title_from_filename(filename)
    floor_minutes = int(script.estimated_minutes / speed_value * DURATION_FLOOR_FRACTION)
    fmt = "two_host" if len(script.speakers) > 1 else "solo"

    conn = get_connection()
    try:
        recipients = episodes._validate_recipients(
            recipient_emails if recipient_emails is not None else config.default_recipients, conn
        )
        cur = conn.execute(
            """
            INSERT INTO episodes (
                title, topic_or_query, format, tone, speed, cadence,
                audience_level, length_minutes_target, voice_profile_ids,
                recipient_emails, status, defaults_applied, autopilot,
                autopilot_note, created_at, updated_at
            ) VALUES (?, ?, ?, '', ?, ?, NULL, ?, ?, ?, 'script_ready', ?, 1, ?, ?, ?)
            """,
            (
                title,
                f"Uploaded script: {filename} ({script.word_count:,} words, "
                f"about {script.estimated_minutes:.0f} minutes)",
                fmt,
                speed_value,
                episodes.DEFAULT_CADENCE,
                floor_minutes,
                json.dumps(script.speakers),
                json.dumps(recipients),
                json.dumps(["tone", "cadence", "audience_level", "sources (uploaded script)"]),
                "Queued — waiting to start the render.",
                _now(),
                _now(),
            ),
        )
        episode_id = int(cur.lastrowid)
        conn.execute(
            "INSERT INTO episode_documents (episode_id, kind, body, created_at) VALUES (?, 'script', ?, ?)",
            (episode_id, script.text, _now()),
        )
        conn.commit()
    finally:
        conn.close()
    return episode_id


# --- The state machine --------------------------------------------------------

def _hand_off_without_email(episode: dict, why: str) -> str:
    """Copy the finished email MP3 to SHARE_LOCATION and return the note to
    show. Used when QA can't pass or the email can't go out."""
    try:
        path = delivery.copy_to_share_location(episode)
    except delivery.DeliveryError as exc:
        return f"{why} The audio could not be copied to the share folder either: {exc}"
    return f"{why} The finished episode was copied to {path}."


def advance(episode_id: int) -> Step:
    """Take the next step for one autopilot episode, based only on what the
    database says. Returns whether to keep going and how long to wait first."""
    ep = _load(episode_id)
    if ep is None or not ep.get("autopilot"):
        return Step(False)
    status = ep["status"]
    job = render.get_latest_job_for_episode(episode_id)

    if status in DONE_STATUSES:
        record = delivery.latest_delivery_record(episode_id) or {}
        if record.get("status") == "sent":
            note = f"Done — emailed to {', '.join(json.loads(ep['recipient_emails']))}."
        else:
            note = f"Done — the episode is in {config.share_location}."
        set_note(episode_id, note, stop=True)
        return Step(False)

    if status == "script_ready" and job is None:
        active = render.get_system_active_job()
        if active is not None:
            set_note(episode_id, f"Queued — waiting for episode {active['episode_id']}'s render to finish.")
            return Step(True, POLL_SECONDS)
        ahead = _earliest_waiting_upload()
        if ahead is not None and ahead != episode_id:
            set_note(episode_id, f"Queued — episode {ahead} was uploaded first and renders next.")
            return Step(True, POLL_SECONDS)
        probe = comfyui.check_reachability(config.comfyui_url)
        if not probe["reachable"]:
            set_note(
                episode_id,
                f"Waiting for ComfyUI at {config.comfyui_url} — start ComfyUI and the render "
                f"begins automatically. ({probe['error_detail']})",
            )
            return Step(True, POLL_SECONDS)
        try:
            started = render.start_render_job(episode_id)
        except render.AlreadyRenderingError:
            return Step(True, POLL_SECONDS)
        note = "Rendering voice in ComfyUI."
        if started["status"] == "awaiting_cap_confirmation":
            render.confirm_render_job(started["job_id"])
            note = (
                f"Rendering voice in ComfyUI. Projected {started['projected_hours']}h is over the "
                f"{started['cap_hours']}h cap — confirmed automatically because the script was uploaded "
                "for autopilot."
            )
        set_note(episode_id, note)
        return Step(True, POLL_SECONDS)

    if status in ("script_ready", "rendering") and job is not None:
        if job["status"] in ("running", "awaiting_cap_confirmation"):
            if job["status"] == "awaiting_cap_confirmation":
                render.confirm_render_job(job["id"])
            progress = render.job_progress(job["id"])
            c = progress["counts"]
            mins = progress["remaining_seconds"] / 60.0
            set_note(
                episode_id,
                f"Rendering voice in ComfyUI — {c['succeeded']} of {c['total']} chunks done, "
                f"about {mins:.0f} min left.",
            )
            return Step(True, POLL_SECONDS)
        if job["status"] == "cancelled":
            set_note(episode_id, "Stopped — the render was cancelled.", stop=True)
            return Step(False)
        if job["status"] == "failed":
            if ep["autopilot_render_retries"] >= MAX_RENDER_RETRIES:
                set_note(
                    episode_id,
                    f"Stopped — the render failed {MAX_RENDER_RETRIES + 1} times: {job['error_detail']} "
                    "Use 'Resume render' on the episode page once the problem is fixed.",
                    stop=True,
                )
                return Step(False)
            if not comfyui.check_reachability(config.comfyui_url)["reachable"]:
                set_note(episode_id, f"Render paused — waiting for ComfyUI at {config.comfyui_url} to come back.")
                return Step(True, POLL_SECONDS)
            try:
                render.resume_failed_job(job["id"])
            except render.AlreadyRenderingError:
                return Step(True, POLL_SECONDS)
            _bump(episode_id, "autopilot_render_retries")
            set_note(episode_id, f"Render hit a problem ({job['error_detail']}); resuming from where it stopped.")
            return Step(True, POLL_SECONDS)
        if job["status"] == "succeeded" and status == "rendering":
            return Step(True, 1.0)  # the worker is about to flip the episode to rendered

    if status == "rendered" or (status == "postproduction" and not _master_exists(ep)):
        set_note(episode_id, "Mastering — joining chunks, levelling loudness, exporting MP3s.")
        postprod.run_postproduction(episode_id)
        return Step(True)

    if status in ("postproduction", "qa"):
        set_note(episode_id, "Checking quality (QA).")
        try:
            result = qa.run_qa(episode_id)
        except delivery.DeliveryError as exc:
            fresh = _load(episode_id) or ep
            set_note(episode_id, _hand_off_without_email(fresh, f"QA passed but email was not sent: {exc}"),
                     stop=True)
            return Step(False)
        if result["overall_pass"]:
            return Step(True)
        return Step(True)  # falls through to the qa_failed branch next time

    if status == "qa_failed":
        if ep["autopilot_qa_retries"] < MAX_QA_RETRIES:
            try:
                retried = qa.retry_failing_chunks(episode_id)
            except qa.QAError:
                retried = None
            if retried is not None:
                _bump(episode_id, "autopilot_qa_retries")
                set_note(
                    episode_id,
                    f"QA flagged {len(retried['requeued_chunk_ids'])} chunk(s); re-rendering just those.",
                )
                return Step(True, POLL_SECONDS)
        note = _hand_off_without_email(
            ep, "Finished, but QA did not pass (see the QA report on this page), so it was not emailed."
        )
        set_note(episode_id, note, stop=True)
        return Step(False)

    if status == "ready":
        # QA passed but delivery didn't complete (e.g. the app stopped mid-send).
        try:
            delivery.run_delivery_on_qa_pass(episode_id)
        except delivery.DeliveryError as exc:
            set_note(episode_id, _hand_off_without_email(ep, f"Email was not sent: {exc}"), stop=True)
            return Step(False)
        return Step(True)

    set_note(episode_id, f"Stopped — unexpected episode status '{status}'.", stop=True)
    return Step(False)


def _earliest_waiting_upload() -> int | None:
    """Uploads render first-come, first-served: the lowest-id autopilot
    episode that is script_ready and has never had a render job."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT e.id FROM episodes e WHERE e.autopilot = 1 AND e.status = 'script_ready' "
            "AND NOT EXISTS (SELECT 1 FROM render_jobs j WHERE j.episode_id = e.id) "
            "ORDER BY e.id ASC LIMIT 1"
        ).fetchone()
        return int(row["id"]) if row else None
    finally:
        conn.close()


def _master_exists(ep: dict) -> bool:
    path = ep.get("wav_master_path")
    return bool(path) and Path(path).exists()


# --- Threads ------------------------------------------------------------------

def _drive(episode_id: int) -> None:
    try:
        while True:
            try:
                step = advance(episode_id)
            except Exception as exc:  # noqa: BLE001 - surface anything, then stop
                set_note(episode_id, f"Stopped — {type(exc).__name__}: {exc}", stop=True)
                return
            if not step.keep_going:
                return
            if step.wait_seconds:
                time.sleep(step.wait_seconds)
    finally:
        with _threads_lock:
            _threads.pop(episode_id, None)


def start(episode_id: int) -> None:
    with _threads_lock:
        existing = _threads.get(episode_id)
        if existing is not None and existing.is_alive():
            return
        t = threading.Thread(target=_drive, args=(episode_id,), daemon=True, name=f"autopilot-{episode_id}")
        _threads[episode_id] = t
        t.start()


def resume_autopilots() -> list[int]:
    """App startup: restart the driver for every episode still on autopilot."""
    conn = get_connection()
    try:
        ids = [int(r["id"]) for r in conn.execute("SELECT id FROM episodes WHERE autopilot = 1").fetchall()]
    finally:
        conn.close()
    for episode_id in ids:
        start(episode_id)
    return ids


def restart(episode_id: int) -> None:
    """'Resume' button: turn autopilot back on for an episode it stopped on,
    giving it a fresh retry budget."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE episodes SET autopilot = 1, autopilot_render_retries = 0, autopilot_qa_retries = 0, "
            "autopilot_note = 'Resuming.', updated_at = ? WHERE id = ?",
            (_now(), episode_id),
        )
        # A cancelled render is picked back up the same way as a failed one:
        # keep the finished chunks, render the rest.
        conn.execute(
            "UPDATE render_jobs SET status = 'failed', error_detail = 'Cancelled, then resumed.' "
            "WHERE id = (SELECT MAX(id) FROM render_jobs WHERE episode_id = ?) AND status = 'cancelled'",
            (episode_id,),
        )
        conn.commit()
    finally:
        conn.close()
    start(episode_id)
