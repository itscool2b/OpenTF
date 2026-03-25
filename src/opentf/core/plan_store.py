"""Plan persistence -- save/load plans as YAML."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from opentf.models.plan import Plan

log = logging.getLogger(__name__)

PLANS_DIR = Path("plans")


class PlanStore:
    """Saves and loads plans as YAML files."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or PLANS_DIR

    def save(self, plan: Plan) -> Path:
        """Save plan as YAML. Returns the file path."""
        self.base_dir.mkdir(parents=True, exist_ok=True)
        path = self.base_dir / f"{plan.label}.yaml"

        # Avoid overwriting -- append suffix if exists
        counter = 1
        while path.exists():
            path = self.base_dir / f"{plan.label}-{counter}.yaml"
            counter += 1

        path.write_text(
            yaml.dump(plan.to_dict(), default_flow_style=False, sort_keys=False)
        )
        log.info("Saved plan to %s", path)
        return path

    def load(self, label: str) -> Plan | None:
        """Load a plan from YAML by label."""
        path = self.base_dir / f"{label}.yaml"
        if not path.exists():
            return None

        data = yaml.safe_load(path.read_text())
        if not data:
            return None

        return Plan.from_dict(data)

    def list_plans(self) -> list[str]:
        """List all saved plan labels."""
        if not self.base_dir.exists():
            return []
        return [
            p.stem for p in sorted(self.base_dir.glob("*.yaml"))
        ]
