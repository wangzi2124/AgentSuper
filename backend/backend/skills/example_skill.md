---
name: example-skill
description: An example skill that demonstrates how to define agent capabilities
enabled: false
---
# Example Skill

This is an example skill that demonstrates the skill.md format.

## Usage

This skill can be loaded by the AI agent to provide specialized knowledge.

## Commands

- `use_skill()` - Load this skill's content into the agent context
- `get_capabilities()` - List the capabilities provided by this skill

## Details

Skills are defined in Markdown files with YAML front matter.
They are loaded dynamically by the SkillLoader and exposed as tools to the AI agent.
