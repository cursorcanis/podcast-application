"""Script chunking for ComfyUI rendering — M3 (POD-10).

Chunks by speaker turn (per the POD-3 benchmark: "the pipeline chunks by
speaker turn anyway"), because the workflow's voice-reference loader is
swapped per speaker, not per sentence. A turn whose estimated audio would
exceed `chunk_seconds_target` is further split on sentence boundaries so no
single ComfyUI submission risks the node's own ~163s `max_new_tokens`
ceiling (POD-3 benchmark, section 2) or an over-long OOM-prone chunk.

`[PAUSE:Ns]` tags are stripped from the text sent to the TTS node (Chatterbox
has no pause syntax); turning them into real silence is ffmpeg mastering
(M4), not this module's job.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

WORDS_PER_MINUTE = 150
DEFAULT_SPEAKER = "HOST_A"
MIN_CHUNK_SECONDS_TARGET = 8

SPEAKER_TAG_RE = re.compile(r"\[(HOST_[A-Z0-9]+|NARRATOR|GUEST)\]", re.IGNORECASE)
PAUSE_TAG_RE = re.compile(r"\[PAUSE:\s*[\d.]+\s*s?\]", re.IGNORECASE)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class ScriptChunk:
    speaker: str
    text: str
    estimated_seconds: float


def estimate_seconds(text: str, speed: float = 1.0) -> float:
    """Estimate audio duration at ~150 spoken words/minute, adjusted for the
    episode's configured playback speed (plan section: 150 wpm at 1.0x)."""
    words = len(text.split())
    minutes = words / WORDS_PER_MINUTE
    return round((minutes * 60.0) / max(speed, 0.01), 1)


def _clean_text(raw_text: str) -> str:
    no_pauses = PAUSE_TAG_RE.sub(" ", raw_text)
    return _WHITESPACE_RE.sub(" ", no_pauses).strip()


def _split_into_turns(script: str) -> list[tuple[str, str]]:
    """Split the raw script into (speaker, text) turns on speaker tags. Text
    before the first speaker tag (or the whole script, if it has no tags at
    all — a solo-format episode) is attributed to DEFAULT_SPEAKER."""
    matches = list(SPEAKER_TAG_RE.finditer(script))
    if not matches:
        text = script.strip()
        return [(DEFAULT_SPEAKER, text)] if text else []

    turns: list[tuple[str, str]] = []
    if matches[0].start() > 0:
        preamble = script[: matches[0].start()].strip()
        if preamble:
            turns.append((DEFAULT_SPEAKER, preamble))
    for i, m in enumerate(matches):
        speaker = m.group(1).upper()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(script)
        text = script[start:end].strip()
        if text:
            turns.append((speaker, text))
    return turns


def split_text_to_target(text: str, speed: float, max_seconds: float) -> list[str]:
    """Split one piece of text on sentence boundaries so no resulting piece's
    estimated audio exceeds `max_seconds`. A single sentence longer than the
    target is kept whole (never split mid-word) and simply runs over — the
    OOM backoff path re-calls this with a smaller `max_seconds` rather than
    ever cutting a sentence in half."""
    sentences = [s for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]
    if not sentences:
        return [text] if text.strip() else []
    pieces: list[str] = []
    current: list[str] = []
    for sentence in sentences:
        candidate = " ".join(current + [sentence]) if current else sentence
        if current and estimate_seconds(candidate, speed) > max_seconds:
            pieces.append(" ".join(current))
            current = [sentence]
        else:
            current.append(sentence)
    if current:
        pieces.append(" ".join(current))
    return pieces


def chunk_script(
    script: str, *, speed: float = 1.0, chunk_seconds_target: float = 60.0
) -> list[ScriptChunk]:
    """Chunk a full script into ScriptChunk pieces ready to submit to
    ComfyUI, one speaker turn at a time, sub-split to `chunk_seconds_target`."""
    chunks: list[ScriptChunk] = []
    for speaker, raw_text in _split_into_turns(script):
        clean = _clean_text(raw_text)
        if not clean:
            continue
        for piece in split_text_to_target(clean, speed, chunk_seconds_target):
            piece = piece.strip()
            if piece:
                chunks.append(
                    ScriptChunk(speaker=speaker, text=piece, estimated_seconds=estimate_seconds(piece, speed))
                )
    return chunks
