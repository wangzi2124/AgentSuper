# AgentSuper Context

AgentSuper is a local multi-agent chat platform. Its model pipeline turns a user
request into one or more provider calls, tool rounds, and a final answer. This
glossary names the concepts that shape that pipeline; use these terms in code,
tests, and architecture discussions.

## Language

**Model Runtime**:
The resolved execution context for one model in one turn: endpoint, credentials,
context window, output limit, provider options, and local-model warmth. Callers
use `complete`, `stream`, and `warm`; resolution details stay inside the module.
_Avoid_: model client, LLM service, provider wrapper

**Turn Scope**:
The lifetime of one user request. Model Runtime snapshots are cached for the
duration of a Turn Scope and discarded when it ends.
_Avoid_: request context, session cache

**Tool Part**:
The durable record of one tool call in a turn: lifecycle state (`pending`,
`running`, `completed`, `failed`), effective arguments, result, and target. The
tool loop, event projection, persistence, and tests share the same part.
_Avoid_: tool event, tool trace, tool step

**Tool Part Projection**:
The canonical `agent_step` shape derived from a Tool Part. Live SSE and persisted
replay are two adapters over this one shape; the frontend upserts by `part_id`
and does not re-derive lifecycle or effective arguments.
_Avoid_: per-adapter step lifecycle, effective-args repair

**Model Catalog**:
Declarative data about models and providers: capabilities, context limits,
pricing, and user overrides. It holds no runtime state.
_Avoid_: model registry, model store

**Model Tier**:
A role-based routing alias (`simple`, `medium`, `complex`, `reasoning`, `auto`)
used when no explicit model is selected.
_Avoid_: model level, model class

**Local Model Warmth**:
Whether a local Ollama model is resident and running with the `num_ctx` the turn
expects. A cold or reloaded model adds load latency before the first token.
_Avoid_: preload state, cache state
