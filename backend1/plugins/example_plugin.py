"""
Example Plugin for Knowledge Base System

Define plugin metadata and tool functions.
Tool functions should be prefixed with 'tool_'.
"""

import ast
import math
import operator

PLUGIN_NAME = "example-plugin"
PLUGIN_VERSION = "0.1.0"
PLUGIN_DESCRIPTION = "An example plugin demonstrating the plugin system"


_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

_ALLOWED_FUNCS = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sum": sum,
    "int": int,
    "float": float,
    "sqrt": math.sqrt,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "pi": math.pi,
    "e": math.e,
}


class _SafeEvaluator:
    """AST 白名单求值器，只允许数字运算与受控数学函数，杜绝代码执行。"""

    def visit(self, node):
        if isinstance(node, ast.Expression):
            return self.visit(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return node.value
            raise ValueError(f"Unsupported constant: {type(node.value).__name__}")
        if isinstance(node, ast.BinOp):
            op = _BIN_OPS.get(type(node.op))
            if op is None:
                raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
            return op(self.visit(node.left), self.visit(node.right))
        if isinstance(node, ast.UnaryOp):
            op = _UNARY_OPS.get(type(node.op))
            if op is None:
                raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
            return op(self.visit(node.operand))
        if isinstance(node, ast.Name):
            if node.id in _ALLOWED_FUNCS and isinstance(_ALLOWED_FUNCS[node.id], (int, float)):
                return _ALLOWED_FUNCS[node.id]
            raise ValueError(f"Unknown name: {node.id}")
        if isinstance(node, ast.Call):
            func = _ALLOWED_FUNCS.get(node.func.id) if isinstance(node.func, ast.Name) else None
            if func is None:
                raise ValueError(f"Function not allowed: {node.func.id if isinstance(node.func, ast.Name) else '?'}")
            args = [self.visit(a) for a in node.args]
            if node.keywords:
                raise ValueError("Keyword arguments not allowed")
            return func(*args)
        raise ValueError(f"Unsupported syntax: {type(node).__name__}")


def tool_calculate(expression: str) -> str:
    """Evaluate a mathematical expression and return the result. Supports +-*/% **, parentheses and math functions (sqrt, log, sin...)."""
    try:
        tree = ast.parse(expression, mode="eval")
        result = _SafeEvaluator().visit(tree)
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
