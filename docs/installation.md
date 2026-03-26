# Installation

## Requirements

- Python 3.12 or higher
- An [Anthropic API key](https://console.anthropic.com/)

## pip (recommended)

```bash
pip install opentf
```

## pipx (isolated environment)

```bash
pipx install opentf
```

pipx creates an isolated virtual environment automatically. Recommended if you don't want to pollute your system Python.

## Install script

The install script handles everything -- Python detection, virtual environment, PATH setup:

```bash
curl -fsSL https://raw.githubusercontent.com/itscool2b/opentf/main/install.sh | bash
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

### Script flags

| Flag | Description |
|------|-------------|
| `--upgrade`, `-u` | Upgrade existing installation |
| `--from-source` | Install from current directory (for development) |

## Install from source

```bash
git clone https://github.com/itscool2b/opentf.git
cd opentf
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## First Run

```bash
opentf
```

On first run, you'll be prompted for your Anthropic API key. You can also set it via environment variable:

```bash
# bash / zsh
export ANTHROPIC_API_KEY="sk-ant-..."

# fish
set -Ux ANTHROPIC_API_KEY "sk-ant-..."
```

The key is stored securely at `~/.config/opentf/credentials.json` with `0600` permissions.

| Command | Description |
|---------|-------------|
| `/login` | Set or update API key |
| `/logout` | Remove stored API key |
| `/status` | Check current auth status |

## Updating

### pip / pipx

```bash
pip install --upgrade opentf
# or
pipx upgrade opentf
```

### Install script

```bash
curl -fsSL https://raw.githubusercontent.com/itscool2b/opentf/main/install.sh | bash -s -- --upgrade
```

### Source install

```bash
cd opentf
git pull
pip install -e ".[dev]"
```

## Uninstalling

### pip / pipx

```bash
pip uninstall opentf
# or
pipx uninstall opentf
```

### Install script

```bash
rm -rf ~/.opentf
rm ~/.local/bin/opentf
rm -rf ~/.config/opentf
```

Remove the PATH line from your shell config if desired.

## Troubleshooting

### `opentf: command not found`

Make sure `~/.local/bin` is in your PATH:

```bash
echo $PATH | tr ':' '\n' | grep local
```

If missing:

```bash
# bash / zsh
export PATH="$HOME/.local/bin:$PATH"

# fish
fish_add_path ~/.local/bin
```

### Python version too old

OpenTF requires Python 3.12+. Check your version:

```bash
python3 --version
```

### Permission denied on credentials

```bash
chmod 600 ~/.config/opentf/credentials.json
```

### ChromaDB or sentence-transformers issues

These are used for the memory system. If they fail to install (common on some ARM systems), OpenTF still works -- memory features will be unavailable. Install them separately:

```bash
pip install chromadb sentence-transformers
```
