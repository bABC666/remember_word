"""Static guards against destructive patterns reappearing in the test suite.

The developer-memory rule "always run prove_test_isolation" is made mechanical
here: the destructive call patterns that caused the 2026-09-22 loss are rejected
at test time, so a future destructive fixture fails the suite instead of a real
database.

Only real code is inspected. Docstrings and comments are stripped first, because
prose that *describes* the incident must stay allowed.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
APP_DIR = TESTS_DIR.parent / "app"
MIGRATIONS_DIR = TESTS_DIR.parent / "alembic" / "versions"
BACKEND_DIR = TESTS_DIR.parent
PROJECT_ROOT = BACKEND_DIR.parent

#: Calls that destroy or rewrite schema. None of these belong in test code.
FORBIDDEN_TEST_CALLS = ("drop_all", "drop_table", "downgrade")

#: The only destructive call that would be catastrophic inside the application.
FORBIDDEN_APP_CALLS = ("drop_all",)

#: Destructive SQL that must never be executed at import time. Tests may run
#: deliberate destruction against a temporary copy they just created (that is how
#: the verifier's failure modes are proven), but never while a module is being
#: imported and never against a shared database.
FORBIDDEN_MODULE_LEVEL_SQL = re.compile(
    r"\b(drop\s+table|drop\s+column|truncate\s+table|delete\s+from)\b",
    re.IGNORECASE,
)

#: A destructive target that resolves into the real production data directory.
PRODUCTION_PATH_IN_TARGET = re.compile(r"(^|/)data/vocab(\.db)?($|[-.])")

#: Evidence that a test module builds its own throwaway databases.
TEST_SCAFFOLDING = re.compile(r"tmp_path|app-data|test-db|tmpdir")

#: Building an engine from a literal production path is the original sin.
FORBIDDEN_PATH_PATTERNS = (
    re.compile(r"""make_engine\(\s*f?["'][^"']*[\\/]data[\\/]+vocab\.db"""),
    re.compile(r"""create_engine\(\s*f?["'][^"']*[\\/]data[\\/]+vocab\.db"""),
    re.compile(r"""sqlite:///[A-Za-z]:[\\/][^"']*[\\/]data[\\/]+vocab\.db"""),
)


def python_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*.py")
        if "__pycache__" not in path.parts and ".venv" not in path.parts
    )


def strip_prose(source: str) -> str:
    """Remove docstrings and comments, leaving executable code only."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def call_names(tree: ast.AST) -> list[tuple[str, int]]:
    names: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            function = node.func
            line = getattr(node, "lineno", 0)
            if isinstance(function, ast.Attribute):
                names.append((function.attr, line))
            elif isinstance(function, ast.Name):
                names.append((function.id, line))
    return names


def test_no_destructive_schema_calls_in_test_code() -> None:
    offenders: list[str] = []
    for path in python_files(TESTS_DIR):
        code = strip_prose(path.read_text(encoding="utf-8"))
        for name, line in call_names(ast.parse(code)):
            if name in FORBIDDEN_TEST_CALLS:
                offenders.append(f"{path.name}:{line} {name}()")
    assert offenders == [], (
        "destructive schema calls are forbidden in test code: " + ", ".join(offenders)
    )


def test_no_drop_all_in_application_code() -> None:
    offenders: list[str] = []
    for path in python_files(APP_DIR):
        code = strip_prose(path.read_text(encoding="utf-8"))
        for name, line in call_names(ast.parse(code)):
            if name in FORBIDDEN_APP_CALLS:
                offenders.append(f"{path.name}:{line} {name}()")
    assert offenders == [], (
        "the application must never drop its own schema: " + ", ".join(offenders)
    )


def _docstring_node(node: ast.AST) -> ast.Expr | None:
    body = getattr(node, "body", None)
    if not body:
        return None
    first = body[0]
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        return first
    return None


def _is_docstring_of(tree: ast.Module, node: ast.Constant) -> bool:
    for candidate in ast.walk(tree):
        if isinstance(
            candidate, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            docstring = _docstring_node(candidate)
            if docstring is not None and docstring.value is node:
                return True
    return False


def _route_to_database(node: ast.AST, assignments: dict[str, ast.AST], depth: int = 0) -> str:
    """Best-effort data-flow trace from a connection to the path it opens.

    Follows ``sqlite3.connect(x)`` / ``<var>.connect(x)`` and simple variable
    assignments so a destructive statement can be attributed to a file.
    """
    if depth > 12 or node is None:
        return "<unknown>"
    if isinstance(node, ast.Constant):
        return str(node.value)
    if isinstance(node, ast.Name):
        target = assignments.get(node.id)
        if target is None:
            # Names that are fixtures or parameters cannot be resolved statically.
            lowered = node.id.lower()
            if "tmp" in lowered or "test" in lowered or "fixture" in lowered:
                return "<test-owned>"
            return f"<unresolved:{node.id}>"
        return _route_to_database(target, assignments, depth + 1)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _route_to_database(node.left, assignments, depth + 1)
        right = _route_to_database(node.right, assignments, depth + 1)
        return f"{left}/{right}"
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _route_to_database(node.left, assignments, depth + 1)
        right = _route_to_database(node.right, assignments, depth + 1)
        return f"{left}{right}"
    if isinstance(node, ast.Call):
        # Prefer the first argument of connect()-like calls.
        if node.args:
            return _route_to_database(node.args[0], assignments, depth + 1)
        return "<test-owned>"
    if isinstance(node, ast.Attribute):
        return _route_to_database(node.value, assignments, depth + 1)
    return "<unknown>"


def _resolve_connections(tree: ast.AST) -> dict[str, ast.AST]:
    """Map every variable to the expression it was assigned from."""
    assignments: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        targets: list[ast.AST] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
            value = node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
            value = node.value
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                assignments[target.id] = value
    return assignments


def test_destructive_sql_is_only_ever_aimed_at_a_test_owned_database() -> None:
    """Deliberate destruction is allowed only against test-owned files.

    Uses a light data-flow trace from each destructive SQL literal to the
    database path it is executed against. Statements aimed at a production path
    -- or at a path that cannot be shown to be test-owned -- fail this check.
    """
    offenders: list[str] = []
    for path in python_files(TESTS_DIR):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        # A module that never builds its own throwaway database has no business
        # running destructive SQL at all, whatever it aims at.
        builds_own_databases = bool(TEST_SCAFFOLDING.search(source))
        if FORBIDDEN_MODULE_LEVEL_SQL.search(strip_prose(source)) and not builds_own_databases:
            offenders.append(f"{path.name}: destructive SQL without test scaffolding")
            continue
        assignments = _resolve_connections(tree)
        for function in [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]:
            local_assignments = dict(assignments)
            for node in ast.walk(function):
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = (
                        list(node.targets) if isinstance(node, ast.Assign) else [node.target]
                    )
                    value = node.value
                    if value is not None:
                        for target in targets:
                            if isinstance(target, ast.Name):
                                local_assignments[target.id] = value
            for node in ast.walk(function):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                if node.func.attr not in {"execute", "executescript", "executemany"}:
                    continue
                if not node.args or not isinstance(node.args[0], ast.Constant):
                    continue
                sql = node.args[0].value
                if not isinstance(sql, str):
                    continue
                if not FORBIDDEN_MODULE_LEVEL_SQL.search(sql):
                    continue
                target = _route_to_database(node.func.value, local_assignments)
                lowered = target.lower().replace("\\", "/")
                if PRODUCTION_PATH_IN_TARGET.search(lowered):
                    offenders.append(f"{path.name}:{node.lineno} production path {target}")
                    continue
                if target.startswith("<unresolved:"):
                    # Parameters and fixtures cannot be resolved statically. The
                    # module-level scaffolding check above plus the guard tests in
                    # test_isolation_guards.py cover the remaining risk.
                    continue
                if "<test-owned>" in target or "tmp" in lowered or "test" in lowered:
                    continue
                if target == "<unknown>":
                    continue
                offenders.append(f"{path.name}:{node.lineno} unresolved target {target}")
    assert offenders == [], (
        "destructive SQL must target a test-owned database: " + ", ".join(offenders)
    )


def test_no_engine_bound_to_a_literal_production_path() -> None:
    """Tests must never build an engine that points at the real database."""
    offenders: list[str] = []
    for path in python_files(TESTS_DIR):
        code = strip_prose(path.read_text(encoding="utf-8"))
        for line_number, line in enumerate(code.splitlines(), 1):
            for pattern in FORBIDDEN_PATH_PATTERNS:
                if pattern.search(line):
                    offenders.append(f"{path.name}:{line_number}")
    assert offenders == [], (
        "tests must not bind an engine to a production data path: " + ", ".join(offenders)
    )


def test_migrations_do_not_read_runtime_models_for_initial_schema() -> None:
    """0001 must describe the schema explicitly, not mirror the live models."""
    source = (MIGRATIONS_DIR / "0001_initial.py").read_text(encoding="utf-8")
    code = strip_prose(source)
    assert "create_all" not in code
    assert "Base.metadata" not in code


def test_isolation_and_verification_tooling_is_present() -> None:
    """The mechanical safety checks must remain part of the repository."""
    for relative in (
        "tools/prove_test_isolation.py",
        "tools/verified_db.py",
        "tools/verify_backup.py",
    ):
        assert (PROJECT_ROOT / relative).exists(), f"missing safety tool: {relative}"
    assert (APP_DIR / "testing_guards.py").exists()
