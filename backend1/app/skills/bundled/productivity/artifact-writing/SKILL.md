---
name: artifact-writing
description: Load BEFORE any file/artifact generation task — writing code, documents, reports, scripts or projects, or any multi-step task that creates/modifies files. Contains the mandatory output rules: keep long content in files (not pasted in replies), batch all file writes into one turn to avoid timeouts, and the required plan ('## 实施计划') / completion ('## 完成情况') report format.
enabled: true
---

# File & artifact output rules

Applies **only** to file/artifact tasks — creating or modifying files, code, documents, or any multi-step work that produces files. For plain Q&A (explaining, summarizing, chat, self-introduction), skip everything below and just answer directly.

## 1. Long content goes to files, never inline

- If a reply would exceed roughly 500 Chinese characters (~1000 tokens), write the full content to a file instead of pasting it.
- Text/code → `tool_write_file` for the first chunk, then `tool_append_file` for the rest. Structured documents (`.docx` / `.pdf` / `.xlsx` / `.pptx`) → the matching generator plugin.
- In your reply, output **only**: the saved file path + a summary of the key points + a structure overview. Do **not** paste the full content.
- Do **not** add closing "please verify" remarks (e.g. `文档已生成完毕，请验证文件完整性`) — state the path and summary, then stop.
- Exception: only when the user explicitly asks for the full text inline.

## 2. Batch writes — fewest rounds

Each extra assistant turn is another slow-model round-trip that risks a timeout. Create or update **all** files a task needs in as few turns as possible:

- Put every `tool_write_file` / `tool_append_file` call for the whole project in **one** response (multiple parallel tool calls); **or**
- issue a **single** `tool_apply_patch` whose `patch_text` contains multiple `*** Add File:` blocks.

Do **not** write one file, wait for the result, then write the next. Do **not** scaffold projects via `mkdir` / `Set-Content` / `npm create` / `create-react-app` / `create vite` — `tool_write_file` auto-creates parent dirs, so write all source files first, then run `tool_execute` only for install/build/test.

## 3. Plan & completion report (multi-step file/project tasks)

- Start with a plan block `## 实施计划`.
- End with `## 完成情况` (已完成 / 未完成 / 下一步).
- At the step limit, produce this report **without** calling more tools.
- For plain Q&A, skip the plan/report format entirely.
