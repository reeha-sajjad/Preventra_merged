"""
Static checks on LLM-written pipeline code, before it is run.

One layer of several, not the whole defence. The runner also executes in a
separate process with the API's secrets stripped from its environment and with
time and memory limits (api/model_studio.py). What this layer adds is that the
obvious ways out - a network library, a shell, reading a file, reaching the
interpreter's internals through dunder attributes - are refused before a
single line executes, and the refusal is specific enough to hand back to the
model as a correction.

The pipeline contract needs none of these: the runner does all file I/O, and
preprocess/transform/train receive and return in-memory data only.
"""

from __future__ import annotations

import ast

ALLOWED_MODULES = {
    "pandas", "numpy", "sklearn", "scipy", "math", "re", "datetime", "warnings",
    "collections", "itertools", "functools", "typing", "random", "statistics",
    "dataclasses", "numbers", "string",
}
# Allowed packages whose submodules still reach the network or the filesystem.
BLOCKED_SUBMODULES = ("sklearn.datasets", "scipy.io", "numpy.lib.npyio", "pandas.io")

BLOCKED_NAMES = {
    "open", "eval", "exec", "compile", "__import__", "input", "breakpoint",
    "globals", "locals", "vars", "getattr", "setattr", "delattr", "help",
    "memoryview", "__builtins__", "__loader__", "__spec__",
}
# Reading or writing files through the data libraries.
BLOCKED_ATTRIBUTE_PREFIXES = ("read_", "to_csv", "to_pickle", "to_parquet", "to_json",
                              "to_excel", "to_sql", "to_hdf", "to_feather", "to_stata",
                              "fetch_", "load", "save", "fromfile", "tofile", "genfromtxt")
ALLOWED_DUNDERS = {"__init__", "__name__"}
REQUIRED_FUNCTIONS = ("preprocess", "transform", "train")
# Reading a later row of the same patient. The runner's look-ahead check is the
# real guarantee; these catch the obvious forms early, with a clear message.
LOOKAHEAD_METHODS = {"bfill", "backfill"}
PERIOD_METHODS = {"shift", "diff", "pct_change"}


def check_code(code: str) -> list:
    """Every problem found, as sentences. An empty list means the code may run."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [f"Syntax error on line {exc.lineno}: {exc.msg}"]

    problems = []

    def refuse(node, message):
        problems.append(f"Line {getattr(node, 'lineno', '?')}: {message}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _check_module(node, alias.name, refuse)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                refuse(node, "relative imports are not allowed")
            else:
                _check_module(node, node.module or "", refuse)
        elif isinstance(node, ast.Name) and node.id in BLOCKED_NAMES:
            refuse(node, f"'{node.id}' is not allowed")
        elif isinstance(node, ast.Attribute):
            attr = node.attr
            if attr.startswith("__") and attr.endswith("__") and attr not in ALLOWED_DUNDERS:
                refuse(node, f"dunder attribute '{attr}' is not allowed")
            elif attr in BLOCKED_NAMES:
                refuse(node, f"'{attr}' is not allowed")
            elif attr.startswith(BLOCKED_ATTRIBUTE_PREFIXES):
                refuse(node, f"'.{attr}' reads or writes files, which the pipeline must not do "
                             "(the runner handles all input and output)")

        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            method = node.func.attr
            if method in LOOKAHEAD_METHODS:
                refuse(node, f"'.{method}()' fills from LATER rows (look-ahead); use ffill")
            if method in PERIOD_METHODS:
                periods = node.args[0] if node.args else next(
                    (k.value for k in node.keywords if k.arg == "periods"), None)
                if _is_negative(periods):
                    refuse(node, f"'.{method}()' with a negative period reads a LATER row "
                                 "(look-ahead); use a positive period")
            for k in node.keywords:
                if k.arg == "method" and isinstance(k.value, ast.Constant) and \
                        k.value.value in ("bfill", "backfill"):
                    refuse(node, "filling with method='bfill' uses LATER rows (look-ahead)")
                if k.arg == "center" and isinstance(k.value, ast.Constant) and k.value.value:
                    refuse(node, "a centred window includes LATER rows (look-ahead)")

    defined = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    for name in REQUIRED_FUNCTIONS:
        if name not in defined:
            problems.append(f"The module must define a top-level function {name}(...)")
    return problems


def _is_negative(node) -> bool:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return isinstance(node.operand, ast.Constant) and isinstance(node.operand.value,
                                                                     (int, float))
    return isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
        and node.value < 0


def _check_module(node, name: str, refuse) -> None:
    root = name.split(".")[0]
    if root not in ALLOWED_MODULES:
        refuse(node, f"import of '{name}' is not allowed; only "
                     f"{', '.join(sorted(ALLOWED_MODULES))} may be imported")
    elif name.startswith(BLOCKED_SUBMODULES):
        refuse(node, f"import of '{name}' is not allowed (it reads files or the network)")


def sections(code: str) -> dict:
    """
    The source of preprocess, transform and train, and of everything else
    (imports, constants, helpers), for showing the three stages separately.
    Falls back to the whole file under 'module' when it does not parse.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return {"module": code}
    out, other = {}, []
    for node in tree.body:
        segment = ast.get_source_segment(code, node) or ""
        if isinstance(node, ast.FunctionDef) and node.name in REQUIRED_FUNCTIONS:
            out[node.name] = segment
        else:
            other.append(segment)
    out["helpers"] = "\n\n".join(s for s in other if s.strip())
    return out
