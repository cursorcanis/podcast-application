"""Configuration loader for Podcast Foundry — canonical (app/ package).

Values come from config/app_config.json (the 18 Board-confirmed values from
POD-2 configuration document revision 6) unless named in ENV_ONLY_KEYS or
overridden by a process environment variable of the same name. The file is
committed and contains NO credential; anything that could be a credential
(SMTP_USER / SMTP_PASS) is read from the environment by name only, never
stored, logged, echoed, or displayed.

This module replaces the earlier root-level config.py experiments; the repo
has exactly one canonical implementation (here), and the root modules are
obsolete stubs that fail loud if imported.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # .env support; harmless if python-dotenv is missing
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "app_config.json"

# Secret-adjacent / method keys: env-var ONLY, never from config file.
# These NAMES are read from the environment (POD-2 rows 11-13); if missing the
# app treats delivery as paused and never guesses or prints a value.
ENV_ONLY_KEYS = frozenset({"SMTP_USER", "SMTP_PASS"})

# Keys that may be overridden by an env var of the same name.
OVERRIDABLE_KEYS = frozenset(
    {
        "COMFYUI_URL",
        "MAX_RENDER_HOURS",
        "CHUNK_TIMEOUT_MIN",
        "MAX_ATTACHMENT_MB",
        "OUTPUT_FOLDER",
        "SHARE_LOCATION",
        "DEFAULT_RECIPIENTS",
        "SENDER_ADDRESS",
        "EMAIL_METHOD",
        "PATH_TO_WORKFLOW_JSON",
        "RENDER_SECONDS_PER_AUDIO_SECOND",
        "CHUNK_SECONDS_TARGET_DEFAULT",
    }
)


@dataclass(frozen=True)
class Config:
    values: dict = field(default_factory=dict)

    def get(self, key: str, default=None):
        return self.values.get(key, default)

    @property
    def comfyui_url(self) -> str:
        return str(self.values.get("COMFYUI_URL", "http://127.0.0.1:8188"))

    @property
    def max_render_hours(self) -> float:
        return float(self.values.get("MAX_RENDER_HOURS", 3))

    @property
    def chunk_timeout_min(self) -> int:
        return int(self.values.get("CHUNK_TIMEOUT_MIN", 10))

    @property
    def max_attachment_mb(self) -> int:
        return int(self.values.get("MAX_ATTACHMENT_MB", 20))

    @property
    def monthly_budget(self) -> float | int:
        """Whole-dollar budgets render as an int (so the banner shows `$0`,
        not `$0.0`); a fractional value from an env override is still kept
        honest and shown as a float."""
        val = float(self.values.get("MONTHLY_BUDGET", 0))
        return int(val) if val.is_integer() else val

    @property
    def default_recipients(self) -> list[str]:
        val = self.values.get("DEFAULT_RECIPIENTS") or []
        return [str(x) for x in val]

    @property
    def output_folder(self) -> str:
        return str(self.values.get("OUTPUT_FOLDER") or "")

    @property
    def share_location(self) -> str:
        return str(self.values.get("SHARE_LOCATION") or "")

    @property
    def email_method(self) -> str | None:
        return self.values.get("EMAIL_METHOD")

    @property
    def email_paused(self) -> bool:
        return not bool(self.email_method)

    @property
    def tts_model(self) -> str:
        return str(self.values.get("TTS_MODEL") or "chatterbox")

    @property
    def path_to_workflow_json(self) -> str:
        return str(self.values.get("PATH_TO_WORKFLOW_JSON") or "config/comfyui_workflow.json")

    @property
    def voice_mapping(self) -> dict[str, str]:
        """Speaker tag (e.g. HOST_A) -> voice-reference clip filename, as
        measured on POD-3. Never guessed: an empty mapping here means a
        render job must fail loud naming the unmapped speaker, not silently
        pick a default voice."""
        val = self.values.get("VOICE_MAPPING") or {}
        return {str(k): str(v) for k, v in dict(val).items()}

    @property
    def render_seconds_per_audio_second(self) -> float:
        """The Audio Engineer's measured compute-seconds-per-audio-second
        ratio (POD-3 benchmark document, conservative/worst-case figure).
        Used only to project render time before any chunk has completed;
        once real chunks have rendered, the projection switches to the
        measured average for this job."""
        return float(self.values.get("RENDER_SECONDS_PER_AUDIO_SECOND", 3.83))

    @property
    def chunk_seconds_target_default(self) -> int:
        return int(self.values.get("CHUNK_SECONDS_TARGET_DEFAULT", 60))

    @property
    def pilot_topic(self) -> str:
        return str(self.values.get("PILOT_TOPIC") or "")

    def public_view(self) -> dict:
        """The Status screen view: caps, budget, pause state, and the
        reachability URL — with zero secret material."""
        smtp_configured = bool(
            os.environ.get("SMTP_USER") and os.environ.get("SMTP_PASS")
        )
        return {
            "comfyui_url": self.comfyui_url,
            "max_render_hours": self.max_render_hours,
            "chunk_timeout_min": self.chunk_timeout_min,
            "max_attachment_mb": self.max_attachment_mb,
            "monthly_budget": self.monthly_budget,
            "email_paused": self.email_paused,
            "email_method": self.email_method,
            "smtp_env_names_configured": smtp_configured,
            "default_recipients": self.default_recipients,
            "output_folder": self.output_folder,
            "share_location": self.share_location,
            "tts_model": self.tts_model,
            "path_to_workflow_json": self.path_to_workflow_json,
            "voice_mapping": self.voice_mapping,
            "render_seconds_per_audio_second": self.render_seconds_per_audio_second,
        }


def _parse_env_value(raw: str) -> object:
    """Parse an env-string back to a JSON-ish value so OVERRIDABLE_KEYS that
    are lists (DEFAULT_RECIPIENTS) work from the environment too."""
    raw = raw.strip()
    if not raw:
        return None
    if raw.lower() in ("true", "false"):
        return raw.lower() == "true"
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def load_config() -> Config:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            base = json.load(fh)
    else:
        base = {}

    values = dict(base)
    for key in OVERRIDABLE_KEYS:
        if key in os.environ:
            values[key] = _parse_env_value(os.environ[key])

    # Env-only keys are deliberately NOT copied into `values` — they live only
    # in os.environ and are read on demand. `public_view` reports presence
    # without ever exposing content.
    return Config(values=values)


config = load_config()