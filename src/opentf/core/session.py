"""Session persistence using SQLite.

Inspired by OpenCode/Crush: SQLite provides transactional writes,
incremental saves, search across sessions, and survives partial
writes. Much more robust than JSON file storage.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SESSION_DIR = Path.cwd() / ".opentf" / "sessions"
DB_PATH = SESSION_DIR / "sessions.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT UNIQUE NOT NULL,
    model TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    message_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_label TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    FOREIGN KEY (session_label) REFERENCES sessions(label)
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_label);
"""


@dataclass
class SessionManager:
    """Manages conversation session persistence with SQLite."""

    session_dir: Path = field(default_factory=lambda: SESSION_DIR)
    _conn: sqlite3.Connection | None = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self.session_dir.mkdir(parents=True, exist_ok=True)
            db_path = self.session_dir / "sessions.db"
            self._conn = sqlite3.connect(str(db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.executescript(_SCHEMA)
        return self._conn

    def save(
        self,
        history: list[dict[str, Any]],
        model: str = "",
        label: str = "current",
    ) -> Path:
        """Save conversation history to SQLite."""
        conn = self._get_conn()
        now = datetime.now(timezone.utc).isoformat()

        try:
            # Upsert session
            conn.execute(
                """INSERT INTO sessions (label, model, created_at, updated_at, message_count)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(label) DO UPDATE SET
                       model = excluded.model,
                       updated_at = excluded.updated_at,
                       message_count = excluded.message_count""",
                (label, model, now, now, len(history)),
            )

            # Clear old messages for this session
            conn.execute("DELETE FROM messages WHERE session_label = ?", (label,))

            # Insert all messages
            for msg in history:
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                if isinstance(content, list):
                    content = json.dumps(content, default=str)
                else:
                    content = str(content)
                conn.execute(
                    "INSERT INTO messages (session_label, role, content, timestamp) VALUES (?, ?, ?, ?)",
                    (label, role, content, now),
                )

            conn.commit()
        except Exception as exc:
            log.warning("Failed to save session %s: %s", label, exc)
            conn.rollback()

        return self.session_dir / "sessions.db"

    def load(self, label: str = "current") -> dict[str, Any] | None:
        """Load a saved session. Returns None if not found."""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM sessions WHERE label = ?", (label,)
            ).fetchone()
            if row is None:
                # Fallback: try loading legacy JSON
                return self._load_legacy_json(label)

            messages = conn.execute(
                "SELECT role, content FROM messages WHERE session_label = ? ORDER BY id",
                (label,),
            ).fetchall()

            history = []
            for msg in messages:
                content = msg["content"]
                # Try to parse JSON content (list of blocks)
                try:
                    parsed = json.loads(content)
                    if isinstance(parsed, list):
                        content = parsed
                except (json.JSONDecodeError, TypeError):
                    pass
                history.append({"role": msg["role"], "content": content})

            return {
                "history": history,
                "model": row["model"],
                "saved_at": row["updated_at"],
                "message_count": row["message_count"],
            }
        except Exception as exc:
            log.warning("Failed to load session %s: %s", label, exc)
            return self._load_legacy_json(label)

    def _load_legacy_json(self, label: str) -> dict[str, Any] | None:
        """Fallback: load from legacy JSON format."""
        path = self.session_dir / f"{label}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except Exception:
            return None

    def list_sessions(self) -> list[dict[str, Any]]:
        """List all saved sessions with metadata."""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT label, message_count, model, updated_at FROM sessions ORDER BY updated_at DESC"
            ).fetchall()
            return [
                {
                    "label": row["label"],
                    "message_count": row["message_count"],
                    "model": row["model"],
                    "saved_at": row["updated_at"],
                }
                for row in rows
            ]
        except Exception:
            return []

    def delete(self, label: str) -> bool:
        """Delete a session."""
        conn = self._get_conn()
        try:
            conn.execute("DELETE FROM messages WHERE session_label = ?", (label,))
            result = conn.execute("DELETE FROM sessions WHERE label = ?", (label,))
            conn.commit()
            return result.rowcount > 0
        except Exception:
            return False

    def has_current(self) -> bool:
        """Check if there's a resumable current session."""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT 1 FROM sessions WHERE label = 'current' LIMIT 1"
            ).fetchone()
            if row:
                return True
        except Exception:
            pass
        # Fallback: check legacy JSON
        return (self.session_dir / "current.json").exists()

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None
