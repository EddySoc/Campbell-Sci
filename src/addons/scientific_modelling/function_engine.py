from __future__ import annotations

import ast
from typing import Any

import numpy as np

from i18n import t

FUNCTIONS = {
    "sin": np.sin, "cos": np.cos, "tan": np.tan, "arctan": np.arctan, "tanh": np.tanh,
    "exp": np.exp, "log": np.log, "log10": np.log10, "sqrt": np.sqrt, "abs": np.abs,
    "minimum": np.minimum, "maximum": np.maximum, "where": np.where, "clip": np.clip,
    "power": np.power,
}
CONSTANTS = {"pi": np.pi, "e": np.e}
_BINARY = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod, ast.FloorDiv)
_UNARY = (ast.USub, ast.UAdd)
_COMPARE = (ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq)


def parse_expression(expression: str) -> ast.Expression:
    """Controleert dat de formule alleen rekenkunde, getallen, namen en whitelist-functies bevat."""
    text = expression.strip()
    if "=" in text.replace("==", "").replace("<=", "").replace(">=", "").replace("!=", ""):
        text = text.split("=", 1)[1].strip()
    if not text:
        raise ValueError(t("addon.modelling.empty_formula"))
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise ValueError(t("addon.modelling.invalid_formula", msg=exc.msg)) from exc
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS or node.keywords:
                raise ValueError(t("addon.modelling.function_not_allowed_in_formula"))
        elif isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise ValueError(t("addon.modelling.only_numbers_are_allowed"))
        elif isinstance(node, ast.BinOp):
            if not isinstance(node.op, _BINARY):
                raise ValueError(t("addon.modelling.operator_not_allowed"))
        elif isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, _UNARY):
                raise ValueError(t("addon.modelling.operator_not_allowed"))
        elif isinstance(node, ast.Compare):
            if not all(isinstance(op, _COMPARE) for op in node.ops):
                raise ValueError(t("addon.modelling.operator_not_allowed"))
        elif not isinstance(
            node,
            (ast.Expression, ast.Name, ast.Load, ast.operator, ast.unaryop, ast.cmpop),
        ):
            raise ValueError(t("addon.modelling.unsupported_syntax_in_formula"))
    return tree


def free_names(expression: str) -> list[str]:
    """Namen in de formule die geen functie of constante zijn."""
    tree = parse_expression(expression)
    called = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call)}
    names = {
        node.id for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id not in called and node.id not in CONSTANTS
    }
    return sorted(names)


def evaluate(expression: str, variables: dict[str, Any]) -> Any:
    tree = parse_expression(expression)
    namespace = {**FUNCTIONS, **CONSTANTS, **variables}
    with np.errstate(all="ignore"):
        return eval(compile(tree, "<model>", "eval"), {"__builtins__": {}}, namespace)
