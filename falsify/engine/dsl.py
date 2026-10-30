"""A tiny, safe expression language for entry/exit conditions.

Examples::

    rsi(14) < 30
    close > sma(200) and cross_above(sma(20), sma(50))
    streak_down() >= 3 and vol_ratio(20) > 1.5
    close > shift(high_n(20), 1)          # breakout above the previous 20-bar high
    dow() == 0                            # Monday

Grammar (Python expression syntax, whitelisted):
  comparisons  < <= > >= == !=      boolean and / or / not
  arithmetic   + - * /              numbers, columns, indicator calls
  helpers      shift(x, n)  cross_above(a, b)  cross_below(a, b)  abs(x)  pct_diff(a, b)
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import indicators as I


class DSLError(ValueError):
    pass


_ALLOWED_BIN = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b}
_ALLOWED_CMP = {
    ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b,
    ast.Gt: lambda a, b: a > b,
    ast.GtE: lambda a, b: a >= b,
    ast.Eq: lambda a, b: a == b,
    ast.NotEq: lambda a, b: a != b,
}


@dataclass
class Compiled:
    expression: str
    used_indicators: list[str] = field(default_factory=list)
    constants: list[float] = field(default_factory=list)

    def evaluate(self, df: pd.DataFrame) -> pd.Series:
        ev = _Evaluator(df)
        tree = ast.parse(self.expression, mode="eval")
        out = ev.visit(tree.body)
        if isinstance(out, (bool, np.bool_, int, float)):
            out = pd.Series(bool(out), index=df.index)
        if out.dtype != bool:
            raise DSLError("Condition must be true/false per bar, got %s" % out.dtype)
        return out.fillna(False).astype(bool)


def _as_series(x, index):
    if isinstance(x, pd.Series):
        return x
    return pd.Series(float(x), index=index)


def _shift(x, n, index):
    return _as_series(x, index).shift(int(n))


def _cross_above(a, b, index):
    a, b = _as_series(a, index), _as_series(b, index)
    return (a > b) & (a.shift(1) <= b.shift(1))


def _cross_below(a, b, index):
    a, b = _as_series(a, index), _as_series(b, index)
    return (a < b) & (a.shift(1) >= b.shift(1))


def _pct_diff(a, b, index):
    a, b = _as_series(a, index), _as_series(b, index)
    return (a / b.replace(0.0, np.nan) - 1.0) * 100.0


HELPERS = {
    "shift": (_shift, 2, "shift(x, n): value of x n bars ago"),
    "cross_above": (_cross_above, 2, "cross_above(a, b): a crossed above b on this bar"),
    "cross_below": (_cross_below, 2, "cross_below(a, b): a crossed below b on this bar"),
    "pct_diff": (_pct_diff, 2, "pct_diff(a, b): (a/b - 1) * 100"),
    "abs": (lambda x, index: _as_series(x, index).abs(), 1, "abs(x)"),
}


class _Evaluator(ast.NodeVisitor):
    def __init__(self, df: pd.DataFrame):
        self.df = df
        self.index = df.index
        self._cache: dict[str, pd.Series] = {}

    def generic_visit(self, node):
        raise DSLError("Unsupported syntax: %s" % type(node).__name__)

    def visit_Constant(self, node):
        if isinstance(node.value, bool):
            return node.value
        if isinstance(node.value, (int, float)):
            return float(node.value)
        raise DSLError("Only numbers are allowed as constants, got %r" % (node.value,))

    def visit_UnaryOp(self, node):
        v = self.visit(node.operand)
        if isinstance(node.op, ast.Not):
            return ~_as_bool(v, self.index)
        if isinstance(node.op, ast.USub):
            return -v
        if isinstance(node.op, ast.UAdd):
            return v
        raise DSLError("Unsupported unary operator")

    def visit_BoolOp(self, node):
        vals = [_as_bool(self.visit(v), self.index) for v in node.values]
        out = vals[0]
        for v in vals[1:]:
            out = (out & v) if isinstance(node.op, ast.And) else (out | v)
        return out

    def visit_BinOp(self, node):
        fn = _ALLOWED_BIN.get(type(node.op))
        if fn is None:
            raise DSLError("Unsupported operator %s" % type(node.op).__name__)
        return fn(self.visit(node.left), self.visit(node.right))

    def visit_Compare(self, node):
        left = self.visit(node.left)
        out = None
        for op, comp in zip(node.ops, node.comparators):
            fn = _ALLOWED_CMP.get(type(op))
            if fn is None:
                raise DSLError("Unsupported comparison %s" % type(op).__name__)
            right = self.visit(comp)
            res = fn(left, right)
            res = _as_bool(res, self.index)
            out = res if out is None else (out & res)
            left = right
        return out

    def visit_Name(self, node):
        name = node.id
        if name in I.COLUMNS:
            return self.df[name].astype(float)
        if name in ("True", "true"):
            return True
        if name in ("False", "false"):
            return False
        if name in I.CATALOGUE:
            return self._indicator(name, [])
        raise DSLError("Unknown name %r. Columns: %s. Indicators: %s" % (name, ", ".join(I.COLUMNS), ", ".join(sorted(I.CATALOGUE))))

    def visit_Call(self, node):
        if not isinstance(node.func, ast.Name):
            raise DSLError("Only simple function calls are allowed")
        name = node.func.id
        if node.keywords:
            raise DSLError("Keyword arguments are not supported in %s(...)" % name)
        if name in HELPERS:
            fn, arity, _ = HELPERS[name]
            if len(node.args) != arity:
                raise DSLError("%s expects %d argument(s)" % (name, arity))
            args = [self.visit(a) for a in node.args]
            return fn(*args, index=self.index)
        if name in I.CATALOGUE:
            args = []
            for a in node.args:
                v = self.visit(a)
                if isinstance(v, pd.Series):
                    raise DSLError("Indicator parameters must be plain numbers in %s(...)" % name)
                args.append(v)
            return self._indicator(name, args)
        raise DSLError("Unknown function %r. Indicators: %s. Helpers: %s" % (name, ", ".join(sorted(I.CATALOGUE)), ", ".join(HELPERS)))

    def _indicator(self, name: str, args: list) -> pd.Series:
        d = I.CATALOGUE[name]
        if len(args) > len(d.params):
            raise DSLError("%s takes at most %d parameter(s): %s" % (name, len(d.params), ", ".join(d.params)))
        full = list(args) + list(d.defaults[len(args):])
        for p, v in zip(d.params, full):
            if p in ("n", "fast", "slow", "sig") and (v < 1 or v > 2000 or float(v) != int(v)):
                raise DSLError("%s(%s=%r): must be a whole number between 1 and 2000" % (name, p, v))
        key = name + "(" + ",".join(str(v) for v in full) + ")"
        if key not in self._cache:
            self._cache[key] = d.fn(self.df, *full)
        return self._cache[key]


def _as_bool(v, index):
    if isinstance(v, (bool, np.bool_)):
        return pd.Series(bool(v), index=index)
    if isinstance(v, pd.Series):
        if v.dtype != bool:
            # numeric series in boolean context is ambiguous
            raise DSLError("Expected a true/false condition but got numbers; compare it with a value (e.g. rsi(14) < 30)")
        return v.fillna(False).astype(bool)
    raise DSLError("Expected a condition, got %r" % (v,))


class _Collector(ast.NodeVisitor):
    def __init__(self):
        self.indicators: list[str] = []
        self.constants: list[float] = []

    def visit_Call(self, node):
        if isinstance(node.func, ast.Name) and node.func.id in I.CATALOGUE:
            args = []
            for a in node.args:
                if isinstance(a, ast.Constant) and isinstance(a.value, (int, float)):
                    args.append(str(a.value))
            self.indicators.append(node.func.id + "(" + ",".join(args) + ")")
        for a in node.args:
            self.visit(a)
        if not (isinstance(node.func, ast.Name) and node.func.id in I.CATALOGUE):
            self.visit(node.func)

    def visit_Name(self, node):
        if node.id in I.CATALOGUE:
            self.indicators.append(node.id + "()")

    def visit_Constant(self, node):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            self.constants.append(float(node.value))


def compile_expression(expr: str) -> Compiled:
    """Parse and validate an expression. Raises DSLError with a readable message."""
    if not isinstance(expr, str) or not expr.strip():
        raise DSLError("Empty condition")
    if len(expr) > 2000:
        raise DSLError("Condition is too long")
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError as e:
        raise DSLError("Syntax error in condition: %s" % e.msg) from None
    for node in ast.walk(tree):
        if isinstance(node, (ast.Attribute, ast.Subscript, ast.Lambda, ast.Dict, ast.List, ast.Tuple, ast.Set, ast.comprehension, ast.IfExp, ast.Starred, ast.JoinedStr, ast.Await, ast.Yield)):
            raise DSLError("Unsupported syntax: %s" % type(node).__name__)
    col = _Collector()
    col.visit(tree)
    # dry run on a tiny synthetic frame to catch type/arity errors early
    probe = _probe_frame()
    try:
        res = _Evaluator(probe).visit(tree.body)
        _as_bool(res, probe.index)
    except DSLError:
        raise
    except Exception as e:  # pragma: no cover - defensive
        raise DSLError("Condition failed to evaluate: %s" % e) from None
    return Compiled(expr.strip(), sorted(set(col.indicators)), col.constants)


def _probe_frame(n: int = 400) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    ts = pd.date_range("2020-01-01", periods=n, freq="D", tz="UTC")
    return pd.DataFrame({
        "ts": ts,
        "open": close + rng.normal(0, 0.5, n),
        "high": close + abs(rng.normal(0, 1, n)) + 0.5,
        "low": close - abs(rng.normal(0, 1, n)) - 0.5,
        "close": close,
        "volume": abs(rng.normal(1000, 100, n)),
    })


def grammar_text() -> str:
    helpers = "\n".join("- %s" % h[2] for h in HELPERS.values())
    return (
        "Columns: open, high, low, close, volume (current bar).\n"
        "Indicators (defaults in parentheses):\n" + I.catalogue_text() + "\n"
        "Helpers:\n" + helpers + "\n"
        "Operators: < <= > >= == !=, and / or / not, + - * /. Only numbers as constants. "
        "No forward-looking values exist: the condition is evaluated at the close of a bar and the trade opens at the next bar's open."
    )
