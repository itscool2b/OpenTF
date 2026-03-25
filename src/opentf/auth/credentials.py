"""Secure credential manager for OpenTF.

Resolution order (per provider):
  1. Provider-specific environment variable (ANTHROPIC_API_KEY, OPENAI_API_KEY)
  2. ~/.config/opentf/credentials.json (file mode 0600)

Security:
  - Credential file created with 0600 (owner read/write only)
  - Config directory created with 0700
  - API keys never logged -- redacted to 'sk-ant-...xxxx'
  - Keys never included in error messages
"""

from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

CONFIG_DIR_NAME = "opentf"

# Provider -> env var name
PROVIDER_ENV_VARS: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "ollama": "",
}

# Provider -> credential file key
PROVIDER_CRED_KEYS: dict[str, str] = {
    "anthropic": "anthropic_api_key",
    "openai": "openai_api_key",
}


class CredentialManager:
    """Manages secure storage and retrieval of API credentials."""

    def __init__(self, config_dir: Path | None = None) -> None:
        if config_dir:
            self._config_dir = config_dir
        else:
            xdg = os.environ.get("XDG_CONFIG_HOME", "")
            base = Path(xdg) if xdg else Path.home() / ".config"
            self._config_dir = base / CONFIG_DIR_NAME

    @property
    def config_dir(self) -> Path:
        return self._config_dir

    @property
    def credentials_file(self) -> Path:
        return self._config_dir / "credentials.json"

    def resolve_api_key(self, provider: str = "anthropic") -> str | None:
        """Resolve API key for a provider from env var or credential file.

        Returns the key string, or None if not found anywhere.
        """
        # 1. Environment variable (highest priority)
        env_var = PROVIDER_ENV_VARS.get(provider, "")
        if env_var:
            env_key = os.environ.get(env_var)
            if env_key:
                log.debug("API key for %s resolved from environment variable", provider)
                return env_key

        # 2. Credential file
        if self.credentials_file.exists():
            try:
                data = json.loads(self.credentials_file.read_text())
                # Try provider-specific key first
                cred_key = PROVIDER_CRED_KEYS.get(provider, "")
                if cred_key:
                    stored_key = data.get(cred_key)
                    if stored_key:
                        log.debug("API key for %s resolved from credential file", provider)
                        return stored_key
                # Backward compat: "api_key" is treated as anthropic key
                if provider == "anthropic":
                    stored_key = data.get("api_key")
                    if stored_key:
                        log.debug("API key for anthropic resolved from legacy credential file")
                        return stored_key
            except (json.JSONDecodeError, OSError):
                log.warning("Failed to read credential file")

        return None

    def resolve_source(self, provider: str = "anthropic") -> str:
        """Return a label describing where the key came from."""
        env_var = PROVIDER_ENV_VARS.get(provider, "")
        if env_var and os.environ.get(env_var):
            return "environment variable"
        if self.credentials_file.exists():
            try:
                data = json.loads(self.credentials_file.read_text())
                cred_key = PROVIDER_CRED_KEYS.get(provider, "")
                if cred_key and data.get(cred_key):
                    return "stored credential"
                if provider == "anthropic" and data.get("api_key"):
                    return "stored credential"
            except (json.JSONDecodeError, OSError):
                pass
        return "not configured"

    def store_api_key(self, key: str, provider: str = "anthropic") -> None:
        """Store API key to credentials.json with restricted permissions."""
        self._config_dir.mkdir(parents=True, exist_ok=True)
        self._config_dir.chmod(stat.S_IRWXU)

        # Load existing data to preserve other provider keys
        existing: dict[str, Any] = {}
        if self.credentials_file.exists():
            try:
                existing = json.loads(self.credentials_file.read_text())
            except (json.JSONDecodeError, OSError):
                pass

        # Store under provider-specific key
        cred_key = PROVIDER_CRED_KEYS.get(provider, f"{provider}_api_key")
        existing[cred_key] = key

        # Also store as "api_key" for backward compat if anthropic
        if provider == "anthropic":
            existing["api_key"] = key

        data = json.dumps(existing, indent=2)
        self.credentials_file.write_text(data)
        self.credentials_file.chmod(stat.S_IRUSR | stat.S_IWUSR)

        log.info("API key for %s stored at %s", provider, self.credentials_file)

    def clear_credentials(self) -> None:
        """Delete the credential file."""
        if self.credentials_file.exists():
            self.credentials_file.unlink()
            log.info("Credentials cleared")

    @staticmethod
    def redact_key(key: str) -> str:
        """Redact an API key for safe display."""
        if not key:
            return "(none)"
        if len(key) <= 12:
            return key[:4] + "..." + key[-4:]
        return key[:6] + "..." + key[-4:]

    @staticmethod
    def validate_key_format(key: str, provider: str = "anthropic") -> bool:
        """Basic format check for a provider's API key."""
        key = key.strip()
        if not key:
            return False
        if provider == "anthropic":
            if key.startswith("sk-ant-"):
                return True
        elif provider == "openai":
            if key.startswith("sk-"):
                return True
        # Accept keys that are at least 20 chars (for flexibility)
        return len(key) >= 20
