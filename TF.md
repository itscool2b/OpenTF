# OpenTF -- Project Knowledge

Autonomous AI coding agent for the terminal. Single-agent architecture with guardrail gates, hybrid memory, and a Textual TUI.

## Commands

| Command | What it does |
|---------|-------------|
| `/plan` | Enter planning mode -- describe goal, get structured plan, `/confirm` to execute |
| `/janitor` | Scan codebase for code quality issues and AI slop |
| `/model` | Switch between opus/sonnet/haiku |
| `/undo` | Undo last file change (backups in `.opentf/backups/`) |
| `/review on/off` | Toggle manual approval for file writes/edits |
| `/compact` | Summarize conversation history to save tokens |
| `/resume [name]` | Resume a saved session |
| `/save [name]` | Save current session (auto-saves as `current` after each exchange) |
| `/sessions` | List all saved sessions |
| `/new` | Clear history and start fresh |
| `/status` | Auth, model, token count |
| `/cost` | Token usage breakdown with per-model pricing |
| `/help` | Show all commands |
| `/clear` | Clear output |
| `/exit` | Quit |

Escape cancels active work. Ctrl+C quits. Ctrl+L clears.

## Architecture

```
User Input -> SecurityAgent (rule-based, blocks injections) -> ContextAgent (enriches with workspace + memory) -> MainAgent (tools + LLM) -> Response
```

- **SecurityAgent**: Pre-gate guardrail. Pattern matching for injection, path traversal, command injection, exfiltration. Zero LLM tokens.
- **ContextAgent**: Pre-gate. Retrieves memories via hybrid search, builds workspace context, tracks session state. Zero LLM tokens.
- **MainAgent**: Primary agent. Has file tools (read/write/edit/list/search), shell commands, data tools, and optional research tools (web search via DuckDuckGo). Uses ToolLoop for multi-turn tool conversations.
- **PlannerAgent**: Invoked by `/plan`. Generates structured JSON plans, executes steps through MainAgent.
- **JanitorAgent**: Invoked by `/janitor`. Read-only scan mode + fix mode. Detects dead code, unused imports, silent failures, AI slop.
- **SkillBuilderAgent**: Meta-agent that creates new prompt-based specialists at runtime. Persisted to `~/.config/opentf/skills/`.

All agents communicate through a `MessageBus` (async pub/sub). Messages are protocol-typed (not free-text parsed).

## Key Files

- `src/opentf/cli/app.py` -- Main Textual app, command handlers, bus wiring
- `src/opentf/cli/theme.py` -- Gruvbox dark color palette, model pricing
- `src/opentf/cli/widgets/` -- All UI widgets (output, prompt, status, activity, log, palettes)
- `src/opentf/core/orchestrator.py` -- Pipeline: security -> context -> dispatch -> execute
- `src/opentf/core/tool_loop.py` -- LLM tool_use conversation loop with approval system
- `src/opentf/core/bus.py` -- Async message bus
- `src/opentf/core/compaction.py` -- LLM-based conversation summarization
- `src/opentf/core/config.py` -- YAML config loader
- `src/opentf/core/session.py` -- Session save/load/list
- `src/opentf/agents/base.py` -- BaseAgent ABC, AgentResult, AgentRole
- `src/opentf/agents/specialists/main_agent.py` -- Primary agent with unified tools
- `src/opentf/agents/specialists/planner.py` -- Plan generation with JSON extraction
- `src/opentf/agents/specialists/janitor.py` -- Code quality scanner
- `src/opentf/agents/guardrails/security.py` -- Input safety patterns
- `src/opentf/agents/guardrails/context.py` -- Memory retrieval + workspace enrichment
- `src/opentf/tools/file_tools.py` -- Sandboxed file/command tools, backup/undo, review mode, permissions
- `src/opentf/memory/store.py` -- ChromaDB + sentence-transformers vector store
- `src/opentf/memory/retriever.py` -- Hybrid retriever (vector + BM25 + reciprocal rank fusion)
- `src/opentf/llm/client.py` -- Anthropic SDK wrapper with retry, caching, streaming
- `src/opentf/models/` -- Pydantic models (Task, Plan, Message, Context, JanitorReport)
- `config/default.yaml` -- Default configuration (loaded by config.py)

## Design Decisions

- **Single-agent loop** over multi-agent routing. MainAgent handles everything, specialists invoked explicitly. Fewer LLM calls, simpler debugging.
- **Guardrails as pre-gates**, not post-validators. Security and context run before the LLM, not after.
- **Protocol-based messages** on the bus. Deterministic type routing, no free-text parsing.
- **Hybrid memory** (vector + BM25 + RRF). Better recall than vector-only search.
- **Claude Code style permissions**. Safe commands auto-approve, everything else prompts y/n/a (always allow per session).
- **File backups** before every write/edit. Undo stack in `.opentf/backups/`.
- **Rich markup, not Markdown** in Static widgets. `[bold]text[/]` and `[color]text[/]`, never `**bold**` or `` `code` ``.
- **Async-first** everywhere. Memory store uses `asyncio.to_thread()` for blocking operations.

## Configuration

`config/default.yaml` is loaded on startup. Fields:

```yaml
llm:
  model: claude-sonnet-4-20250514
  max_tokens: 8192
  temperature: 0.7

memory:
  persist_dir: .opentf/memory
  embedding_model: all-MiniLM-L6-v2
  top_k: 5
```

## Testing

```bash
source .venv/bin/activate
pytest tests/ -v
```

61 tests covering: security guardrail, file tool sandboxing, planner JSON parsing, Pydantic models, message bus, session persistence.

## Data Directories

- `.opentf/memory/` -- ChromaDB vector store (persists across sessions)
- `.opentf/backups/` -- File backups before agent modifications
- `.opentf/sessions/` -- Saved conversation sessions (JSON)
- `plans/` -- Saved execution plans (YAML)
- `~/.config/opentf/credentials.json` -- API key (mode 0600)
- `~/.config/opentf/skills/` -- Persisted custom agents (YAML)

All `.opentf/` and `plans/` directories are in `.gitignore`.
