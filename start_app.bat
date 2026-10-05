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

REM Pick the venv by its activate script, not by the folder name. A .venv
REM created under WSL/Linux has bin\, not Scripts\ — and this tree has one.
REM The old `if not exist .venv` guard passed on it, so creation was skipped,
REM activate.bat failed with a path error, and pip/python then ran against
REM the global interpreter instead. Keep the Linux venv intact, use its own.
set VENV_DIR=.venv
if not exist "%VENV_DIR%\Scripts\activate.bat" (
  if exist "%VENV_DIR%" set VENV_DIR=.venv-win
)

if not exist "%VENV_DIR%\Scripts\activate.bat" (
  echo [start_app] Creating virtual environment in %VENV_DIR% ...
  python -m venv "%VENV_DIR%"
  if errorlevel 1 (
    echo [start_app] ERROR: python not found or venv creation failed.
    echo [start_app] Install Python 3.11+ from https://www.python.org/downloads/
    exit /b 1
  )
)

call "%VENV_DIR%\Scripts\activate.bat"
if errorlevel 1 (
  echo [start_app] ERROR: could not activate %VENV_DIR%.
  echo [start_app] Delete that folder and re-run this script.
  exit /b 1
)
python -m pip install --upgrade pip >nul
pip install -r requirements.txt

echo [start_app] Starting Podcast Foundry at http://127.0.0.1:8000
python -m uvicorn app:app --host 127.0.0.1 --port 8000