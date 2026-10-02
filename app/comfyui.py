"""ComfyUI HTTP client — reachability probe (M1) and, from M3, the render path.

M1: a live probe of {url}/system_stats with honest failure behaviour — the
Status screen always gets a renderable dict (reachable | unreachable) with the
exact error and the documented fallback order, never a generic message and
never an HTTP 500. This is the single reachability implementation; the
root-level app.py stub no longer duplicates it.

M3 will add POST /prompt, /history/{id}, /view against the Board-approved
workflow JSON (read as config, never hardcoded node ids). That code lands with
the render pipeline milestone and reuses this module's httpx client.
"""
from __future__ import annotations

from typing import Any, Dict

import httpx

SYSTEM_STATS_TIMEOUT_S = 3.0

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