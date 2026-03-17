# Agent Capability Audit: Memory, User Understanding, and Reasoning Depth

## Scope
This audit evaluates whether current architecture limitations are primarily caused by model choice or by orchestration/memory design. It focuses on:
1. Context memory durability and retrieval quality.
2. User understanding quality across multi-turn tasks.
3. Decision quality vs token-spend optimization tradeoffs.
4. Framework/package options to close the gap to a custom “smart agent.”

## Executive conclusion
The biggest limiter is **agent architecture**, not just the base model. The current implementation is intentionally lightweight and demo-oriented, with shallow state, minimal retrieval, and limited planning loops. Upgrading model tier can improve outputs, but the current orchestration will still bottleneck quality in memory, continuity, and intuitive task handling.

## Current-state findings

### 1) Memory is session-local and shallow
- Conversation state is a compact `ConversationContext` object containing only `intent`, `action_type`, `collected_fields`, and `missing_fields`.
- The frontend sends this context back each turn, so continuity depends on client state rather than durable server memory.
- There is no long-term episodic memory (thread summaries, user habits timeline, semantic memory retrieval).

**Impact**: The assistant loses nuanced user preferences and historical grounding, so interactions feel brittle and repetitive.

### 2) Intent understanding is partly heuristic and brittle
- Request routing uses phrase-matching shortcuts for read intents and a keyword parser fallback.
- The deterministic fallback parser performs first-match keyword detection and simple risk heuristics.

**Impact**: User understanding degrades on indirect phrasing, mixed intents, or implicit requests.

### 3) Prompting strategy is optimized for low cost over deep reasoning
- “Mini” model is used for conversational turn and triage; heavier model is only used for selected draft/briefing tasks.
- Single-pass JSON extraction is used, without guardrail retries, self-critique, tool planning loops, or schema-constrained decoding.

**Impact**: Fast and cheap, but weaker robustness and “intuition” compared to stronger agent stacks.

### 4) Data grounding exists, but only in narrow pathways
- Calendar/email integrations can ground read operations and email drafts.
- However, there is no generalized retrieval layer (vector store + recency + semantic rank), no user memory graph, and no multi-source planner.

**Impact**: The system can appear capable in happy paths yet fail in broader context-aware decision making.

### 5) Infrastructure indicates demo maturity stage
- In-memory stores for clients/actions/approvals/integrations are resettable and non-durable.
- Dependencies do not include any dedicated agent framework, workflow engine, vector DB client, or memory package.

**Impact**: Appropriate for prototyping, but below the architecture typically seen in production “smart custom agents.”

## Model vs framework diagnosis

### What model upgrades alone would fix
- Better instruction following and richer language quality.
- Better extraction under ambiguous input.
- Improved draft quality and fewer awkward responses.

### What model upgrades alone will not fix
- Missing long-term memory and recall.
- Lack of task decomposition/planning across multiple tools.
- Inconsistent behavior over long multi-turn sessions.
- Weak personalization and continuity.

## Recommended package/framework options

### Option A: LangGraph (+ LangChain memory/retrieval components)
Best when you want explicit state machines, durable checkpoints, and recoverable multi-step flows.

- Pros: strong control over multi-turn state, tool routing, retries, and approval gates.
- Fit: excellent for your approval-first workflow and branchy logic.
- Add-ons: Postgres checkpointing + vector retrieval + structured output guards.

### Option B: PydanticAI
Best when type-safe structured outputs and robust schema handling are the priority.

- Pros: tight Python ergonomics, typed tools, model abstraction.
- Fit: strong replacement for ad hoc JSON parsing and brittle extraction.
- Add-ons: pair with LangGraph-like orchestration or Temporal for long-running flows.

### Option C: LlamaIndex Agent + Memory + RAG pipeline
Best when retrieval quality and document/tool grounding are central.

- Pros: mature retrieval patterns, memory modules, ingestion/query pipelines.
- Fit: ideal if user-specific context corpus will grow (emails, notes, CRM records).

### Option D: Semantic Kernel
Best for enterprise governance and plugin patterns (especially Microsoft-heavy stacks).

- Pros: planner abstractions, skill/plugin ecosystem, strong enterprise posture.
- Fit: useful if governance/compliance and connector ecosystem are primary concerns.

### Option E: Lightweight custom orchestrator + targeted libraries
Best when minimizing framework lock-in.

Suggested stack:
- Workflow/state: custom FSM or Temporal.
- Memory: Mem0 or custom episodic memory service (Postgres + embeddings).
- Structured outputs: instructor / pydantic-based validators with retry.
- Evaluation: promptfoo + regression suites.

## Practical architecture upgrades (highest ROI)
1. **Durable conversation and memory store**
   - Persist turns, decisions, and extracted preferences server-side.
   - Add semantic retrieval over historical user interactions.
2. **Two-layer memory strategy**
   - Working memory (active task state) + long-term memory (summaries/preferences/facts).
3. **Planner/executor split**
   - Plan tool calls first, then execute; include confidence and fallback routes.
4. **Schema-first generation with auto-repair**
   - Enforce strict typed outputs, retry on schema failure, and validate entities.
5. **Evaluation harness**
   - Build scenario tests for continuity, personalization, and ambiguous requests.
6. **Selective model upgrade**
   - Keep mini for low-risk extraction, use stronger model for planning and ambiguity resolution.

## Priority implementation roadmap

### Phase 1 (1-2 weeks): reliability baseline
- Introduce durable state store (Postgres) for conversation + preferences.
- Replace regex/keyword fallback with typed extraction + repair loop.
- Add regression tests for multi-turn context retention.

### Phase 2 (2-4 weeks): smart-agent behavior
- Add memory retrieval layer (semantic + recency hybrid).
- Add planner/executor workflow with explicit tool-use steps.
- Add user profile synthesis (communication style, scheduling patterns).

### Phase 3 (4-8 weeks): production-grade intelligence
- Add continuous eval dashboards (quality + cost + latency).
- Add offline replay benchmarking against historical transcripts.
- Tune routing policy (small vs large model) by uncertainty score.

## Bottom line
Your observed gap is real and expected for this architecture stage. The present codebase is a solid controlled demo, but not yet structured like a high-context adaptive agent. The fastest path forward is **memory + orchestration upgrades first**, then targeted model/routing improvements.
