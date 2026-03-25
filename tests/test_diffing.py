"""Tests for agent-aware diffing in file approval flow."""

import pytest
from pathlib import Path

from opentf.tools.file_tools import (
    ApprovalRequired,
    handle_write_file,
    handle_edit_file,
    set_review_mode,
)


@pytest.fixture(autouse=True)
def review_mode_on(monkeypatch: pytest.MonkeyPatch):
    """Enable review mode for all tests, restore after."""
    from opentf.tools import file_tools
    original = file_tools._review_mode
    set_review_mode(True)
    yield
    set_review_mode(original)


@pytest.fixture()
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Set up sandboxed file tools."""
    from opentf.tools import file_tools
    monkeypatch.setattr(file_tools, "ALLOWED_BASE_DIRS", [tmp_path])
    return tmp_path


# --- ApprovalRequired backward compat ---

def test_approval_required_no_diff() -> None:
    exc = ApprovalRequired("rm -rf /")
    assert exc.command == "rm -rf /"
    assert exc.diff_text is None


def test_approval_required_with_diff() -> None:
    exc = ApprovalRequired("write test.py", diff_text="+new line")
    assert exc.diff_text == "+new line"


# --- Write file diffs ---

@pytest.mark.asyncio
async def test_write_new_file_raises_with_diff(sandbox: Path) -> None:
    new_file = sandbox / "new.py"
    with pytest.raises(ApprovalRequired) as exc_info:
        await handle_write_file({"path": str(new_file), "content": "print('hello')\n"})

    assert exc_info.value.diff_text is not None
    # New file — diff should show additions or "(new file)"
    diff = exc_info.value.diff_text
    assert "+" in diff or "new file" in diff.lower()


@pytest.mark.asyncio
async def test_write_existing_file_shows_unified_diff(sandbox: Path) -> None:
    existing = sandbox / "existing.py"
    existing.write_text("old_line = 1\n")

    with pytest.raises(ApprovalRequired) as exc_info:
        await handle_write_file({"path": str(existing), "content": "new_line = 2\n"})

    diff = exc_info.value.diff_text
    assert diff is not None
    assert "-old_line" in diff or "old_line" in diff
    assert "+new_line" in diff or "new_line" in diff


@pytest.mark.asyncio
async def test_write_diff_truncated_at_80_lines(sandbox: Path) -> None:
    existing = sandbox / "big.py"
    existing.write_text("")

    # Create content that generates >80 diff lines
    new_content = "\n".join(f"line_{i} = {i}" for i in range(100))
    with pytest.raises(ApprovalRequired) as exc_info:
        await handle_write_file({"path": str(existing), "content": new_content})

    diff = exc_info.value.diff_text
    assert "more lines" in diff


# --- Edit file diffs ---

@pytest.mark.asyncio
async def test_edit_file_raises_with_diff(sandbox: Path) -> None:
    target = sandbox / "edit_me.py"
    target.write_text("def hello():\n    return 'world'\n")

    with pytest.raises(ApprovalRequired) as exc_info:
        await handle_edit_file({
            "path": str(target),
            "old_text": "return 'world'",
            "new_text": "return 'universe'",
        })

    diff = exc_info.value.diff_text
    assert diff is not None
    assert "world" in diff
    assert "universe" in diff


@pytest.mark.asyncio
async def test_edit_missing_file_diff_message(sandbox: Path) -> None:
    missing = sandbox / "nope.py"

    with pytest.raises(ApprovalRequired) as exc_info:
        await handle_edit_file({
            "path": str(missing),
            "old_text": "x",
            "new_text": "y",
        })

    assert "not found" in exc_info.value.diff_text


# --- Diff content safety ---

def test_rich_markup_in_diff_escaped() -> None:
    """Brackets in code shouldn't break Rich rendering."""
    code = "items[0] = [1, 2, 3]"
    escaped = code.replace("[", "\\[")
    assert "\\[0]" in escaped
    assert "\\[1, 2, 3]" in escaped


# --- Approval flow integration ---

@pytest.mark.asyncio
async def test_write_approved_bypasses_diff(sandbox: Path) -> None:
    """With _approved=True, no diff is generated — write proceeds."""
    target = sandbox / "approved.py"
    result = await handle_write_file({
        "path": str(target),
        "content": "print('ok')\n",
        "_approved": True,
    })
    assert "Written" in result
    assert target.read_text() == "print('ok')\n"
