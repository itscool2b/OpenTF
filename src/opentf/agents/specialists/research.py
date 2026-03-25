"""Research specialist agent.

Web-enabled research: combines Claude's knowledge with live web search
and page fetching via ToolLoop. Uses DuckDuckGo (no API key needed)
and httpx for async HTTP.
"""

from __future__ import annotations

import html
import json
import logging
import re
from typing import Any
from urllib.parse import quote_plus

import httpx

from opentf.agents.base import AgentResult, AgentRole, BaseAgent
from opentf.core.tool_loop import ToolLoop
from opentf.llm.client import LLMClient
from opentf.models.context import AgentContext

log = logging.getLogger(__name__)

MAX_PAGE_TEXT = 10_000  # max chars to extract from a page
SEARCH_TIMEOUT = 15  # seconds
FETCH_TIMEOUT = 15

# Tool definitions
TOOLS = [
    {
        "name": "web_search",
        "description": "Search the web for a query. Returns titles, URLs, and snippets.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "max_results": {"type": "integer", "description": "Max results (default 5)", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "read_url",
        "description": "Fetch a web page and extract its text content.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to fetch"},
            },
            "required": ["url"],
        },
    },
]

SYSTEM_PROMPT = """\
You are a Research agent with web access. Your job is to provide thorough,
well-sourced research on any topic.

You have two tools:
- web_search: Search the web for current information
- read_url: Read the full text of a web page

Guidelines:
- Use web_search when the topic needs current information or verification
- Use read_url to dive deeper into promising search results
- Cite your sources with URLs when using web information
- Be explicit about uncertainty -- say what you know vs what you found online
- Structure your findings with clear sections and headings
- Compare multiple perspectives when relevant
- State limitations of your analysis

Output your findings as well-structured markdown."""


def _strip_html(text: str) -> str:
    """Remove HTML tags and decode entities. Simple regex approach."""
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


async def _web_search(input_data: dict[str, Any]) -> str:
    """Search DuckDuckGo HTML for results."""
    query = input_data["query"]
    max_results = input_data.get("max_results", 5)

    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; OpenTF/0.1)"}

    try:
        async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            body = resp.text
    except Exception as exc:
        return f"Search failed: {exc}"

    # Parse results from DuckDuckGo HTML
    results = []
    # DuckDuckGo HTML results are in <a class="result__a"> tags
    links = re.findall(
        r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
        body, re.DOTALL,
    )
    snippets = re.findall(
        r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
        body, re.DOTALL,
    )

    for i, (href, title_html) in enumerate(links[:max_results]):
        title = _strip_html(title_html)
        snippet = _strip_html(snippets[i]) if i < len(snippets) else ""
        # DuckDuckGo wraps URLs in a redirect -- extract actual URL
        actual_url = href
        if "uddg=" in href:
            match = re.search(r"uddg=([^&]+)", href)
            if match:
                from urllib.parse import unquote
                actual_url = unquote(match.group(1))
        results.append({"title": title, "url": actual_url, "snippet": snippet})

    if not results:
        return f"No results found for: {query}"

    return json.dumps(results, indent=2)


async def _read_url(input_data: dict[str, Any]) -> str:
    """Fetch a URL and extract text content."""
    url = input_data["url"]

    try:
        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; OpenTF/0.1)"},
            )
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")

            if "application/json" in content_type:
                return resp.text[:MAX_PAGE_TEXT]

            text = _strip_html(resp.text)
            if len(text) > MAX_PAGE_TEXT:
                text = text[:MAX_PAGE_TEXT] + f"\n... (truncated, {len(resp.text)} total chars)"
            return text
    except Exception as exc:
        return f"Failed to fetch {url}: {exc}"


class ResearchAgent(BaseAgent):
    """Web-enabled research agent with search and page fetching."""

    def __init__(self, llm: LLMClient) -> None:
        super().__init__(
            name="research",
            description="Deep research and analysis with web search -- fact-checking, comparisons, topic exploration",
            capabilities=["research", "analyze", "summarize", "explain_topic",
                          "compare", "fact_check", "web_search"],
            role=AgentRole.SPECIALIST,
        )
        self.llm = llm

    async def process(self, context: AgentContext) -> AgentResult:
        handlers = {
            "web_search": _web_search,
            "read_url": _read_url,
        }

        bus = context.constraints.get("_bus")
        loop = ToolLoop(
            llm=self.llm,
            tools=TOOLS,
            handlers=handlers,
            max_iterations=10,
            bus=bus,
            source=self.name,
        )

        messages: list[dict] = []
        for msg in context.conversation_history[-5:]:
            messages.append(msg)

        # Include memory context if available
        task_content = context.task.description
        memory = context.constraints.get("memory_context", "")
        if memory:
            task_content += f"\n\nRelevant context from previous sessions:\n{memory}"

        messages.append({"role": "user", "content": task_content})

        text, tokens = await loop.run(
            messages=messages,
            system=SYSTEM_PROMPT,
            temperature=0.5,
        )

        return AgentResult(
            success=True,
            output={
                "response": text,
            },
            token_usage=tokens,
        )
