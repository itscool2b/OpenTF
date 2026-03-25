"""Tests for the ContextAgent guardrail."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from opentf.agents.guardrails.context import ContextAgent, SessionState
from opentf.models.context import AgentContext
from opentf.models.task import Task
from opentf.memory.store import MemoryEntry


def _make_context(description: str = "test", history: list | None = None) -> AgentContext:
    return AgentContext(
        task=Task(description=description, agent_type="main"),
        conversation_history=history or [],
    )


# --- Memory retrieval ---

@pytest.mark.asyncio
async def test_no_retriever_returns_empty_memory() -> None:
    agent = ContextAgent(retriever=None)
    result = await agent.process(_make_context("hello"))
    assert result.success
    assert result.output["memory_context"] == ""


@pytest.mark.asyncio
async def test_retriever_returns_relevant_memories() -> None:
    retriever = MagicMock()
    retriever.search = AsyncMock(return_value=[
        MemoryEntry(id="1", content="past solution", distance=0.3),
    ])
    agent = ContextAgent(retriever=retriever)
    result = await agent.process(_make_context("find bug"))
    assert "past solution" in result.output["memory_context"]


@pytest.mark.asyncio
async def test_retriever_filters_high_distance() -> None:
    retriever = MagicMock()
    retriever.search = AsyncMock(return_value=[
        MemoryEntry(id="1", content="irrelevant", distance=0.9),
    ])
    agent = ContextAgent(retriever=retriever)
    result = await agent.process(_make_context("test"))
    assert result.output["memory_context"] == ""


@pytest.mark.asyncio
async def test_retriever_exception_non_blocking() -> None:
    retriever = MagicMock()
    retriever.search = AsyncMock(side_effect=RuntimeError("db error"))
    agent = ContextAgent(retriever=retriever)
    result = await agent.process(_make_context("test"))
    assert result.success
    assert result.output["memory_context"] == ""


# --- Workspace info ---

@pytest.mark.asyncio
async def test_workspace_info_included() -> None:
    workspace = MagicMock()
    workspace.language = "python"
    workspace.framework = "fastapi"
    workspace.config_files = ["pyproject.toml"]
    workspace.test_command = "pytest"
    workspace.file_count = 42
    workspace.tree = "project/"
    workspace.root = "/home/user/project"

    agent = ContextAgent(workspace=workspace)
    result = await agent.process(_make_context("test"))

    assert "python" in result.output["workspace_summary"]
    assert result.output["test_command"] == "pytest"
    assert result.output["workspace_root"] == "/home/user/project"


@pytest.mark.asyncio
async def test_no_workspace_no_workspace_keys() -> None:
    agent = ContextAgent(workspace=None)
    result = await agent.process(_make_context("test"))
    assert "workspace_summary" not in result.output


# --- Session state ---

@pytest.mark.asyncio
async def test_turn_count_increments() -> None:
    agent = ContextAgent()
    await agent.process(_make_context("first"))
    await agent.process(_make_context("second"))
    assert agent._session.turn_count == 2


@pytest.mark.asyncio
async def test_session_context_from_history() -> None:
    agent = ContextAgent()
    history = [
        {"role": "user", "content": "write a function"},
        {"role": "assistant", "content": "def hello(): pass"},
    ]
    result = await agent.process(_make_context("next step", history=history))
    session = result.output["session_context"]
    assert "user:" in session.lower()
    assert "assistant:" in session.lower()


@pytest.mark.asyncio
async def test_session_context_empty_history() -> None:
    agent = ContextAgent()
    result = await agent.process(_make_context("test", history=[]))
    assert result.output["session_context"] == ""


@pytest.mark.asyncio
async def test_context_summary_turn_1() -> None:
    agent = ContextAgent()
    result = await agent.process(_make_context("test"))
    # Turn 1 should not include "Turn X" message
    assert "Turn" not in result.output["context_summary"]


@pytest.mark.asyncio
async def test_context_summary_turn_2_includes_turn_count() -> None:
    agent = ContextAgent()
    await agent.process(_make_context("first"))
    result = await agent.process(_make_context("second"))
    assert "Turn 2" in result.output["context_summary"]
