---
name: domain-modeling
description: Sharpen the project's domain language — challenge fuzzy terms, resolve overloaded words, record hard-to-reverse decisions as ADRs. Use when another skill needs the domain vocabulary, or when the user wants to improve naming/terminology.
enabled: true
---

# Domain Modeling

Actively build and maintain the project's domain model. Challenge terms against the glossary, stress-test with edge-case scenarios, and update `CONTEXT.md` and ADRs inline.

## Process

1. **Read the existing glossary.** Check `CONTEXT.md` at the repo root (or `CONTEXT-MAP.md` if it exists). If neither exists, create `CONTEXT.md` lazily.
2. **Identify fuzzy or overloaded terms.** Look for words doing double duty, inconsistent naming, or concepts without a clear term.
3. **Propose definitions.** Be opinionated — pick the best term, list alternatives under `_Avoid_`.
4. **Record hard-to-reverse decisions as ADRs.** See [ADR-FORMAT.md](ADR-FORMAT.md) for the template and when to offer an ADR.
5. **Update `CONTEXT.md`** with new or sharpened terms.

## When to use

- During `/grill-with-docs` sessions (the grilling drives domain modeling)
- During `/improve-codebase-architecture` (naming new modules)
- When the user asks to improve naming or resolve ambiguous terms
- When another skill needs domain vocabulary

## See also

- [CONTEXT-FORMAT.md](CONTEXT-FORMAT.md) — how to structure `CONTEXT.md`
- [ADR-FORMAT.md](ADR-FORMAT.md) — when and how to write ADRs
