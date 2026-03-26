# OpenTF

Autonomous AI coding agent for your terminal.

## Install

```bash
pip install opentf
```

Requires Python 3.12+. Then run:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
opentf
```

Or let OpenTF prompt you for your API key on first launch.

## What It Does

OpenTF reads, writes, and edits code in your project. It runs shell commands, searches your codebase, and uses a 9-strategy fuzzy edit engine so code changes land even when the AI gets indentation or whitespace slightly wrong. LSP diagnostics catch type errors after every edit. File changes are backed up and undoable.

## Commands

| Command | Description |
|---------|-------------|
| `/plan` | Create a structured execution plan |
| `/janitor` | Scan code for quality issues |
| `/model` | Switch between Claude models |
| `/provider` | Switch between Anthropic / OpenAI / Ollama |
| `/compact` | Compress conversation history |
| `/save` | Save session |
| `/resume` | Resume a saved session |
| `/undo` | Undo last file change |
| `/cost` | Show token usage and cost |
| `/status` | Show current session info |
| `/login` | Set API key |
| `/clear` | Clear conversation |
| `/exit` | Quit |

## Install Methods

### pip (recommended)

```bash
pip install opentf
```

### pipx (isolated)

```bash
pipx install opentf
```

### Install script

```bash
curl -fsSL https://raw.githubusercontent.com/itscool2b/opentf/main/install.sh | bash
```

Creates an isolated environment at `~/.opentf/` and adds the `opentf` command to your PATH.

### From source

```bash
git clone https://github.com/itscool2b/opentf.git
cd opentf
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
opentf
```

## Modes

```bash
opentf              # Full terminal UI (default)
opentf --repl       # Lightweight REPL mode
opentf -p "prompt"  # Single-shot headless mode
```

## Development

```bash
git clone https://github.com/itscool2b/opentf.git
cd opentf
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## License

MIT
