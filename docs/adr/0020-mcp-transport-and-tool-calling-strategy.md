# ADR-0020: Streamable HTTP for MCP, no built-in tools, prompt-parsed emulation, and
# an in-stream (not persisted) tool loop

**Status:** accepted; the transport section is superseded by ADR-0022
**Date:** 2026-09-14

## Context
Phase 8 needed four decisions the brief left open on purpose (documented as ambiguous
or "decide and document"): which MCP HTTP transport to speak, whether to ship any
built-in (non-MCP) tools, how to give tool calling to models with no native support,
and how the tool-call loop interacts with the existing streaming/persistence
architecture (ADR-0004, ADR-0005, ADR-0006).

## Decision

### Transport: Streamable HTTP
The MCP spec's original "HTTP+SSE" transport (a `GET` opening a long-lived event
stream, requests over separate `POST`s) was superseded by "Streamable HTTP" in the
2025-03-26 revision, which every current MCP server implements. `mcp/http_sse.py`
speaks Streamable HTTP: one `POST` per JSON-RPC request/response, reading either a
plain JSON body or the first complete message off an SSE-framed response. It keeps the
module name `http_sse.py` from the design doc — the response framing is still SSE —
and does not implement the separate server-initiated notification stream (a standalone
`GET`) the full spec also defines, because nothing in velox-ui's tool-calling surface
(list tools, call a tool) needs an unsolicited push from the server.

### No built-in tools this phase
ADR-0014 names entry-point plugins as the mechanism for a built-in tool (calculator,
code execution, HTTP fetch). None is implemented in phase 8: the brief asks to "keep
this minimal", and every one of those candidates is itself a small design question
(a sandboxing story for code execution, an allow-list for HTTP fetch) that phase 8's
brief does not settle. `GET /api/tools` is therefore exactly the union of the caller's
enabled MCP servers' cached tools (`api/routes/tools.py`) — real, not a stub, just
scoped to MCP-sourced tools only. A built-in tool is a later addition through the
existing plugin entry-point group, not a phase-8 gap to work around.

### Emulated tool calling: prompt + best-effort text parse, not GBNF for the whole turn
`providers/tools/emulated.py` asks a model with no native tool support to answer with a
single `<tool_call>{"name": ..., "arguments": {...}}</tool_call>` block in its system
prompt, and parses that block out of the finished text after generation. This is
explicitly best-effort: nothing constrains the model to actually produce that shape,
and a model that ignores the instructions or emits malformed JSON simply does not call
a tool that turn (`parse_tool_call` returns `None`, not an error).

`providers/tools/gbnf.py` — a JSON-Schema-to-GBNF compiler for llama.cpp's
grammar-constrained decoding — is built and unit-tested, but **not** wired into the
live chat loop. The reason is a real correctness problem, not an oversight: a grammar
that constrains the *entire* turn's output to the tool-call JSON shape would make it
impossible for the model to ever answer in plain text, because GBNF has no way to
express "either this exact JSON shape, or arbitrary free text" without the free-text
branch being fully permissive and thereby erasing the constraint on the other branch
too. Solving that correctly needs a narrower move this phase's brief does not ask
for — e.g. a dedicated "does this turn want a tool?" sub-step, decided separately from
the answer itself — so `gbnf.py` stands as a real, tested building block for that
later work rather than a half-wired feature in the hot path today. This mirrors how
phase 7 treated `/api/websearch`: a deliberate, documented stub rather than a partial
implementation of an unbriefed shape.

### The tool-call loop lives entirely in the live stream; only the final message persists
`services/chat.py`'s `PreparedTurn.stream()` runs up to `MAX_TOOL_ITERATIONS` (4)
rounds: stream from the provider, detect a tool call (native `ToolCallDelta`s or the
emulated text parse), execute it through `McpManager` behind the approval gate, append
the assistant-tool-call and tool-result messages to the **in-memory** message list, and
re-invoke `provider.stream_chat` with the extended list — the same shape a
multi-turn OpenAI/Anthropic tool loop uses on the wire (`ChatMessage(role="tool",
tool_call_id=...)` for native; a plain `user` message stating the result for emulated
models, which were never taught the `tool` role exists).

None of the intermediate assistant/tool messages is written to the conversation tree
as its own `message` row. Only the turn's final assistant reply is persisted, same as
every turn before phase 8 — one user row, one assistant row (`PreparedTurn._insert_turn`,
unchanged since phase 2). The full trace (`{id, name, args, ok, content}` per call) is
attached to that one persisted row's `meta.tool_trace` instead, so re-opening a chat
can still re-render what happened, but a later turn's context is rebuilt purely from
persisted tree messages via `services/context.py:build_messages` — it does not replay
the tool-call loop. Making every intermediate tool-loop message a first-class branching
tree node (ADR-0006) would let a chat resume mid-tool-loop after a page reload, which
nothing in this phase's brief asks for, and would require the branching/regenerate
logic to understand a new kind of node; keeping the loop turn-local avoids that
redesign while still giving the SSE stream and the persisted trace everything the
brief's event contract (`tool_call`/`tool_result`) describes.

### Approval gate
`McpManager` holds pending approvals in an in-process dict, keyed by the same id the
`tool_call` SSE event already carries — not a new id — so `POST /api/tools/approve`
needs nothing beyond the id the client already has. This is the same "in-memory,
per-process, lost on restart" trade ADR-0017 and ADR-0019 already made for jobs, for
the same reason: an approval mid-flight is meaningless after a restart cancels the
request that was waiting on it anyway.

## Consequences
- A local model with `ToolSupport.NONE`/`EMULATED` gets tool calling that is honestly
  unreliable — no grammar enforcement, no retry on a malformed attempt. This is
  disclosed in `providers/tools/emulated.py`'s docstring and in the UI copy
  (`frontend/src/lib/i18n/en.json`), not hidden behind a confident-looking feature.
- `gbnf.py` is unused dead weight from the request-serving path's point of view today;
  it is kept because it is correct, tested, and the natural building block for the
  narrower "decide, then constrain" tool-selection step named above, not because
  phase 8 activates it.
- A chat reload shows the final answer and the tool trace, but cannot resume a
  turn that was mid-tool-loop when the client disconnected — the same as a plain
  generation being cut off mid-token before phase 8 (ADR-0005: an abandoned stream
  keeps what the model produced up to that point, not "resumes").
- `openai_compat.py`'s `capabilities()` now reports `tools=ToolSupport.NATIVE`
  unconditionally for every model a backend's `/models` lists, since the OpenAI
  protocol has no per-model "supports tools" field and most current
  OpenAI-compatible servers (and every OpenAI-protocol cloud preset) accept the
  `tools` request field whenever the loaded model supports it. A model that cannot
  use it simply never emits a `tool_calls` finish reason — the same graceful ignoring
  this protocol already has for unsupported sampling parameters.
