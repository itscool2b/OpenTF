"""Shared file/system tool definitions and sandboxed handlers.

Extracted so tool logic lives in one place with configurable
command allowlists and approval for dangerous operations.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Any, Callable, Awaitable

log = logging.getLogger(__name__)


class ApprovalRequired(Exception):
    """Raised when a command needs user approval before execution."""

    def __init__(self, command: str) -> None:
        self.command = command
        super().__init__(f"Approval required for: {command}")


# --- Sandboxing config ---

ALLOWED_BASE_DIRS = [Path.cwd()]
BLOCKED_PATH_SEGMENTS = [
    ".env", "credentials", ".git/config", "id_rsa", "id_ed25519",
    ".ssh", ".gnupg", ".aws", ".config/opentf/credentials",
]
MAX_FILE_SIZE = 1_000_000    # 1MB read limit
MAX_OUTPUT_LENGTH = 50_000   # 50k chars output limit

# Default safe commands (read-only operations)
DEFAULT_SAFE_COMMANDS = [
    "ls", "cat", "head", "tail", "grep", "find", "wc", "sort", "uniq",
    "diff", "file", "stat", "du", "tree",
    "git status", "git log", "git diff", "git branch", "git show",
    "python --version", "node --version", "pip list",
]

# Extended commands for coding (includes build/test tools)
CODE_SAFE_COMMANDS = [
    *DEFAULT_SAFE_COMMANDS,
    "python", "python3",
    "pip list", "pip show",
    "pytest", "python -m pytest",
    "node", "npx", "npm test", "npm run",
    "cargo test", "cargo check", "cargo build",
    "go test", "go build", "go vet",
    "make",
    "tsc", "tsc --noEmit",
]

# Commands that need user approval before execution
DANGEROUS_PATTERNS = [
    "rm ", "rm\t", "rmdir ", "mv ", "chmod ", "chown ",
    "kill ", "pkill ", "sudo ", "dd ", "mkfs",
    "> /dev/", "pip install", "pip uninstall",
    "npm install", "apt ", "yum ", "brew ",
]

# --- Tool definitions (Anthropic format) ---

READ_FILE_TOOL = {
    "name": "read_file",
    "description": "Read the contents of a file by path. Returns the file text.",
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path to read"},
        },
        "required": ["path"],
    },
}

WRITE_FILE_TOOL = {
    "name": "write_file",
    "description": "Write content to a file. Creates the file if it doesn't exist.",
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path to write"},
            "content": {"type": "string", "description": "Content to write"},
        },
        "required": ["path", "content"],
    },
}

EDIT_FILE_TOOL = {
    "name": "edit_file",
    "description": (
        "Edit a file by replacing an exact text match. Use this instead of "
        "write_file when modifying existing files. old_text must appear exactly once."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path to edit"},
            "old_text": {"type": "string", "description": "Exact text to find (must match exactly once)"},
            "new_text": {"type": "string", "description": "Replacement text"},
        },
        "required": ["path", "old_text", "new_text"],
    },
}

LIST_DIR_TOOL = {
    "name": "list_directory",
    "description": "List files and directories at a path.",
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory path to list"},
            "pattern": {"type": "string", "description": "Optional glob pattern to filter (e.g. '*.py')"},
        },
        "required": ["path"],
    },
}

SEARCH_FILES_TOOL = {
    "name": "search_files",
    "description": "Search for a text pattern in files using grep.",
    "input_schema": {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Text pattern to search for"},
            "path": {"type": "string", "description": "Directory to search in"},
            "glob": {"type": "string", "description": "File pattern to filter (e.g. '*.py')"},
        },
        "required": ["pattern"],
    },
}

RUN_COMMAND_TOOL = {
    "name": "run_command",
    "description": "Run a shell command. Only whitelisted commands are allowed.",
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to run"},
        },
        "required": ["command"],
    },
}

ALL_TOOLS = [READ_FILE_TOOL, WRITE_FILE_TOOL, EDIT_FILE_TOOL, LIST_DIR_TOOL, SEARCH_FILES_TOOL, RUN_COMMAND_TOOL]

# --- Validators ---


def validate_path(path_str: str) -> Path:
    """Validate and resolve a path against sandbox rules."""
    path = Path(path_str).resolve()

    allowed = any(
        path == base or base in path.parents
        for base in ALLOWED_BASE_DIRS
    )
    if not allowed:
        raise PermissionError(f"Access denied: {path} is outside allowed directories")

    path_lower = str(path).lower()
    for blocked in BLOCKED_PATH_SEGMENTS:
        if blocked in path_lower:
            raise PermissionError(f"Access denied: path contains blocked segment '{blocked}'")

    return path


def validate_command(command: str, allowed: list[str]) -> None:
    """Validate a command against an allowlist."""
    cmd_stripped = command.strip()
    if not any(cmd_stripped.startswith(safe) for safe in allowed):
        raise PermissionError(
            f"Command not allowed: '{cmd_stripped}'. "
            f"Allowed: {', '.join(allowed[:10])}..."
        )


# --- Helpers ---


def _is_binary(path: Path) -> bool:
    """Check if a file is binary by looking for null bytes."""
    try:
        chunk = path.read_bytes()[:8192]
        return b"\x00" in chunk
    except Exception:
        return False


# --- Async handlers ---


async def handle_read_file(input_data: dict[str, Any]) -> str:
    """Read a file with sandboxing."""
    path = validate_path(input_data["path"])
    if not path.exists():
        return f"Error: file not found: {path}"
    if not path.is_file():
        return f"Error: not a file: {path}"
    if _is_binary(path):
        return f"Error: {path} appears to be a binary file"
    if path.stat().st_size > MAX_FILE_SIZE:
        return f"Error: file too large ({path.stat().st_size} bytes, max {MAX_FILE_SIZE})"

    content = path.read_text(errors="replace")
    if len(content) > MAX_OUTPUT_LENGTH:
        content = content[:MAX_OUTPUT_LENGTH] + f"\n... (truncated, {len(content)} total chars)"
    return content


async def handle_write_file(input_data: dict[str, Any]) -> str:
    """Write a file with sandboxing."""
    path = validate_path(input_data["path"])
    content = input_data["content"]

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return f"Written {len(content)} chars to {path}"


async def handle_edit_file(input_data: dict[str, Any]) -> str:
    """Edit a file by replacing exact text. old_text must appear exactly once."""
    path = validate_path(input_data["path"])
    old_text = input_data["old_text"]
    new_text = input_data["new_text"]

    if not old_text:
        return "Error: old_text cannot be empty"
    if old_text == new_text:
        return "No changes needed -- old_text and new_text are identical"
    if not path.exists():
        return f"Error: file not found: {path}"
    if not path.is_file():
        return f"Error: not a file: {path}"
    if _is_binary(path):
        return f"Error: {path} appears to be a binary file"
    if path.stat().st_size > MAX_FILE_SIZE:
        return f"Error: file too large ({path.stat().st_size} bytes, max {MAX_FILE_SIZE})"

    content = path.read_text(errors="replace")
    count = content.count(old_text)

    if count == 0:
        return (
            f"Error: old_text not found in {path}. "
            "Make sure the text matches exactly, including whitespace and indentation."
        )
    if count > 1:
        return (
            f"Error: old_text appears {count} times in {path}. "
            "Provide a longer or more specific old_text that matches exactly once."
        )

    new_content = content.replace(old_text, new_text, 1)
    path.write_text(new_content)

    diff = len(new_text) - len(old_text)
    sign = "+" if diff >= 0 else ""
    return f"Edited {path} ({sign}{diff} chars, {len(new_content)} total)"


async def handle_list_directory(input_data: dict[str, Any]) -> str:
    """List directory contents with sandboxing."""
    path = validate_path(input_data["path"])
    if not path.exists():
        return f"Error: directory not found: {path}"
    if not path.is_dir():
        return f"Error: not a directory: {path}"

    pattern = input_data.get("pattern", "*")
    entries = sorted(path.glob(pattern))[:100]

    lines = []
    for entry in entries:
        prefix = "d " if entry.is_dir() else "f "
        lines.append(f"{prefix}{entry.name}")
    return "\n".join(lines) if lines else "(empty directory)"


async def handle_search_files(input_data: dict[str, Any]) -> str:
    """Search files using grep with sandboxing."""
    pattern = input_data["pattern"]
    search_path = input_data.get("path", ".")
    file_glob = input_data.get("glob", "")

    path = validate_path(search_path)

    cmd = ["grep", "-rn", "--include", file_glob or "*", pattern, str(path)]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        result = stdout.decode(errors="replace")
        if len(result) > MAX_OUTPUT_LENGTH:
            result = result[:MAX_OUTPUT_LENGTH] + "\n... (truncated)"
        return result if result else f"No matches for '{pattern}'"
    except asyncio.TimeoutError:
        return "Error: search timed out"
    except Exception as exc:
        return f"Error: {exc}"


def _is_dangerous(command: str) -> bool:
    """Check if a command matches dangerous patterns requiring approval."""
    cmd_lower = command.lower().strip()
    return any(pat in cmd_lower for pat in DANGEROUS_PATTERNS)


def make_command_handler(allowed_commands: list[str]) -> Callable:
    """Create a run_command handler with a specific command allowlist."""

    async def handler(input_data: dict[str, Any]) -> str:
        command = input_data["command"]
        validate_command(command, allowed_commands)

        if _is_dangerous(command):
            raise ApprovalRequired(command)

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(ALLOWED_BASE_DIRS[0]),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            output = stdout.decode(errors="replace")
            if stderr:
                output += "\n" + stderr.decode(errors="replace")
            if len(output) > MAX_OUTPUT_LENGTH:
                output = output[:MAX_OUTPUT_LENGTH] + "\n... (truncated)"
            return output
        except asyncio.TimeoutError:
            return "Error: command timed out (30s limit)"
        except Exception as exc:
            return f"Error: {exc}"

    return handler


def all_handlers(command_allowlist: list[str] | None = None) -> dict[str, Callable]:
    """Build the standard handler dict with a configurable command allowlist."""
    return {
        "read_file": handle_read_file,
        "write_file": handle_write_file,
        "edit_file": handle_edit_file,
        "list_directory": handle_list_directory,
        "search_files": handle_search_files,
        "run_command": make_command_handler(command_allowlist or DEFAULT_SAFE_COMMANDS),
    }
