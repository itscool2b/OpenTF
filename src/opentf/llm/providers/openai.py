"""OpenAI provider backend.

Wraps the OpenAI SDK and converts between OpenAI's message/tool format
and OpenTF's internal (Anthropic-style) format at the provider boundary.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import Any, Callable, Awaitable

from opentf.llm.types import LLMResponse, TextBlock, ToolUseBlock, Usage

log = logging.getLogger(__name__)

try:
    import openai
    _HAS_OPENAI = True
except ImportError:
    _HAS_OPENAI = False


def _convert_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert Anthropic-style tool defs to OpenAI function-calling format."""
    result = []
    for tool in tools:
        schema = dict(tool.get("input_schema", {}))
        schema.pop("cache_control", None)
        func_def: dict[str, Any] = {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": schema,
            },
        }
        result.append(func_def)
    return result


def _convert_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert internal (Anthropic-style) messages to OpenAI format.

    Key differences:
    - Anthropic tool_use blocks in assistant content -> OpenAI tool_calls
    - Anthropic tool_result in user content -> OpenAI role=tool messages
    """
    result: list[dict[str, Any]] = []

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")

        # Simple text messages
        if isinstance(content, str):
            result.append({"role": role, "content": content})
            continue

        # List of content blocks (Anthropic format)
        if isinstance(content, list):
            # Check if this is an assistant message with tool_use blocks
            if role == "assistant":
                text_parts = []
                tool_calls = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        elif block.get("type") == "tool_use":
                            tool_calls.append({
                                "id": block.get("id", ""),
                                "type": "function",
                                "function": {
                                    "name": block.get("name", ""),
                                    "arguments": json.dumps(block.get("input", {})),
                                },
                            })

                msg_dict: dict[str, Any] = {"role": "assistant"}
                if text_parts:
                    msg_dict["content"] = "\n".join(text_parts)
                else:
                    msg_dict["content"] = None
                if tool_calls:
                    msg_dict["tool_calls"] = tool_calls
                result.append(msg_dict)

            # Check if this is a user message with tool_result blocks
            elif role == "user":
                text_parts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "tool_result":
                            result.append({
                                "role": "tool",
                                "tool_call_id": block.get("tool_use_id", ""),
                                "content": str(block.get("content", "")),
                            })
                        elif block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                # Add any text parts as a separate user message
                if text_parts:
                    result.append({"role": "user", "content": "\n".join(text_parts)})
            else:
                # Other roles with list content -- flatten to text
                texts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        texts.append(block.get("text", ""))
                    elif isinstance(block, str):
                        texts.append(block)
                result.append({"role": role, "content": "\n".join(texts)})
        else:
            result.append({"role": role, "content": str(content)})

    return result


class OpenAIProvider:
    """OpenAI API provider."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        if not _HAS_OPENAI:
            raise ImportError(
                "openai package not installed. Run: pip install openai"
            )
        self._api_key = api_key
        self._base_url = base_url
        self._client: Any = None  # openai.AsyncOpenAI

    @property
    def client(self) -> Any:
        if self._client is None:
            key = self._api_key
            if not key:
                import os
                key = os.environ.get("OPENAI_API_KEY", "")
            if not key:
                raise RuntimeError(
                    "No OpenAI API key found. Set OPENAI_API_KEY "
                    "or configure via 'opentf' onboarding."
                )
            kwargs: dict[str, Any] = {"api_key": key}
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._client = openai.AsyncOpenAI(**kwargs)
        return self._client

    def reset_client(self) -> None:
        self._client = None

    def _build_kwargs(
        self,
        messages: list[dict[str, Any]],
        system: str | list[dict[str, Any]],
        model: str,
        max_tokens: int,
        temperature: float,
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        """Build kwargs for chat.completions.create()."""
        # Prepend system message
        oai_messages = []
        if system:
            sys_text = system if isinstance(system, str) else " ".join(
                b.get("text", "") for b in system if isinstance(b, dict)
            )
            if sys_text:
                oai_messages.append({"role": "system", "content": sys_text})

        oai_messages.extend(_convert_messages(messages))

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": oai_messages,
        }

        if tools:
            kwargs["tools"] = _convert_tools(tools)

        return kwargs

    def _parse_response(self, response: Any) -> LLMResponse:
        """Convert an OpenAI response to LLMResponse."""
        choice = response.choices[0]
        message = choice.message
        blocks: list[TextBlock | ToolUseBlock] = []

        if message.content:
            blocks.append(TextBlock(text=message.content))

        if message.tool_calls:
            for tc in message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    args = {}
                blocks.append(ToolUseBlock(
                    id=tc.id,
                    name=tc.function.name,
                    input=args,
                ))

        usage = Usage(
            input_tokens=getattr(response.usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(response.usage, "completion_tokens", 0) or 0,
        )

        return LLMResponse(
            content=blocks,
            usage=usage,
            stop_reason=choice.finish_reason,
            model=response.model,
        )

    async def complete(
        self,
        messages: list[dict[str, Any]],
        system: str | list[dict[str, Any]] = "",
        model: str = "",
        max_tokens: int = 8192,
        temperature: float = 0.7,
        tools: list[dict[str, Any]] | None = None,
        cache: bool = False,
    ) -> LLMResponse:
        kwargs = self._build_kwargs(
            messages, system, model, max_tokens, temperature, tools,
        )

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await self.client.chat.completions.create(**kwargs)
                return self._parse_response(response)
            except (
                openai.RateLimitError,
                openai.InternalServerError,
                openai.APIConnectionError,
            ) as exc:
                last_error = exc
                delay = (2 ** attempt) + random.uniform(0, 1)
                log.warning(
                    "OpenAI request failed (attempt %d/3): %s. Retrying in %.1fs",
                    attempt + 1, exc, delay,
                )
                await asyncio.sleep(delay)

        raise last_error  # type: ignore[misc]

    async def stream(
        self,
        messages: list[dict[str, Any]],
        system: str | list[dict[str, Any]] = "",
        model: str = "",
        max_tokens: int = 8192,
        temperature: float = 0.7,
        tools: list[dict[str, Any]] | None = None,
        cache: bool = False,
        on_text: Callable[[str], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        if on_text is None:
            return await self.complete(
                messages, system=system, model=model, max_tokens=max_tokens,
                temperature=temperature, tools=tools, cache=cache,
            )

        kwargs = self._build_kwargs(
            messages, system, model, max_tokens, temperature, tools,
        )
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                collected_text = ""
                tool_calls_data: dict[int, dict[str, Any]] = {}
                usage_data = Usage()
                finish_reason = None
                model_name = model

                stream = await self.client.chat.completions.create(**kwargs)
                async for chunk in stream:
                    if chunk.choices:
                        delta = chunk.choices[0].delta
                        if delta and delta.content:
                            await on_text(delta.content)
                            collected_text += delta.content
                        if delta and delta.tool_calls:
                            for tc_delta in delta.tool_calls:
                                idx = tc_delta.index
                                if idx not in tool_calls_data:
                                    tool_calls_data[idx] = {
                                        "id": "", "name": "", "arguments": "",
                                    }
                                if tc_delta.id:
                                    tool_calls_data[idx]["id"] = tc_delta.id
                                if tc_delta.function:
                                    if tc_delta.function.name:
                                        tool_calls_data[idx]["name"] = tc_delta.function.name
                                    if tc_delta.function.arguments:
                                        tool_calls_data[idx]["arguments"] += tc_delta.function.arguments
                        if chunk.choices[0].finish_reason:
                            finish_reason = chunk.choices[0].finish_reason
                    if chunk.usage:
                        usage_data = Usage(
                            input_tokens=chunk.usage.prompt_tokens or 0,
                            output_tokens=chunk.usage.completion_tokens or 0,
                        )
                    if hasattr(chunk, "model") and chunk.model:
                        model_name = chunk.model

                blocks: list[TextBlock | ToolUseBlock] = []
                if collected_text:
                    blocks.append(TextBlock(text=collected_text))
                for idx in sorted(tool_calls_data.keys()):
                    tc = tool_calls_data[idx]
                    try:
                        args = json.loads(tc["arguments"])
                    except (json.JSONDecodeError, TypeError):
                        args = {}
                    blocks.append(ToolUseBlock(
                        id=tc["id"],
                        name=tc["name"],
                        input=args,
                    ))

                return LLMResponse(
                    content=blocks,
                    usage=usage_data,
                    stop_reason=finish_reason,
                    model=model_name,
                )
            except (
                openai.RateLimitError,
                openai.InternalServerError,
                openai.APIConnectionError,
            ) as exc:
                last_error = exc
                delay = (2 ** attempt) + random.uniform(0, 1)
                log.warning(
                    "OpenAI stream failed (attempt %d/3): %s. Retrying in %.1fs",
                    attempt + 1, exc, delay,
                )
                await asyncio.sleep(delay)

        raise last_error  # type: ignore[misc]
