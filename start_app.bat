@echo off
REM Podcast Foundry launcher (native Windows).
REM M1: serves the Status / Settings screen at http://127.0.0.1:8000
REM
REM Create a venv on first run and install requirements; then start uvicorn.
REM No credential is needed or referenced here. Env vars (NAMES only) are read
REM from the process environment / a local gitignored .env:
REM   COMFYUI_URL, OUTPUT_FOLDER, SHARE_LOCATION, MAX_RENDER_HOURS,
REM   CHUNK_TIMEOUT_MIN, MAX_ATTACHMENT_MB, MONTHLY_BUDGET, EMAIL_METHOD,
REM   SMTP_USER, SMTP_PASS
cd /d "%~dp0"

if not exist .venv (
  echo [start_app] Creating virtual environment...
  python -m venv .venv
  if errorlevel 1 (
    echo [start_app] ERROR: python not found or venv creation failed.
    echo [start_app] Install Python 3.11+ from https://www.python.org/downloads/
    exit /b 1
  )
  call .venv\Scripts\activate.bat
  python -m pip install --upgrade pip
  pip install -r requirements.txt
) else (
  call .venv\Scripts\activate.bat
)

echo [start_app] Starting Podcast Foundry at http://127.0.0.1:8000
python -m uvicorn app:app --host 127.0.0.1 --port 8000