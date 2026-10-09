"""QA and show notes — M4 (POD-11).

Runs after app/postprod.py masters an episode. Checks what can honestly be
checked without a human listener or a funded ASR model (MONTHLY_BUDGET is
$0 — no guessed LLM/ASR integration, per the build plan's Risk R5 lens):

  - duration floor: the master is at/above the episode's requested length.
  - clipping: each chunk's OWN rendered audio (before mastering touches it)
    is checked with ffmpeg's `astats`, so a clipped chunk can be pinned to
    its exact chunk id and re-rendered alone.
  - silence gaps over 3s: the final master is checked with ffmpeg's
    `silencedetect` — this includes gaps the script's own `[PAUSE:Ns]` tags
    created; a tag asking for more than 3s fails the same as a TTS glitch,
    honestly, rather than this module silently capping it.
  - speaker-voice match: every chunk's `voice_reference_used` (recorded at
    render time, app/render.py) is compared against the CURRENT
    VOICE_MAPPING for that speaker — bookkeeping, not acoustic
    classification, but it is what this app can actually verify; the
    mapping itself is a Board/Audio-Engineer decision, never guessed here.

Mispronunciation checking has no automatable proxy this app can run today
(that would need an ASR model this project has no funded or proven path to
run — Risk R5). The QA report says so explicitly and does NOT gate pass/fail
on it, rather than faking a check that never happened.

On failure, only the chunks that actually failed a per-chunk check (clipping
or voice mismatch) are queued for re-render via
render.requeue_chunks_for_rerender — never the whole episode.

On pass (M5, POD-12), QA hands the episode to delivery.run_delivery_on_qa_pass,
which — in today's paused state — records `status='paused'`, copies the final
email MP3 to SHARE_LOCATION, and surfaces the "collect your episode here"
notice. That path is config-gated, never a code change, so the same QA pass
activates a real send the moment EMAIL_METHOD is set (POD-7). QA never emails
directly: the delivery module owns that boundary end to end.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from . import delivery, postprod, render
from .config import config
from .db import get_connection

CLIP_PEAK_DB_THRESHOLD = -0.5   # astats "Peak level dB" at/above this is at-ceiling
CLIP_FLAT_FACTOR_THRESHOLD = 0.5  # astats "Flat factor" above this indicates a flattened (clipped) waveform
# Chatterbox peak-normalises its output to about -0.09 dBFS, so a few samples
# sitting at that ceiling is normal, not clipping (mastering's -1.5 dBTP
# limiter handles them). Only a chunk with a real run of flattened peaks —
# more than this fraction of its samples at the ceiling — is flagged.
CLIP_PEAK_SAMPLE_FRACTION = 1e-4
MAX_SILENCE_GAP_SECONDS = 3.0
SILENCE_NOISE_FLOOR_DB = "-50dB"
SILENCE_MIN_DURATION_S = 0.5

_PEAK_RE = re.compile(r"Peak level dB:\s*(-?inf|-?[\d.]+)")
_FLAT_RE = re.compile(r"Flat factor:\s*(-?inf|-?[\d.]+)")
_PEAK_COUNT_RE = re.compile(r"Peak count:\s*([\d.]+)")
_SAMPLES_RE = re.compile(r"Number of samples:\s*([\d.]+)")
_SILENCE_START_RE = re.compile(r"silence_start:\s*([\d.]+)")
_SILENCE_DURATION_RE = re.compile(r"silence_duration:\s*([\d.]+)")


class QAError(ValueError):
    """Raised for a rejected QA action (e.g. QA run on an episode that
    hasn't been mastered yet). Names the exact reason."""


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_last_float(pattern: re.Pattern, text: str) -> float | None:
    matches = pattern.findall(text)
    if not matches:
        return None
    raw = matches[-1]
    return float("-inf") if raw == "-inf" else float("inf") if raw == "inf" else float(raw)


def _check_chunk_clipping(path: str) -> tuple[bool, str]:
    """Returns (clipped, detail) for one chunk's own rendered audio file."""
    result = postprod.run_ffmpeg(["ffmpeg", "-i", path, "-af", "astats=metadata=0:reset=0", "-f", "null", "-"])
    stderr = result.stderr
    peak_db = _parse_last_float(_PEAK_RE, stderr)
    flat_factor = _parse_last_float(_FLAT_RE, stderr)
    if peak_db is None or flat_factor is None:
        return False, "astats produced no Peak level / Flat factor reading"
    peak_count = _parse_last_float(_PEAK_COUNT_RE, stderr)
    samples = _parse_last_float(_SAMPLES_RE, stderr)
    clipped = peak_db >= CLIP_PEAK_DB_THRESHOLD and flat_factor >= CLIP_FLAT_FACTOR_THRESHOLD
    detail = f"Peak level {peak_db:.2f} dB, flat factor {flat_factor:.2f}"
    if clipped and peak_count is not None and samples:
        fraction = peak_count / samples
        clipped = fraction > CLIP_PEAK_SAMPLE_FRACTION
        detail += f", {int(peak_count)} of {int(samples)} samples at peak"
    return clipped, detail


def _find_silence_gaps(path: str) -> list[float]:
    result = postprod.run_ffmpeg([
        "ffmpeg", "-i", path, "-af",
        f"silencedetect=noise={SILENCE_NOISE_FLOOR_DB}:d={SILENCE_MIN_DURATION_S}",
        "-f", "null", "-",
    ])
    starts = [float(x) for x in _SILENCE_START_RE.findall(result.stderr)]
    durations = [float(x) for x in _SILENCE_DURATION_RE.findall(result.stderr)]
    return durations[: len(starts)]


def _write_document(conn, episode_id: int, kind: str, body: str) -> None:
    conn.execute(
        "INSERT INTO episode_documents (episode_id, kind, body, created_at) VALUES (?, ?, ?, ?)",
        (episode_id, kind, body, _utcnow_iso()),
    )


def _render_qa_report_markdown(
    episode: dict,
    *,
    duration_seconds: float,
    duration_floor_seconds: float,
    duration_ok: bool,
    clip_results: list[dict],
    silence_gaps: list[float],
    silence_ok: bool,
    voice_results: list[dict],
    voice_ok: bool,
    overall_pass: bool,
) -> str:
    lines = [
        f"# QA report — {episode['title']}",
        "",
        f"Checked at {_utcnow_iso()}. Overall: **{'PASS' if overall_pass else 'FAIL'}**",
        "",
        "## Duration floor",
        f"- Requested: {episode['length_minutes_target']} min ({duration_floor_seconds:.0f}s)",
        f"- Measured master duration: {duration_seconds:.1f}s",
        f"- Result: {'OK' if duration_ok else 'FAIL — below the requested length'}",
        "",
        "## Clipping (per chunk, on the chunk's own rendered audio)",
    ]
    if clip_results:
        for r in clip_results:
            mark = "FAIL" if r["clipped"] else "ok"
            lines.append(f"- chunk {r['chunk_index']} ({r['speaker']}): {mark} — {r['detail']}")
    else:
        lines.append("- No chunks to check.")
    lines += [
        "",
        "## Silence gaps over 3s (final master, includes script [PAUSE] tags)",
        f"- Max cap: {MAX_SILENCE_GAP_SECONDS:.1f}s",
    ]
    if silence_gaps:
        lines.append(f"- Gaps found: {', '.join(f'{g:.1f}s' for g in silence_gaps)}")
    else:
        lines.append("- No silence gaps detected at or above the detection floor.")
    lines.append(f"- Result: {'OK' if silence_ok else 'FAIL — a gap exceeds the 3s cap'}")
    lines += [
        "",
        "## Speaker-voice match (recorded render voice vs. current VOICE_MAPPING)",
    ]
    for r in voice_results:
        mark = "ok" if r["match"] else "FAIL"
        lines.append(
            f"- chunk {r['chunk_index']} ({r['speaker']}): {mark} — used '{r['voice_reference_used']}', "
            f"configured '{r['configured_voice_reference']}'"
        )
    lines.append(f"- Result: {'OK' if voice_ok else 'FAIL — at least one chunk used an unexpected voice'}")
    lines += [
        "",
        "## Mispronunciations",
        "- NOT AUTOMATED. This app has no funded/proven ASR path (build plan Risk R5) to check "
        "this without a human listener. Does not gate pass/fail. A human must listen and confirm "
        "separately before this episode is considered fully signed off.",
    ]
    return "\n".join(lines)


def _render_show_notes_markdown(episode: dict, sources: list[dict], duration_seconds: float) -> str:
    lines = [
        f"# {episode['title']}",
        "",
        f"*{episode['topic_or_query']}*",
        "",
        f"Duration: {duration_seconds / 60.0:.1f} minutes · Format: {episode['format']} · "
        f"Audience: {episode['audience_level'] or 'general'}",
        "",
        "## Sources",
    ]
    approved = [s for s in sources if s["board_decision"] in ("approved", "added")]
    if approved:
        for s in approved:
            pub = f" — {s['publication']}" if s["publication"] else ""
            date = f" ({s['published_at']})" if s["published_at"] else ""
            url = f"\n  {s['url']}" if s["url"] else ""
            lines.append(f"- {s['title']}{pub}{date}{url}")
    else:
        lines.append("- (no approved sources recorded)")
    return "\n".join(lines)


def run_qa(episode_id: int) -> dict[str, Any]:
    conn = get_connection()
    try:
        episode = conn.execute("SELECT * FROM episodes WHERE id = ?", (episode_id,)).fetchone()
        if episode is None:
            raise QAError(f"Episode {episode_id} does not exist.")
        episode = dict(episode)
        if episode["status"] not in ("postproduction", "qa", "qa_failed"):
            raise QAError(
                f"Episode {episode_id} has not been mastered yet (status is "
                f"'{episode['status']}') — run post-production before QA."
            )
        if not episode["wav_master_path"]:
            raise QAError(f"Episode {episode_id} has no mastered WAV to check.")
        job = conn.execute(
            "SELECT * FROM render_jobs WHERE episode_id = ? ORDER BY id DESC LIMIT 1", (episode_id,)
        ).fetchone()
        if job is None:
            raise QAError(f"Episode {episode_id} has no render job on record.")
        job_id = int(job["id"])
        sources = [dict(r) for r in conn.execute(
            "SELECT * FROM sources WHERE episode_id = ?", (episode_id,)
        ).fetchall()]
        conn.execute(
            "UPDATE episodes SET status = 'qa', updated_at = ? WHERE id = ?",
            (_utcnow_iso(), episode_id),
        )
        conn.commit()
    finally:
        conn.close()

    chunks = render.list_succeeded_chunks_for_job(job_id)

    duration_seconds = postprod.ffprobe_duration_seconds(episode["wav_master_path"])
    duration_floor_seconds = float(episode["length_minutes_target"]) * 60.0
    duration_ok = duration_seconds >= duration_floor_seconds

    clip_results = []
    clipped_chunk_ids: list[int] = []
    voice_mapping = config.voice_mapping
    voice_results = []
    for chunk in chunks:
        clipped, detail = _check_chunk_clipping(chunk["output_wav_path"])
        clip_results.append({
            "chunk_index": chunk["chunk_index"], "speaker": chunk["speaker"],
            "clipped": clipped, "detail": detail,
        })
        if clipped:
            clipped_chunk_ids.append(chunk["id"])

        configured = voice_mapping.get(chunk["speaker"])
        used = chunk["voice_reference_used"]
        voice_results.append({
            "chunk_index": chunk["chunk_index"], "speaker": chunk["speaker"],
            "voice_reference_used": used, "configured_voice_reference": configured,
            "match": used == configured,
        })
    clipping_ok = not clipped_chunk_ids
    voice_ok = all(r["match"] for r in voice_results)
    mismatched_chunk_ids = [
        c["id"] for c, r in zip(chunks, voice_results) if not r["match"]
    ]

    silence_gaps = _find_silence_gaps(episode["wav_master_path"])
    over_cap_gaps = [g for g in silence_gaps if g > MAX_SILENCE_GAP_SECONDS]
    silence_ok = not over_cap_gaps

    overall_pass = duration_ok and clipping_ok and silence_ok and voice_ok

    conn = get_connection()
    try:
        for chunk_id in clipped_chunk_ids:
            conn.execute("UPDATE render_chunks SET qa_clip_detected = 1 WHERE id = ?", (chunk_id,))
        qa_report_md = _render_qa_report_markdown(
            episode,
            duration_seconds=duration_seconds, duration_floor_seconds=duration_floor_seconds,
            duration_ok=duration_ok, clip_results=clip_results, silence_gaps=silence_gaps,
            silence_ok=silence_ok, voice_results=voice_results, voice_ok=voice_ok,
            overall_pass=overall_pass,
        )
        _write_document(conn, episode_id, "qa_report", qa_report_md)
        if overall_pass:
            show_notes_md = _render_show_notes_markdown(episode, sources, duration_seconds)
            _write_document(conn, episode_id, "show_notes", show_notes_md)
        conn.execute(
            "UPDATE episodes SET status = ?, qa_status = ?, updated_at = ? WHERE id = ?",
            (
                "ready" if overall_pass else "qa_failed",
                "pass" if overall_pass else "fail",
                _utcnow_iso(),
                episode_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    failing_chunk_ids = sorted(set(clipped_chunk_ids) | set(mismatched_chunk_ids))

    delivery_result = None
    if overall_pass:
        # M5 (POD-12): QA never emails directly — it hands to the delivery
        # boundary, which records 'paused' + shares the MP3 today and would
        # send the moment EMAIL_METHOD is set.
        delivery_result = delivery.run_delivery_on_qa_pass(episode_id)

    return {
        "episode_id": episode_id,
        "job_id": job_id,
        "overall_pass": overall_pass,
        "duration_ok": duration_ok,
        "clipping_ok": clipping_ok,
        "silence_ok": silence_ok,
        "voice_ok": voice_ok,
        "failing_chunk_ids": failing_chunk_ids,
        "duration_seconds": duration_seconds,
        "delivery": delivery_result,
    }


def retry_failing_chunks(episode_id: int) -> dict[str, Any]:
    """Re-render only the chunks the last QA pass flagged (clipping or
    voice mismatch) using render.requeue_chunks_for_rerender — never the
    whole episode. Raises QAError if the last QA run has no flagged chunks
    (e.g. it failed only on duration/silence, which no single chunk fix can
    address)."""
    conn = get_connection()
    try:
        episode = conn.execute("SELECT * FROM episodes WHERE id = ?", (episode_id,)).fetchone()
        if episode is None:
            raise QAError(f"Episode {episode_id} does not exist.")
        episode = dict(episode)
        if episode["qa_status"] != "fail":
            raise QAError(f"Episode {episode_id} has no failing QA run to retry.")
        job = conn.execute(
            "SELECT * FROM render_jobs WHERE episode_id = ? ORDER BY id DESC LIMIT 1", (episode_id,)
        ).fetchone()
        if job is None:
            raise QAError(f"Episode {episode_id} has no render job on record.")
        job_id = int(job["id"])
        voice_mapping = config.voice_mapping
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM render_chunks WHERE job_id = ?", (job_id,)
        ).fetchall()]
    finally:
        conn.close()

    failing_ids = [
        r["id"] for r in rows
        if r["qa_clip_detected"] or voice_mapping.get(r["speaker"]) != r["voice_reference_used"]
    ]
    if not failing_ids:
        raise QAError(
            f"Episode {episode_id}'s last QA failure has no specific chunk to re-render "
            "(it failed on duration or an overlong silence gap, not a per-chunk check) — "
            "fix the script/settings and re-run the full render."
        )
    render.requeue_chunks_for_rerender(job_id, failing_ids)
    return {"job_id": job_id, "requeued_chunk_ids": failing_ids}
