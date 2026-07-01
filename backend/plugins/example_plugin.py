"""
Example Plugin for Knowledge Base System

Define plugin metadata and tool functions.
Tool functions should be prefixed with 'tool_'.
"""

PLUGIN_NAME = "example-plugin"
PLUGIN_VERSION = "0.1.0"
PLUGIN_DESCRIPTION = "An example plugin demonstrating the plugin system"


def tool_calculate(expression: str) -> str:
    """Evaluate a mathematical expression and return the result."""
    try:
        result = eval(expression, {"__builtins__": {}}, {})
        return f"Result: {result}"
    except Exception as e:
        return f"Error: {str(e)}"


def tool_get_current_time(format: str = "%Y-%m-%d %H:%M:%S") -> str:
    """Get the current date and time in the specified format."""
    from datetime import datetime
    return datetime.now().strftime(format)


def tool_hello(name: str = "World") -> str:
    """Return a greeting message."""
    return f"Hello, {name}!"
