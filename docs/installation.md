# Installation

## Requirements

- Python 3.12 or higher
- An [Anthropic API key](https://console.anthropic.com/)

## Quick Install (Recommended)

The install script handles everything -- Python detection, virtual environment, PATH setup:

```bash
curl -fsSL https://opentf.dev/install.sh | bash
```

This will:

1. Find Python 3.12+ on your system
2. Create `~/.opentf/` with an isolated virtual environment
3. Install OpenTF from PyPI into that environment
4. Add the `opentf` command to `~/.local/bin/`
5. Update your shell config (bash, zsh, or fish) to include `~/.local/bin` in PATH

After install, restart your shell or run:

```bash
source ~/.bashrc    # bash
source ~/.zshrc     # zsh
source ~/.config/fish/config.fish  # fish
```

Then run:

```bash
opentf
```

## Install from Source

Clone the repo and install in development mode:

```bash
git clone https://github.com/itscool2b/opentf.git
cd opentf
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Run directly:

```bash
opentf
```

## Install via pip

```bash
pip install opentf
```

## Updating

### curl install

Re-run the install script. It will upgrade the existing installation in place:

```bash
curl -fsSL https://opentf.dev/install.sh | bash
```

The script detects the existing `~/.opentf/` directory and upgrades the package without recreating the virtual environment.

### Source install

Pull the latest changes and reinstall:

```bash
cd opentf
git pull
source .venv/bin/activate
pip install -e ".[dev]"
```

### pip install

```bash
pip install --upgrade opentf
```

## API Key Setup

OpenTF needs an Anthropic API key. There are two ways to provide it:

### Option 1: Environment variable (recommended for CI/scripts)

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
opentf
```

Add it to your shell config to persist:

```bash
# bash/zsh
echo 'export ANTHROPIC_API_KEY="sk-ant-..."' >> ~/.bashrc

# fish
set -Ux ANTHROPIC_API_KEY "sk-ant-..."
```

### Option 2: Built-in credential store

On first run, OpenTF prompts for your API key and stores it at `~/.config/opentf/credentials.json` with `0600` permissions (owner read/write only).

You can also manage it with commands inside OpenTF:

| Command | Description |
|---------|-------------|
| `/login` | Set or update API key |
| `/logout` | Remove stored API key |
| `/status` | Check current auth status |

**Resolution order:** Environment variable takes priority over stored credentials.

## Uninstalling

### curl install

```bash
rm -rf ~/.opentf
rm ~/.local/bin/opentf
rm -rf ~/.config/opentf
```

Then remove the PATH line from your shell config (`~/.bashrc`, `~/.zshrc`, or `~/.config/fish/config.fish`).

### Source install

```bash
source .venv/bin/activate
pip uninstall opentf
deactivate
rm -rf .venv
```

### pip install

```bash
pip uninstall opentf
rm -rf ~/.config/opentf
```

## Troubleshooting

### `opentf: command not found`

Make sure `~/.local/bin` is in your PATH:

```bash
echo $PATH | tr ':' '\n' | grep local
```

If it's missing, add it:

```bash
# bash/zsh
export PATH="$HOME/.local/bin:$PATH"

# fish
fish_add_path ~/.local/bin
```

### Python version too old

OpenTF requires Python 3.12+. Check your version:

```bash
python3 --version
```

Install a newer version via your package manager or [python.org](https://python.org).

### Permission denied on credentials file

The credentials file must be owned by you with `0600` permissions:

```bash
chmod 600 ~/.config/opentf/credentials.json
```

### ChromaDB or sentence-transformers issues

These are optional dependencies used for the memory system. If they fail to install (common on some ARM systems), OpenTF still works -- memory features will be unavailable.

To install them separately:

```bash
pip install chromadb sentence-transformers
```
