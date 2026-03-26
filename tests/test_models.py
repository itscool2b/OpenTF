"""Tests for Pydantic data models."""

from opentf.models.task import Task, TaskStatus
from opentf.models.plan import Plan, PlanPhase, PlanStep, StepStatus
from opentf.models.message import Message, MessageType


def test_task_creation() -> None:
    task = Task(description="test task")
    assert task.status == TaskStatus.PENDING
    assert task.description == "test task"
    assert task.id


def test_task_transition() -> None:
    task = Task(description="test")
    task.transition(TaskStatus.EXECUTING, agent="main", reason="starting")
    assert task.status == TaskStatus.EXECUTING
    assert len(task.history) == 1
    assert task.history[0].agent == "main"


def test_task_completion_sets_timestamp() -> None:
    task = Task(description="test")
    assert task.completed_at is None
    task.transition(TaskStatus.COMPLETED, agent="main", reason="done")
    assert task.completed_at is not None


def test_plan_roundtrip() -> None:
    plan = Plan(
        label="test-plan",
        description="A test plan",
        goal="test something",
        phases=[
            PlanPhase(
                name="Phase 1",
                description="First phase",
                steps=[
                    PlanStep(
                        id="1.1",
                        name="Step one",
                        description="Do the thing",
                        agent_type="code",
                        depends_on=[],
                        success_criteria="thing is done",
                    ),
                ],
            ),
        ],
    )
    data = plan.to_dict()
    restored = Plan.from_dict(data)
    assert restored.label == "test-plan"
    assert len(restored.phases) == 1
    assert restored.phases[0].steps[0].id == "1.1"
    assert restored.phases[0].steps[0].name == "Step one"


def test_plan_all_steps() -> None:
    plan = Plan(
        label="multi",
        description="",
        goal="",
        phases=[
            PlanPhase(name="P1", steps=[
                PlanStep(id="1.1", name="a", description="", agent_type="code"),
                PlanStep(id="1.2", name="b", description="", agent_type="code"),
            ]),
            PlanPhase(name="P2", steps=[
                PlanStep(id="2.1", name="c", description="", agent_type="code"),
            ]),
        ],
    )
    assert len(plan.all_steps()) == 3


def test_plan_completion_summary() -> None:
    plan = Plan(
        label="done-plan",
        description="",
        goal="",
        status="completed",
        phases=[
            PlanPhase(name="P1", steps=[
                PlanStep(id="1.1", name="step", description="", agent_type="code", status=StepStatus.DONE),
            ]),
        ],
    )
    summary = plan.format_completion_summary()
    # Title chars are gradient-wrapped; check for step data and glyphs
    assert "1 done" in summary
    assert "●" in summary  # done step icon
    assert "1.1" in summary


def test_plan_failed_summary() -> None:
    plan = Plan(
        label="fail-plan",
        description="",
        goal="",
        status="failed",
        phases=[
            PlanPhase(name="P1", steps=[
                PlanStep(id="1.1", name="ok", description="", agent_type="code", status=StepStatus.DONE),
                PlanStep(id="1.2", name="bad", description="", agent_type="code",
                         status=StepStatus.FAILED, error="something broke"),
            ]),
        ],
    )
    summary = plan.format_completion_summary()
    # Title is gradient-wrapped; check for step data and error text
    assert "1 done" in summary
    assert "1 failed" in summary
    assert "something broke" in summary


def test_message_creation() -> None:
    msg = Message(
        type=MessageType.TASK_COMPLETED,
        source="main",
        payload={"result": "ok"},
    )
    assert msg.type == MessageType.TASK_COMPLETED
    assert msg.source == "main"
    assert msg.id


def test_message_approval_always_type() -> None:
    msg = Message(type=MessageType.APPROVAL_ALWAYS, source="user")
    assert msg.type.value == "approval_always"
