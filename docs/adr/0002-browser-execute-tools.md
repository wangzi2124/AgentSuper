# ADR 0002: Browser / arbitrary-execute tools

Status: deferred (2026-11-08)

## Context

opencode exposes `browser` and `execute`-style tools. AgentSuper already has
`shell_exec`, `http_request` and a sandboxed workspace VFS. Adding a browser
driver or arbitrary code executor widens the security boundary far beyond the
current approval model (turn-scoped approvals, no container isolation).

## Decision

- Do not implement `browser` or generic `execute` in this phase.
- Do not fake them with `shell_exec` aliases.
- Revisit only with a dedicated sandbox design (separate process/container,
  network policy, artifact capture, audit trail).

## Consequences

- `apply_patch` (unified diff parser, no shell) and `question` remain the only
  G-15 additions at this time.
- Session/worktree tools require a git worktree design and are deferred with
  the same rationale.
