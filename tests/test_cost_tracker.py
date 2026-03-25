"""Tests for CostTracker real-time cost metrics."""

import time
import pytest
from unittest.mock import patch

from opentf.core.cost_tracker import CostTracker, TaskCost, ModelUsage


def test_initial_rate_zero() -> None:
    tracker = CostTracker()
    assert tracker.dollars_per_hour(0.0) == 0.0


def test_dollars_per_hour() -> None:
    tracker = CostTracker()
    # Simulate 1 hour elapsed with $10 spent
    tracker._start_time = time.monotonic() - 3600
    rate = tracker.dollars_per_hour(10.0)
    assert abs(rate - 10.0) < 0.1


def test_dollars_per_hour_short_session() -> None:
    tracker = CostTracker()
    # Less than 1 second elapsed — should return 0
    assert tracker.dollars_per_hour(5.0) == 0.0


def test_start_end_task() -> None:
    tracker = CostTracker()
    tracker.start_task(total_tokens=0, total_cost=0.0)
    tracker.end_task("test task", total_tokens=100, total_cost=0.05, model="sonnet")

    assert tracker.task_count == 1
    assert tracker.task_history[0].label == "test task"
    assert tracker.task_history[0].tokens == 100
    assert tracker.task_history[0].cost == 0.05
    assert tracker.task_history[0].model == "sonnet"


def test_multiple_tasks_average() -> None:
    tracker = CostTracker()

    tracker.start_task(0, 0.0)
    tracker.end_task("t1", 100, 0.10, "sonnet")

    tracker.start_task(100, 0.10)
    tracker.end_task("t2", 300, 0.30, "sonnet")

    assert tracker.task_count == 2
    avg = tracker.average_task_cost()
    assert abs(avg - 0.15) < 0.001  # (0.10 + 0.20) / 2


def test_per_model_tracking() -> None:
    tracker = CostTracker()

    tracker.start_task(0, 0.0)
    tracker.end_task("t1", 100, 0.10, "sonnet")

    tracker.start_task(100, 0.10)
    tracker.end_task("t2", 200, 0.30, "opus")

    models = tracker.per_model
    assert "sonnet" in models
    assert "opus" in models
    assert models["sonnet"].requests == 1
    assert models["opus"].requests == 1
    assert models["sonnet"].tokens == 100
    assert models["opus"].tokens == 100


def test_task_history_order() -> None:
    tracker = CostTracker()
    for i in range(5):
        tracker.start_task(i * 10, i * 0.01)
        tracker.end_task(f"task-{i}", (i + 1) * 10, (i + 1) * 0.01, "sonnet")

    labels = [t.label for t in tracker.task_history]
    assert labels == ["task-0", "task-1", "task-2", "task-3", "task-4"]


def test_empty_tracker_properties() -> None:
    tracker = CostTracker()
    assert tracker.task_count == 0
    assert tracker.task_history == []
    assert tracker.per_model == {}
    assert tracker.average_task_cost() == 0.0


def test_task_label_truncated() -> None:
    tracker = CostTracker()
    long_label = "x" * 100
    tracker.start_task(0, 0.0)
    tracker.end_task(long_label, 10, 0.01, "sonnet")
    assert len(tracker.task_history[0].label) == 60
