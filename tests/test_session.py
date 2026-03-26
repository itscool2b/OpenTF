"""Tests for session persistence."""

import json
import pytest
from pathlib import Path

from opentf.core.session import SessionManager


@pytest.fixture
def mgr(tmp_path: Path) -> SessionManager:
    return SessionManager(session_dir=tmp_path / "sessions")


def test_save_creates_file(mgr: SessionManager) -> None:
    history = [{"role": "user", "content": "hello"}]
    path = mgr.save(history, model="sonnet")
    assert path.exists()
    # Now uses SQLite -- verify via load()
    data = mgr.load()
    assert data is not None
    assert data["message_count"] == 1
    assert data["model"] == "sonnet"
    assert len(data["history"]) == 1


def test_load_returns_saved_data(mgr: SessionManager) -> None:
    history = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    mgr.save(history, model="opus")
    loaded = mgr.load()
    assert loaded is not None
    assert loaded["message_count"] == 2
    assert loaded["history"][0]["content"] == "hello"


def test_load_missing_returns_none(mgr: SessionManager) -> None:
    assert mgr.load("nonexistent") is None


def test_has_current(mgr: SessionManager) -> None:
    assert not mgr.has_current()
    mgr.save([{"role": "user", "content": "hi"}])
    assert mgr.has_current()


def test_delete(mgr: SessionManager) -> None:
    mgr.save([{"role": "user", "content": "hi"}])
    assert mgr.has_current()
    mgr.delete("current")
    assert not mgr.has_current()


def test_delete_nonexistent(mgr: SessionManager) -> None:
    assert mgr.delete("ghost") is False


def test_list_sessions_empty(mgr: SessionManager) -> None:
    assert mgr.list_sessions() == []


def test_list_sessions(mgr: SessionManager) -> None:
    mgr.save([{"role": "user", "content": "a"}], label="session-1")
    mgr.save([{"role": "user", "content": "b"}, {"role": "assistant", "content": "c"}], label="session-2")
    sessions = mgr.list_sessions()
    assert len(sessions) == 2
    labels = {s["label"] for s in sessions}
    assert "session-1" in labels
    assert "session-2" in labels


def test_save_named_session(mgr: SessionManager) -> None:
    history = [{"role": "user", "content": "test"}]
    mgr.save(history, label="my-project")
    loaded = mgr.load("my-project")
    assert loaded is not None
    assert loaded["message_count"] == 1


def test_save_overwrites(mgr: SessionManager) -> None:
    mgr.save([{"role": "user", "content": "v1"}])
    mgr.save([{"role": "user", "content": "v2"}, {"role": "assistant", "content": "v2r"}])
    loaded = mgr.load()
    assert loaded["message_count"] == 2
    assert loaded["history"][0]["content"] == "v2"
