# Model Runtime owns endpoint resolution and local model lifecycle

We created a deep `ModelRuntime` module in `backend/models/runtime.py`. It owns
endpoint/credential resolution, context-window selection, provider options
(`num_ctx`, `think`), and local Ollama warmth (`keep_alive` and preload). The
existing `TierModel` stays as a facade so callers and tests keep the same
interface.

Resolution is cached in a Turn Scope owned by the models layer; background
callers without a scope resolve once and do not cache. `ModelCatalog` remains
declarative — it stores catalog entries, providers, overrides, capabilities, and
limits, but no runtime window or warmth state. `/api/ps` refresh runs in a
background thread so model calls never block on a network probe.

Local models default to `keep_alive="30m"` and auto-expanded `num_ctx` values
stick per model, trading memory for avoiding repeated loads. Both are
configurable. The rejected alternatives were keeping provider knowledge in
`TierModel` (no locality), a process-wide cache (stale config after model
management writes), and synchronous `/api/ps` probing (event-loop stalls).
