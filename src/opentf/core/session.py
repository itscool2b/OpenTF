"""Session persistence -- save and resume conversations."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SESSION_DIR = Path.cwd() / ".opentf" / "sessions"


@dataclass
class SessionManager:
    """Manages conversation session persistence to disk."""

    session_dir: Path = field(default_factory=lambda: SESSION_DIR)

    def save(
        self,
        history: list[dict[str, Any]],
        model: str = "",
        label: str = "current",
    ) -> Path:
        """Save conversation history to a JSON file."""
        self.session_dir.mkdir(parents=True, exist_ok=True)
        path = self.session_dir / f"{label}.json"
        data = {
            "history": history,
            "model": model,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "message_count": len(history),
        }
        path.write_text(json.dumps(data, indent=2, default=str))
        return path

    def load(self, label: str = "current") -> dict[str, Any] | None:
        """Load a saved session. Returns None if not found."""
        path = self.session_dir / f"{label}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Failed to load session %s: %s", label, exc)
            return None

    def list_sessions(self) -> list[dict[str, Any]]:
        """List all saved sessions with metadata."""
        if not self.session_dir.exists():
            return []
        sessions = []
        for path in sorted(self.session_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text())
                sessions.append({
                    "label": path.stem,
                    "message_count": data.get("message_count", 0),
                    "model": data.get("model", ""),
                    "saved_at": data.get("saved_at", ""),
                })
            except (json.JSONDecodeError, OSError):
                continue
        return sessions

    def delete(self, label: str) -> bool:
        """Delete a session file."""
        path = self.session_dir / f"{label}.json"
        if path.exists():
            path.unlink()
            return True
        return False

    def has_current(self) -> bool:
        """Check if there's a resumable current session."""
        path = self.session_dir / "current.json"
        return path.exists()
