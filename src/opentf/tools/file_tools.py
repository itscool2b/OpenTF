"""Shared file/system tool definitions and sandboxed handlers.

Extracted so tool logic lives in one place with configurable
command allowlists and approval for dangerous operations.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import logging
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Awaitable

log = logging.getLogger(__name__)


# --- Auto-formatter detection and execution ---

_FORMATTER_COMMANDS: dict[str, str] = {}
_FORMATTERS_DETECTED = False


def _detect_formatters() -> dict[str, str]:
    """Auto-detect available code formatters. Returns {ext: command} mapping."""
    global _FORMATTERS_DETECTED, _FORMATTER_COMMANDS
    if _FORMATTERS_DETECTED:
        return _FORMATTER_COMMANDS

    _FORMATTERS_DETECTED = True
    formatters: dict[str, str] = {}

    # Python: black or ruff
    if shutil.which("black"):
        formatters[".py"] = "black -q"
    elif shutil.which("ruff"):
        formatters[".py"] = "ruff format"

    # JavaScript/TypeScript: prettier or biome
    if shutil.which("prettier"):
        formatters[".js"] = "prettier --write"
        formatters[".jsx"] = "prettier --write"
        formatters[".ts"] = "prettier --write"
        formatters[".tsx"] = "prettier --write"
    elif shutil.which("biome"):
        formatters[".js"] = "biome format --write"
        formatters[".ts"] = "biome format --write"

    # Go
    if shutil.which("gofmt"):
        formatters[".go"] = "gofmt -w"

    # Rust
    if shutil.which("rustfmt"):
        formatters[".rs"] = "rustfmt"

    _FORMATTER_COMMANDS = formatters
    if formatters:
        log.info("Auto-detected formatters: %s", formatters)
    return formatters


async def _format_file(path: Path) -> str | None:
    """Run detected formatter on a file. Returns None on success, error on failure."""
    formatters = _detect_formatters()
    ext = path.suffix.lower()
    cmd_template = formatters.get(ext)
    if not cmd_template:
        return None

    cmd = f"{cmd_template} {shlex.quote(str(path))}"
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=str(ALLOWED_BASE_DIRS[0]),
        )
        _, stderr_out = await asyncio.wait_for(proc.communicate(), timeout=5)
        if proc.returncode == 0:
            formatter_name = cmd_template.split()[0]
            return f"Auto-formatted with {formatter_name}."
        else:
            log.warning("Formatter failed on %s: %s", path, stderr_out.decode(errors="replace"))
            return None
    except (asyncio.TimeoutError, Exception):
        return None


class ApprovalRequired(Exception):
    """Raised when a command needs user approval before execution."""

    def __init__(self, command: str, diff_text: str | None = None) -> None:
        self.command = command
        self.diff_text = diff_text
        super().__init__(f"Approval required for: {command}")


# --- File change event bus ---

_file_bus: Any | None = None


def set_file_bus(bus: Any) -> None:
    """Set the bus for publishing file change events."""
    global _file_bus
    _file_bus = bus


async def _publish_file_event(path: Path, action: str) -> None:
    """Publish a file change event on the bus."""
    if _file_bus is None:
        return
    try:
        from opentf.models.message import Message, MessageType
        msg_type = MessageType.FILE_CREATED if action == "create" else MessageType.FILE_CHANGED
        await _file_bus.publish(Message(
            type=msg_type,
            source="file_tools",
            payload={"path": str(path), "action": action},
        ))
    except Exception:
        pass


# --- LSP manager (shared with main_agent for post-edit diagnostics) ---

_lsp_manager: Any | None = None


def set_lsp_manager(manager: Any) -> None:
    """Set the shared LSP manager for post-edit diagnostics."""
    global _lsp_manager
    _lsp_manager = manager


def get_lsp_manager() -> Any | None:
    """Get the shared LSP manager."""
    return _lsp_manager


async def _collect_lsp_diagnostics(path: Path) -> str:
    """Collect LSP diagnostics after a file edit. Returns appended text or empty string."""
    if _lsp_manager is None:
        return ""
    try:
        result = await _lsp_manager.collect_diagnostics_after_edit(str(path))
        if result:
            return f"\n\n{result}"
    except Exception:
        pass
    return ""


# --- Read-before-edit tracking (inspired by Claude Code) ---

_files_read_this_session: set[str] = set()


def mark_file_read(path: str) -> None:
    """Record that a file has been read this session."""
    _files_read_this_session.add(str(Path(path).resolve()))


def was_file_read(path: str) -> bool:
    """Check if a file was read this session."""
    return str(Path(path).resolve()) in _files_read_this_session


def reset_read_tracker() -> None:
    """Reset the read tracker (e.g., on new session)."""
    _files_read_this_session.clear()


# --- Review mode ---

_review_mode = False


def set_review_mode(enabled: bool) -> None:
    """Toggle manual review for file writes/edits."""
    global _review_mode
    _review_mode = enabled


def get_review_mode() -> bool:
    return _review_mode


# --- Session allowlist (commands user said "always allow") ---

_session_allowlist: set[str] = set()


def add_to_session_allowlist(command: str) -> None:
    """Add a command prefix to the session allowlist."""
    prefix = command.strip().split()[0]
    _session_allowlist.add(prefix)


def is_session_allowed(command: str) -> bool:
    """Check if command matches a session-allowed prefix."""
    cmd = command.strip()
    return any(cmd.startswith(prefix) for prefix in _session_allowlist)


# --- File backup / undo ---

BACKUP_DIR = Path.cwd() / ".opentf" / "backups"
_file_backups: list[dict] = []


def _backup_file(path: Path) -> None:
    """Save a copy before modification."""
    if not path.exists():
        _file_backups.append({"path": str(path), "existed": False})
        return
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup_path = BACKUP_DIR / f"{path.name}.{int(time.time() * 1000)}"
    backup_path.write_text(path.read_text(errors="replace"))
    _file_backups.append({
        "path": str(path),
        "existed": True,
        "backup": str(backup_path),
    })


def undo_last() -> str:
    """Undo the last file modification."""
    if not _file_backups:
        return "Nothing to undo."
    entry = _file_backups.pop()
    path = Path(entry["path"])
    if not entry["existed"]:
        if path.exists():
            path.unlink()
        return f"Removed {path} (was newly created)"
    backup = Path(entry["backup"])
    if backup.exists():
        path.write_text(backup.read_text())
        backup.unlink()
        return f"Restored {path}"
    return f"Backup not found for {path}"


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
        "Edit a file by replacing a text match. Supports exact and fuzzy matching. "
        "Use this instead of write_file when modifying existing files. "
        "Optionally provide line_range (e.g. '10-25') to replace by line numbers."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path to edit"},
            "old_text": {"type": "string", "description": "Text to find and replace (should be unique in the file)"},
            "new_text": {"type": "string", "description": "Replacement text"},
            "line_range": {
                "type": "string",
                "description": "Line range to replace, e.g. '10-25'. Use when text matching fails.",
            },
        },
        "required": ["path", "new_text"],
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
    "description": (
        "Search for a regex pattern in files. Uses ripgrep if available (fast, "
        ".gitignore-aware), falls back to grep. Supports file type filtering."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regex pattern to search for"},
            "path": {"type": "string", "description": "Directory to search in"},
            "glob": {"type": "string", "description": "File pattern to filter (e.g. '*.py')"},
            "file_type": {"type": "string", "description": "File type filter (e.g. 'py', 'js', 'ts')"},
            "max_results": {"type": "integer", "description": "Max results to return (default 50)"},
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

# --- Dedicated grep and glob tools (like Claude Code / OpenCode) ---

GREP_TOOL = {
    "name": "grep",
    "description": (
        "Search file contents for a regex pattern. Uses ripgrep if available "
        "(fast, .gitignore-aware). Returns matching lines with file paths and "
        "line numbers."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regex pattern to search for"},
            "path": {"type": "string", "description": "Directory to search in (default: '.')"},
            "glob": {"type": "string", "description": "File pattern filter (e.g. '*.py')"},
            "file_type": {"type": "string", "description": "File type (e.g. 'py', 'js', 'ts')"},
            "context_lines": {"type": "integer", "description": "Lines of context around matches (default: 0)"},
            "max_results": {"type": "integer", "description": "Max results (default: 50)"},
        },
        "required": ["pattern"],
    },
}

GLOB_TOOL = {
    "name": "glob",
    "description": (
        "Find files by name/path pattern. Returns matching file paths sorted "
        "by modification time (most recent first). Use this to discover files "
        "in the project."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Glob pattern (e.g. '**/*.py', 'src/**/test_*.ts', '*.json')",
            },
            "path": {"type": "string", "description": "Root directory (default: '.')"},
        },
        "required": ["pattern"],
    },
}

ASK_QUESTION_TOOL = {
    "name": "ask_question",
    "description": (
        "Ask the user a question during task execution. Use when you need "
        "clarification, confirmation, or a choice between options before proceeding."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "The question to ask"},
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of choices for the user",
            },
        },
        "required": ["question"],
    },
}

ALL_TOOLS = [
    READ_FILE_TOOL, WRITE_FILE_TOOL, EDIT_FILE_TOOL, LIST_DIR_TOOL,
    SEARCH_FILES_TOOL, GREP_TOOL, GLOB_TOOL, RUN_COMMAND_TOOL, ASK_QUESTION_TOOL,
]

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
    mark_file_read(str(path))
    if len(content) > MAX_OUTPUT_LENGTH:
        content = content[:MAX_OUTPUT_LENGTH] + f"\n... (truncated, {len(content)} total chars)"
    return content


async def handle_write_file(input_data: dict[str, Any]) -> str:
    """Write a file with sandboxing."""
    path = validate_path(input_data["path"])
    content = input_data["content"]

    if _review_mode and not input_data.get("_approved"):
        old = path.read_text(errors="replace") if path.exists() and path.is_file() else ""
        diff_lines = list(difflib.unified_diff(
            old.splitlines(keepends=True), content.splitlines(keepends=True),
            fromfile=str(path), tofile=str(path), lineterm="",
        ))
        diff_text = "\n".join(diff_lines[:80])
        if len(diff_lines) > 80:
            diff_text += f"\n... ({len(diff_lines) - 80} more lines)"
        raise ApprovalRequired(
            f"write {path} ({len(content)} chars)",
            diff_text=diff_text or "(new file)",
        )

    is_new = not path.exists()
    _backup_file(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    msg = f"Written {len(content)} chars to {path}"
    fmt_result = await _format_file(path)
    if fmt_result:
        msg += f" {fmt_result}"
    msg += await _collect_lsp_diagnostics(path)
    await _publish_file_event(path, "create" if is_new else "write")
    return msg


async def handle_edit_file(input_data: dict[str, Any]) -> str:
    """Edit a file using multi-strategy matching (exact, normalized, fuzzy, line-range)."""
    from opentf.tools.edit_engine import EditEngine

    path = validate_path(input_data["path"])
    old_text = input_data.get("old_text", "")
    new_text = input_data["new_text"]
    line_range = input_data.get("line_range")

    if not old_text and not line_range:
        return "Error: provide either old_text or line_range"
    if old_text and old_text == new_text:
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
    engine = EditEngine()

    new_content, match, msg = engine.apply_edit(
        content, old_text, new_text, line_range=line_range,
    )

    if match is None:
        # msg contains the error message from the engine
        return f"{msg}"

    # Review mode: show diff for approval
    if _review_mode and not input_data.get("_approved"):
        diff_lines = list(difflib.unified_diff(
            content.splitlines(keepends=True), new_content.splitlines(keepends=True),
            fromfile=str(path), tofile=str(path), lineterm="",
        ))
        diff_text = "\n".join(diff_lines[:80])
        if len(diff_lines) > 80:
            diff_text += f"\n... ({len(diff_lines) - 80} more lines)"
        raise ApprovalRequired(f"edit {path}", diff_text=diff_text)

    # Read-before-edit warning
    read_warning = ""
    if not was_file_read(str(path)):
        read_warning = "\nNote: this file was not explicitly read before editing. Consider reading files first to avoid stale content."

    _backup_file(path)
    path.write_text(new_content)
    mark_file_read(str(path))  # Mark as read since we just read it for the edit

    result_msg = f"Edited {path} ({msg}, {len(new_content)} total chars)"
    fmt_result = await _format_file(path)
    if fmt_result:
        result_msg += f" {fmt_result}"
    result_msg += await _collect_lsp_diagnostics(path)
    await _publish_file_event(path, "edit")
    if read_warning:
        result_msg += read_warning
    return result_msg


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


def _detect_rg() -> str | None:
    """Detect ripgrep binary. Returns path or None."""
    import shutil
    return shutil.which("rg")


_RG_PATH: str | None = _detect_rg()


async def handle_search_files(input_data: dict[str, Any]) -> str:
    """Search files using ripgrep (preferred) or grep with sandboxing."""
    pattern = input_data["pattern"]
    search_path = input_data.get("path", ".")
    file_glob = input_data.get("glob", "")
    file_type = input_data.get("file_type", "")
    max_results = input_data.get("max_results", 50)

    path = validate_path(search_path)

    if _RG_PATH:
        # Ripgrep: fast, .gitignore-aware by default
        cmd = [_RG_PATH, "--line-number", "--no-heading", "--color", "never",
               "--max-count", str(max_results)]
        if file_glob:
            cmd.extend(["--glob", file_glob])
        if file_type:
            cmd.extend(["--type", file_type])
        cmd.extend([pattern, str(path)])
    else:
        # Fallback to grep
        cmd = ["grep", "-rn"]
        if file_glob:
            cmd.extend(["--include", file_glob])
        cmd.extend([pattern, str(path)])

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        result = stdout.decode(errors="replace")
        if len(result) > MAX_OUTPUT_LENGTH:
            result = result[:MAX_OUTPUT_LENGTH] + "\n... (truncated)"
        return result if result else f"No matches for '{pattern}'"
    except asyncio.TimeoutError:
        return "Error: search timed out"
    except Exception as exc:
        return f"Error: {exc}"


async def handle_grep(input_data: dict[str, Any]) -> str:
    """Dedicated content search tool using ripgrep/grep."""
    # Delegate to the existing search_files handler with same params
    return await handle_search_files(input_data)


async def handle_glob(input_data: dict[str, Any]) -> str:
    """Find files by name/path pattern, sorted by modification time."""
    pattern = input_data["pattern"]
    search_path = input_data.get("path", ".")

    path = validate_path(search_path)

    # Use ripgrep --files for speed if available, otherwise Path.glob
    if _RG_PATH:
        cmd = [_RG_PATH, "--files", "--glob", pattern, str(path)]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            files = stdout.decode(errors="replace").strip().split("\n")
            files = [f for f in files if f.strip()]
        except Exception:
            files = []
    else:
        files = []

    # Fallback to Path.glob if rg didn't find anything or isn't available
    if not files:
        try:
            matches = list(path.glob(pattern))
            files = [str(m.relative_to(path)) for m in matches if m.is_file()]
        except Exception:
            files = []

    if not files:
        return f"No files matching '{pattern}'"

    # Sort by modification time (most recent first)
    def _mtime(f: str) -> float:
        try:
            return (path / f).stat().st_mtime
        except Exception:
            return 0.0

    files.sort(key=_mtime, reverse=True)

    # Limit output
    total = len(files)
    files = files[:100]
    result = "\n".join(files)
    if total > 100:
        result += f"\n... ({total - 100} more files)"
    return result


async def handle_ask_question(input_data: dict[str, Any]) -> str:
    """Ask the user a question. Blocks until user responds.

    Uses ApprovalRequired pattern -- the UI handles displaying the question
    and collecting the response.
    """
    question = input_data["question"]
    options = input_data.get("options", [])

    prompt = question
    if options:
        prompt += "\n\nOptions:\n" + "\n".join(f"  {i+1}. {opt}" for i, opt in enumerate(options))

    # Use ApprovalRequired to pause and ask the user
    raise ApprovalRequired(f"Question: {prompt}")


def _is_dangerous(command: str) -> bool:
    """Check if a command matches dangerous patterns requiring approval."""
    cmd_lower = command.lower().strip()
    return any(pat in cmd_lower for pat in DANGEROUS_PATTERNS)


async def _execute_command(command: str) -> str:
    """Execute a shell command with output capture."""
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


def make_command_handler(safe_commands: list[str]) -> Callable:
    """Create a run_command handler with Claude Code style permissions.

    Safe commands auto-approve. Everything else needs user approval.
    User can say yes (once), no (skip), or always (session-wide).
    """

    async def handler(input_data: dict[str, Any]) -> str:
        command = input_data["command"]

        # Already approved (re-call after user said yes/always)
        if input_data.get("_approved"):
            return await _execute_command(command)

        # Session allowlist (user said "always" earlier)
        if is_session_allowed(command):
            return await _execute_command(command)

        # Safe commands (read-only, auto-approve)
        cmd_stripped = command.strip()
        if any(cmd_stripped.startswith(safe) for safe in safe_commands):
            return await _execute_command(command)

        # Everything else needs approval
        raise ApprovalRequired(command)

    return handler


def all_handlers(command_allowlist: list[str] | None = None) -> dict[str, Callable]:
    """Build the standard handler dict with a configurable command allowlist."""
    return {
        "read_file": handle_read_file,
        "write_file": handle_write_file,
        "edit_file": handle_edit_file,
        "list_directory": handle_list_directory,
        "search_files": handle_search_files,
        "grep": handle_grep,
        "glob": handle_glob,
        "ask_question": handle_ask_question,
        "run_command": make_command_handler(command_allowlist or DEFAULT_SAFE_COMMANDS),
    }
