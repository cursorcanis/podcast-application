"""ffmpeg mastering + export — M4 (POD-11).

Takes every succeeded render_chunk for an episode's render job (app/render.py
owns that state) and turns it into the three deliverable files the plan
requires: a WAV master, a 192kbps stereo archive MP3, and a 96kbps mono
44.1kHz "email" MP3 with ID3 tags — written to
`OUTPUT_FOLDER/<date>_<slug>/`.

Pipeline, each step a separate `ffmpeg`/`ffprobe` subprocess so a failure
names the exact command and the exact stderr, never "something went wrong":

1. Normalize each chunk's own audio file (whatever codec ComfyUI's SaveAudio
   produced — FLAC in practice) to a common PCM WAV (44.1kHz mono).
2. Generate real silence clips for every `[PAUSE:Ns]` gap recorded on the
   chunks by app/chunking.py (a gap between two chunks is the SUM of the
   left chunk's pause_after and the right chunk's pause_before — see
   `_build_segments` — because two adjacent pause tags in the script are two
   separate, additive gaps, not the same gap counted twice).
3. Concatenate chunk audio and silence in script order (ffmpeg concat
   demuxer, same PCM format on every input so no filter graph is needed).
4. Apply the episode's configured speed via `atempo` (skipped at 1.0x).
5. Normalize to -16 LUFS via ffmpeg's two-pass `loudnorm` (a measure pass,
   then an apply pass with the measured values fed back in — single-pass
   loudnorm is explicitly inaccurate per ffmpeg's own docs, and "normalize to
   -16 LUFS" is a Board-confirmed number this module must actually hit, not
   approximate).
6. Export the WAV master, then the two MP3s from that master, with ID3 tags
   written directly by ffmpeg's mp3 muxer (no extra tagging dependency).

Never invents the TTS model, workflow, or render settings — this module only
ever reads `output_wav_path` from `render_chunks` rows app/render.py already
produced.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import render
from .config import config
from .db import get_connection

WORKING_SAMPLE_RATE = 44100
WORKING_CHANNELS = 1
TARGET_LUFS = -16.0
TARGET_TRUE_PEAK = -1.5
TARGET_LRA = 11.0
ARCHIVE_BITRATE_K = 192
EMAIL_BITRATE_K = 96
# A long episode's email copy steps down from 96 kbps until it fits under
# MAX_ATTACHMENT_MB (a 33-minute episode at 96 kbps is ~23 MB). Mono speech
# stays clear down to this floor.
EMAIL_BITRATE_FLOOR_K = 40
_EMAIL_BITRATE_STEPS_K = (96, 80, 64, 56, 48, EMAIL_BITRATE_FLOOR_K)
_MP3_SIZE_HEADROOM = 0.95  # ID3 tags + frame overhead


def email_bitrate_for(duration_seconds: float, max_mb: float) -> int:
    """Highest standard bitrate whose MP3 of `duration_seconds` fits inside
    `max_mb`, never below EMAIL_BITRATE_FLOOR_K (delivery's own size guard
    still catches anything that is too long even at the floor)."""
    budget_bits = max_mb * 1024 * 1024 * 8 * _MP3_SIZE_HEADROOM
    for kbps in _EMAIL_BITRATE_STEPS_K:
        if kbps * 1000 * duration_seconds <= budget_bits:
            return kbps
    return EMAIL_BITRATE_FLOOR_K

_SLUG_RE = re.compile(r"[^a-z0-9]+")


class PostprodError(ValueError):
    """A mastering/export step failed. The message names the exact ffmpeg/
    ffprobe command and the exact stderr tail — never a generic failure."""


def slugify(title: str) -> str:
    slug = _SLUG_RE.sub("-", (title or "").strip().lower()).strip("-")
    return slug or "episode"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_ffmpeg(cmd: list[str]) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    except FileNotFoundError as exc:
        raise PostprodError(
            f"'{cmd[0]}' is not on PATH. Install ffmpeg (and ffprobe) and make sure the "
            "Board's machine can run it from a terminal — see the README's setup section."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise PostprodError(f"Command timed out after 30 minutes: {' '.join(cmd)}") from exc
    if result.returncode != 0:
        stderr_tail = "\n".join(result.stderr.strip().splitlines()[-20:])
        raise PostprodError(f"Command failed ({result.returncode}): {' '.join(cmd)}\n{stderr_tail}")
    return result


def ffprobe_duration_seconds(path: Path) -> float:
    result = run_ffmpeg([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ])
    try:
        return float(result.stdout.strip())
    except ValueError as exc:
        raise PostprodError(f"ffprobe returned no duration for {path}: {result.stdout!r}") from exc


def _normalize_to_pcm(src: Path, dst: Path) -> None:
    if not src.exists():
        raise PostprodError(
            f"Chunk audio file does not exist on disk: {src}. ComfyUI's SaveAudio node may "
            "have written somewhere the app's OUTPUT_FOLDER resolution didn't expect."
        )
    run_ffmpeg([
        "ffmpeg", "-y", "-i", str(src),
        "-ar", str(WORKING_SAMPLE_RATE), "-ac", str(WORKING_CHANNELS),
        "-c:a", "pcm_s16le", str(dst),
    ])


def _make_silence(dst: Path, seconds: float) -> None:
    run_ffmpeg([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"anullsrc=r={WORKING_SAMPLE_RATE}:cl=mono",
        "-t", f"{max(seconds, 0.0):.3f}",
        "-c:a", "pcm_s16le", str(dst),
    ])


@dataclass(frozen=True)
class _Segment:
    kind: str  # "audio" | "silence"
    duration_seconds: float
    chunk: dict | None = None


def _build_segments(chunks: list[dict]) -> list[_Segment]:
    """Script order: audio and real silence gaps interleaved. A gap between
    chunk i and i+1 is chunk[i].pause_after_seconds + chunk[i+1].pause_before_seconds
    (additive — see module docstring), except the leading gap before the
    first chunk (chunk[0].pause_before_seconds alone) and the trailing gap
    after the last chunk (chunk[-1].pause_after_seconds alone)."""
    segments: list[_Segment] = []
    for i, chunk in enumerate(chunks):
        if i == 0 and chunk["pause_before_seconds"] > 0:
            segments.append(_Segment("silence", chunk["pause_before_seconds"]))
        segments.append(_Segment("audio", chunk["measured_render_seconds"] or 0.0, chunk))
        gap = chunk["pause_after_seconds"] or 0.0
        if i + 1 < len(chunks):
            gap += chunks[i + 1]["pause_before_seconds"] or 0.0
        if gap > 0:
            segments.append(_Segment("silence", gap))
    return segments


def _concat_segments(segments: list[_Segment], workdir: Path, dst: Path) -> None:
    list_path = workdir / "concat_list.txt"
    lines: list[str] = []
    for idx, seg in enumerate(segments):
        piece_path = workdir / f"seg_{idx:04d}.wav"
        if seg.kind == "audio":
            assert seg.chunk is not None
            _normalize_to_pcm(Path(seg.chunk["output_wav_path"]), piece_path)
        else:
            _make_silence(piece_path, seg.duration_seconds)
        escaped = str(piece_path).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    run_ffmpeg([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_path),
        "-c:a", "pcm_s16le", str(dst),
    ])


def _apply_speed(src: Path, dst: Path, speed: float) -> None:
    if abs(speed - 1.0) < 1e-6:
        shutil.copyfile(src, dst)
        return
    run_ffmpeg(["ffmpeg", "-y", "-i", str(src), "-filter:a", f"atempo={speed:.4f}", "-c:a", "pcm_s16le", str(dst)])


def _loudnorm_measure(src: Path) -> dict[str, str]:
    result = run_ffmpeg([
        "ffmpeg", "-i", str(src),
        "-af", f"loudnorm=I={TARGET_LUFS}:TP={TARGET_TRUE_PEAK}:LRA={TARGET_LRA}:print_format=json",
        "-f", "null", "-",
    ])
    text = result.stderr
    start = text.rfind("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise PostprodError(f"Could not parse loudnorm measurement output for {src}:\n{text[-800:]}")
    return json.loads(text[start : end + 1])


def _loudnorm_apply(src: Path, dst: Path, measured: dict[str, str]) -> None:
    filt = (
        f"loudnorm=I={TARGET_LUFS}:TP={TARGET_TRUE_PEAK}:LRA={TARGET_LRA}:"
        f"measured_I={measured['input_i']}:measured_TP={measured['input_tp']}:"
        f"measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}:"
        f"offset={measured['target_offset']}:linear=true"
    )
    run_ffmpeg([
        "ffmpeg", "-y", "-i", str(src), "-af", filt,
        "-ar", str(WORKING_SAMPLE_RATE), "-ac", str(WORKING_CHANNELS),
        "-c:a", "pcm_s16le", str(dst),
    ])


def _export_mp3(src_wav: Path, dst_mp3: Path, *, bitrate_k: int, channels: int, tags: dict[str, str]) -> None:
    cmd = [
        "ffmpeg", "-y", "-i", str(src_wav),
        "-codec:a", "libmp3lame", "-b:a", f"{bitrate_k}k",
        "-ar", str(WORKING_SAMPLE_RATE), "-ac", str(channels),
        "-id3v2_version", "3",
    ]
    for key, value in tags.items():
        cmd += ["-metadata", f"{key}={value}"]
    cmd.append(str(dst_mp3))
    run_ffmpeg(cmd)


def episode_output_dir(episode: dict) -> Path:
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return Path(config.output_folder) / f"{date_str}_{slugify(episode['title'])}"


def run_postproduction(episode_id: int) -> dict[str, Any]:
    """Master and export an episode whose render job has fully succeeded.
    Raises PostprodError naming the exact failing step; never leaves
    `episodes.qa_status` set on a partial/failed run."""
    conn = get_connection()
    try:
        episode = conn.execute("SELECT * FROM episodes WHERE id = ?", (episode_id,)).fetchone()
        if episode is None:
            raise PostprodError(f"Episode {episode_id} does not exist.")
        episode = dict(episode)
        if episode["status"] not in ("rendered", "postproduction", "qa_failed"):
            raise PostprodError(
                f"Episode {episode_id} is not ready for mastering (status is "
                f"'{episode['status']}'); the render stage must succeed first."
            )
        job = conn.execute(
            "SELECT * FROM render_jobs WHERE episode_id = ? ORDER BY id DESC LIMIT 1", (episode_id,)
        ).fetchone()
        if job is None or job["status"] != "succeeded":
            raise PostprodError(f"Episode {episode_id} has no succeeded render job to master.")
        job_id = int(job["id"])
    finally:
        conn.close()

    chunks = render.list_succeeded_chunks_for_job(job_id)
    if not chunks:
        raise PostprodError(f"Render job {job_id} has no chunks to master.")

    conn = get_connection()
    try:
        conn.execute(
            "UPDATE episodes SET status = 'postproduction', updated_at = ? WHERE id = ?",
            (_utcnow_iso(), episode_id),
        )
        conn.commit()
    finally:
        conn.close()

    out_dir = episode_output_dir(episode)
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = slugify(episode["title"])

    try:
        with tempfile.TemporaryDirectory(prefix=f"podcast_foundry_postprod_ep{episode_id}_") as tmp:
            workdir = Path(tmp)
            segments = _build_segments(chunks)
            concatenated = workdir / "concatenated.wav"
            _concat_segments(segments, workdir, concatenated)

            tempo_applied = workdir / "tempo.wav"
            _apply_speed(concatenated, tempo_applied, float(episode["speed"]))

            measured = _loudnorm_measure(tempo_applied)
            wav_master_path = out_dir / f"{slug}_master.wav"
            _loudnorm_apply(tempo_applied, wav_master_path, measured)

        duration_seconds = ffprobe_duration_seconds(wav_master_path)

        tags = {
            "title": episode["title"],
            "artist": "Podcast Foundry",
            "album": "Podcast Foundry",
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        }
        archive_mp3_path = out_dir / f"{slug}_archive_192k.mp3"
        _export_mp3(wav_master_path, archive_mp3_path, bitrate_k=ARCHIVE_BITRATE_K, channels=2, tags=tags)

        email_kbps = email_bitrate_for(duration_seconds, config.max_attachment_mb)
        email_mp3_path = out_dir / f"{slug}_email_{email_kbps}k.mp3"
        _export_mp3(wav_master_path, email_mp3_path, bitrate_k=email_kbps, channels=1, tags=tags)
    except PostprodError:
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE episodes SET status = 'rendered', updated_at = ? WHERE id = ?",
                (_utcnow_iso(), episode_id),
            )
            conn.commit()
        finally:
            conn.close()
        raise

    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE episodes SET
                output_dir = ?, wav_master_path = ?, archive_mp3_path = ?, email_mp3_path = ?,
                measured_duration_seconds = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                str(out_dir), str(wav_master_path), str(archive_mp3_path), str(email_mp3_path),
                duration_seconds, _utcnow_iso(), episode_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "episode_id": episode_id,
        "job_id": job_id,
        "output_dir": str(out_dir),
        "wav_master_path": str(wav_master_path),
        "archive_mp3_path": str(archive_mp3_path),
        "email_mp3_path": str(email_mp3_path),
        "measured_duration_seconds": duration_seconds,
        "chunks": chunks,
    }
