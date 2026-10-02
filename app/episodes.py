"""Episode intake, the Board source-approval gate, and hand-entered script
review — M2 (POD-9).

Everything here is hand-entry only: no automatic source discovery, no
automatic outline/script generation (Risk R5 on the build plan — open Board
decision, not resolved by this milestone). An episode moves through exactly
four states in this milestone:

    sources_pending_approval -> sources_approved -> script_ready

(the fourth, `draft`, exists in the schema for a future "save before
submitting" flow but M2's New Episode form is a single complete submission,
so every episode is created directly at `sources_pending_approval`.)

The source gate is enforced here, not just in the UI: `close_source_gate`
refuses to advance the episode until every candidate source has an explicit
Board decision and at least one is approved, and every other write in this
module checks the episode's current status before allowing the action it
guards. A client that skips the UI and posts directly to a later-stage route
gets the same refusal the UI would have shown.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from .config import config
from .db import get_connection

MIN_SPEED = 0.8
MAX_SPEED = 1.3
DEFAULT_SPEED = 1.0
DEFAULT_CADENCE = "conversational"
DEFAULT_FORMAT = "solo"
DEFAULT_LENGTH_MINUTES = 22
MIN_LENGTH_MINUTES = 5
MAX_LENGTH_MINUTES = 180
VALID_FORMATS = ("solo", "two_host")
VALID_AUDIENCE_LEVELS = ("general", "informed", "expert")
VALID_SOURCE_DECISIONS = ("approved", "removed")

# `sources.board_decision` also accepts 'added', reserved for a future
# milestone that distinguishes auto-researched candidates from sources the
# Board adds itself (Risk R5 — no auto-research exists yet in M2, so every
# source is hand-entered the same way and that distinction has no meaning
# today). Not used by this module.


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ValidationError(ValueError):
    """Raised for a rejected episode/source/script input. The message is
    shown to the Board verbatim, so it names the exact field and constraint
    — never a generic 'invalid input'."""


def allowed_recipient_emails(conn: sqlite3.Connection | None = None) -> list[str]:
    """The only emails an episode may ever be sent to: the Board's
    configured DEFAULT_RECIPIENTS plus any saved recipient_lists emails.
    Never an arbitrary typed-in address beyond this set."""
    owns_conn = conn is None
    conn = conn or get_connection()
    try:
        emails: list[str] = list(config.default_recipients)
        rows = conn.execute("SELECT emails FROM recipient_lists").fetchall()
        for row in rows:
            try:
                for email in json.loads(row["emails"]):
                    if email not in emails:
                        emails.append(str(email))
            except (json.JSONDecodeError, TypeError):
                continue
        return emails
    finally:
        if owns_conn:
            conn.close()


def _validate_recipients(recipient_emails: Iterable[str], conn: sqlite3.Connection) -> list[str]:
    cleaned = [e.strip() for e in recipient_emails if e and e.strip()]
    if not cleaned:
        raise ValidationError("Select at least one recipient.")
    allowed = set(allowed_recipient_emails(conn))
    rejected = [e for e in cleaned if e not in allowed]
    if rejected:
        raise ValidationError(
            "Recipient(s) not in DEFAULT_RECIPIENTS or a saved recipient list, "
            f"rejected: {', '.join(rejected)}. Typed-in addresses outside that "
            "set are never accepted."
        )
    return cleaned


def _validate_speed(raw: Any) -> float:
    try:
        speed = float(raw)
    except (TypeError, ValueError):
        raise ValidationError(f"Speed must be a number between {MIN_SPEED}x and {MAX_SPEED}x.")
    if not (MIN_SPEED <= speed <= MAX_SPEED):
        raise ValidationError(f"Speed must be between {MIN_SPEED}x and {MAX_SPEED}x, got {speed}x.")
    return speed


def _validate_length(raw: Any) -> int:
    try:
        minutes = int(raw)
    except (TypeError, ValueError):
        raise ValidationError("Target length (minutes) must be a whole number.")
    if not (MIN_LENGTH_MINUTES <= minutes <= MAX_LENGTH_MINUTES):
        raise ValidationError(
            f"Target length must be between {MIN_LENGTH_MINUTES} and {MAX_LENGTH_MINUTES} minutes."
        )
    return minutes


def _validate_format(raw: Any) -> str:
    fmt = (raw or DEFAULT_FORMAT).strip()
    if fmt not in VALID_FORMATS:
        raise ValidationError(f"Format must be one of: {', '.join(VALID_FORMATS)}.")
    return fmt


def _validate_audience_level(raw: Any) -> str | None:
    level = (raw or "").strip()
    if not level:
        return None
    if level not in VALID_AUDIENCE_LEVELS:
        raise ValidationError(f"Audience level must be one of: {', '.join(VALID_AUDIENCE_LEVELS)}.")
    return level


@dataclass
class EpisodeInput:
    title: str
    topic_or_query: str
    format: str = DEFAULT_FORMAT
    tone: str = ""
    speed: Any = DEFAULT_SPEED
    cadence: str = ""
    audience_level: str = ""
    length_minutes_target: Any = DEFAULT_LENGTH_MINUTES
    voices: list[str] | None = None
    recipient_emails: list[str] | None = None


def create_episode(data: EpisodeInput) -> int:
    """Validate and insert a new episode at status=sources_pending_approval.
    Raises ValidationError naming the exact rejected field; never silently
    substitutes a value for something the Board must decide (recipients)."""
    title = (data.title or "").strip()
    topic_or_query = (data.topic_or_query or "").strip()
    if not title:
        raise ValidationError("Title is required.")
    if not topic_or_query:
        raise ValidationError("Topic / query / source URLs is required.")

    fmt = _validate_format(data.format)
    speed = _validate_speed(data.speed if data.speed not in (None, "") else DEFAULT_SPEED)
    length_minutes = _validate_length(
        data.length_minutes_target if data.length_minutes_target not in (None, "") else DEFAULT_LENGTH_MINUTES
    )
    audience_level = _validate_audience_level(data.audience_level)

    defaults_applied: list[str] = []
    tone = (data.tone or "").strip()
    if not tone:
        defaults_applied.append("tone")
        tone = ""
    cadence = (data.cadence or "").strip()
    if not cadence:
        defaults_applied.append("cadence")
        cadence = DEFAULT_CADENCE
    if speed == DEFAULT_SPEED and (data.speed in (None, "")):
        defaults_applied.append("speed")
    if length_minutes == DEFAULT_LENGTH_MINUTES and (data.length_minutes_target in (None, "")):
        defaults_applied.append("length_minutes_target")
    if audience_level is None:
        defaults_applied.append("audience_level")

    voices = [v.strip() for v in (data.voices or []) if v and v.strip()]
    if not voices:
        defaults_applied.append("voices")

    conn = get_connection()
    try:
        recipients = _validate_recipients(data.recipient_emails or [], conn)
        cur = conn.execute(
            """
            INSERT INTO episodes (
                title, topic_or_query, format, tone, speed, cadence,
                audience_level, length_minutes_target, voice_profile_ids,
                recipient_emails, status, defaults_applied, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'sources_pending_approval', ?, ?, ?)
            """,
            (
                title,
                topic_or_query,
                fmt,
                tone,
                speed,
                cadence,
                audience_level,
                length_minutes,
                json.dumps(voices),
                json.dumps(recipients),
                json.dumps(defaults_applied),
                utcnow_iso(),
                utcnow_iso(),
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def _row_to_episode(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["voice_profile_ids"] = json.loads(d.get("voice_profile_ids") or "[]")
    d["recipient_emails"] = json.loads(d.get("recipient_emails") or "[]")
    d["defaults_applied"] = json.loads(d.get("defaults_applied") or "[]")
    return d


def list_episodes(status: str | None = None) -> list[dict]:
    conn = get_connection()
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM episodes WHERE status = ? ORDER BY id DESC", (status,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM episodes ORDER BY id DESC").fetchall()
        episodes = [_row_to_episode(r) for r in rows]
        for ep in episodes:
            counts = conn.execute(
                """
                SELECT
                    SUM(CASE WHEN board_decision IS NULL THEN 1 ELSE 0 END) AS pending,
                    SUM(CASE WHEN board_decision IN ('approved', 'added') THEN 1 ELSE 0 END) AS approved,
                    SUM(CASE WHEN board_decision = 'removed' THEN 1 ELSE 0 END) AS removed,
                    COUNT(*) AS total
                FROM sources WHERE episode_id = ?
                """,
                (ep["id"],),
            ).fetchone()
            ep["source_counts"] = dict(counts)
        return episodes
    finally:
        conn.close()


def get_episode(episode_id: int) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM episodes WHERE id = ?", (episode_id,)).fetchone()
        return _row_to_episode(row) if row else None
    finally:
        conn.close()


def _require_episode(conn: sqlite3.Connection, episode_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM episodes WHERE id = ?", (episode_id,)).fetchone()
    if row is None:
        raise ValidationError(f"Episode {episode_id} does not exist.")
    return row


# --- Sources / the Board gate -------------------------------------------------

def list_sources(episode_id: int) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM sources WHERE episode_id = ? ORDER BY id ASC", (episode_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def gate_status(episode_id: int, sources: list[dict] | None = None) -> dict:
    sources = sources if sources is not None else list_sources(episode_id)
    pending = [s for s in sources if s["board_decision"] is None]
    approved = [s for s in sources if s["board_decision"] in ("approved", "added")]
    removed = [s for s in sources if s["board_decision"] == "removed"]
    can_close = len(sources) > 0 and not pending and len(approved) >= 1
    return {
        "total": len(sources),
        "pending_count": len(pending),
        "approved_count": len(approved),
        "removed_count": len(removed),
        "can_close": can_close,
    }


def add_source(episode_id: int, data: dict) -> int:
    title = (data.get("title") or "").strip()
    if not title:
        raise ValidationError("Source title is required.")
    conn = get_connection()
    try:
        episode = _require_episode(conn, episode_id)
        if episode["status"] != "sources_pending_approval":
            raise ValidationError(
                "Sources can only be added while the source gate is open "
                f"(episode status is '{episode['status']}')."
            )
        cur = conn.execute(
            """
            INSERT INTO sources (
                episode_id, title, publication, published_at, url, summary,
                credibility_note, board_decision, decided_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)
            """,
            (
                episode_id,
                title,
                (data.get("publication") or "").strip() or None,
                (data.get("published_at") or "").strip() or None,
                (data.get("url") or "").strip() or None,
                (data.get("summary") or "").strip() or None,
                (data.get("credibility_note") or "").strip() or None,
                utcnow_iso(),
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def decide_source(episode_id: int, source_id: int, decision: str) -> None:
    if decision not in VALID_SOURCE_DECISIONS:
        raise ValidationError(f"Decision must be one of: {', '.join(VALID_SOURCE_DECISIONS)}.")
    conn = get_connection()
    try:
        episode = _require_episode(conn, episode_id)
        if episode["status"] != "sources_pending_approval":
            raise ValidationError(
                "Source decisions can only be changed while the gate is open "
                f"(episode status is '{episode['status']}')."
            )
        source = conn.execute(
            "SELECT * FROM sources WHERE id = ? AND episode_id = ?", (source_id, episode_id)
        ).fetchone()
        if source is None:
            raise ValidationError(f"Source {source_id} does not belong to episode {episode_id}.")
        conn.execute(
            "UPDATE sources SET board_decision = ?, decided_at = ? WHERE id = ?",
            (decision, utcnow_iso(), source_id),
        )
        conn.commit()
    finally:
        conn.close()


def close_source_gate(episode_id: int) -> None:
    """Advance sources_pending_approval -> sources_approved. Refuses unless
    every source has an explicit decision and at least one is approved — the
    gate is a DB-enforced check, not a UI convention."""
    conn = get_connection()
    try:
        episode = _require_episode(conn, episode_id)
        if episode["status"] != "sources_pending_approval":
            raise ValidationError(
                f"Episode {episode_id} is not awaiting source approval "
                f"(status is '{episode['status']}')."
            )
        sources = [dict(r) for r in conn.execute(
            "SELECT * FROM sources WHERE episode_id = ?", (episode_id,)
        ).fetchall()]
        status = gate_status(episode_id, sources)
        if status["total"] == 0:
            raise ValidationError("Add at least one candidate source before closing the gate.")
        if status["pending_count"] > 0:
            raise ValidationError(
                f"{status['pending_count']} source(s) still need an approve/remove decision."
            )
        if status["approved_count"] < 1:
            raise ValidationError("At least one source must be approved to close the gate.")
        conn.execute(
            "UPDATE episodes SET status = 'sources_approved', updated_at = ? WHERE id = ?",
            (utcnow_iso(), episode_id),
        )
        conn.commit()
    finally:
        conn.close()


# --- Script review -------------------------------------------------------------

DOCUMENT_KINDS = ("outline", "script", "citation_map")


def latest_episode_documents(episode_id: int) -> dict[str, dict | None]:
    conn = get_connection()
    try:
        result: dict[str, dict | None] = {}
        for kind in DOCUMENT_KINDS:
            row = conn.execute(
                """
                SELECT * FROM episode_documents
                WHERE episode_id = ? AND kind = ?
                ORDER BY id DESC LIMIT 1
                """,
                (episode_id, kind),
            ).fetchone()
            result[kind] = dict(row) if row else None
        return result
    finally:
        conn.close()


def save_script_documents(episode_id: int, outline: str, script: str, citation_map: str) -> None:
    """Append a new revision for each non-empty document kind. Blank fields
    are simply skipped (no empty revision row written) rather than
    overwriting a prior non-empty revision with blank text."""
    conn = get_connection()
    try:
        episode = _require_episode(conn, episode_id)
        if episode["status"] not in ("sources_approved", "script_ready"):
            raise ValidationError(
                "Script review is only reachable once the source gate has closed "
                f"(episode status is '{episode['status']}')."
            )
        for kind, body in (("outline", outline), ("script", script), ("citation_map", citation_map)):
            body = (body or "").strip()
            if body:
                conn.execute(
                    "INSERT INTO episode_documents (episode_id, kind, body, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (episode_id, kind, body, utcnow_iso()),
                )
        conn.execute(
            "UPDATE episodes SET updated_at = ? WHERE id = ?", (utcnow_iso(), episode_id)
        )
        conn.commit()
    finally:
        conn.close()


def mark_script_ready(episode_id: int) -> None:
    conn = get_connection()
    try:
        episode = _require_episode(conn, episode_id)
        if episode["status"] not in ("sources_approved", "script_ready"):
            raise ValidationError(
                "Script can only be marked ready once the source gate has closed "
                f"(episode status is '{episode['status']}')."
            )
        script_row = conn.execute(
            "SELECT body FROM episode_documents WHERE episode_id = ? AND kind = 'script' "
            "ORDER BY id DESC LIMIT 1",
            (episode_id,),
        ).fetchone()
        if script_row is None or not (script_row["body"] or "").strip():
            raise ValidationError("Paste script text and save it before marking the script ready.")
        conn.execute(
            "UPDATE episodes SET status = 'script_ready', updated_at = ? WHERE id = ?",
            (utcnow_iso(), episode_id),
        )
        conn.commit()
    finally:
        conn.close()
