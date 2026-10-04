"""Voice profiles, tone/cadence presets, saved recipient lists, and the
30-second voice sample render — M6 (POD-13).

These are the configuration conveniences that let the Board drive an episode
without re-typing every choice, built strictly on what M1–M5 already proved:

- `voice_profiles` — a named voice (label) tied to a Chatterbox reference
  clip (comfyui_voice_ref) and, optionally, the path where that clip lives
  under ComfyUI/input. The 30s sample-render action below renders a short
  test line through the SAME ComfyUI client and the SAME workflow the episode
  render uses, with this profile's reference clip substituted at build time.
  It invents nothing: workflow, chunk timeout, model, and result format all
  come from the measured POD-3 values / Audio Engineer gate (app/render.py).
- `presets` — named tone / cadence presets offered as suggestions on the New
  Episode form. They are never silently substituted: the Board still picks,
  and the episode row records which defaults were applied.
- `recipient_lists` — named groupings of recipients for the New Episode form.
  Safety invariant (defense in depth, same rule as app/episodes.py): a saved
  list may only contain addresses already on the Board's DEFAULT_RECIPIENTS
  list, so a list can never smuggle an unapproved address into an episode or
  a future send. The "never exceeds DEFAULT_RECIPIENTS plus explicit
  per-episode additions" requirement from POD-13 is honoured by keeping the
  list ⊆ DEFAULT_RECIPIENTS and leaving per-episode additions to the episode
  record itself (which app/episodes.py already validates against the allowed
  set).

The sample render is an audition, not an episode artifact. Its status is held
in memory (a crash loses at most one short test line — documented, not
silent), it refuses to run while an episode render job is system-wide active
(the GPU is the bottleneck), and only one sample render may run at a time.
Progress is measured, never guessed: the status endpoint reports real elapsed
seconds and the exact error on failure.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import comfyui, workflow
from .config import config
from .db import get_connection
from .render import _extract_output_file, _resolve_output_path, get_system_active_job

# ~56 words ≈ 20–25s of speech at ~150 wpm — a short audition line, not a
# scripted sample with editorial meaning. Kept constant so every profile is
# auditioned with the identical text.
SAMPLE_TEXT = (
    "This is a thirty second voice sample produced by Podcast Foundry, so you "
    "can audition this voice profile before using it in an episode. It runs "
    "through the same ComfyUI workflow and the same reference clip the episode "
    "render will use, at the same measured setting. Listen for clarity, pacing, "
    "and whether the voice fits your show."
)

PRESET_KINDS = ("tone", "cadence")

_sample_lock = threading.Lock()
_sample_state: dict[int, dict[str, Any]] = {}


class SettingsError(ValueError):
    """A rejected settings write or sample-render action. The message names
    the exact field/constraint — never a generic 'invalid input'."""


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- Voice profiles ------------------------------------------------------------

def list_voice_profiles() -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM voice_profiles ORDER BY id ASC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def create_voice_profile(
    name: str,
    comfyui_voice_ref: str = "",
    reference_clip_path: str = "",
    notes: str = "",
) -> int:
    name = (name or "").strip()
    if not name:
        raise SettingsError("Voice profile name is required.")
    conn = get_connection()
    try:
        try:
            cur = conn.execute(
                "INSERT INTO voice_profiles (name, comfyui_voice_ref, reference_clip_path, sample_output_path, notes) "
                "VALUES (?, ?, ?, NULL, ?)",
                (
                    name,
                    (comfyui_voice_ref or "").strip() or None,
                    (reference_clip_path or "").strip() or None,
                    (notes or "").strip() or None,
                ),
            )
            conn.commit()
            return int(cur.lastrowid)
        except Exception as exc:  # noqa: BLE001 - surface the exact reason
            if "UNIQUE constraint failed" in str(exc):
                raise SettingsError(f"A voice profile named '{name}' already exists.") from exc
            raise
    finally:
        conn.close()


def delete_voice_profile(profile_id: int) -> None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT id FROM voice_profiles WHERE id = ?", (profile_id,)).fetchone()
        if row is None:
            raise SettingsError(f"Voice profile {profile_id} does not exist.")
        conn.execute("DELETE FROM voice_profiles WHERE id = ?", (profile_id,))
        conn.commit()
    finally:
        conn.close()


# --- Presets (tone / cadence) --------------------------------------------------

def list_presets(kind: str | None = None) -> list[dict]:
    conn = get_connection()
    try:
        if kind:
            rows = conn.execute(
                "SELECT * FROM presets WHERE kind = ? ORDER BY id ASC", (kind,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM presets ORDER BY kind ASC, id ASC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def create_preset(kind: str, name: str, params: str) -> int:
    kind = (kind or "").strip().lower()
    name = (name or "").strip()
    params = (params or "").strip()
    if kind not in PRESET_KINDS:
        raise SettingsError(f"Preset kind must be one of: {', '.join(PRESET_KINDS)}.")
    if not name:
        raise SettingsError("Preset name is required.")
    if not params:
        raise SettingsError("Preset value is required.")
    conn = get_connection()
    try:
        try:
            cur = conn.execute(
                "INSERT INTO presets (kind, name, params) VALUES (?, ?, ?)",
                (kind, name, params),
            )
            conn.commit()
            return int(cur.lastrowid)
        except Exception as exc:  # noqa: BLE001
            if "UNIQUE constraint failed" in str(exc):
                raise SettingsError(f"A {kind} preset named '{name}' already exists.") from exc
            raise
    finally:
        conn.close()


def delete_preset(preset_id: int) -> None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT id FROM presets WHERE id = ?", (preset_id,)).fetchone()
        if row is None:
            raise SettingsError(f"Preset {preset_id} does not exist.")
        conn.execute("DELETE FROM presets WHERE id = ?", (preset_id,))
        conn.commit()
    finally:
        conn.close()


# --- Saved recipient lists -----------------------------------------------------

def list_recipient_lists() -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM recipient_lists ORDER BY id ASC").fetchall()
        result = []
        for row in rows:
            d = dict(row)
            try:
                d["emails"] = json.loads(d.get("emails") or "[]")
            except (json.JSONDecodeError, TypeError):
                d["emails"] = []
            result.append(d)
        return result
    finally:
        conn.close()


def create_recipient_list(name: str, emails: list[str]) -> int:
    name = (name or "").strip()
    if not name:
        raise SettingsError("Recipient list name is required.")
    cleaned = [e.strip() for e in emails if e and e.strip()]
    if not cleaned:
        raise SettingsError("Add at least one recipient to the list.")
    allowed = set(config.default_recipients)
    rejected = [e for e in cleaned if e not in allowed]
    if rejected:
        raise SettingsError(
            "A saved recipient list may only contain addresses already on "
            f"DEFAULT_RECIPIENTS. Rejected: {', '.join(rejected)}."
        )
    conn = get_connection()
    try:
        try:
            cur = conn.execute(
                "INSERT INTO recipient_lists (name, emails) VALUES (?, ?)",
                (name, json.dumps(cleaned)),
            )
            conn.commit()
            return int(cur.lastrowid)
        except Exception as exc:  # noqa: BLE001
            if "UNIQUE constraint failed" in str(exc):
                raise SettingsError(f"A recipient list named '{name}' already exists.") from exc
            raise
    finally:
        conn.close()


def delete_recipient_list(list_id: int) -> None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT id FROM recipient_lists WHERE id = ?", (list_id,)).fetchone()
        if row is None:
            raise SettingsError(f"Recipient list {list_id} does not exist.")
        conn.execute("DELETE FROM recipient_lists WHERE id = ?", (list_id,))
        conn.commit()
    finally:
        conn.close()


# --- 30s voice sample render ---------------------------------------------------

def sample_status(profile_id: int) -> dict[str, Any]:
    """Measured status for a profile's sample render. Never raises — returns
    an honest idle state when there is no render in flight."""
    with _sample_lock:
        state = _sample_state.get(profile_id)
    if state is None:
        return {"profile_id": profile_id, "status": "idle", "elapsed_seconds": 0.0}
    elapsed = round(time.monotonic() - state["started_mono"], 1) if state.get("started_mono") else 0.0
    return {
        "profile_id": profile_id,
        "status": state["status"],
        "elapsed_seconds": elapsed,
        "output_path": state.get("output_path"),
        "error_detail": state.get("error_detail"),
    }


def _any_sample_running() -> int | None:
    with _sample_lock:
        for profile_id, state in _sample_state.items():
            if state["status"] == "running":
                return profile_id
    return None


def start_sample_render(profile_id: int) -> None:
    """Start a 30s audition render for this voice profile in a background
    thread. Refuses when an episode render job is system-wide active (one GPU,
    one job) or when another sample is already running."""
    active_job = get_system_active_job()
    if active_job is not None:
        raise SettingsError(
            f"Render job {active_job['id']} for episode {active_job['episode_id']} is "
            f"{active_job['status']} — only one render may occupy the GPU at a time."
        )
    running = _any_sample_running()
    if running is not None:
        raise SettingsError(
            f"A sample render for voice profile {running} is already running — "
            "only one sample render at a time."
        )

    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM voice_profiles WHERE id = ?", (profile_id,)).fetchone()
        if row is None:
            raise SettingsError(f"Voice profile {profile_id} does not exist.")
        profile = dict(row)
    finally:
        conn.close()

    voice_ref = profile.get("comfyui_voice_ref")
    if not voice_ref:
        raise SettingsError(
            f"Voice profile '{profile['name']}' has no comfyui_voice_ref (reference clip "
            "filename). Set one before rendering a sample."
        )

    with _sample_lock:
        _sample_state[profile_id] = {
            "status": "running",
            "started_mono": time.monotonic(),
            "output_path": None,
            "error_detail": None,
        }
    thread = threading.Thread(
        target=_run_sample, args=(profile_id, voice_ref), name=f"voice-sample-{profile_id}", daemon=True
    )
    thread.start()


def _run_sample(profile_id: int, voice_ref: str) -> None:
    def finish(*, status: str, output_path: str | None = None, error_detail: str | None = None) -> None:
        with _sample_lock:
            _sample_state[profile_id] = {
                "status": status,
                "started_mono": _sample_state[profile_id]["started_mono"],
                "output_path": output_path,
                "error_detail": error_detail,
            }

    try:
        wf = workflow.load_workflow(config.path_to_workflow_json)
        roles = workflow.resolve_roles(wf)
        prefix = f"podcast_foundry/voice_sample/profile_{profile_id}_{int(time.time())}"
        prompt = workflow.build_prompt(
            wf, roles, text=SAMPLE_TEXT, filename_prefix=prefix, voice_reference_filename=voice_ref
        )
        prompt_id = comfyui.submit_prompt(config.comfyui_url, prompt, "podcast_foundry_sample")
        result = comfyui.poll_history(
            config.comfyui_url, prompt_id, timeout_s=config.chunk_timeout_min * 60.0
        )
    except workflow.WorkflowError as exc:
        finish(status="failed", error_detail=f"Workflow error: {exc}")
        return
    except comfyui.ComfyUIError as exc:
        finish(status="failed", error_detail=f"ComfyUI error: {exc}")
        return

    if result["outcome"] != "succeeded":
        finish(
            status="failed",
            error_detail=f"Sample render {result['outcome']}: {result.get('error_detail')}",
        )
        return

    output = _extract_output_file(result.get("outputs", {}), roles.save_node_id)
    if output is None:
        finish(
            status="failed",
            error_detail=f"ComfyUI reported success but no output file under save node "
            f"'{roles.save_node_id}': {result.get('outputs')}",
        )
        return
    subfolder, filename = output
    output_path = _resolve_output_path(subfolder, filename)

    conn = get_connection()
    try:
        conn.execute(
            "UPDATE voice_profiles SET sample_output_path = ? WHERE id = ?",
            (output_path, profile_id),
        )
        conn.commit()
    finally:
        conn.close()

    finish(status="succeeded", output_path=output_path)