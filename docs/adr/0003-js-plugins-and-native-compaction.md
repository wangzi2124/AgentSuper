# ADR 0003: JS/TS plugins and native/background compaction

Status: accepted (2026-11-08)

## JS/TS plugins

Python plugins are in-process and fully trusted. Loading `.js`/`.ts` would
require a Node worker with a stable host API, IPC serialization, timeout and
sandbox rules. Until that exists, JS/TS plugins are explicitly unsupported;
Python plugins expose manifest/options/hooks/unload only. No Python shim loads
JavaScript.

## Compaction

`harness/compression.py` + `harness/compression_agent.py` remains the single
canonical path. Provider-native compaction and background compaction are
optional experiments:

- They must call the same `ContextCompactor`/compression agent.
- They must not introduce a third summarizer.
- Background compaction must persist state through the same
  `session_messages(type=compaction)` + epoch mechanism before it can be enabled.

They stay disabled by default and are not exposed in config until those
invariants have tests.
