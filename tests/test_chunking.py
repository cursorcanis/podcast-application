"""Unit tests for app/chunking.py — M3 (POD-10). Pure functions, no DB/IO."""
from __future__ import annotations

from app import chunking


def test_two_host_script_splits_into_turns_by_speaker():
    script = "[HOST_A] Welcome to the show. [HOST_B] Thanks for having me."
    chunks = chunking.chunk_script(script)
    assert [c.speaker for c in chunks] == ["HOST_A", "HOST_B"]
    assert chunks[0].text == "Welcome to the show."
    assert chunks[1].text == "Thanks for having me."


def test_solo_script_with_no_speaker_tags_is_one_default_speaker_chunk():
    script = "This is a solo narration with no speaker tags at all."
    chunks = chunking.chunk_script(script)
    assert len(chunks) == 1
    assert chunks[0].speaker == chunking.DEFAULT_SPEAKER


def test_pause_tags_are_stripped_from_tts_text_but_duration_is_preserved():
    # M4 (POD-11): a [PAUSE:Ns] tag must become real silence at mastering
    # time, which means the text on either side of it has to render as
    # SEPARATE chunks (there is no other way to splice silence into the
    # middle of a single rendered clip) — the duration itself is kept on
    # the chunk boundary rather than discarded.
    script = "[HOST_A] First part. [PAUSE:0.5s] Second part."
    chunks = chunking.chunk_script(script)
    assert len(chunks) == 2
    assert [c.text for c in chunks] == ["First part.", "Second part."]
    assert all("PAUSE" not in c.text for c in chunks)
    assert chunks[0].pause_after_seconds == 0.5
    assert chunks[1].pause_before_seconds == 0.0


def test_pause_at_start_of_turn_is_not_dropped():
    script = "[HOST_A] [PAUSE:1.5s] Hello there."
    chunks = chunking.chunk_script(script)
    assert len(chunks) == 1
    assert chunks[0].text == "Hello there."
    assert chunks[0].pause_before_seconds == 1.5


def test_consecutive_pause_tags_are_additive_across_the_boundary():
    script = "[HOST_A] First. [PAUSE:0.3s][PAUSE:0.5s] Second."
    chunks = chunking.chunk_script(script)
    assert len(chunks) == 2
    assert chunks[0].pause_after_seconds == 0.3
    assert chunks[1].pause_before_seconds == 0.5
    # The two tags are additive, not duplicates of the same gap — the total
    # silence between "First." and "Second." is 0.3 + 0.5 = 0.8s.
    assert chunks[0].pause_after_seconds + chunks[1].pause_before_seconds == 0.8


def test_pause_only_turn_between_two_speakers_carries_forward_to_next_chunk():
    script = "[HOST_A] Welcome. [PAUSE:2s] [HOST_B] Hi there."
    chunks = chunking.chunk_script(script)
    # The pause sits at the very end of HOST_A's turn text, so it is
    # captured as HOST_A's trailing pause, not lost at the turn boundary.
    assert chunks[0].speaker == "HOST_A" and chunks[0].pause_after_seconds == 2.0
    assert chunks[1].speaker == "HOST_B" and chunks[1].pause_before_seconds == 0.0


def test_long_turn_is_split_on_sentence_boundaries_to_target():
    sentence = "This is a reasonably long sentence about nothing in particular."
    long_text = " ".join([sentence] * 20)  # several minutes of audio at 150 wpm
    script = f"[HOST_A] {long_text}"
    chunks = chunking.chunk_script(script, chunk_seconds_target=20.0)
    assert len(chunks) > 1
    for c in chunks:
        assert c.estimated_seconds <= 20.0 + 1e-6
    # No sentence was split mid-word: rejoining every chunk reproduces the
    # original text exactly.
    assert " ".join(c.text for c in chunks) == long_text


def test_single_oversized_sentence_is_kept_whole_never_mid_word_split():
    one_giant_sentence = "word " * 500 + "."
    pieces = chunking.split_text_to_target(one_giant_sentence, speed=1.0, max_seconds=5.0)
    assert len(pieces) == 1
    assert pieces[0] == one_giant_sentence


def test_estimate_seconds_scales_inversely_with_speed():
    text = "word " * 150  # ~150 words -> ~60s at 1.0x, 150wpm
    normal = chunking.estimate_seconds(text, speed=1.0)
    fast = chunking.estimate_seconds(text, speed=1.5)
    assert fast < normal
