# OpenTF Authentication

## Overview

OpenTF supports multiple LLM providers: **Anthropic** (Claude), **OpenAI** (GPT), and **Ollama** (local). For Anthropic, both API keys and Claude.ai session tokens are accepted.

## Credential Resolution

Credentials are checked per provider in this order (first match wins):

1. **Environment variable** -- highest priority
   - Anthropic: `ANTHROPIC_API_KEY`
   - OpenAI: `OPENAI_API_KEY`
   - Ollama: no key needed
2. **`~/.config/opentf/credentials.json`** -- stored by the CLI on first run

## First-Run Setup

When you launch `opentf` without a configured credential:

1. Choose your LLM provider (Anthropic, OpenAI, or Ollama)
2. For Anthropic: enter your API key (`sk-ant-...`) or Claude session token
3. For OpenAI: enter your API key (`sk-...`)
4. For Ollama: no key needed (connects to local server)
5. Paste it in (input is masked)
4. OpenTF validates the key with a lightweight API call
5. On success, the key is stored and the main UI loads

## Credential Storage

| Item | Path |
|------|------|
| Config directory | `~/.config/opentf/` |
| Credentials file | `~/.config/opentf/credentials.json` |

The config directory respects `XDG_CONFIG_HOME` if set.

### File Format

```json
{
  "api_key": "sk-ant-..."
}
```

## Security Measures

### File Permissions

- Config directory: `0700` (owner only: read, write, execute)
- Credentials file: `0600` (owner only: read, write)

These permissions are set automatically when the key is stored.

### Key Protection

- API keys are **never logged** -- they are redacted to `sk-ant-...xxxx` in any output
- Keys are **never included in error messages** or stack traces
- The key input field in the onboarding screen is **masked** (`password=True`)
- Keys are **never sent anywhere** except Anthropic's API endpoint

### Environment Variable

Setting `ANTHROPIC_API_KEY` takes priority over the stored credential. This is useful for:
- CI/CD environments
- Temporary key overrides
- Teams that manage keys through their shell environment

## CLI Commands

| Command | Description |
|---------|-------------|
| `/status` | Shows current auth method (env var or stored), redacted key, and model |
| `/login` | Re-enter or change your API key |
| `/logout` | Delete stored credentials |

## Programmatic Usage

```python
from opentf.core.engine import Engine

# Option 1: Let it resolve from env var or stored credential
engine = Engine()

# Option 2: Pass key directly
engine = Engine(api_key="sk-ant-...")
```

## Credential Manager API

```python
from opentf.auth.credentials import CredentialManager

cm = CredentialManager()

# Resolve key from all sources
key = cm.resolve_api_key()

# Check where the key came from
source = cm.resolve_source()  # "environment variable" | "stored credential" | "not configured"

# Store a key securely
cm.store_api_key("sk-ant-...")

# Clear stored credentials
cm.clear_credentials()

# Redact for display
cm.redact_key("sk-ant-api03-abc123xyz789")  # "sk-ant...z789"

# Validate format
cm.validate_key_format("sk-ant-api03-test")  # True
```
