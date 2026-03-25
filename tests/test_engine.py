"""Tests for Engine lifecycle management."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture()
def mock_engine():
    """Create an Engine with all heavy dependencies mocked."""
    with patch("opentf.core.engine.LLMClient") as MockLLM, \
         patch("opentf.core.workspace.scan_workspace") as MockScan, \
         patch("opentf.core.engine.MainAgent"), \
         patch("opentf.core.engine.SecurityAgent"), \
         patch("opentf.core.engine.ContextAgent"), \
         patch("opentf.core.engine.JanitorAgent"), \
         patch("opentf.core.engine.PlannerAgent"), \
         patch("opentf.core.engine.SkillBuilderAgent") as MockSkill:

        MockScan.return_value = MagicMock()
        MockSkill.return_value.load_saved_skills = MagicMock()
        # Mock LLMClient to return a mock with settable attributes
        mock_llm = MagicMock()
        MockLLM.return_value = mock_llm

        from opentf.core.engine import Engine
        engine = Engine(provider_name="anthropic", model="test-model")
        yield engine


def test_engine_sets_model(mock_engine) -> None:
    assert mock_engine.llm.model == "test-model"


@pytest.mark.asyncio
async def test_engine_run_adds_to_history(mock_engine) -> None:
    mock_engine.orchestrator.run = AsyncMock(return_value=[
        MagicMock(success=True, output={"response": "hello"}, errors=[], token_usage=10),
    ])
    results = await mock_engine.run("test input")
    assert len(mock_engine.conversation_history) == 2  # user + assistant
    assert mock_engine.conversation_history[0]["role"] == "user"


@pytest.mark.asyncio
async def test_engine_run_failed_not_in_history(mock_engine) -> None:
    mock_engine.orchestrator.run = AsyncMock(return_value=[
        MagicMock(success=False, output={}, errors=["failed"], token_usage=0),
    ])
    await mock_engine.run("test")
    assert len(mock_engine.conversation_history) == 1  # only user, no assistant


@pytest.mark.asyncio
async def test_engine_close(mock_engine) -> None:
    mock_engine.conversation_history.append({"role": "user", "content": "test"})
    await mock_engine.close()
    assert len(mock_engine.conversation_history) == 0


@pytest.mark.asyncio
async def test_engine_double_close_no_crash(mock_engine) -> None:
    await mock_engine.close()
    await mock_engine.close()  # Should not raise


@pytest.mark.asyncio
async def test_engine_context_manager() -> None:
    with patch("opentf.core.engine.LLMClient"), \
         patch("opentf.core.workspace.scan_workspace") as MockScan, \
         patch("opentf.core.engine.MainAgent"), \
         patch("opentf.core.engine.SecurityAgent"), \
         patch("opentf.core.engine.ContextAgent"), \
         patch("opentf.core.engine.JanitorAgent"), \
         patch("opentf.core.engine.PlannerAgent"), \
         patch("opentf.core.engine.SkillBuilderAgent") as MockSkill:

        MockScan.return_value = MagicMock()
        MockSkill.return_value.load_saved_skills = MagicMock()

        from opentf.core.engine import Engine
        async with Engine() as engine:
            engine.conversation_history.append({"role": "user", "content": "test"})
        assert len(engine.conversation_history) == 0


@pytest.mark.asyncio
async def test_engine_run_returns_correct_shape(mock_engine) -> None:
    mock_engine.orchestrator.run = AsyncMock(return_value=[
        MagicMock(success=True, output={"response": "ok"}, errors=[], token_usage=42),
    ])
    results = await mock_engine.run("test")
    assert len(results) == 1
    assert results[0]["success"] is True
    assert results[0]["output"]["response"] == "ok"
