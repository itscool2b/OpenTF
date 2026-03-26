"""Unified diff application tool.

Accepts a unified diff string and applies it to the target file.
Inspired by Aider's udiff edit format -- flexible, doesn't require
exact line numbers.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from opentf.tools.file_tools import (
    ApprovalRequired, MAX_FILE_SIZE, _backup_file, _is_binary,
    _review_mode, validate_path,
)

APPLY_DIFF_TOOL = {
    "name": "apply_diff",
    "description": (
        "Apply a unified diff to a file. Useful for large edits where "
        "providing the full old_text/new_text would be verbose. "
        "The diff should be in standard unified diff format."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path to patch"},
            "diff": {
                "type": "string",
                "description": (
                    "Unified diff text. Lines starting with '-' are removed, "
                    "'+' are added, ' ' (space) are context. "
                    "Include enough context lines for accurate matching."
                ),
            },
        },
        "required": ["path", "diff"],
    },
}


async def handle_apply_diff(input_data: dict[str, Any]) -> str:
    """Apply a unified diff to a file with flexible context matching."""
    path = validate_path(input_data["path"])
    diff_text = input_data["diff"]

    if not path.exists():
        return f"Error: file not found: {path}"
    if not path.is_file():
        return f"Error: not a file: {path}"
    if _is_binary(path):
        return f"Error: {path} appears to be a binary file"
    if path.stat().st_size > MAX_FILE_SIZE:
        return f"Error: file too large"

    content = path.read_text(errors="replace")
    hunks = _parse_hunks(diff_text)

    if not hunks:
        return "Error: no valid diff hunks found. Use unified diff format with context lines."

    # Validate all hunks first (dry run on a copy)
    lines = content.splitlines(keepends=True)
    test_lines = list(lines)
    all_valid = True

    for hunk in reversed(hunks):
        result = _apply_hunk(test_lines, hunk)
        if result is not None:
            test_lines = result
        else:
            all_valid = False
            break

    if all_valid:
        # All hunks validated -- use the clean result
        lines = test_lines
        hunks_applied = len(hunks)
    else:
        # Partial apply with warning -- fall back to best-effort
        hunks_applied = 0
        for hunk in reversed(hunks):
            result = _apply_hunk(lines, hunk)
            if result is not None:
                lines = result
                hunks_applied += 1

        if hunks_applied == 0:
            return "Error: no hunks could be applied. Context lines may not match the file."

    new_content = "".join(lines)
    partial_warning = ""
    if not all_valid and hunks_applied > 0:
        partial_warning = " WARNING: partial apply -- some hunks failed, file may be in mixed state."

    # Review mode
    if _review_mode and not input_data.get("_approved"):
        import difflib
        diff_lines = list(difflib.unified_diff(
            content.splitlines(keepends=True), new_content.splitlines(keepends=True),
            fromfile=str(path), tofile=str(path), lineterm="",
        ))
        raise ApprovalRequired(
            f"apply diff to {path}",
            diff_text="\n".join(diff_lines[:80]),
        )

    _backup_file(path)
    path.write_text(new_content)

    result_msg = f"Applied {hunks_applied}/{len(hunks)} hunks to {path}"
    if partial_warning:
        result_msg += partial_warning

    # LSP diagnostics feedback after diff apply
    try:
        from opentf.tools.file_tools import get_lsp_manager
        lsp = get_lsp_manager()
        if lsp is not None:
            diag = await lsp.collect_diagnostics_after_edit(str(path))
            if diag:
                result_msg += f"\n\n{diag}"
    except Exception:
        pass

    return result_msg


def _parse_hunks(diff_text: str) -> list[dict]:
    """Parse unified diff text into hunk dicts.

    Each hunk has:
        context_before: list[str]  -- lines starting with ' '
        removals: list[str]        -- lines starting with '-'
        additions: list[str]       -- lines starting with '+'
        context_after: list[str]   -- trailing context lines
    """
    hunks: list[dict] = []
    current: dict | None = None

    for line in diff_text.splitlines():
        # Skip diff headers
        if line.startswith("---") or line.startswith("+++"):
            continue
        if line.startswith("@@"):
            if current and (current["removals"] or current["additions"]):
                hunks.append(current)
            current = {
                "context_before": [],
                "removals": [],
                "additions": [],
                "context_after": [],
                "in_changes": False,
            }
            continue

        if current is None:
            # Start a new implicit hunk if no @@ header
            current = {
                "context_before": [],
                "removals": [],
                "additions": [],
                "context_after": [],
                "in_changes": False,
            }

        if line.startswith("-"):
            current["in_changes"] = True
            current["removals"].append(line[1:])
        elif line.startswith("+"):
            current["in_changes"] = True
            current["additions"].append(line[1:])
        elif line.startswith(" ") or line == "":
            ctx_line = line[1:] if line.startswith(" ") else line
            if current["in_changes"]:
                current["context_after"].append(ctx_line)
            else:
                current["context_before"].append(ctx_line)

    if current and (current["removals"] or current["additions"]):
        hunks.append(current)

    return hunks


def _apply_hunk(lines: list[str], hunk: dict) -> list[str] | None:
    """Apply a single hunk to file lines using flexible context matching.

    Returns new lines if successful, None if context doesn't match.
    """
    context_before = hunk["context_before"]
    removals = hunk["removals"]
    additions = hunk["additions"]
    context_after = hunk["context_after"]

    # Build the expected sequence of lines (context + removals)
    expected = context_before + removals + context_after
    if not expected:
        # Pure addition with no context -- append at end
        return lines + [l + "\n" for l in additions]

    # Search for the expected sequence in the file
    match_start = _find_context_match(lines, expected)
    if match_start is None:
        return None

    # Calculate replacement range
    replace_start = match_start + len(context_before)
    replace_end = replace_start + len(removals)

    # Build new lines
    new_lines = (
        lines[:replace_start]
        + [l + "\n" for l in additions]
        + lines[replace_end:]
    )

    return new_lines


def _find_context_match(lines: list[str], expected: list[str]) -> int | None:
    """Find where expected lines match in the file, with flexible whitespace."""
    file_stripped = [l.rstrip("\n\r") for l in lines]
    expected_stripped = [l.rstrip("\n\r") for l in expected]

    for i in range(len(file_stripped) - len(expected_stripped) + 1):
        chunk = file_stripped[i : i + len(expected_stripped)]
        if all(
            a.rstrip() == b.rstrip()
            for a, b in zip(chunk, expected_stripped)
        ):
            return i

    return None


def diff_handlers() -> dict:
    """Return diff tool handlers."""
    return {"apply_diff": handle_apply_diff}


def diff_tools() -> list[dict]:
    """Return diff tool definitions."""
    return [APPLY_DIFF_TOOL]
