"""ComfyUI HTTP client — reachability probe (M1) and the render path (M3).

M1: a live probe of {url}/system_stats with honest failure behaviour — the
Status screen always gets a renderable dict (reachable | unreachable) with the
exact error and the documented fallback order, never a generic message and
never an HTTP 500. This is the single reachability implementation; the
root-level app.py stub no longer duplicates it.

M3: POST /prompt to submit one chunk, poll /history/{prompt_id} for
completion. Per POD-3's own finding, SaveAudio writes directly to
OUTPUT_FOLDER on the same machine the app runs on, so render.py reads the
rendered file straight off disk instead of round-tripping it through
/view — documented here, not a silent shortcut.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict

import httpx

SYSTEM_STATS_TIMEOUT_S = 3.0
PROMPT_SUBMIT_TIMEOUT_S = 15.0
HISTORY_POLL_INTERVAL_S = 3.0
HISTORY_REQUEST_TIMEOUT_S = 10.0

# Substrings (matched case-insensitively against ComfyUI's own history error
# messages) recognized as an out-of-memory failure rather than an ordinary
# one. If nothing matches, the failure is still surfaced in full via
# `error_detail` — an unrecognized error is never silently reclassified.
_OOM_MARKERS = (
    "out of memory",
    "cuda error",
    "hip error",
    " oom",
    "oom ",
    "allocation on device",
    "allocation failed",
)


class ComfyUIError(RuntimeError):
    """Raised with the exact ComfyUI request/response detail — never a
    generic 'something went wrong'. Callers that need the chunk index or
    node id for the ticket/log add that context when they catch this."""


def submit_prompt(
    url: str, prompt: Dict[str, Any], client_id: str, *, timeout_s: float = PROMPT_SUBMIT_TIMEOUT_S
) -> str:
    """POST /prompt. Returns the prompt_id ComfyUI assigned. Raises
    ComfyUIError with the exact response body on a validation error (e.g. a
    bad node input) rather than swallowing it."""
    submit_url = f"{url.rstrip('/')}/prompt"
    try:
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.post(submit_url, json={"prompt": prompt, "client_id": client_id})
    except httpx.HTTPError as exc:
        raise ComfyUIError(f"POST {submit_url} failed: {type(exc).__name__}: {exc}") from exc
    if resp.status_code >= 400:
        raise ComfyUIError(f"POST {submit_url} -> HTTP {resp.status_code}: {resp.text}")
    try:
        body = resp.json()
    except ValueError as exc:
        raise ComfyUIError(f"POST {submit_url} returned a non-JSON body: {resp.text}") from exc
    prompt_id = body.get("prompt_id")
    if not prompt_id:
        raise ComfyUIError(f"POST {submit_url} returned no prompt_id: {body}")
    return str(prompt_id)


def poll_history(
    url: str,
    prompt_id: str,
    *,
    timeout_s: float,
    poll_interval_s: float = HISTORY_POLL_INTERVAL_S,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> Dict[str, Any]:
    """Poll GET /history/{prompt_id} until ComfyUI reports completion, an
    explicit error, or `timeout_s` elapses (the configured
    CHUNK_TIMEOUT_MIN). Returns a dict with `outcome` in
    {"succeeded", "oom", "failed", "timed_out"}. A slow-but-still-running
    chunk is not a failed chunk — this only returns once ComfyUI itself
    reports done/error, or the timeout is reached. Raises ComfyUIError only
    for a transport failure talking to ComfyUI itself, never for a slow
    render."""
    history_url = f"{url.rstrip('/')}/history/{prompt_id}"
    deadline = now() + timeout_s
    with httpx.Client(timeout=HISTORY_REQUEST_TIMEOUT_S) as client:
        while True:
            try:
                resp = client.get(history_url)
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPError as exc:
                raise ComfyUIError(f"GET {history_url} failed: {type(exc).__name__}: {exc}") from exc
            except ValueError as exc:
                raise ComfyUIError(f"GET {history_url} returned a non-JSON body: {exc}") from exc

            entry = data.get(prompt_id) if isinstance(data, dict) else None
            if entry is not None:
                status = entry.get("status", {}) or {}
                messages = status.get("messages", []) or []
                status_str = str(status.get("status_str", "")).lower()
                if status.get("completed") and status_str != "error":
                    return {"outcome": "succeeded", "outputs": entry.get("outputs", {}), "raw": entry}
                if status_str == "error" or any(
                    isinstance(m, (list, tuple)) and m and str(m[0]).lower() == "execution_error"
                    for m in messages
                ):
                    error_text = str(messages).lower()
                    is_oom = any(marker in error_text for marker in _OOM_MARKERS)
                    return {
                        "outcome": "oom" if is_oom else "failed",
                        "error_detail": str(messages),
                        "raw": entry,
                    }
            if now() >= deadline:
                return {
                    "outcome": "timed_out",
                    "error_detail": f"No completion from {history_url} after {timeout_s:.0f}s",
                }
            sleep(poll_interval_s)

# Documented fallback order, from POD-2 configuration row 1 (unchanged fact:
# COMFYUI_URL is unreachable from WSL as of 2026-10-02; the native Windows app
# talks to ComfyUI on the same host, so this matters most when the app is run
# under WSL for development).
COMFYUI_FALLBACK_ORDER = [
    "Resolve the Windows host IP at run time and retry that address on port 8188.",
    "Start ComfyUI with --listen 0.0.0.0 and allow TCP 8188 in the firewall.",
    "Enable WSL mirrored networking (.wslconfig networkingMode=mirrored) and "
    "retry http://127.0.0.1:8188.",
]


def check_reachability(url: str, timeout_s: float = SYSTEM_STATS_TIMEOUT_S) -> Dict[str, Any]:
    """Probe {url}/system_stats and return a plain, honest payload.

    Never raises: the caller always gets a dict with an explicit `reachable`
    state, the exact error (never 'something went wrong'), and the documented
    fallback order on failure. That includes the case where the endpoint
    answers with a 200 but a non-JSON body (e.g. some other service on the
    port): it is surfaced as a specific failure, never as an unhandled
    exception that would turn the Status screen into a 500.
    """
    probe_url = f"{url.rstrip('/')}/system_stats"
    try:
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.get(probe_url)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "reachable": False,
            "url": url,
            "error_detail": f"HTTP {exc.response.status_code} from {probe_url}",
            "fallback_order": COMFYUI_FALLBACK_ORDER,
        }
    except httpx.HTTPError as exc:
        return {
            "reachable": False,
            "url": url,
            "error_detail": f"{type(exc).__name__}: {exc}",
            "fallback_order": COMFYUI_FALLBACK_ORDER,
        }
    except ValueError as exc:  # includes json.JSONDecodeError from resp.json()
        return {
            "reachable": False,
            "url": url,
            "error_detail": f"Non-JSON response from {probe_url}: {type(exc).__name__}: {exc}",
            "fallback_order": COMFYUI_FALLBACK_ORDER,
        }

    system = data.get("system", {}) if isinstance(data, dict) else {}
    version = system.get("comfyui_version") or data.get("comfyui_version") or "unknown"
    devices = system.get("devices") or []
    vram_free = None
    if devices and isinstance(devices[0], dict):
        vram_free = devices[0].get("vram_free")
    return {
        "reachable": True,
        "url": url,
        "version": str(version),
        "vram_free_bytes": vram_free,
        "error_detail": None,
        "fallback_order": COMFYUI_FALLBACK_ORDER,
    }