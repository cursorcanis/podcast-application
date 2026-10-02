"""SQLite schema + connection helpers for Podcast Foundry — canonical (app/).

The full build-plan schema (plan section 3) is created at startup: episodes,
sources, episode_documents, render_jobs, render_chunks (with the
one-job-system-wide partial unique index — a DB constraint, not a convention),
voice_profiles, recipient_lists, presets, delivery_records. Later milestones
add rows and UI; the schema is the durable queue of record and the
honest-state record for episodes. This replaces the earlier root-level db.py
experiments; the root module is an obsolete stub that fails loud if imported.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DB_DIR / "podcast_foundry.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings_status (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    schema_version INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    topic_or_query TEXT NOT NULL,
    format TEXT NOT NULL DEFAULT 'solo',
    tone TEXT,
    speed REAL NOT NULL DEFAULT 1.0,
    cadence TEXT NOT NULL DEFAULT 'conversational',
    audience_level TEXT,
    length_minutes_target INTEGER NOT NULL DEFAULT 22,
    voice_profile_ids TEXT NOT NULL DEFAULT '[]',
    recipient_emails TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'draft',
    defaults_applied TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id INTEGER NOT NULL REFERENCES episodes(id),
    title TEXT NOT NULL,
    publication TEXT,
    published_at TEXT,
    url TEXT,
    summary TEXT,
    credibility_note TEXT,
    board_decision TEXT,             -- approved | removed | added
    decided_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS episode_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id INTEGER NOT NULL REFERENCES episodes(id),
    kind TEXT NOT NULL,              -- outline | script | citation_map | show_notes | qa_report
    body TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS render_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id INTEGER NOT NULL REFERENCES episodes(id),
    status TEXT NOT NULL DEFAULT 'queued',
        -- queued | running | awaiting_cap_confirmation | succeeded | failed | cancelled
    cap_hours INTEGER NOT NULL,
    projected_hours REAL,
    confirmed_over_cap INTEGER NOT NULL DEFAULT 0,
    started_at TEXT,
    finished_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS render_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES render_jobs(id),
    chunk_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
        -- pending | submitted | rendering | succeeded | timed_out | failed
    comfyui_prompt_id TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    chunk_seconds_target INTEGER,
    measured_render_seconds REAL,
    error_detail TEXT,
    output_wav_path TEXT,
    started_at TEXT,
    completed_at TEXT
);

-- One render job system-wide at a time: the application enforces it in code
-- AND this partial index makes it a DB constraint, not a convention.
CREATE UNIQUE INDEX IF NOT EXISTS ux_render_jobs_active
    ON render_jobs(status) WHERE status IN ('running', 'awaiting_cap_confirmation');

CREATE TABLE IF NOT EXISTS voice_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    comfyui_voice_ref TEXT,
    reference_clip_path TEXT,
    sample_output_path TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS recipient_lists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    emails TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS presets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,              -- tone | cadence
    name TEXT NOT NULL,
    params TEXT NOT NULL,
    UNIQUE(kind, name)
);

CREATE TABLE IF NOT EXISTS delivery_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id INTEGER NOT NULL REFERENCES episodes(id),
    status TEXT NOT NULL,            -- not_attempted | paused | sent | failed
    provider_message_id TEXT,
    recipients_sent_to TEXT,
    attempted_at TEXT,
    reason TEXT
);
"""


def get_connection(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db(db_path: Path | str = DB_PATH) -> None:
    conn = get_connection(db_path)
    try:
        conn.executescript(SCHEMA)
        row = conn.execute(
            "SELECT schema_version FROM settings_status WHERE id = 1"
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO settings_status (id, schema_version) VALUES (1, ?)",
                (1,),
            )
        conn.commit()
    finally:
        conn.close()


def health_check(db_path: Path | str = DB_PATH) -> dict:
    """Small honest check used by the Status screen: DB writable, current
    schema version, config source file present."""
    from .config import CONFIG_PATH

    try:
        init_db(db_path)
        conn = get_connection(db_path)
        try:
            row = conn.execute(
                "SELECT schema_version FROM settings_status WHERE id = 1"
            ).fetchone()
            version = int(row["schema_version"]) if row else None
        finally:
            conn.close()
        return {
            "ok": True,
            "db_path": str(db_path),
            "schema_version": version,
            "config_file_present": CONFIG_PATH.exists(),
        }
    except Exception as exc:  # noqa: BLE001 - surface anything, loudly
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}