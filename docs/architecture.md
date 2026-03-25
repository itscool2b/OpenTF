# OpenTF Architecture

## Overview

OpenTF (Open Task Force) is an autonomous multi-agent orchestration platform. Users type plain English, and a pipeline of AI agents decomposes, validates, refines, and executes the request.

## Five-Layer Architecture

```
Layer 1: CLI (Textual TUI)
    User input -> slash commands, output display, agent activity panel

Layer 2: Orchestrator + Prompt Architect
    Natural language -> structured tasks -> optimized prompts

Layer 3: Guardrail Agents
    Pre/post validation on every task (safety, quality, completeness)

Layer 4: Specialist Agents
    Code Agent (MVP), future: Research, File, Data agents

Layer 5: Infrastructure
    LLM client, message bus, memory store, credential manager
```

## Execution Pipeline

For each user request, the orchestrator runs:

```
User Input
    |
    v
Orchestrator (decompose into tasks via Claude)
    |
    v
For each task:
    |
    +-> Validator (pre-check: well-formed? safe? feasible?)
    |       |
    |       v [if rejected, return error]
    |
    +-> Prompt Architect (refine task -> optimized prompt)
    |
    +-> Specialist Agent (execute: Code Agent, etc.)
    |
    +-> Validator (post-check: complete? correct? safe?)
    |       |
    |       v [if failed, retry once with feedback]
    |
    v
Aggregated results -> displayed in CLI
```

## Data Models

### Task (`src/opentf/models/task.py`)

The core unit of work. Each task carries immutable state snapshots so previous state is always recoverable.

- `TaskStatus`: PENDING -> VALIDATED -> EXECUTING -> COMPLETED / FAILED
- `TaskSnapshot`: Immutable record of state at each transition (agent, timestamp, data, reason)
- `Task.transition()`: Records a snapshot before changing state
- `idempotency_key`: Prevents duplicate execution on retry

### Message (`src/opentf/models/message.py`)

Protocol-based inter-agent messages. Agents interpret these deterministically by type, not by parsing free-form text.

Types: TASK_CREATED, TASK_VALIDATED, TASK_REJECTED, TASK_EXECUTING, TASK_COMPLETED, TASK_FAILED, AGENT_REQUEST, AGENT_RESPONSE

### AgentContext (`src/opentf/models/context.py`)

Scoped context per agent execution. Each agent receives only what it needs -- task state, relevant conversation history, system prompt, available tools, and constraints. No centralized shared memory.

## Agent System

### BaseAgent (`src/opentf/agents/base.py`)

Abstract base class. Every agent implements:
- `process(context: AgentContext) -> AgentResult`: Execute the task
- `can_handle(task: Task) -> bool`: Whether this agent handles the task type

### AgentRegistry (`src/opentf/agents/registry.py`)

Dynamic registration system. Agents register at startup, and the orchestrator discovers them at runtime.
- `register(agent)`: Add an agent
- `find_for_task(task)`: Find the first agent that can handle a task
- `agent_descriptions()`: Used by the orchestrator's decomposition prompt

### ValidatorAgent (`src/opentf/agents/guardrails/validator.py`)

Guardrail agent with two modes:
- **Pre-execution**: Checks task is well-formed, has clear intent, no injection attempts. Includes fast pattern-matching for known injection keywords before calling the LLM.
- **Post-execution**: Checks output meets requirements, is complete, no data leakage.

### CodeAgent (`src/opentf/agents/specialists/code.py`)

Specialist for code generation, analysis, explanation, debugging, and refactoring. Returns structured output: `{code, language, explanation, files}`.

## Core Components

### Orchestrator (`src/opentf/core/orchestrator.py`)

The brain. Decomposes user requests into tasks via Claude, routes each task through the full pipeline (validate -> refine -> execute -> validate), and aggregates results.

### PromptArchitect (`src/opentf/core/prompt_architect.py`)

Transforms raw task descriptions into optimized prompts for specialist agents. Uses Claude for meta-prompting.

### MessageBus (`src/opentf/core/bus.py`)

In-memory async pub/sub event bus. Agents communicate through protocol-based messages. All messages are logged for audit trails. Swappable for Redis/NATS later.

### Engine (`src/opentf/core/engine.py`)

High-level API that wires everything together. Usable from the CLI or programmatically.

## Context Management

### Layer 1: Agent Handoffs (Structured Context Objects)

Each agent receives an `AgentContext` with only the data it needs. Prior agent work is "narrative cast" as structured context, not raw message dumps.

### Layer 2: Session Context (Server-Side Compaction)

Uses Claude API's built-in context compaction. Keeps recent turns intact, lets compaction handle older messages.

### Layer 3: Long-Term Memory (Hybrid Retrieval)

- `MemoryStore` (`src/opentf/memory/store.py`): ChromaDB + sentence-transformers (`all-MiniLM-L6-v2`, 22MB, local). Stores completed solutions, user preferences, error patterns.
- `HybridRetriever` (`src/opentf/memory/retriever.py`): Combines vector similarity with BM25 keyword search using reciprocal rank fusion.

## CLI UI

Built with Textual (Python TUI framework).

- **OutputDisplay**: Scrollable markdown output with syntax-highlighted code blocks
- **ActivityPanel**: Live agent status with animated spinners per agent
- **StatusBar**: Token usage, active agent count, task progress, elapsed time
- **PromptInput**: Input with command history (up/down arrows)
- **OnboardingScreen**: First-run API key setup

## Authentication

See `docs/authentication.md` for details.

## Dependencies

| Package | Purpose |
|---------|---------|
| anthropic | Claude API client |
| pydantic | Data models with validation |
| textual | Terminal UI framework |
| rich | Rich text rendering (used by textual) |
| pyyaml | Configuration files |
| chromadb | Vector database for long-term memory |
| sentence-transformers | Local embeddings (all-MiniLM-L6-v2) |
| rank-bm25 | BM25 keyword search |

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| Immutable task snapshots | Prevents context loss (learned from OpenClaw PR #52080) |
| Protocol-based messages | Free-form agent dialogue breaks at scale (research) |
| Scoped agent context | Centralized shared memory causes contamination |
| Idempotency keys on tasks | Safe retries without duplicate execution |
| Pluggable agent registry | OpenClaw had to refactor later -- we start pluggable |
| Hybrid retrieval (vector + BM25) | Better recall than vector-only search |
| Textual for CLI | Full TUI framework, not just print statements |
| curl install script | Matches Claude Code / OpenClaw conventions |
