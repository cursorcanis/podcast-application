@echo off
REM Podcast Foundry launcher (native Windows).
REM Serves the app (Status/Settings, episode intake, source gate, script
REM review as of M2) at http://127.0.0.1:8000
REM
REM Create a venv on first run; always re-run `pip install -r
REM requirements.txt` (fast/no-op once satisfied) so a later milestone's new
REM dependency is picked up on an existing .venv, not just a fresh clone.
REM No credential is needed or referenced here. Env vars (NAMES only) are read
REM from the process environment / a local gitignored .env:
REM   COMFYUI_URL, OUTPUT_FOLDER, SHARE_LOCATION, MAX_RENDER_HOURS,
REM   CHUNK_TIMEOUT_MIN, MAX_ATTACHMENT_MB, MONTHLY_BUDGET, EMAIL_METHOD,
REM   SMTP_USER, SMTP_PASS, PATH_TO_WORKFLOW_JSON,
REM   RENDER_SECONDS_PER_AUDIO_SECOND, CHUNK_SECONDS_TARGET_DEFAULT
cd /d "%~dp0"

if not exist .venv (
  echo [start_app] Creating virtual environment...
  python -m venv .venv
  if errorlevel 1 (
    echo [start_app] ERROR: python not found or venv creation failed.
    echo [start_app] Install Python 3.11+ from https://www.python.org/downloads/
    exit /b 1
  )
)

call .venv\Scripts\activate.bat
python -m pip install --upgrade pip >nul
pip install -r requirements.txt

echo [start_app] Starting Podcast Foundry at http://127.0.0.1:8000
python -m uvicorn app:app --host 127.0.0.1 --port 8000