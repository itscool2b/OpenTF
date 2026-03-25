"""First-class git tool definitions and sandboxed handlers.

Read operations auto-approve. Write operations require user approval
via the existing ApprovalRequired workflow. Destructive operations
(push, rebase, reset --hard) are blocked entirely.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Any, Callable, Awaitable

from opentf.tools.file_tools import ApprovalRequired, ALLOWED_BASE_DIRS

log = logging.getLogger(__name__)

# Timeout limits
_READ_TIMEOUT = 10
_WRITE_TIMEOUT = 30

# Operations that are too destructive even with approval
BLOCKED_OPERATIONS = [
    "push", "push --force", "push -f",
    "rebase", "reset --hard", "reset --mixed",
    "clean -f", "clean -fd",
]


# --- Tool definitions ---

GIT_STATUS_TOOL = {
    "name": "git_status",
    "description": "Show the working tree status: staged, unstaged, and untracked files.",
    "input_schema": {
        "type": "object",
        "properties": {},
    },
}

GIT_DIFF_TOOL = {
    "name": "git_diff",
    "description": (
        "Show changes between commits, working tree, and staging area. "
        "Use target to diff a specific file, branch, or pass '--staged' for staged changes."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": "Optional: file path, branch name, or '--staged'",
            },
        },
    },
}

GIT_LOG_TOOL = {
    "name": "git_log",
    "description": "Show recent commit history.",
    "input_schema": {
        "type": "object",
        "properties": {
            "count": {
                "type": "integer",
                "description": "Number of commits to show (default: 20)",
            },
            "oneline": {
                "type": "boolean",
                "description": "Use compact one-line format (default: true)",
            },
        },
    },
}

GIT_SHOW_TOOL = {
    "name": "git_show",
    "description": "Show the contents of a commit (message, diff, files changed).",
    "input_schema": {
        "type": "object",
        "properties": {
            "ref": {
                "type": "string",
                "description": "Commit hash, branch name, tag, or ref to show",
            },
        },
        "required": ["ref"],
    },
}

GIT_BRANCH_TOOL = {
    "name": "git_branch",
    "description": "List all local branches, highlighting the current branch.",
    "input_schema": {
        "type": "object",
        "properties": {},
    },
}

GIT_ADD_TOOL = {
    "name": "git_add",
    "description": "Stage files for commit. Requires user approval.",
    "input_schema": {
        "type": "object",
        "properties": {
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of file paths to stage",
            },
        },
        "required": ["files"],
    },
}

GIT_COMMIT_TOOL = {
    "name": "git_commit",
    "description": "Create a commit with a message. Requires user approval.",
    "input_schema": {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "Commit message",
            },
        },
        "required": ["message"],
    },
}

GIT_CHECKOUT_TOOL = {
    "name": "git_checkout",
    "description": (
        "Switch branches or create a new branch. "
        "Use '-b branch_name' to create and switch to a new branch. Requires user approval."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": "Branch name, or '-b new_branch' to create a new branch",
            },
        },
        "required": ["target"],
    },
}

GIT_TOOLS = [
    GIT_STATUS_TOOL,
    GIT_DIFF_TOOL,
    GIT_LOG_TOOL,
    GIT_SHOW_TOOL,
    GIT_BRANCH_TOOL,
    GIT_ADD_TOOL,
    GIT_COMMIT_TOOL,
    GIT_CHECKOUT_TOOL,
]


# --- Helpers ---

async def _run_git(
    *args: str,
    timeout: int = _READ_TIMEOUT,
) -> str:
    """Run a git command and return its output."""
    cmd = ["git", *args]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(ALLOWED_BASE_DIRS[0]),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = stdout.decode(errors="replace")
        if proc.returncode != 0:
            err = stderr.decode(errors="replace")
            return f"Error (exit {proc.returncode}): {err.strip()}"
        if stderr:
            err_text = stderr.decode(errors="replace").strip()
            if err_text:
                output += f"\n{err_text}"
        return output.strip() if output.strip() else "(no output)"
    except asyncio.TimeoutError:
        return f"Error: git command timed out ({timeout}s limit)"
    except Exception as exc:
        return f"Error: {exc}"


def _check_blocked(operation: str) -> str | None:
    """Return an error message if the operation is blocked, else None."""
    op_lower = operation.lower().strip()
    for blocked in BLOCKED_OPERATIONS:
        if op_lower.startswith(blocked) or op_lower == blocked:
            return (
                f"Error: 'git {blocked}' is blocked. "
                "OpenTF does not perform destructive or remote git operations. "
                "Run this command manually if needed."
            )
    return None


# --- Handlers ---

async def handle_git_status(input_data: dict[str, Any]) -> str:
    """Show working tree status."""
    return await _run_git("status", "--short", "--branch")


async def handle_git_diff(input_data: dict[str, Any]) -> str:
    """Show diff output."""
    target = input_data.get("target", "")
    args = ["diff"]
    if target:
        args.extend(target.split())
    return await _run_git(*args)


async def handle_git_log(input_data: dict[str, Any]) -> str:
    """Show commit log."""
    count = input_data.get("count", 20)
    oneline = input_data.get("oneline", True)
    args = ["log", f"-{count}"]
    if oneline:
        args.append("--oneline")
    args.append("--graph")
    return await _run_git(*args)


async def handle_git_show(input_data: dict[str, Any]) -> str:
    """Show a commit."""
    ref = input_data["ref"]
    return await _run_git("show", "--stat", ref)


async def handle_git_branch(input_data: dict[str, Any]) -> str:
    """List branches."""
    return await _run_git("branch", "-a")


async def handle_git_add(input_data: dict[str, Any]) -> str:
    """Stage files for commit. Requires approval."""
    files = input_data["files"]
    if not files:
        return "Error: no files specified"

    if not input_data.get("_approved"):
        file_list = ", ".join(files[:10])
        if len(files) > 10:
            file_list += f" ... (+{len(files) - 10} more)"
        raise ApprovalRequired(f"git add {file_list}")

    return await _run_git("add", *files, timeout=_WRITE_TIMEOUT)


async def handle_git_commit(input_data: dict[str, Any]) -> str:
    """Create a commit. Requires approval."""
    message = input_data["message"]
    if not message.strip():
        return "Error: commit message cannot be empty"

    if not input_data.get("_approved"):
        preview = message[:120]
        raise ApprovalRequired(f'git commit -m "{preview}"')

    return await _run_git("commit", "-m", message, timeout=_WRITE_TIMEOUT)


async def handle_git_checkout(input_data: dict[str, Any]) -> str:
    """Switch or create branches. Requires approval."""
    target = input_data["target"]
    if not target.strip():
        return "Error: target branch cannot be empty"

    # Check for blocked operations embedded in checkout
    blocked = _check_blocked(target)
    if blocked:
        return blocked

    if not input_data.get("_approved"):
        raise ApprovalRequired(f"git checkout {target}")

    args = ["checkout"]
    args.extend(target.split())
    return await _run_git(*args, timeout=_WRITE_TIMEOUT)


# --- Factory ---

def git_handlers() -> dict[str, Callable[..., Any]]:
    """Return the handler dict for git tools."""
    return {
        "git_status": handle_git_status,
        "git_diff": handle_git_diff,
        "git_log": handle_git_log,
        "git_show": handle_git_show,
        "git_branch": handle_git_branch,
        "git_add": handle_git_add,
        "git_commit": handle_git_commit,
        "git_checkout": handle_git_checkout,
    }
