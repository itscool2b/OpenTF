"""Extended refactoring tools: batch edit, find references, replace in files.

These tools enable multi-file operations that would otherwise require
many individual tool calls.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

from opentf.tools.file_tools import (
    ApprovalRequired, MAX_FILE_SIZE, _backup_file, _is_binary, _review_mode,
    validate_path,
)
from opentf.tools.edit_engine import EditEngine

# --- Tool definitions ---

BATCH_EDIT_TOOL = {
    "name": "batch_edit",
    "description": (
        "Apply multiple file edits atomically. Validates ALL edits before "
        "applying any. If any edit fails validation, none are applied. "
        "More efficient than multiple edit_file calls for multi-file changes."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "edits": {
                "type": "array",
                "description": "List of edits to apply",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path"},
                        "old_text": {"type": "string", "description": "Text to find"},
                        "new_text": {"type": "string", "description": "Replacement text"},
                    },
                    "required": ["path", "old_text", "new_text"],
                },
            },
        },
        "required": ["edits"],
    },
}

FIND_REFERENCES_TOOL = {
    "name": "find_references",
    "description": (
        "Find all references to a symbol (function, class, variable) across "
        "the codebase. Uses ripgrep for fast .gitignore-aware search."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "description": "Symbol name to search for"},
            "path": {"type": "string", "description": "Directory to search in (default: '.')"},
            "file_type": {"type": "string", "description": "File type filter (e.g. 'py', 'js')"},
        },
        "required": ["symbol"],
    },
}

REPLACE_IN_FILES_TOOL = {
    "name": "replace_in_files",
    "description": (
        "Find and replace text across multiple files. Shows a preview of all "
        "changes before applying. Use for bulk renames or pattern replacements."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "find": {"type": "string", "description": "Text or regex to find"},
            "replace": {"type": "string", "description": "Replacement text"},
            "path": {"type": "string", "description": "Directory to search in (default: '.')"},
            "glob": {"type": "string", "description": "File pattern (e.g. '*.py')"},
            "preview_only": {"type": "boolean", "description": "If true, only show preview without applying"},
        },
        "required": ["find", "replace"],
    },
}


# --- Handlers ---

async def handle_batch_edit(input_data: dict[str, Any]) -> str:
    """Apply multiple edits atomically -- validate all first, then apply."""
    edits = input_data.get("edits", [])
    if not edits:
        return "Error: no edits provided"

    engine = EditEngine()

    # Phase 1: Validate all edits
    validated: list[dict] = []
    for i, edit in enumerate(edits):
        try:
            path = validate_path(edit["path"])
        except PermissionError as e:
            return f"Error in edit {i + 1}: {e}"

        if not path.exists() or not path.is_file():
            return f"Error in edit {i + 1}: file not found: {path}"
        if _is_binary(path):
            return f"Error in edit {i + 1}: binary file: {path}"

        content = path.read_text(errors="replace")
        new_content, match, msg = engine.apply_edit(
            content, edit["old_text"], edit["new_text"],
        )

        if match is None:
            return f"Error in edit {i + 1} ({path}): {msg}"

        validated.append({
            "path": path,
            "original": content,
            "new_content": new_content,
            "msg": msg,
        })

    # Phase 2: Apply all (atomic -- backup everything first, rollback on failure)
    for v in validated:
        _backup_file(v["path"])

    applied: list[dict] = []
    try:
        for v in validated:
            v["path"].write_text(v["new_content"])
            applied.append(v)
    except Exception as exc:
        # Rollback: restore all applied files from their originals
        for a in applied:
            try:
                a["path"].write_text(a["original"])
            except Exception:
                pass
        return (
            f"Error: batch edit rolled back after {len(applied)}/{len(validated)} writes. "
            f"All files restored to original state. Cause: {exc}"
        )

    results = [f"  {v['path']}: {v['msg']}" for v in validated]
    return f"Applied {len(validated)} edits:\n" + "\n".join(results)


async def handle_find_references(input_data: dict[str, Any]) -> str:
    """Find all references to a symbol across the codebase."""
    import shutil

    symbol = input_data["symbol"]
    search_path = input_data.get("path", ".")
    file_type = input_data.get("file_type", "")

    path = validate_path(search_path)

    # Use ripgrep with word boundary matching
    rg = shutil.which("rg")
    if rg:
        cmd = [rg, "--line-number", "--no-heading", "--color", "never",
               "--word-regexp", "--max-count", "30"]
        if file_type:
            cmd.extend(["--type", file_type])
        cmd.extend([symbol, str(path)])
    else:
        cmd = ["grep", "-rn", "--word-regexp", symbol, str(path)]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        result = stdout.decode(errors="replace")
        if not result:
            return f"No references found for '{symbol}'"

        lines = result.strip().split("\n")
        return f"Found {len(lines)} references to '{symbol}':\n{result[:5000]}"
    except asyncio.TimeoutError:
        return "Error: search timed out"
    except Exception as exc:
        return f"Error: {exc}"


async def handle_replace_in_files(input_data: dict[str, Any]) -> str:
    """Find and replace across multiple files with preview."""
    import shutil

    find_text = input_data["find"]
    replace_text = input_data["replace"]
    search_path = input_data.get("path", ".")
    glob_pattern = input_data.get("glob", "")
    preview_only = input_data.get("preview_only", False)

    path = validate_path(search_path)

    # Find matching files using ripgrep or grep
    rg = shutil.which("rg")
    if rg:
        cmd = [rg, "--files-with-matches", "--color", "never"]
        if glob_pattern:
            cmd.extend(["--glob", glob_pattern])
        cmd.extend([find_text, str(path)])
    else:
        cmd = ["grep", "-rl"]
        if glob_pattern:
            cmd.extend(["--include", glob_pattern])
        cmd.extend([find_text, str(path)])

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        file_list = stdout.decode(errors="replace").strip().split("\n")
        file_list = [f for f in file_list if f.strip()]
    except Exception:
        return f"No files contain '{find_text}'"

    if not file_list:
        return f"No files contain '{find_text}'"

    # Preview changes
    changes: list[dict] = []
    for fpath_str in file_list[:20]:  # Cap at 20 files
        try:
            fpath = validate_path(fpath_str.strip())
            if not fpath.is_file() or _is_binary(fpath):
                continue
            content = fpath.read_text(errors="replace")
            count = content.count(find_text)
            if count > 0:
                # Exact str.replace is intentional here -- bulk replace needs
                # deterministic exact matching, not fuzzy EditEngine matching
                new_content = content.replace(find_text, replace_text)
                changes.append({
                    "path": fpath,
                    "original": content,
                    "new_content": new_content,
                    "count": count,
                })
        except Exception:
            continue

    if not changes:
        return f"No replaceable matches for '{find_text}'"

    total_replacements = sum(c["count"] for c in changes)
    preview_lines = [f"Replace '{find_text}' -> '{replace_text}' ({total_replacements} occurrences in {len(changes)} files):"]
    for c in changes:
        preview_lines.append(f"  {c['path']} ({c['count']} replacements)")

    if preview_only:
        return "\n".join(preview_lines)

    # Apply changes
    for c in changes:
        _backup_file(c["path"])
        c["path"].write_text(c["new_content"])

    return "\n".join(preview_lines + [f"\nApplied {total_replacements} replacements across {len(changes)} files."])


def refactor_tools() -> list[dict]:
    """Return refactoring tool definitions."""
    return [BATCH_EDIT_TOOL, FIND_REFERENCES_TOOL, REPLACE_IN_FILES_TOOL]


def refactor_handlers() -> dict[str, Any]:
    """Return refactoring tool handlers."""
    return {
        "batch_edit": handle_batch_edit,
        "find_references": handle_find_references,
        "replace_in_files": handle_replace_in_files,
    }
