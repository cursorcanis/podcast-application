"""M1 smoke tests — run:  python -m pytest tests/ -q

Verify the Status screen renders with the caps/budget/email-paused banners and
the ComfyUI probe fails loud and specific when the configured URL is
unreachable (the honest state today: COMFYUI_URL has no listener as of
2026-10-02; the demo shows the red state, never a spinner).

Also asserts there is exactly ONE canonical implementation: the root `app/`
package, not the superseded root `app.py`/`config.py`/`db.py` stub modules
(kept only as fail-loud guards; see their docstrings).
"""
import importlib.util
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import app as app_module
from app.config import config


def test_status_screen_renders_defaults():
    with TestClient(app_module.app) as client:
        resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text
    assert "Podcast Foundry" in html
    assert "MONTHLY BUDGET $0" in html  # whole-dollar, not $0.0
    # The banner mirrors EMAIL_METHOD and nothing else (same invariant as
    # test_api_status_shape below) — it used to assert the PAUSED text
    # outright, which was only true while POD-7 was unresolved.
    if config.email_paused:
        assert "EMAIL DELIVERY PAUSED" in html
    else:
        assert "EMAIL DELIVERY LIVE" in html
    assert "MAX_RENDER_HOURS" in html
    assert "CHUNK_TIMEOUT_MIN" in html
    assert "MAX_ATTACHMENT_MB" in html
    # The comfyui card is present and starts in the honest 'checking' state;
    # the live result is rendered by status.js polling /api/comfyui/status.
    assert "comfyui-status" in html


def test_api_status_shape():
    with TestClient(app_module.app) as client:
        resp = client.get("/api/status")
    assert resp.status_code == 200
    body = resp.json()
    assert "caps" in body and "budget" in body and "email" in body and "comfyui" in body
    # The pause flag mirrors EMAIL_METHOD and nothing else. This used to assert
    # `is True` outright, which was only true while POD-7 was unresolved; the
    # Board set EMAIL_METHOD=smtp on 2026-10-05, so pin the invariant instead
    # of the ambient value — otherwise the test fails the moment delivery is
    # legitimately switched on.
    assert body["email"]["paused"] is (not bool(config.email_method))
    assert body["budget"]["free_only"] is True  # MONTHLY_BUDGET $0
    # Whole-dollar budget not shown as a float canvas of $0.0.
    assert body["caps"]["max_render_hours"] == 3.0


def test_comfyui_probe_fails_loud_when_unreachable(monkeypatch):
    # Starlette's TestClient is itself an httpx.Client subclass (it drives
    # requests to the ASGI app over its own httpx.Client.get calls), so a
    # blanket `httpx.Client.get` patch would intercept the test's own HTTP
    # call to the app, not just the app's outbound probe to ComfyUI. Scope
    # the fake to the probe URL only and pass every other call through to
    # the real implementation.
    real_get = httpx.Client.get

    def fake_get(self, url, *args, **kwargs):
        if "8188" in str(url):
            request = httpx.Request("GET", url)
            raise httpx.ConnectError("connection refused", request=request)
        return real_get(self, url, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "get", fake_get)
    with TestClient(app_module.app) as client:
        resp = client.get("/api/comfyui/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["reachable"] is False
    assert "ConnectError" in body["error_detail"]
    assert len(body["fallback_order"]) >= 1  # documented fallback, never generic
    assert "checked_at" in body


def test_comfyui_probe_surfaces_non_json_response(monkeypatch):
    """A 200 with a non-JSON body (some other thing on port 8188) must come
    back as a specific UNREACHABLE, never raise through to an HTTP 500 on the
    Status screen."""
    class FakeResponse:
        def __init__(self):
            self.status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            raise ValueError("Expecting value: line 1 column 1 (char 0)")

    real_get = httpx.Client.get

    def fake_get(self, url, *args, **kwargs):
        if "8188" in str(url):
            return FakeResponse()
        return real_get(self, url, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "get", fake_get)
    with TestClient(app_module.app) as client:
        resp = client.get("/api/comfyui/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["reachable"] is False
    assert "Non-JSON response" in body["error_detail"]
    assert len(body["fallback_order"]) >= 1


def test_healthz():
    with TestClient(app_module.app) as client:
        resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


@pytest.mark.parametrize("stub_name", ["app.py", "config.py", "db.py"])
def test_root_stub_fails_loud_on_import(stub_name):
    # The root-level stubs must never silently run in place of the app/
    # package: executing them must raise, not provide a duplicate implementation.
    stub_path = Path(__file__).resolve().parent.parent / stub_name
    spec = importlib.util.spec_from_file_location(f"_root_stub_{stub_name}", stub_path)
    module = importlib.util.module_from_spec(spec)
    with pytest.raises(ImportError):
        spec.loader.exec_module(module)


def test_canonical_is_the_app_package():
    # The single canonical entry point is the app/ package, not the
    # root-level stub modules.
    assert app_module.__name__ == "app"
    assert app_module.app is not None