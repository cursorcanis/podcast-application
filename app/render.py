"""ComfyUI render pipeline orchestration — M3 (POD-10).

One worker, one job: the GPU is the bottleneck, so exactly one render_job may
be `running` or `awaiting_cap_confirmation` system-wide at a time (enforced
both here and by the DB's partial unique index — a constraint, not a
convention). Chunks submit serially via POST /prompt against the workflow at
PATH_TO_WORKFLOW_JSON (node ids resolved dynamically — see app/workflow.py),
poll /history/{id}, and persist every chunk's state in `render_chunks` so a
crash, a reboot, or a closed browser tab costs at most the one in-flight
chunk — resuming means finding the first non-succeeded chunk, never
re-rendering the whole episode.

Progress is measured, not guessed: before any chunk completes, the
projection comes from the Audio Engineer's benchmarked
RENDER_SECONDS_PER_AUDIO_SECOND ratio; once at least one chunk has rendered,
the projection switches to this job's own measured wall-clock average.

The render loop runs in a background thread owned by this process (not by
the browser), started when a job begins and re-started for any job still
`running` when the process starts up (`resume_pending_jobs`, called from
app/__init__.py).
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import chunking, comfyui, workflow
from .config import config
from .db import get_connection

MAX_CHUNK_ATTEMPTS = 3
CLIENT_ID = "podcast_foundry_app"
OUTPUT_PREFIX_ROOT = "podcast_foundry"

_ACTIVE_JOB_STATUSES = ("running", "awaiting_cap_confirmation")

_threads_lock = threading.Lock()
_active_threads: dict[int, threading.Thread] = {}


class RenderError(ValueError):
    """A rejected render action. Names the exact reason — never generic —
    so the UI can show it verbatim (same pattern as episodes.ValidationError)."""


class AlreadyRenderingError(RenderError):
    """Raised when a render is requested while another render_job is
    system-wide active. Names the job/episode that is already running."""


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


# --- Script -> chunk plan -----------------------------------------------------

def _load_script_text(conn: sqlite3.Connection, episode_id: int) -> str:
    row = conn.execute(
        "SELECT body FROM episode_documents WHERE episode_id = ? AND kind = 'script' "
        "ORDER BY id DESC LIMIT 1",
        (episode_id,),
    ).fetchone()
    if row is None or not (row["body"] or "").strip():
        raise RenderError(f"Episode {episode_id} has no saved script to render.")
    return row["body"]


def plan_chunks(episode: dict, script_text: str, chunk_seconds_target: float) -> list[chunking.ScriptChunk]:
    return chunking.chunk_script(
        script_text, speed=float(episode["speed"]), chunk_seconds_target=chunk_seconds_target
    )


def projected_hours_for(chunks: list[chunking.ScriptChunk]) -> float:
    total_audio_seconds = sum(c.estimated_seconds for c in chunks)
    return round((total_audio_seconds * config.render_seconds_per_audio_second) / 3600.0, 2)


def dry_run_projection(episode_id: int) -> dict[str, Any]:
    """Preview the chunk plan and projected render time without creating a
    job — used by the 'Start render' screen before the Board commits."""
    conn = get_connection()
    try:
        episode = conn.execute("SELECT * FROM episodes WHERE id = ?", (episode_id,)).fetchone()
        if episode is None:
            raise RenderError(f"Episode {episode_id} does not exist.")
        episode = dict(episode)
        script_text = _load_script_text(conn, episode_id)
    finally:
        conn.close()
    target = config.chunk_seconds_target_default
    chunks = plan_chunks(episode, script_text, target)
    projected = projected_hours_for(chunks)
    return {
        "chunk_count": len(chunks),
        "total_estimated_seconds": sum(c.estimated_seconds for c in chunks),
        "projected_hours": projected,
        "cap_hours": config.max_render_hours,
        "over_cap": projected > config.max_render_hours,
        "chunk_seconds_target": target,
    }


# --- Job lifecycle --------------------------------------------------------------

def get_system_active_job() -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM render_jobs WHERE status IN (?, ?) ORDER BY id DESC LIMIT 1",
            _ACTIVE_JOB_STATUSES,
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_latest_job_for_episode(episode_id: int) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM render_jobs WHERE episode_id = ? ORDER BY id DESC LIMIT 1",
            (episode_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _insert_chunks(conn: sqlite3.Connection, job_id: int, chunks: list[chunking.ScriptChunk]) -> None:
    for idx, chunk in enumerate(chunks):
        conn.execute(
            """
            INSERT INTO render_chunks (
                job_id, chunk_index, speaker, text, status, chunk_seconds_target,
                pause_before_seconds, pause_after_seconds
            ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)
            """,
            (
                job_id, idx, chunk.speaker, chunk.text, chunk.estimated_seconds,
                chunk.pause_before_seconds, chunk.pause_after_seconds,
            ),
        )


def start_render_job(episode_id: int) -> dict:
    """Create (and, if under the cap, start) a render_job for this episode.
    Raises AlreadyRenderingError if another job is system-wide active, or
    RenderError if the episode is not script_ready / has no script."""
    conn = get_connection()
    try:
        episode = conn.execute("SELECT * FROM episodes WHERE id = ?", (episode_id,)).fetchone()
        if episode is None:
            raise RenderError(f"Episode {episode_id} does not exist.")
        episode = dict(episode)
        if episode["status"] not in ("script_ready",):
            raise RenderError(
                f"Episode {episode_id} is not script_ready (status is "
                f"'{episode['status']}'); render requires a ready script."
            )
        active = conn.execute(
            "SELECT * FROM render_jobs WHERE status IN (?, ?) ORDER BY id DESC LIMIT 1",
            _ACTIVE_JOB_STATUSES,
        ).fetchone()
        if active is not None:
            raise AlreadyRenderingError(
                f"Render job {active['id']} for episode {active['episode_id']} is already "
                f"{active['status']} — only one render job may run system-wide at a time."
            )

        script_text = _load_script_text(conn, episode_id)
        workflow_path = config.path_to_workflow_json
        # Fail loud before creating any job row if the workflow can't be
        # resolved — never leave a half-started job behind a bad config.
        wf = workflow.load_workflow(workflow_path)
        workflow.resolve_roles(wf)

        chunk_target = config.chunk_seconds_target_default
        chunks = plan_chunks(episode, script_text, chunk_target)
        if not chunks:
            raise RenderError(f"Episode {episode_id}'s script produced zero chunks to render.")
        projected = projected_hours_for(chunks)
        over_cap = projected > config.max_render_hours

        cur = conn.execute(
            """
            INSERT INTO render_jobs (
                episode_id, status, cap_hours, projected_hours, confirmed_over_cap,
                chunk_seconds_target, workflow_path, comfyui_url, render_ratio_used,
                started_at, created_at
            ) VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)
            """,
            (
                episode_id,
                "awaiting_cap_confirmation" if over_cap else "running",
                config.max_render_hours,
                projected,
                chunk_target,
                workflow_path,
                config.comfyui_url,
                config.render_seconds_per_audio_second,
                None if over_cap else utcnow_iso(),
                utcnow_iso(),
            ),
        )
        job_id = int(cur.lastrowid)
        _insert_chunks(conn, job_id, chunks)
        if not over_cap:
            conn.execute(
                "UPDATE episodes SET status = 'rendering', updated_at = ? WHERE id = ?",
                (utcnow_iso(), episode_id),
            )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        # Backstop: the DB's own partial unique index caught a race the
        # application check above missed.
        raise AlreadyRenderingError(
            "Another render job became active system-wide while this one was being "
            f"created: {exc}"
        ) from exc
    finally:
        conn.close()

    if not over_cap:
        _start_thread(job_id)
    return {"job_id": job_id, "status": "awaiting_cap_confirmation" if over_cap else "running",
            "projected_hours": projected, "cap_hours": config.max_render_hours}


def confirm_render_job(job_id: int) -> None:
    """The Board's explicit confirm-to-proceed for a job whose projection
    exceeded MAX_RENDER_HOURS. Flips the job to running and starts it."""
    conn = get_connection()
    try:
        job = conn.execute("SELECT * FROM render_jobs WHERE id = ?", (job_id,)).fetchone()
        if job is None:
            raise RenderError(f"Render job {job_id} does not exist.")
        if job["status"] != "awaiting_cap_confirmation":
            raise RenderError(
                f"Render job {job_id} is not awaiting cap confirmation (status is "
                f"'{job['status']}')."
            )
        conn.execute(
            "UPDATE render_jobs SET status = 'running', confirmed_over_cap = 1, started_at = ? "
            "WHERE id = ?",
            (utcnow_iso(), job_id),
        )
        conn.execute(
            "UPDATE episodes SET status = 'rendering', updated_at = ? WHERE id = ?",
            (utcnow_iso(), job["episode_id"]),
        )
        conn.commit()
    finally:
        conn.close()
    _start_thread(job_id)


def cancel_render_job(job_id: int) -> None:
    conn = get_connection()
    try:
        job = conn.execute("SELECT * FROM render_jobs WHERE id = ?", (job_id,)).fetchone()
        if job is None:
            raise RenderError(f"Render job {job_id} does not exist.")
        if job["status"] not in _ACTIVE_JOB_STATUSES:
            raise RenderError(f"Render job {job_id} is not active (status is '{job['status']}').")
        conn.execute(
            "UPDATE render_jobs SET status = 'cancelled', finished_at = ? WHERE id = ?",
            (utcnow_iso(), job_id),
        )
        conn.commit()
    finally:
        conn.close()


def list_succeeded_chunks_for_job(job_id: int) -> list[dict]:
    """Every succeeded chunk for a job, in chunk_index order — the sequence
    app/postprod.py concatenates. Raises if any chunk is not succeeded, since
    mastering a partially-rendered job would silently produce a wrong-length
    episode."""
    conn = get_connection()
    try:
        job = conn.execute("SELECT * FROM render_jobs WHERE id = ?", (job_id,)).fetchone()
        if job is None:
            raise RenderError(f"Render job {job_id} does not exist.")
        chunks = [dict(r) for r in conn.execute(
            "SELECT * FROM render_chunks WHERE job_id = ? ORDER BY chunk_index ASC", (job_id,)
        ).fetchall()]
    finally:
        conn.close()
    not_succeeded = [c for c in chunks if c["status"] != "succeeded"]
    if not_succeeded:
        raise RenderError(
            f"Render job {job_id} has {len(not_succeeded)} chunk(s) not yet succeeded "
            f"(first: chunk {not_succeeded[0]['chunk_index']}, status "
            f"'{not_succeeded[0]['status']}') — mastering requires every chunk to have "
            "rendered first."
        )
    return chunks


def requeue_chunks_for_rerender(job_id: int, chunk_ids: list[int]) -> None:
    """QA found that specific chunks' own rendered audio failed a check (e.g.
    clipping) — reset exactly those chunk rows to 'pending' and resume this
    SAME job/thread, reusing M3's resumability mechanism so only the failing
    chunks re-render, never the whole episode."""
    if not chunk_ids:
        raise RenderError("requeue_chunks_for_rerender called with no chunk ids.")
    conn = get_connection()
    try:
        job = conn.execute("SELECT * FROM render_jobs WHERE id = ?", (job_id,)).fetchone()
        if job is None:
            raise RenderError(f"Render job {job_id} does not exist.")
        if job["status"] not in ("succeeded", "failed"):
            raise RenderError(
                f"Render job {job_id} is not finished (status is '{job['status']}') — "
                "cannot requeue chunks for a job that is still active."
            )
        active = conn.execute(
            "SELECT * FROM render_jobs WHERE status IN (?, ?) ORDER BY id DESC LIMIT 1",
            _ACTIVE_JOB_STATUSES,
        ).fetchone()
        if active is not None:
            raise AlreadyRenderingError(
                f"Render job {active['id']} for episode {active['episode_id']} is already "
                f"{active['status']} — only one render job may run system-wide at a time."
            )
        rows = conn.execute(
            f"SELECT id FROM render_chunks WHERE job_id = ? AND id IN "
            f"({','.join('?' for _ in chunk_ids)})",
            (job_id, *chunk_ids),
        ).fetchall()
        found_ids = {int(r["id"]) for r in rows}
        missing = set(chunk_ids) - found_ids
        if missing:
            raise RenderError(f"Chunk id(s) {sorted(missing)} do not belong to render job {job_id}.")
        conn.execute(
            f"UPDATE render_chunks SET status = 'pending', attempt_count = 0, error_detail = NULL, "
            f"rerender_count = rerender_count + 1, "
            f"output_wav_path = NULL, measured_render_seconds = NULL, qa_clip_detected = 0, "
            f"comfyui_prompt_id = NULL WHERE id IN ({','.join('?' for _ in chunk_ids)})",
            tuple(chunk_ids),
        )
        conn.execute(
            "UPDATE render_jobs SET status = 'running', finished_at = NULL, error_detail = NULL "
            "WHERE id = ?",
            (job_id,),
        )
        conn.execute(
            "UPDATE episodes SET status = 'rendering', updated_at = ? WHERE id = ?",
            (utcnow_iso(), job["episode_id"]),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        raise AlreadyRenderingError(
            f"Another render job became active system-wide while requeueing: {exc}"
        ) from exc
    finally:
        conn.close()
    _start_thread(job_id)


def resume_failed_job(job_id: int) -> None:
    """Restart a job that failed part-way (ComfyUI went down, a chunk ran out
    of attempts): its unfinished chunks get a fresh attempt budget and the
    job resumes from the first of them. Succeeded chunks are kept — and a
    chunk whose last ComfyUI prompt finished after the app gave up on it is
    adopted rather than re-rendered (see _recover_prior_attempt)."""
    conn = get_connection()
    try:
        job = conn.execute("SELECT * FROM render_jobs WHERE id = ?", (job_id,)).fetchone()
        if job is None:
            raise RenderError(f"Render job {job_id} does not exist.")
        if job["status"] != "failed":
            raise RenderError(f"Render job {job_id} has not failed (status is '{job['status']}').")
        active = conn.execute(
            "SELECT * FROM render_jobs WHERE status IN (?, ?) ORDER BY id DESC LIMIT 1",
            _ACTIVE_JOB_STATUSES,
        ).fetchone()
        if active is not None:
            raise AlreadyRenderingError(
                f"Render job {active['id']} for episode {active['episode_id']} is already "
                f"{active['status']} — only one render job may run system-wide at a time."
            )
        conn.execute(
            "UPDATE render_chunks SET status = 'pending', attempt_count = 0 "
            "WHERE job_id = ? AND status != 'succeeded'",
            (job_id,),
        )
        conn.execute(
            "UPDATE render_jobs SET status = 'running', finished_at = NULL, error_detail = NULL "
            "WHERE id = ?",
            (job_id,),
        )
        conn.execute(
            "UPDATE episodes SET status = 'rendering', updated_at = ? WHERE id = ?",
            (utcnow_iso(), job["episode_id"]),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        raise AlreadyRenderingError(
            f"Another render job became active system-wide while resuming: {exc}"
        ) from exc
    finally:
        conn.close()
    _start_thread(job_id)


def resume_pending_jobs() -> list[int]:
    """Called at app startup. Any job left `running` when the process last
    stopped (crash, reboot, or a plain restart) gets its worker thread
    re-started — it resumes from the first non-succeeded chunk because that
    chunk state is what's in the DB, not from the beginning."""
    conn = get_connection()
    try:
        rows = conn.execute("SELECT id FROM render_jobs WHERE status = 'running'").fetchall()
        job_ids = [int(r["id"]) for r in rows]
    finally:
        conn.close()
    for job_id in job_ids:
        _start_thread(job_id)
    return job_ids


def _start_thread(job_id: int) -> None:
    with _threads_lock:
        existing = _active_threads.get(job_id)
        if existing is not None and existing.is_alive():
            return
        t = threading.Thread(target=_run_job, args=(job_id,), daemon=True, name=f"render-job-{job_id}")
        _active_threads[job_id] = t
        t.start()


# --- Output path resolution -----------------------------------------------------

def _resolve_output_path(subfolder: str, filename: str) -> str:
    """SaveAudio writes relative to ComfyUI's own output root; our
    filename_prefix always starts with OUTPUT_PREFIX_ROOT ("podcast_foundry"),
    and OUTPUT_FOLDER is configured as exactly
    "<comfyui output root>/podcast_foundry" — so the path under OUTPUT_FOLDER
    is whatever subfolder ComfyUI reports, with that leading segment
    stripped."""
    rel = (subfolder or "").replace("\\", "/").strip("/")
    if rel.startswith(OUTPUT_PREFIX_ROOT):
        rel = rel[len(OUTPUT_PREFIX_ROOT):].strip("/")
    base = Path(config.output_folder)
    return str(base / rel / filename) if rel else str(base / filename)


def _extract_output_file(outputs: dict, save_node_id: str) -> tuple[str, str] | None:
    node_outputs = outputs.get(save_node_id) or {}
    for value in node_outputs.values():
        if isinstance(value, list) and value:
            first = value[0]
            if isinstance(first, dict) and "filename" in first:
                return str(first.get("subfolder", "")), str(first["filename"])
    return None


# --- Worker loop -----------------------------------------------------------------

def _episode_speed(conn: sqlite3.Connection, episode_id: int) -> float:
    episode = conn.execute("SELECT speed FROM episodes WHERE id = ?", (episode_id,)).fetchone()
    return float(episode["speed"]) if episode else 1.0


def _replace_chunk_with_pieces(
    conn: sqlite3.Connection, job_id: int, chunk: dict, pieces: list[str], speed: float
) -> None:
    """Swap one chunk row for `pieces` (pending), shifting later chunks
    along. The original chunk's pause_before/after belonged to its
    first/last piece respectively — the new middle pieces carry no pause."""
    conn.execute("DELETE FROM render_chunks WHERE id = ?", (chunk["id"],))
    conn.execute(
        "UPDATE render_chunks SET chunk_index = chunk_index + ? WHERE job_id = ? AND chunk_index > ?",
        (len(pieces) - 1, job_id, chunk["chunk_index"]),
    )
    for offset, piece in enumerate(pieces):
        conn.execute(
            "INSERT INTO render_chunks (job_id, chunk_index, speaker, text, status, "
            "chunk_seconds_target, pause_before_seconds, pause_after_seconds) "
            "VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)",
            (
                job_id,
                chunk["chunk_index"] + offset,
                chunk["speaker"],
                piece,
                chunking.estimate_seconds(piece, speed),
                chunk["pause_before_seconds"] if offset == 0 else 0.0,
                chunk["pause_after_seconds"] if offset == len(pieces) - 1 else 0.0,
            ),
        )


# Fraction of the TTS node's maximum output length at which a rendered chunk
# is treated as cut off rather than finished.
LENGTH_CEILING_FRACTION = 0.97


def _hit_length_ceiling(output_path: str, max_audio_seconds: float | None) -> bool:
    if not max_audio_seconds or not Path(output_path).exists():
        return False
    from .postprod import PostprodError, ffprobe_duration_seconds  # postprod imports render

    try:
        duration = ffprobe_duration_seconds(Path(output_path))
    except (PostprodError, OSError, ValueError):
        return False
    return duration >= max_audio_seconds * LENGTH_CEILING_FRACTION


def _recover_prior_attempt(comfyui_url: str, chunk: dict, timeout_s: float) -> dict | None:
    """If this chunk already has a ComfyUI prompt on record, return that
    prompt's successful result — waiting for it if ComfyUI still has it
    queued or running — instead of submitting the text again. None means
    there is nothing to recover and the chunk should be submitted fresh."""
    prior = chunk.get("comfyui_prompt_id")
    if not prior:
        return None
    try:
        result = comfyui.fetch_finished_result(comfyui_url, prior)
        if result is None and comfyui.queue_position(comfyui_url, prior) is not None:
            result = comfyui.poll_history(comfyui_url, prior, timeout_s=timeout_s)
    except comfyui.ComfyUIError:
        return None
    if result is not None and result.get("outcome") == "succeeded":
        return result
    return None


def _run_job(job_id: int) -> None:
    conn = get_connection()
    try:
        job = conn.execute("SELECT * FROM render_jobs WHERE id = ?", (job_id,)).fetchone()
        if job is None or job["status"] != "running":
            return
        job = dict(job)
        episode_id = job["episode_id"]
        comfyui_url = job["comfyui_url"] or config.comfyui_url
        workflow_path = job["workflow_path"] or config.path_to_workflow_json

        try:
            wf = workflow.load_workflow(workflow_path)
            roles = workflow.resolve_roles(wf)
        except workflow.WorkflowError as exc:
            conn.execute(
                "UPDATE render_jobs SET status = 'failed', error_detail = ?, finished_at = ? "
                "WHERE id = ?",
                (f"Workflow resolution failed: {exc}", utcnow_iso(), job_id),
            )
            conn.commit()
            return

        voice_mapping = config.voice_mapping
        chunk_timeout_s = config.chunk_timeout_min * 60.0
        max_audio_seconds = workflow.max_audio_seconds(wf, roles)

        while True:
            current_job = conn.execute("SELECT status FROM render_jobs WHERE id = ?", (job_id,)).fetchone()
            if current_job is None or current_job["status"] != "running":
                return  # cancelled, or no longer ours

            chunk = conn.execute(
                "SELECT * FROM render_chunks WHERE job_id = ? AND status != 'succeeded' "
                "ORDER BY chunk_index ASC LIMIT 1",
                (job_id,),
            ).fetchone()
            if chunk is None:
                conn.execute(
                    "UPDATE render_jobs SET status = 'succeeded', finished_at = ? WHERE id = ?",
                    (utcnow_iso(), job_id),
                )
                conn.execute(
                    "UPDATE episodes SET status = 'rendered', updated_at = ? WHERE id = ?",
                    (utcnow_iso(), episode_id),
                )
                conn.commit()
                return
            chunk = dict(chunk)

            if chunk["attempt_count"] >= MAX_CHUNK_ATTEMPTS:
                conn.execute(
                    "UPDATE render_jobs SET status = 'failed', error_detail = ?, finished_at = ? "
                    "WHERE id = ?",
                    (
                        f"Chunk {chunk['chunk_index']} failed after {MAX_CHUNK_ATTEMPTS} attempts: "
                        f"{chunk['error_detail']}",
                        utcnow_iso(),
                        job_id,
                    ),
                )
                conn.commit()
                return

            voice_ref = voice_mapping.get(chunk["speaker"])
            if roles.loader_node_id is not None and voice_ref is None:
                conn.execute(
                    "UPDATE render_jobs SET status = 'failed', error_detail = ?, finished_at = ? "
                    "WHERE id = ?",
                    (
                        f"No VOICE_MAPPING entry for speaker '{chunk['speaker']}' (chunk "
                        f"{chunk['chunk_index']}). Configure VOICE_MAPPING for every speaker tag "
                        "used in the script.",
                        utcnow_iso(),
                        job_id,
                    ),
                )
                conn.commit()
                return

            filename_prefix = f"{OUTPUT_PREFIX_ROOT}/ep{episode_id}_job{job_id}/chunk_{chunk['chunk_index']:04d}"

            # An earlier attempt (one that timed out, or was in flight when
            # the app stopped) may have finished in ComfyUI after all —
            # adopt its audio rather than render the same text again.
            result = _recover_prior_attempt(comfyui_url, chunk, chunk_timeout_s)
            started_at = chunk["started_at"] or utcnow_iso()
            if result is None:
                started_at = utcnow_iso()
                conn.execute(
                    "UPDATE render_chunks SET status = 'submitted', attempt_count = attempt_count + 1, "
                    "started_at = ?, error_detail = NULL, voice_reference_used = ? WHERE id = ?",
                    (started_at, voice_ref, chunk["id"]),
                )
                conn.commit()

                try:
                    prompt = workflow.build_prompt(
                        wf,
                        roles,
                        text=chunk["text"],
                        filename_prefix=filename_prefix,
                        voice_reference_filename=voice_ref,
                        # A retry or QA re-render gets a different seed — the
                        # same seed reproduces the same flawed take exactly.
                        seed_offset=chunk["attempt_count"] + 100 * chunk["rerender_count"],
                    )
                    prompt_id = comfyui.submit_prompt(comfyui_url, prompt, CLIENT_ID)
                    conn.execute(
                        "UPDATE render_chunks SET status = 'rendering', comfyui_prompt_id = ? WHERE id = ?",
                        (prompt_id, chunk["id"]),
                    )
                    conn.commit()
                    result = comfyui.poll_history(comfyui_url, prompt_id, timeout_s=chunk_timeout_s)
                except comfyui.ComfyUIError as exc:
                    conn.execute(
                        "UPDATE render_chunks SET status = 'failed', error_detail = ? WHERE id = ?",
                        (f"ComfyUI error: {exc}", chunk["id"]),
                    )
                    conn.commit()
                    continue
            else:
                conn.execute(
                    "UPDATE render_chunks SET voice_reference_used = ? WHERE id = ?",
                    (voice_ref, chunk["id"]),
                )
                conn.commit()

            if result["outcome"] == "succeeded":
                output = _extract_output_file(result.get("outputs", {}), roles.save_node_id)
                if output is None:
                    conn.execute(
                        "UPDATE render_chunks SET status = 'failed', error_detail = ? WHERE id = ?",
                        (
                            f"ComfyUI reported success but no output file under save node "
                            f"'{roles.save_node_id}': {result.get('outputs')}",
                            chunk["id"],
                        ),
                    )
                    conn.commit()
                    continue
                subfolder, filename = output
                output_path = _resolve_output_path(subfolder, filename)
                if _hit_length_ceiling(output_path, max_audio_seconds):
                    # Chatterbox stopped at its max_new_tokens ceiling, so the
                    # end of this chunk's text was never spoken (or it ran on
                    # babbling). Halve the text and render both halves; text
                    # too short to halve is retried with a fresh seed.
                    pieces = chunking.split_in_half(chunk["text"])
                    if len(pieces) > 1:
                        _replace_chunk_with_pieces(
                            conn, job_id, chunk, pieces, _episode_speed(conn, episode_id)
                        )
                    else:
                        conn.execute(
                            # prompt id cleared so the retry can't "recover"
                            # this same rejected take.
                            "UPDATE render_chunks SET status = 'failed', comfyui_prompt_id = NULL, "
                            "error_detail = ? WHERE id = ?",
                            (f"Audio ran to the {max_audio_seconds:.0f}s TTS length ceiling for a "
                             "short line — retrying with a new seed.", chunk["id"]),
                        )
                    conn.commit()
                    continue
                completed_at = utcnow_iso()
                wall_seconds = None
                start_dt, end_dt = _parse_iso(started_at), _parse_iso(completed_at)
                if start_dt and end_dt:
                    wall_seconds = (end_dt - start_dt).total_seconds()
                conn.execute(
                    "UPDATE render_chunks SET status = 'succeeded', output_wav_path = ?, "
                    "measured_render_seconds = ?, completed_at = ? WHERE id = ?",
                    (output_path, wall_seconds, completed_at, chunk["id"]),
                )
                conn.commit()
            elif result["outcome"] == "oom":
                new_target = max(chunk["chunk_seconds_target"] / 2.0, chunking.MIN_CHUNK_SECONDS_TARGET)
                speed = _episode_speed(conn, episode_id)
                pieces = chunking.split_text_to_target(chunk["text"], speed, new_target)
                conn.execute(
                    "UPDATE render_jobs SET chunk_seconds_target = ? WHERE id = ?",
                    (new_target, job_id),
                )
                if len(pieces) <= 1:
                    # Already as small as it gets; nothing left to split —
                    # record it as a straight failure instead of looping forever.
                    conn.execute(
                        "UPDATE render_chunks SET status = 'failed', error_detail = ? WHERE id = ?",
                        (f"OOM at chunk_seconds_target={new_target:.0f}s with no smaller split "
                         f"available: {result.get('error_detail')}", chunk["id"]),
                    )
                else:
                    _replace_chunk_with_pieces(conn, job_id, chunk, pieces, speed)
                conn.commit()
            else:  # failed or timed_out
                conn.execute(
                    "UPDATE render_chunks SET status = ?, error_detail = ? WHERE id = ?",
                    (
                        "timed_out" if result["outcome"] == "timed_out" else "failed",
                        result.get("error_detail"),
                        chunk["id"],
                    ),
                )
                conn.commit()
    finally:
        conn.close()
        with _threads_lock:
            _active_threads.pop(job_id, None)


# --- Progress / projection --------------------------------------------------------

def job_progress(job_id: int) -> dict[str, Any]:
    conn = get_connection()
    try:
        job = conn.execute("SELECT * FROM render_jobs WHERE id = ?", (job_id,)).fetchone()
        if job is None:
            raise RenderError(f"Render job {job_id} does not exist.")
        job = dict(job)
        chunks = [dict(r) for r in conn.execute(
            "SELECT * FROM render_chunks WHERE job_id = ? ORDER BY chunk_index ASC", (job_id,)
        ).fetchall()]
    finally:
        conn.close()

    total = len(chunks)
    succeeded = [c for c in chunks if c["status"] == "succeeded"]
    failed = [c for c in chunks if c["status"] in ("failed", "timed_out")]
    in_flight = [c for c in chunks if c["status"] in ("submitted", "rendering")]
    pending = [c for c in chunks if c["status"] == "pending"]

    now = datetime.now(timezone.utc)
    started_at = _parse_iso(job.get("started_at"))
    elapsed_seconds = (now - started_at).total_seconds() if started_at else 0.0

    if succeeded:
        wall_times = [c["measured_render_seconds"] for c in succeeded if c["measured_render_seconds"]]
        avg_wall = sum(wall_times) / len(wall_times) if wall_times else elapsed_seconds / max(len(succeeded), 1)
        remaining_chunks = total - len(succeeded)
        projected_total_seconds = elapsed_seconds + avg_wall * remaining_chunks
        projection_basis = "measured"
    else:
        projected_total_seconds = (job.get("projected_hours") or 0.0) * 3600.0
        projection_basis = "initial_estimate"

    return {
        "job": job,
        "chunks": chunks,
        "counts": {
            "total": total,
            "succeeded": len(succeeded),
            "failed": len(failed),
            "in_flight": len(in_flight),
            "pending": len(pending),
        },
        "elapsed_seconds": round(elapsed_seconds, 1),
        "projected_total_seconds": round(projected_total_seconds, 1),
        "remaining_seconds": round(max(projected_total_seconds - elapsed_seconds, 0.0), 1),
        "projection_basis": projection_basis,
    }
