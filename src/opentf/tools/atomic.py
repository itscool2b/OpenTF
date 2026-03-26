"""Atomic multi-file operations with rollback on failure.

Inspired by Cursor's validate-then-apply pattern: stage all file writes,
then commit atomically. On any failure, roll back all already-written files
to their original content.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from opentf.tools.file_tools import _backup_file

log = logging.getLogger(__name__)


@dataclass
class FileWrite:
    """A staged file write."""
    path: Path
    content: str
    original: str | None  # None for new files


class AtomicFileTransaction:
    """Stages file writes and commits atomically with rollback.

    Usage:
        tx = AtomicFileTransaction()
        tx.stage(path1, new_content1)
        tx.stage(path2, new_content2)
        try:
            committed = tx.commit()
        except RuntimeError:
            # All files rolled back automatically
            pass
    """

    def __init__(self) -> None:
        self._writes: list[FileWrite] = []

    def stage(self, path: Path, content: str) -> None:
        """Stage a file write. Captures original content for rollback."""
        original = path.read_text(errors="replace") if path.exists() else None
        self._writes.append(FileWrite(path=path, content=content, original=original))

    @property
    def staged_count(self) -> int:
        return len(self._writes)

    def commit(self) -> list[Path]:
        """Write all staged files. Rollback on any failure.

        Returns list of committed file paths on success.
        Raises RuntimeError with rollback details on failure.
        """
        # Backup all files first
        for w in self._writes:
            _backup_file(w.path)

        committed: list[FileWrite] = []
        try:
            for w in self._writes:
                w.path.parent.mkdir(parents=True, exist_ok=True)
                w.path.write_text(w.content)
                committed.append(w)
            return [w.path for w in committed]
        except Exception as exc:
            # Rollback all committed writes
            rolled_back = 0
            for w in committed:
                try:
                    if w.original is not None:
                        w.path.write_text(w.original)
                    elif w.path.exists():
                        w.path.unlink()
                    rolled_back += 1
                except Exception:
                    log.error("Failed to rollback %s during atomic transaction", w.path)
            raise RuntimeError(
                f"Atomic write failed after {len(committed)}/{len(self._writes)} files. "
                f"Rolled back {rolled_back} files. Cause: {exc}"
            ) from exc

    def rollback(self) -> int:
        """Explicitly rollback all staged writes to originals.

        Returns number of files restored.
        """
        restored = 0
        for w in self._writes:
            try:
                if w.original is not None:
                    w.path.write_text(w.original)
                    restored += 1
                elif w.path.exists():
                    w.path.unlink()
                    restored += 1
            except Exception:
                pass
        return restored
