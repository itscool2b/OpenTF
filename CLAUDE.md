# OpenTF (Open Task Force)

## Python Environment

Always use a virtual environment. Activate `.venv` before running any Python commands (pip, python, pytest, etc.). If `.venv` doesn't exist, create it with `python -m venv .venv` first.

## Documentation

Document absolutely everything you do in this file. Every change, every decision, every new module — keep this file up to date.

## Debugging

When debugging, look for ALL edge cases before writing a fix. Test the fix until it actually works — do not move on until verified. Always choose the best solution, not the quickest hack.

## Project Structure

- Source code lives in `src/opentf/`
- Tests live in `tests/`
- Config files live in `config/`
- Docs live in `docs/`

## Running

```bash
source .venv/bin/activate
pip install -e ".[dev]"
opentf
```

## Testing

```bash
pytest
```

## Code Style

- Python 3.12+
- Type hints on all function signatures
- Pydantic models for all data structures
- Async-first (use `async def` by default)
- No emojis in code or output

## Pipeline Architecture (2026-03-25 Upgrade)

### Edit Engine (`src/opentf/tools/edit_engine.py`)
9-strategy matching pipeline: exact -> line_trimmed -> whitespace_normalized -> indentation_flexible -> escape_normalized -> block_anchor -> levenshtein -> fuzzy -> line_range.

### Tool Loop (`src/opentf/core/tool_loop.py`)
- Three-tier termination: soft limit (80%), stuck detection (5-iter window, same-tool detection), hard limit
- Token budget tracking (150K default)
- Parallel execution for read-only tools, sequential for writes
- Auto-compaction safety net at 95% context capacity
- Smart truncation: line-based (2000 max) with important-line preservation + char-based

### LSP Integration (`src/opentf/tools/lsp_client.py`)
- LSP manager shared via `set_lsp_manager()` / `get_lsp_manager()` in file_tools.py
- Diagnostics collected automatically after every edit/write/diff-apply
- Lazy server launch per language, auto-restart on crash
- Shutdown wired into Engine.close()

### Subagent System (`src/opentf/core/subagent.py`)
- Git worktree isolation via `isolate=True` parameter (from Cursor)
- Rich system prompt with coding rules (read-before-edit, no nesting)
- Worktree management in `src/opentf/core/worktree.py`

### Compaction (`src/opentf/core/compaction.py`)
- Chunked summarization with file change preservation
- Model-controlled via COMPRESS_TOOL (LLM calls proactively)
- target_tokens iterative loop (up to 3 passes with reduced keep_recent)

### Repo Map (`src/opentf/core/repo_map.py`)
- Tree-sitter parsing + PageRank ranking
- Word-boundary regex for reference detection (no false edges from short names)
- Semantic indexing via sentence-transformers embeddings (optional)
- `build_semantic_index()`, `semantic_search()`, `invalidate_semantic_cache()`

### File Tools (`src/opentf/tools/file_tools.py`)
- Read-before-edit tracking: `mark_file_read()`, `was_file_read()`, warns on unread edits
- Auto-format after writes (black, prettier, gofmt, rustfmt)
- Path injection prevention via `shlex.quote()`
- File change event bus via `set_file_bus()` / `_publish_file_event()`
- Timestamp-based backups (no cross-session collisions)

### Atomic Operations (`src/opentf/tools/atomic.py`)
- `AtomicFileTransaction`: stage -> commit with rollback on failure
- Used by batch_edit and diff application

### New Files
- `src/opentf/tools/atomic.py` - AtomicFileTransaction
- `src/opentf/core/worktree.py` - Git worktree management for subagent isolation
