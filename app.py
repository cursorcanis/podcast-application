"""Podcast Foundry — root-level module is SUPERSEDED.

The canonical implementation lives in the `app/` package (app/__init__.py,
app/config.py, app/db.py, app/comfyui.py). `uvicorn app:app` resolves to the
package, not this file, because a directory package shadows a same-named
module.

This stub fails loud on import so a script, test, or shell that references
the root module can never accidentally run the duplicate M1 implementation or
silently diverge from the package. That fate happened once during the M1
heartbeat (two implementations in the tree); it must not happen again.
"""
raise ImportError(
    "app.py at the repo root is superseded by the app/ package. "
    "Run `uvicorn app:app` and import from the app package (from app import ...). "
    "If a tool insists on this module, delete it — it carries no code anymore."
)