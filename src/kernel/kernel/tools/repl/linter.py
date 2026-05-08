"""AST guardrails for model-authored REPL scripts."""

from __future__ import annotations

import ast


class ReplLintError(ValueError):
    """Raised when REPL code uses a disallowed Python construct."""


_BANNED_NAMES = {
    "__import__",
    "breakpoint",
    "compile",
    "eval",
    "exec",
    "globals",
    "locals",
    "open",
    "vars",
}

_BANNED_ATTRS = {
    "__bases__",
    "__builtins__",
    "__class__",
    "__dict__",
    "__globals__",
    "__mro__",
    "__subclasses__",
}


class ReplLinter(ast.NodeVisitor):
    """Reject constructs that do not belong in first-version REPL scripts."""

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        raise ReplLintError("import is not allowed in REPL scripts")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        raise ReplLintError("import is not allowed in REPL scripts")

    def visit_While(self, node: ast.While) -> None:  # noqa: N802
        raise ReplLintError("while loops are not allowed in REPL scripts")

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        raise ReplLintError("function definitions are not allowed in REPL v1")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        raise ReplLintError("function definitions are not allowed in REPL v1")

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        raise ReplLintError("class definitions are not allowed in REPL v1")

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
        raise ReplLintError("lambda is not allowed in REPL v1")

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        if node.id in _BANNED_NAMES:
            raise ReplLintError(f"name {node.id!r} is not allowed in REPL scripts")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        if node.attr in _BANNED_ATTRS or (node.attr.startswith("__") and node.attr.endswith("__")):
            raise ReplLintError(f"attribute {node.attr!r} is not allowed in REPL scripts")
        self.generic_visit(node)


def lint_repl_code(code: str) -> ast.Module:
    """Parse and lint REPL code, returning the AST on success."""
    tree = ast.parse(code, mode="exec")
    ReplLinter().visit(tree)
    return tree


__all__ = ["ReplLintError", "ReplLinter", "lint_repl_code"]
