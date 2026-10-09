"""Script chunking for ComfyUI rendering — M3 (POD-10).

Chunks by speaker turn (per the POD-3 benchmark: "the pipeline chunks by
speaker turn anyway"), because the workflow's voice-reference loader is
swapped per speaker, not per sentence. A turn whose estimated audio would
exceed `chunk_seconds_target` is further split on sentence boundaries so no
single ComfyUI submission risks the node's own ~163s `max_new_tokens`
ceiling (POD-3 benchmark, section 2) or an over-long OOM-prone chunk.

`[PAUSE:Ns]` tags are stripped from the text sent to the TTS node (Chatterbox
has no pause syntax) but their durations are NOT discarded: each ScriptChunk
carries `pause_before_seconds`/`pause_after_seconds` for any pause tag that
sat immediately before/after its text in the original script, so M4's
ffmpeg mastering (app/postprod.py) can insert real silence of the right
length at the right point when concatenating rendered chunks. Turning a
duration into actual silence audio is mastering's job, not this module's —
this module's job is to make sure that duration survives chunking at all.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

WORDS_PER_MINUTE = 150
DEFAULT_SPEAKER = "HOST_A"
MIN_CHUNK_SECONDS_TARGET = 8

SPEAKER_TAG_RE = re.compile(r"\[(HOST_[A-Z0-9]+|NARRATOR|GUEST)\]", re.IGNORECASE)
PAUSE_TAG_RE = re.compile(r"\[PAUSE:\s*([\d.]+)\s*s?\]", re.IGNORECASE)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class ScriptChunk:
    speaker: str
    text: str
    estimated_seconds: float
    pause_before_seconds: float = 0.0
    pause_after_seconds: float = 0.0


def estimate_seconds(text: str, speed: float = 1.0) -> float:
    """Estimate audio duration at ~150 spoken words/minute, adjusted for the
    episode's configured playback speed (plan section: 150 wpm at 1.0x)."""
    words = len(text.split())
    minutes = words / WORDS_PER_MINUTE
    return round((minutes * 60.0) / max(speed, 0.01), 1)


def _normalize_whitespace(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text).strip()


def _split_text_and_pauses(raw_text: str) -> list[tuple[str, float]]:
    """Split turn text on `[PAUSE:Ns]` tags into (text_segment, pause_after_seconds)
    pairs, in order. `pause_after_seconds` is the duration of any pause tag that
    immediately followed this text segment (0.0 if none). A pause with no
    preceding text — consecutive pause tags, or a pause at the very start of a
    turn — produces a ("", seconds) pair so its duration is never silently
    dropped; the caller folds that into the next non-empty segment's leading
    pause."""
    parts = PAUSE_TAG_RE.split(raw_text)
    pairs: list[tuple[str, float]] = []
    for i in range(0, len(parts), 2):
        text = parts[i]
        pause = float(parts[i + 1]) if i + 1 < len(parts) else 0.0
        pairs.append((text, pause))
    return pairs


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


MIN_WORDS_TO_SPLIT_SENTENCE = 12


def split_in_half(text: str) -> list[str]:
    """Split text into two roughly equal pieces: on a sentence boundary when
    there is more than one sentence, else at the comma (or, for a long
    sentence, the word) nearest the middle. Returns [text] when it is too
    short to split sensibly. Used when a rendered chunk came back cut off at
    the TTS length ceiling — proof the length estimate was wrong, so the
    split can't be driven by the estimate."""
    sentences = [s for s in _SENTENCE_SPLIT_RE.split(text.strip()) if s.strip()]
    if len(sentences) > 1:
        total = sum(len(s.split()) for s in sentences)
        running, cut = 0, 1
        for i, s in enumerate(sentences[:-1], start=1):
            running += len(s.split())
            cut = i
            if running >= total / 2:
                break
        return [" ".join(sentences[:cut]), " ".join(sentences[cut:])]
    words = text.split()
    if len(words) < MIN_WORDS_TO_SPLIT_SENTENCE:
        return [text]
    mid = len(words) // 2
    comma_positions = [i + 1 for i, w in enumerate(words[:-1]) if w.endswith((",", ";", ":"))]
    if comma_positions:
        mid = min(comma_positions, key=lambda i: abs(i - len(words) / 2))
    return [" ".join(words[:mid]), " ".join(words[mid:])]


def chunk_script(
    script: str, *, speed: float = 1.0, chunk_seconds_target: float = 60.0
) -> list[ScriptChunk]:
    """Chunk a full script into ScriptChunk pieces ready to submit to
    ComfyUI, one speaker turn at a time, sub-split to `chunk_seconds_target`.
    Every `[PAUSE:Ns]` tag's duration is preserved on the chunk immediately
    before/after it (pause_before_seconds / pause_after_seconds) rather than
    discarded — see module docstring."""
    chunks: list[ScriptChunk] = []
    pending_pause_before = 0.0
    for speaker, raw_text in _split_into_turns(script):
        for text_segment, pause_after in _split_text_and_pauses(raw_text):
            clean = _normalize_whitespace(text_segment)
            if not clean:
                # No spoken text in this segment (consecutive pause tags, or a
                # pause at the very start of the turn) — carry its duration
                # forward onto the next segment's leading pause instead of
                # dropping it.
                pending_pause_before += pause_after
                continue
            pieces = [p.strip() for p in split_text_to_target(clean, speed, chunk_seconds_target) if p.strip()]
            for idx, piece in enumerate(pieces):
                chunks.append(
                    ScriptChunk(
                        speaker=speaker,
                        text=piece,
                        estimated_seconds=estimate_seconds(piece, speed),
                        pause_before_seconds=pending_pause_before if idx == 0 else 0.0,
                        pause_after_seconds=pause_after if idx == len(pieces) - 1 else 0.0,
                    )
                )
            pending_pause_before = 0.0
    return chunks
