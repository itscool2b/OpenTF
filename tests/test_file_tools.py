"""Tests for file tool sandboxing and permissions."""

import pytest
from pathlib import Path

from opentf.tools.file_tools import (
    validate_path,
    _is_dangerous,
    add_to_session_allowlist,
    is_session_allowed,
    set_review_mode,
    get_review_mode,
    handle_read_file,
    handle_edit_file,
    ApprovalRequired,
    _session_allowlist,
)


def test_validate_path_within_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from opentf.tools import file_tools
    monkeypatch.setattr(file_tools, "ALLOWED_BASE_DIRS", [tmp_path])
    test_file = tmp_path / "test.txt"
    test_file.touch()
    result = validate_path(str(test_file))
    assert result == test_file


def test_validate_path_outside_cwd_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from opentf.tools import file_tools
    monkeypatch.setattr(file_tools, "ALLOWED_BASE_DIRS", [tmp_path / "subdir"])
    with pytest.raises(PermissionError, match="outside allowed"):
        validate_path(str(tmp_path / "other" / "file.txt"))


def test_validate_path_blocked_segments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from opentf.tools import file_tools
    monkeypatch.setattr(file_tools, "ALLOWED_BASE_DIRS", [tmp_path])
    with pytest.raises(PermissionError, match="blocked segment"):
        validate_path(str(tmp_path / ".env"))


def test_is_dangerous_rm() -> None:
    assert _is_dangerous("rm -rf /tmp/stuff")


def test_is_dangerous_sudo() -> None:
    assert _is_dangerous("echo hi; sudo rm file")


def test_is_dangerous_safe_command() -> None:
    assert not _is_dangerous("ls -la")
    assert not _is_dangerous("grep pattern file.txt")


def test_session_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    from opentf.tools import file_tools
    monkeypatch.setattr(file_tools, "_session_allowlist", set())
    assert not is_session_allowed("pip install flask")
    add_to_session_allowlist("pip install flask")
    assert is_session_allowed("pip install numpy")
    assert not is_session_allowed("rm -rf /")


def test_review_mode_toggle() -> None:
    original = get_review_mode()
    set_review_mode(True)
    assert get_review_mode() is True
    set_review_mode(False)
    assert get_review_mode() is False
    set_review_mode(original)


async def test_read_file_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from opentf.tools import file_tools
    monkeypatch.setattr(file_tools, "ALLOWED_BASE_DIRS", [tmp_path])
    result = await handle_read_file({"path": str(tmp_path / "nonexistent.txt")})
    assert "not found" in result.lower()


async def test_edit_file_empty_old_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from opentf.tools import file_tools
    monkeypatch.setattr(file_tools, "ALLOWED_BASE_DIRS", [tmp_path])
    test_file = tmp_path / "test.txt"
    test_file.write_text("hello")
    result = await handle_edit_file({
        "path": str(test_file),
        "old_text": "",
        "new_text": "world",
    })
    assert "empty" in result.lower()


async def test_edit_file_no_match(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from opentf.tools import file_tools
    monkeypatch.setattr(file_tools, "ALLOWED_BASE_DIRS", [tmp_path])
    test_file = tmp_path / "test.txt"
    test_file.write_text("hello world")
    result = await handle_edit_file({
        "path": str(test_file),
        "old_text": "nonexistent text",
        "new_text": "replacement",
    })
    assert "not found" in result.lower()
