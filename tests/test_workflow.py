"""Unit tests for app/workflow.py — dynamic node discovery, M3 (POD-10).

These exercise the discovery logic against hand-built workflow dicts (never
hardcoding node ids in the assertions below either — the point of this
module is that a workflow can be renumbered or repackaged and discovery
still works), plus the real repo workflow file at
config/comfyui_workflow.json.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app import workflow
from app.config import CONFIG_PATH


def _three_node_workflow(tts_class="ChatterboxTTS", save_class="SaveAudio", loader_class="LoadAudio"):
    return {
        "7": {"class_type": loader_class, "inputs": {"audio": "voice_a.wav"}},
        "9": {
            "class_type": tts_class,
            "inputs": {"text": "placeholder", "seed": 1, "audio_prompt": ["7", 0]},
        },
        "12": {"class_type": save_class, "inputs": {"audio": ["9", 0], "filename_prefix": "x"}},
    }


def test_resolves_roles_regardless_of_node_id_numbering():
    wf = _three_node_workflow()
    roles = workflow.resolve_roles(wf)
    assert roles.tts_node_id == "9"
    assert roles.loader_node_id == "7"
    assert roles.save_node_id == "12"
    assert roles.save_filename_input_key == "filename_prefix"


def test_resolves_roles_for_a_differently_named_chatterbox_package():
    # Simulates the Board approving a different Chatterbox-family node
    # package (e.g. FL_ChatterboxDialogTTS instead of ChatterboxTTS) — no
    # code change should be required, only this file.
    wf = _three_node_workflow(tts_class="FL_ChatterboxDialogTTS", save_class="SaveAudioWithPath")
    roles = workflow.resolve_roles(wf)
    assert roles.tts_node_id == "9"
    assert roles.save_node_id == "12"


def test_no_chatterbox_node_raises_specific_error():
    wf = {"1": {"class_type": "LoadImage", "inputs": {}}}
    with pytest.raises(workflow.WorkflowError, match="Chatterbox-family"):
        workflow.resolve_roles(wf)


def test_ambiguous_chatterbox_nodes_raises_specific_error():
    wf = _three_node_workflow()
    wf["99"] = {"class_type": "ChatterboxTTS", "inputs": {"text": "dup"}}
    with pytest.raises(workflow.WorkflowError, match="found 2"):
        workflow.resolve_roles(wf)


def test_missing_text_input_raises_specific_error():
    wf = _three_node_workflow()
    del wf["9"]["inputs"]["text"]
    with pytest.raises(workflow.WorkflowError, match="no 'text' input"):
        workflow.resolve_roles(wf)


def test_no_save_node_wired_to_tts_output_raises_specific_error():
    wf = _three_node_workflow()
    wf["12"]["inputs"]["audio"] = ["7", 0]  # wired to the loader, not the TTS node
    with pytest.raises(workflow.WorkflowError, match="save-type node"):
        workflow.resolve_roles(wf)


def test_build_prompt_substitutes_without_mutating_source_workflow():
    wf = _three_node_workflow()
    roles = workflow.resolve_roles(wf)
    prompt = workflow.build_prompt(
        wf, roles, text="hello world", filename_prefix="ep1/chunk_0000",
        voice_reference_filename="voice_b.wav",
    )
    assert prompt["9"]["inputs"]["text"] == "hello world"
    assert prompt["12"]["inputs"]["filename_prefix"] == "ep1/chunk_0000"
    assert prompt["7"]["inputs"]["audio"] == "voice_b.wav"
    # Original untouched.
    assert wf["9"]["inputs"]["text"] == "placeholder"
    assert wf["7"]["inputs"]["audio"] == "voice_a.wav"


def test_real_repo_workflow_file_resolves():
    repo_root = Path(CONFIG_PATH).resolve().parent.parent
    wf = workflow.load_workflow(repo_root / "config" / "comfyui_workflow.json")
    roles = workflow.resolve_roles(wf)
    assert roles.tts_node_id is not None
    assert roles.save_node_id is not None
    assert roles.loader_node_id is not None
