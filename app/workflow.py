"""Dynamic ComfyUI workflow node discovery — M3 (POD-10).

The workflow JSON at `PATH_TO_WORKFLOW_JSON` is read as configuration, not
code: this module never assumes a specific node id or a specific Chatterbox
node package. The production workflow (which node package, which exact
inputs) is still a pending Board approval held by the Audio Engineer (see
POD-3) — the file can be swapped for a different Chatterbox-family workflow
with no code change here, as long as it keeps exactly one Chatterbox-family
node and exactly one save-type node wired to that node's audio output.

Roles are resolved from the graph itself:
  - the TTS node: the one node whose class_type contains "chatterbox"
    (case-insensitive) — vendor-stable even if the node package changes.
  - the voice-reference loader (optional): whichever node the TTS node's own
    inputs link to via a load/audio-type class_type — found by following the
    graph's wiring, not by a hardcoded input name.
  - the save node: the one node whose class_type contains "save" and whose
    inputs link to the TTS node's output.

Every failure to resolve a role raises WorkflowError naming the exact node
id / class_type / input keys involved — fail loud, fail specific, never a
generic "bad workflow".
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class WorkflowError(ValueError):
    """The configured workflow JSON could not be resolved into the roles
    this app needs to submit a chunk. The message names the exact node(s),
    class_type(s), or input key(s) that are missing or ambiguous."""


@dataclass(frozen=True)
class WorkflowRoles:
    tts_node_id: str
    text_input_key: str
    save_node_id: str
    save_filename_input_key: str
    loader_node_id: str | None
    loader_filename_input_key: str | None


def load_workflow(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise WorkflowError(
            f"Workflow JSON not found at '{p}'. Set PATH_TO_WORKFLOW_JSON to the "
            "Audio Engineer's approved workflow file."
        )
    with open(p, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    nodes = {k: v for k, v in data.items() if isinstance(v, dict) and "class_type" in v}
    if not nodes:
        raise WorkflowError(
            f"Workflow JSON at '{p}' has no API-format nodes (objects with a "
            "'class_type' key)."
        )
    return nodes


def _is_link(value: Any) -> bool:
    """ComfyUI API-format link syntax: [source_node_id, output_index]."""
    return (
        isinstance(value, list)
        and len(value) == 2
        and isinstance(value[1], int)
        and (isinstance(value[0], str) or isinstance(value[0], int))
    )


def resolve_roles(workflow: dict[str, Any]) -> WorkflowRoles:
    tts_candidates = [
        node_id
        for node_id, node in workflow.items()
        if "chatterbox" in str(node.get("class_type", "")).lower()
    ]
    if len(tts_candidates) != 1:
        raise WorkflowError(
            "Expected exactly one Chatterbox-family node (class_type containing "
            f"'chatterbox'), found {len(tts_candidates)}: {tts_candidates}. Check "
            "the workflow file at PATH_TO_WORKFLOW_JSON."
        )
    tts_node_id = tts_candidates[0]
    tts_inputs = workflow[tts_node_id].get("inputs", {})
    if "text" not in tts_inputs:
        raise WorkflowError(
            f"Chatterbox node '{tts_node_id}' ({workflow[tts_node_id].get('class_type')}) "
            f"has no 'text' input to carry the chunk's script text. Available inputs: "
            f"{sorted(tts_inputs.keys())}."
        )

    loader_node_id: str | None = None
    loader_filename_key: str | None = None
    for value in tts_inputs.values():
        if not _is_link(value):
            continue
        candidate_id = str(value[0])
        candidate = workflow.get(candidate_id)
        if candidate is None:
            continue
        candidate_class = str(candidate.get("class_type", "")).lower()
        if "load" in candidate_class or "audio" in candidate_class:
            loader_node_id = candidate_id
            break
    if loader_node_id is not None:
        loader_inputs = workflow[loader_node_id].get("inputs", {})
        string_keys = [k for k, v in loader_inputs.items() if isinstance(v, str)]
        if len(string_keys) != 1:
            raise WorkflowError(
                f"Voice-reference loader node '{loader_node_id}' "
                f"({workflow[loader_node_id].get('class_type')}) must have exactly one "
                f"string-valued input to carry the reference-clip filename; found "
                f"{len(string_keys)}: {string_keys}."
            )
        loader_filename_key = string_keys[0]

    save_candidates = [
        node_id
        for node_id, node in workflow.items()
        if "save" in str(node.get("class_type", "")).lower()
        and any(_is_link(v) and str(v[0]) == tts_node_id for v in node.get("inputs", {}).values())
    ]
    if len(save_candidates) != 1:
        raise WorkflowError(
            "Expected exactly one save-type node wired to the Chatterbox node's "
            f"output (node '{tts_node_id}'), found {len(save_candidates)}: {save_candidates}."
        )
    save_node_id = save_candidates[0]
    save_inputs = workflow[save_node_id].get("inputs", {})
    filename_candidates = [
        k for k in save_inputs if "prefix" in k.lower() or "filename" in k.lower()
    ]
    if not filename_candidates:
        filename_candidates = [k for k, v in save_inputs.items() if isinstance(v, str)]
    if len(filename_candidates) != 1:
        raise WorkflowError(
            f"Save node '{save_node_id}' ({workflow[save_node_id].get('class_type')}) needs "
            f"exactly one filename/prefix input to target per chunk; found "
            f"{len(filename_candidates)}: {filename_candidates}."
        )

    return WorkflowRoles(
        tts_node_id=tts_node_id,
        text_input_key="text",
        save_node_id=save_node_id,
        save_filename_input_key=filename_candidates[0],
        loader_node_id=loader_node_id,
        loader_filename_input_key=loader_filename_key,
    )


def build_prompt(
    workflow: dict[str, Any],
    roles: WorkflowRoles,
    *,
    text: str,
    filename_prefix: str,
    voice_reference_filename: str | None,
) -> dict[str, Any]:
    """A deep copy of `workflow` with this chunk's text, output filename, and
    (if the workflow has a voice-reference loader) reference clip filename
    substituted at the already-resolved roles. Never mutates the loaded
    workflow dict — the same in-memory workflow is reused for every chunk."""
    prompt = copy.deepcopy(workflow)
    prompt[roles.tts_node_id]["inputs"][roles.text_input_key] = text
    prompt[roles.save_node_id]["inputs"][roles.save_filename_input_key] = filename_prefix
    if roles.loader_node_id is not None and voice_reference_filename is not None:
        prompt[roles.loader_node_id]["inputs"][roles.loader_filename_input_key] = (
            voice_reference_filename
        )
    return prompt
