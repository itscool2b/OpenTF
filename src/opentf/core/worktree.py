"""Git worktree management for subagent isolation.

Inspired by Cursor 2.0: parallel agents work in isolated git worktrees
so they can't clobber each other's edits. Each subagent gets its own
copy of the repo, and changes are merged back after completion.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

WORKTREE_BASE = Path.cwd() / ".opentf" / "worktrees"


@dataclass
class WorktreeContext:
    """Context for an active git worktree."""
    branch_name: str
    worktree_path: Path
    original_cwd: Path


async def create_worktree(name: str) -> WorktreeContext | None:
    """Create an isolated git worktree for a subagent.

    Returns WorktreeContext on success, None if git worktree is not available
    or the repo is not a git repository.
    """
    branch = f"opentf-worker-{name}-{int(time.time())}"
    worktree_dir = WORKTREE_BASE / branch
    worktree_dir.parent.mkdir(parents=True, exist_ok=True)

    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "worktree", "add", str(worktree_dir), "-b", branch,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=str(Path.cwd()),
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)

        if proc.returncode != 0:
            log.warning("Failed to create worktree: %s", stderr.decode(errors="replace"))
            return None

        log.info("Created worktree at %s (branch: %s)", worktree_dir, branch)
        return WorktreeContext(
            branch_name=branch,
            worktree_path=worktree_dir,
            original_cwd=Path.cwd(),
        )
    except Exception as exc:
        log.warning("Failed to create worktree: %s", exc)
        return None


async def get_worktree_diff(ctx: WorktreeContext) -> str:
    """Get the diff of changes made in a worktree.

    Returns a unified diff string of all changes, or empty string if no changes.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "diff", "HEAD",
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=str(ctx.worktree_path),
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        return stdout.decode(errors="replace")
    except Exception:
        return ""


async def merge_worktree_changes(ctx: WorktreeContext) -> str:
    """Cherry-pick worktree changes back to the original branch.

    Returns a status message describing what was merged.
    """
    # First check if there are changes
    diff = await get_worktree_diff(ctx)
    if not diff.strip():
        return "No changes to merge."

    # Commit changes in the worktree
    try:
        # Stage all changes
        proc = await asyncio.create_subprocess_exec(
            "git", "add", "-A",
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=str(ctx.worktree_path),
        )
        await asyncio.wait_for(proc.communicate(), timeout=10)

        # Commit
        proc = await asyncio.create_subprocess_exec(
            "git", "commit", "-m", f"[opentf subagent] Changes from {ctx.branch_name}",
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=str(ctx.worktree_path),
        )
        await asyncio.wait_for(proc.communicate(), timeout=10)

        if proc.returncode != 0:
            return f"Worktree has changes but commit failed. Diff:\n{diff[:2000]}"

        # Cherry-pick into original branch
        proc = await asyncio.create_subprocess_exec(
            "git", "cherry-pick", ctx.branch_name,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=str(ctx.original_cwd),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)

        if proc.returncode == 0:
            return f"Merged changes from worktree {ctx.branch_name}."
        else:
            # Cherry-pick failed (likely conflicts) -- abort and report
            await asyncio.create_subprocess_exec(
                "git", "cherry-pick", "--abort",
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=str(ctx.original_cwd),
            )
            return (
                f"Cherry-pick from {ctx.branch_name} had conflicts. "
                f"Changes remain in branch {ctx.branch_name}. "
                f"Diff:\n{diff[:2000]}"
            )
    except Exception as exc:
        return f"Merge failed: {exc}. Changes remain in branch {ctx.branch_name}."


async def cleanup_worktree(ctx: WorktreeContext) -> None:
    """Remove a git worktree and its temporary branch."""
    try:
        # Remove the worktree
        proc = await asyncio.create_subprocess_exec(
            "git", "worktree", "remove", str(ctx.worktree_path), "--force",
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=str(ctx.original_cwd),
        )
        await asyncio.wait_for(proc.communicate(), timeout=10)

        # Delete the temporary branch
        proc = await asyncio.create_subprocess_exec(
            "git", "branch", "-D", ctx.branch_name,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=str(ctx.original_cwd),
        )
        await asyncio.wait_for(proc.communicate(), timeout=5)

        log.info("Cleaned up worktree %s", ctx.worktree_path)
    except Exception as exc:
        log.warning("Failed to clean up worktree %s: %s", ctx.worktree_path, exc)
