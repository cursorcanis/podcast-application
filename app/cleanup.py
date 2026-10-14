"""Delete an episode completely: stop its render, remove every audio file it
produced, and drop its database rows — the Episode Library's Delete button.

Files are only ever deleted inside OUTPUT_FOLDER (chunk takes, the mastered
WAV/MP3s) or SHARE_LOCATION (the delivered copy), never anywhere else, even
if a path in the database points outside them.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from . import comfyui, delivery, render
from .config import config
from .db import get_connection

_EPISODE_TABLES = ("delivery_records", "episode_documents", "sources")


class CleanupError(Exception):
    pass


def _inside(path: Path, root: str | None) -> bool:
    if not root:
        return False
    try:
        root_path = Path(root).resolve()
        return root_path in path.resolve().parents
    except OSError:
        return False


def _remove_file(path: Path, root: str | None) -> int:
    """Delete one file under `root`; returns the bytes freed (0 if skipped)."""
    if not _inside(path, root) or not path.is_file():
        return 0
    try:
        size = path.stat().st_size
        path.unlink()
        return size
    except OSError:
        return 0


def _remove_tree(path: Path, root: str | None) -> tuple[int, int]:
    """Delete a folder under `root` (never `root` itself) and everything in it."""
    if not _inside(path, root) or not path.is_dir():
        return 0, 0
    files = [f for f in path.rglob("*") if f.is_file()]
    size = 0
    for f in files:
        try:
            size += f.stat().st_size
        except OSError:
            pass
    shutil.rmtree(path, ignore_errors=True)
    return len(files), size


def _paths_used_by_other_episodes(episode_id: int) -> dict[str, set[str]]:
    columns = ("output_dir", "wav_master_path", "archive_mp3_path", "email_mp3_path")
    conn = get_connection()
    try:
        rows = conn.execute(
            f"SELECT {', '.join(columns)} FROM episodes WHERE id != ?", (episode_id,)
        ).fetchall()
    finally:
        conn.close()
    return {c: {r[c] for r in rows if r[c]} for c in columns}


def delete_episode(episode_id: int) -> dict[str, Any]:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM episodes WHERE id = ?", (episode_id,)).fetchone()
        if row is None:
            raise CleanupError(f"Episode {episode_id} does not exist.")
        episode = dict(row)
        # Autopilot off first, so its driver thread stops at its next step
        # instead of queueing or delivering an episode that is going away.
        conn.execute("UPDATE episodes SET autopilot = 0 WHERE id = ?", (episode_id,))
        conn.commit()
        jobs = [dict(r) for r in conn.execute(
            "SELECT * FROM render_jobs WHERE episode_id = ?", (episode_id,)
        ).fetchall()]
    finally:
        conn.close()

    files = size = 0
    stopped_render = False
    for job in jobs:
        if job["status"] in render._DISCARDABLE_STATUSES and job["status"] not in ("cancelled", "failed"):
            # Still running/held: discard pulls its chunk out of ComfyUI,
            # unloads the models, waits for the worker, deletes its takes.
            result = render.discard_render_job(job["id"])
            files += result["files_deleted"]
            size += result["bytes_freed"]
            stopped_render = True

    conn = get_connection()
    try:
        for job in jobs:
            paths = [r["output_wav_path"] for r in conn.execute(
                "SELECT output_wav_path FROM render_chunks WHERE job_id = ? AND output_wav_path IS NOT NULL",
                (job["id"],),
            ).fetchall()]
            f, s = render._delete_job_audio(job, paths)
            files += f
            size += s
    finally:
        conn.close()

    # Mastered files: the episode's own output folder, plus any listed file
    # that lives somewhere else under OUTPUT_FOLDER. Episodes made before
    # output folders were made unique can share one (same title, same day);
    # anything another episode still points at is left alone.
    shared = _paths_used_by_other_episodes(episode_id)
    out_dir = episode.get("output_dir")
    if out_dir and out_dir not in shared["output_dir"]:
        f, s = _remove_tree(Path(out_dir), config.output_folder)
        files += f
        size += s
    for column in ("wav_master_path", "archive_mp3_path", "email_mp3_path"):
        path = episode.get(column)
        if path and path not in shared[column]:
            freed = _remove_file(Path(path), config.output_folder)
            files += 1 if freed else 0
            size += freed
    # The delivered copy in the share folder.
    try:
        share_copy = delivery._share_dest(episode)
        if episode.get("email_mp3_path") not in shared["email_mp3_path"]:
            freed = _remove_file(share_copy, config.share_location)
            files += 1 if freed else 0
            size += freed
    except Exception:  # noqa: BLE001 - no share copy is not an error here
        pass

    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM render_chunks WHERE job_id IN (SELECT id FROM render_jobs WHERE episode_id = ?)",
            (episode_id,),
        )
        conn.execute("DELETE FROM render_jobs WHERE episode_id = ?", (episode_id,))
        for table in _EPISODE_TABLES:
            conn.execute(f"DELETE FROM {table} WHERE episode_id = ?", (episode_id,))
        conn.execute("DELETE FROM episodes WHERE id = ?", (episode_id,))
        conn.commit()
    finally:
        conn.close()

    # Unload ComfyUI's models too, unless another episode is mid-render and
    # would only have to load them straight back.
    if not stopped_render and render.get_system_active_job() is None:
        comfyui.free_memory(config.comfyui_url)
    return {
        "title": episode["title"],
        "files_deleted": files,
        "bytes_freed": size,
        "stopped_render": stopped_render,
    }
