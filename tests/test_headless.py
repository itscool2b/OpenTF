"""Tests for headless (non-interactive) mode."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from opentf.cli.headless import parse_args, run_headless


# --- Argument parsing ---

def test_parse_args_prompt() -> None:
    args = parse_args(["--prompt", "hello world", "-n"])
    assert args.prompt == "hello world"
    assert args.non_interactive is True


def test_parse_args_short_flags() -> None:
    args = parse_args(["-p", "fix tests", "-n"])
    assert args.prompt == "fix tests"
    assert args.non_interactive is True


def test_parse_args_model() -> None:
    args = parse_args(["-p", "test", "-n", "--model", "opus"])
    assert args.model == "opus"


def test_parse_args_provider() -> None:
    args = parse_args(["-p", "test", "-n", "--provider", "openai"])
    assert args.provider == "openai"


def test_parse_args_defaults() -> None:
    args = parse_args(["-p", "test", "-n"])
    assert args.provider == "anthropic"
    assert args.model is None
    assert args.auto_approve is False
    assert args.json_output is False


def test_parse_args_auto_approve() -> None:
    args = parse_args(["-p", "test", "-n", "--auto-approve"])
    assert args.auto_approve is True


def test_parse_args_json_output() -> None:
    args = parse_args(["-p", "test", "-n", "--json"])
    assert args.json_output is True


# --- Headless runner ---

@pytest.mark.asyncio
async def test_headless_no_prompt_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without --prompt and isatty=True, should return exit code 2."""
    monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: True))
    args = parse_args(["-n"])
    exit_code = await run_headless(args)
    assert exit_code == 2


@pytest.mark.asyncio
async def test_headless_empty_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty stdin should return exit code 2."""
    stdin_mock = MagicMock()
    stdin_mock.isatty.return_value = False
    stdin_mock.read.return_value = ""
    monkeypatch.setattr("sys.stdin", stdin_mock)
    args = parse_args(["-n"])
    exit_code = await run_headless(args)
    assert exit_code == 2


@pytest.mark.asyncio
async def test_headless_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Successful run should return exit code 0."""
    mock_results = [
        {
            "success": True,
            "output": {"response": "Done!"},
            "errors": [],
            "token_usage": 100,
        }
    ]

    with patch("opentf.cli.headless.Engine") as MockEngine:
        mock_engine = MagicMock()
        mock_engine.run = AsyncMock(return_value=mock_results)
        mock_engine.close = AsyncMock()
        mock_engine.bus = MagicMock()
        mock_engine.bus.subscribe = MagicMock()
        mock_engine.llm = MagicMock()
        mock_engine.llm.usage = MagicMock(total=100)
        MockEngine.return_value = mock_engine

        args = parse_args(["-p", "do something", "-n"])
        exit_code = await run_headless(args)
        assert exit_code == 0


@pytest.mark.asyncio
async def test_headless_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Failed run should return exit code 1."""
    mock_results = [
        {
            "success": False,
            "output": {},
            "errors": ["Something went wrong"],
            "token_usage": 50,
        }
    ]

    with patch("opentf.cli.headless.Engine") as MockEngine:
        mock_engine = MagicMock()
        mock_engine.run = AsyncMock(return_value=mock_results)
        mock_engine.close = AsyncMock()
        mock_engine.bus = MagicMock()
        mock_engine.bus.subscribe = MagicMock()
        mock_engine.llm = MagicMock()
        mock_engine.llm.usage = MagicMock(total=50)
        MockEngine.return_value = mock_engine

        args = parse_args(["-p", "do something", "-n"])
        exit_code = await run_headless(args)
        assert exit_code == 1


@pytest.mark.asyncio
async def test_headless_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exception during run should return exit code 1."""
    with patch("opentf.cli.headless.Engine") as MockEngine:
        mock_engine = MagicMock()
        mock_engine.run = AsyncMock(side_effect=RuntimeError("No API key"))
        mock_engine.close = AsyncMock()
        mock_engine.bus = MagicMock()
        mock_engine.bus.subscribe = MagicMock()
        MockEngine.return_value = mock_engine

        args = parse_args(["-p", "do something", "-n"])
        exit_code = await run_headless(args)
        assert exit_code == 1


@pytest.mark.asyncio
async def test_headless_json_output(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    """JSON output flag should produce valid JSON."""
    mock_results = [
        {
            "success": True,
            "output": {"response": "All done"},
            "errors": [],
            "token_usage": 200,
        }
    ]

    with patch("opentf.cli.headless.Engine") as MockEngine:
        mock_engine = MagicMock()
        mock_engine.run = AsyncMock(return_value=mock_results)
        mock_engine.close = AsyncMock()
        mock_engine.bus = MagicMock()
        mock_engine.bus.subscribe = MagicMock()
        mock_engine.llm = MagicMock()
        mock_engine.llm.usage = MagicMock(total=200)
        MockEngine.return_value = mock_engine

        args = parse_args(["-p", "do something", "-n", "--json"])
        exit_code = await run_headless(args)
        assert exit_code == 0

        import json
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["success"] is True
        assert result["tokens"] == 200


@pytest.mark.asyncio
async def test_headless_provider_passed_to_engine() -> None:
    """Provider and model args should be passed to Engine."""
    with patch("opentf.cli.headless.Engine") as MockEngine:
        mock_engine = MagicMock()
        mock_engine.run = AsyncMock(return_value=[
            {"success": True, "output": {"response": ""}, "errors": [], "token_usage": 0}
        ])
        mock_engine.close = AsyncMock()
        mock_engine.bus = MagicMock()
        mock_engine.bus.subscribe = MagicMock()
        mock_engine.llm = MagicMock()
        mock_engine.llm.usage = MagicMock(total=0)
        MockEngine.return_value = mock_engine

        args = parse_args(["-p", "test", "-n", "--provider", "openai", "--model", "gpt-4o"])
        await run_headless(args)

        MockEngine.assert_called_once_with(
            model="gpt-4o",
            provider_name="openai",
        )
