"""Slash command registry package (opencode gap G-02)."""

from .registry import CommandDef, CommandRegistry, command_dirs, load_commands, parse_command_file

__all__ = [
    "CommandDef",
    "CommandRegistry",
    "command_dirs",
    "load_commands",
    "parse_command_file",
]
