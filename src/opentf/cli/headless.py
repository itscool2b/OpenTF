"""Headless (non-interactive) runner for CI/CD and scripting.

Usage:
    opentf --prompt "fix the tests" --non-interactive
    opentf -p "refactor auth" -n --provider openai --model gpt-4o
    echo "add type hints" | opentf -n
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from opentf.core.engine import Engine
from opentf.llm.registry import get_default_model
from opentf.models.message import Message, MessageType

log = logging.getLogger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for headless mode."""
    parser = argparse.ArgumentParser(
        prog="opentf",
        description="OpenTF - autonomous AI coding agent",
    )
    parser.add_argument(
        "--prompt", "-p",
        type=str,
        default=None,
        help="Prompt to execute",
    )
    parser.add_argument(
        "--non-interactive", "-n",
        action="store_true",
        help="Run without TUI (headless mode)",
    )
    parser.add_argument(
        "--model", "-m",
        type=str,
        default=None,
        help="Model to use (e.g. sonnet, gpt-4o, llama3.1)",
    )
    parser.add_argument(
        "--provider",
        type=str,
        default="anthropic",
        help="LLM provider: anthropic, openai, ollama (default: anthropic)",
    )
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="Auto-approve all tool calls that require permission",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output result as JSON",
    )
    return parser.parse_args(argv)


async def run_headless(args: argparse.Namespace) -> int:
    """Run OpenTF in headless mode.

    Returns exit code: 0 = success, 1 = failure, 2 = usage error.
    """
    # Resolve prompt from --prompt or stdin
    prompt = args.prompt
    if not prompt:
        if sys.stdin.isatty():
            print(
                "Error: --prompt required in non-interactive mode, "
                "or pipe input via stdin",
                file=sys.stderr,
            )
            return 2
        prompt = sys.stdin.read().strip()
        if not prompt:
            print("Error: empty input", file=sys.stderr)
            return 2

    # Resolve model
    provider = args.provider
    model = args.model or get_default_model(provider)

    # Create engine
    engine = Engine(
        model=model,
        provider_name=provider,
    )

    # Set up auto-approval if requested
    if args.auto_approve:
        async def _auto_grant(msg: Message) -> None:
            if msg.type == MessageType.APPROVAL_REQUESTED:
                tool_id = msg.payload.get("tool_id", "")
                command = msg.payload.get("command", "")
                print(f"[auto-approved] {command}", file=sys.stderr)
                await engine.bus.publish(Message(
                    type=MessageType.APPROVAL_GRANTED,
                    source="headless",
                    payload={"tool_id": tool_id},
                ))

        engine.bus.subscribe(MessageType.APPROVAL_REQUESTED, _auto_grant)
    else:
        async def _auto_deny(msg: Message) -> None:
            if msg.type == MessageType.APPROVAL_REQUESTED:
                tool_id = msg.payload.get("tool_id", "")
                command = msg.payload.get("command", "")
                print(
                    f"[denied] {command} (use --auto-approve to allow)",
                    file=sys.stderr,
                )
                await engine.bus.publish(Message(
                    type=MessageType.APPROVAL_DENIED,
                    source="headless",
                    payload={"tool_id": tool_id},
                ))

        engine.bus.subscribe(MessageType.APPROVAL_REQUESTED, _auto_deny)

    # Run
    try:
        results = await engine.run(prompt)

        if args.json_output:  # noqa: SIM108
            output = {
                "success": all(r["success"] for r in results),
                "results": [
                    {
                        "success": r["success"],
                        "response": str(r["output"].get("response", ""))
                        if r["success"] else "",
                        "errors": r.get("errors", []),
                    }
                    for r in results
                ],
                "tokens": engine.llm.usage.total,
            }
            print(json.dumps(output, indent=2))
        else:
            for result in results:
                if result["success"]:
                    response = result["output"].get("response", "")
                    if response:
                        print(response)
                else:
                    for err in result.get("errors", []):
                        print(f"Error: {err}", file=sys.stderr)

        any_failure = any(not r["success"] for r in results)
        return 1 if any_failure else 0

    except Exception as exc:
        if args.json_output:
            print(json.dumps({"success": False, "error": str(exc)}))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        await engine.close()
