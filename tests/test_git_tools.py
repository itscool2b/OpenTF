"""Tests for git tool handlers."""

import asyncio
import pytest
from pathlib import Path

from opentf.tools.file_tools import ApprovalRequired
from opentf.tools.git_tools import (
    GIT_TOOLS,
    git_handlers,
    handle_git_status,
    handle_git_diff,
    handle_git_log,
    handle_git_show,
    handle_git_branch,
    handle_git_add,
    handle_git_commit,
    handle_git_checkout,
    _check_blocked,
)


@pytest.fixture()
def git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a temporary git repo with an initial commit."""
    import subprocess

    from opentf.tools import git_tools

    monkeypatch.setattr(git_tools, "ALLOWED_BASE_DIRS", [tmp_path])

    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(tmp_path), capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(tmp_path), capture_output=True,
    )
    # Create initial commit
    readme = tmp_path / "README.md"
    readme.write_text("# Test Repo")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=str(tmp_path), capture_output=True,
    )
    return tmp_path


# --- Tool definitions ---

def test_git_tools_list() -> None:
    assert len(GIT_TOOLS) == 8
    names = [t["name"] for t in GIT_TOOLS]
    assert "git_status" in names
    assert "git_diff" in names
    assert "git_log" in names
    assert "git_show" in names
    assert "git_branch" in names
    assert "git_add" in names
    assert "git_commit" in names
    assert "git_checkout" in names


def test_git_tools_have_input_schema() -> None:
    for tool in GIT_TOOLS:
        assert "input_schema" in tool
        assert tool["input_schema"]["type"] == "object"


def test_git_handlers_returns_all() -> None:
    handlers = git_handlers()
    assert len(handlers) == 8
    for tool in GIT_TOOLS:
        assert tool["name"] in handlers


# --- Blocked operations ---

def test_check_blocked_push() -> None:
    assert _check_blocked("push") is not None
    assert _check_blocked("push --force") is not None
    assert _check_blocked("push -f") is not None


def test_check_blocked_rebase() -> None:
    assert _check_blocked("rebase") is not None


def test_check_blocked_reset_hard() -> None:
    assert _check_blocked("reset --hard") is not None


def test_check_blocked_allowed() -> None:
    assert _check_blocked("status") is None
    assert _check_blocked("diff") is None
    assert _check_blocked("log") is None
    assert _check_blocked("commit") is None


# --- Read handlers ---

@pytest.mark.asyncio
async def test_git_status(git_repo: Path) -> None:
    result = await handle_git_status({})
    assert "main" in result or "master" in result


@pytest.mark.asyncio
async def test_git_diff_clean(git_repo: Path) -> None:
    result = await handle_git_diff({})
    # Clean repo should have no diff
    assert "no output" in result.lower() or result.strip() == ""


@pytest.mark.asyncio
async def test_git_diff_with_changes(git_repo: Path) -> None:
    (git_repo / "README.md").write_text("# Changed")
    result = await handle_git_diff({})
    assert "Changed" in result or "diff" in result.lower()


@pytest.mark.asyncio
async def test_git_diff_staged(git_repo: Path) -> None:
    (git_repo / "new.txt").write_text("new file")
    import subprocess
    subprocess.run(["git", "add", "new.txt"], cwd=str(git_repo), capture_output=True)
    result = await handle_git_diff({"target": "--staged"})
    assert "new file" in result or "new.txt" in result


@pytest.mark.asyncio
async def test_git_log(git_repo: Path) -> None:
    result = await handle_git_log({})
    assert "Initial commit" in result


@pytest.mark.asyncio
async def test_git_log_count(git_repo: Path) -> None:
    result = await handle_git_log({"count": 1, "oneline": True})
    assert "Initial commit" in result


@pytest.mark.asyncio
async def test_git_show(git_repo: Path) -> None:
    result = await handle_git_show({"ref": "HEAD"})
    assert "Initial commit" in result


@pytest.mark.asyncio
async def test_git_branch(git_repo: Path) -> None:
    result = await handle_git_branch({})
    assert "main" in result or "master" in result


# --- Write handlers (approval required) ---

@pytest.mark.asyncio
async def test_git_add_requires_approval(git_repo: Path) -> None:
    (git_repo / "new.txt").write_text("new content")
    with pytest.raises(ApprovalRequired, match="git add"):
        await handle_git_add({"files": ["new.txt"]})


@pytest.mark.asyncio
async def test_git_add_with_approval(git_repo: Path) -> None:
    (git_repo / "new.txt").write_text("new content")
    result = await handle_git_add({"files": ["new.txt"], "_approved": True})
    assert "error" not in result.lower()


@pytest.mark.asyncio
async def test_git_add_empty_files(git_repo: Path) -> None:
    result = await handle_git_add({"files": []})
    assert "error" in result.lower()


@pytest.mark.asyncio
async def test_git_commit_requires_approval(git_repo: Path) -> None:
    with pytest.raises(ApprovalRequired, match="git commit"):
        await handle_git_commit({"message": "test commit"})


@pytest.mark.asyncio
async def test_git_commit_with_approval(git_repo: Path) -> None:
    # Stage a file first
    (git_repo / "new.txt").write_text("commit me")
    import subprocess
    subprocess.run(["git", "add", "new.txt"], cwd=str(git_repo), capture_output=True)
    result = await handle_git_commit({"message": "test commit", "_approved": True})
    assert "test commit" in result or "1 file changed" in result


@pytest.mark.asyncio
async def test_git_commit_empty_message(git_repo: Path) -> None:
    result = await handle_git_commit({"message": ""})
    assert "error" in result.lower()


@pytest.mark.asyncio
async def test_git_checkout_requires_approval(git_repo: Path) -> None:
    with pytest.raises(ApprovalRequired, match="git checkout"):
        await handle_git_checkout({"target": "-b test-branch"})


@pytest.mark.asyncio
async def test_git_checkout_new_branch(git_repo: Path) -> None:
    result = await handle_git_checkout({"target": "-b test-branch", "_approved": True})
    assert "error" not in result.lower() or "no output" in result.lower()
    # Verify branch was created
    branch_result = await handle_git_branch({})
    assert "test-branch" in branch_result


@pytest.mark.asyncio
async def test_git_checkout_empty_target(git_repo: Path) -> None:
    result = await handle_git_checkout({"target": ""})
    assert "error" in result.lower()
