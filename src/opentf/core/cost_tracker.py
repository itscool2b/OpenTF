"""Real-time cost tracking with per-task and per-model breakdowns.

Tracks $/hour rate, per-task cost deltas, and per-model usage
across a session. Used by the TUI header and /cost command.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class TaskCost:
    """Cost record for a single task."""

    label: str
    tokens: int
    cost: float
    model: str


@dataclass
class ModelUsage:
    """Cumulative usage for a single model."""

    model: str
    tokens: int = 0
    cost: float = 0.0
    requests: int = 0


@dataclass
class CostTracker:
    """Tracks real-time cost metrics across a session."""

    _start_time: float = field(default_factory=time.monotonic)
    _task_costs: list[TaskCost] = field(default_factory=list)
    _task_start_tokens: int = 0
    _task_start_cost: float = 0.0
    _per_model: dict[str, ModelUsage] = field(default_factory=dict)

    def dollars_per_hour(self, total_cost: float) -> float:
        """Calculate current $/hour rate."""
        elapsed = time.monotonic() - self._start_time
        if elapsed < 1.0:
            return 0.0
        return total_cost / elapsed * 3600

    def start_task(self, total_tokens: int, total_cost: float) -> None:
        """Mark the beginning of a task for delta tracking."""
        self._task_start_tokens = total_tokens
        self._task_start_cost = total_cost

    def end_task(
        self,
        label: str,
        total_tokens: int,
        total_cost: float,
        model: str,
    ) -> None:
        """Mark the end of a task and record its cost."""
        task_tokens = total_tokens - self._task_start_tokens
        task_cost = total_cost - self._task_start_cost

        self._task_costs.append(TaskCost(
            label=label[:60],
            tokens=task_tokens,
            cost=task_cost,
            model=model,
        ))

        if model not in self._per_model:
            self._per_model[model] = ModelUsage(model=model)
        self._per_model[model].tokens += task_tokens
        self._per_model[model].cost += task_cost
        self._per_model[model].requests += 1

    def average_task_cost(self) -> float:
        """Average cost per task."""
        if not self._task_costs:
            return 0.0
        return sum(t.cost for t in self._task_costs) / len(self._task_costs)

    @property
    def task_count(self) -> int:
        return len(self._task_costs)

    @property
    def task_history(self) -> list[TaskCost]:
        return list(self._task_costs)

    @property
    def per_model(self) -> dict[str, ModelUsage]:
        return dict(self._per_model)
