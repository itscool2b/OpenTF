"""Load configuration from YAML with sensible defaults."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path(__file__).parent.parent.parent.parent / "config" / "default.yaml"


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Load config from YAML, falling back to empty dict."""
    config_path = path or DEFAULT_CONFIG_PATH
    if not config_path.exists():
        log.info("No config file at %s, using defaults", config_path)
        return {}
    try:
        data = yaml.safe_load(config_path.read_text()) or {}
        log.info("Loaded config from %s", config_path)
        return data
    except Exception as exc:
        log.warning("Failed to load config: %s", exc)
        return {}
