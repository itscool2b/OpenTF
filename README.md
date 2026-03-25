# OpenTF

Autonomous AI coding agent for your terminal. Type plain English, agents handle the rest.

OpenTF runs a pipeline of specialized AI agents -- security guardrails, context enrichment, planning, code generation, and quality scanning -- all orchestrated through a single conversation loop with a rich terminal UI.

## Quick Install

```bash
curl -fsSL https://opentf.dev/install.sh | bash
```

Requires Python 3.12+. See [Installation docs](docs/installation.md) for detailed setup, updating, and uninstalling.

## Usage

```bash
opentf
```

On first run, you'll be prompted to enter your Anthropic API key. You can also set it via environment variable:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
opentf
```

### Commands

| Command | Description |
|---------|-------------|
| `/plan` | Create a structured execution plan |
| `/janitor` | Scan code for quality issues |
| `/model` | Switch between Claude models |
| `/compact` | Compress conversation history |
| `/status` | Show current session info |
| `/cost` | Show token usage and cost |
| `/login` | Set API key |
| `/logout` | Remove stored API key |
| `/clear` | Clear conversation |
| `/exit` | Quit OpenTF |

## How It Works

```
User prompt
  |
  v
SecurityAgent (blocks injections, path traversal, command injection)
  |
  v
ContextAgent (enriches with workspace info + memory retrieval)
  |
  v
MainAgent (code, files, research, data -- unified tool access)
  |
  v
Response (streamed to TUI with syntax highlighting)
```

**MainAgent** handles everything through a single conversation loop with file tools, data tools, and shell access. Optional specialists can be invoked:

- **PlannerAgent** -- decomposes tasks into phased execution plans
- **JanitorAgent** -- scans for dead code, unused imports, bad naming, silent failures
- **SkillBuilderAgent** -- dynamically creates new specialist agents at runtime

All agents communicate through a protocol-based message bus with immutable task snapshots for safe retries.

## Development

```bash
git clone https://github.com/itscool2b/opentf.git
cd opentf
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
opentf
```

Run tests:

```bash
pytest
```

## Project Structure

```
src/opentf/
  agents/          # Agent system (base, registry, guardrails, specialists)
  auth/            # API key management
  cli/             # Textual TUI (app, widgets, theme)
  core/            # Engine, orchestrator, message bus, tool loop
  llm/             # Anthropic client wrapper
  memory/          # ChromaDB vector store + BM25 hybrid retrieval
  models/          # Pydantic models (task, message, plan, context)
  tools/           # File tools, data tools
config/            # Default YAML configuration
docs/              # Architecture and setup documentation
tests/             # Test suite
```

## Key Design Decisions

- **Single-agent loop** over multi-agent routing -- simpler, fewer LLM calls, unified tool access
- **Guardrails as gates** -- security and context run pre-execution, not as validators
- **Protocol-based messages** -- deterministic type routing, no free-form text parsing
- **Hybrid memory** -- vector search (ChromaDB) + keyword search (BM25) via reciprocal rank fusion
- **Async-first** -- all core operations use `async def`

## License

MIT
