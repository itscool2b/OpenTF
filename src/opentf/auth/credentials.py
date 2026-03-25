"""Secure credential manager for OpenTF.

Resolution order:
  1. ANTHROPIC_API_KEY environment variable
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

log = logging.getLogger(__name__)

ENV_VAR = "ANTHROPIC_API_KEY"
CONFIG_DIR_NAME = "opentf"


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

    def resolve_api_key(self) -> str | None:
        """Resolve API key from env var or credential file.

        Returns the key string, or None if not found anywhere.
        """
        # 1. Environment variable (highest priority)
        env_key = os.environ.get(ENV_VAR)
        if env_key:
            log.debug("API key resolved from environment variable")
            return env_key

        # 2. Credential file
        if self.credentials_file.exists():
            try:
                data = json.loads(self.credentials_file.read_text())
                stored_key = data.get("api_key")
                if stored_key:
                    log.debug("API key resolved from credential file")
                    return stored_key
            except (json.JSONDecodeError, OSError):
                log.warning("Failed to read credential file")

        return None

    def resolve_source(self) -> str:
        """Return a label describing where the key came from."""
        if os.environ.get(ENV_VAR):
            return "environment variable"
        if self.credentials_file.exists():
            try:
                data = json.loads(self.credentials_file.read_text())
                if data.get("api_key"):
                    return "stored credential"
            except (json.JSONDecodeError, OSError):
                pass
        return "not configured"

    def store_api_key(self, key: str) -> None:
        """Store API key to credentials.json with restricted permissions."""
        self._config_dir.mkdir(parents=True, exist_ok=True)
        # Directory: 0700 (owner only)
        self._config_dir.chmod(stat.S_IRWXU)

        data = json.dumps({"api_key": key}, indent=2)
        self.credentials_file.write_text(data)
        # File: 0600 (owner read/write only)
        self.credentials_file.chmod(stat.S_IRUSR | stat.S_IWUSR)

        log.info("API key stored at %s", self.credentials_file)

    def clear_credentials(self) -> None:
        """Delete the credential file."""
        if self.credentials_file.exists():
            self.credentials_file.unlink()
            log.info("Credentials cleared")

    @staticmethod
    def redact_key(key: str) -> str:
        """Redact an API key for safe display.

        'sk-ant-api03-abc...xyz' -> 'sk-ant-...xyz'
        """
        if not key:
            return "(none)"
        if len(key) <= 12:
            return key[:4] + "..." + key[-4:]
        return key[:6] + "..." + key[-4:]

    @staticmethod
    def validate_key_format(key: str) -> bool:
        """Basic format check -- does it look like an Anthropic key?"""
        key = key.strip()
        if not key:
            return False
        # Anthropic keys start with sk-ant-
        if key.startswith("sk-ant-"):
            return True
        # Also accept keys that are at least 20 chars (for flexibility)
        return len(key) >= 20
