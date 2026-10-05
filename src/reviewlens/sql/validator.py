"""SQL validator: AST-based safety check for ReviewLens queries.

This is the key security component (Section 7.2). It uses sqlglot to parse
and validate SQL before execution. Never relies on regex — AST inspection
is bypass-resistant.

Library version checked: sqlglot 30.21.0 (DuckDB dialect).
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

from reviewlens.models import ValidationResult

# ---------------------------------------------------------------------------
# Forbidden statement node types
# ---------------------------------------------------------------------------
# Resolved with hasattr at import time for version tolerance.
_FORBIDDEN_STMT_NAMES: list[str] = [
    "Insert",
    "Update",
    "Delete",
    "Drop",
    "Create",
    "Alter",  # covers AlterTable etc. in sqlglot 30.x (AlterTable does not exist)
    "Merge",
    "Command",
    "Copy",
    "Set",
    "Use",
    "Attach",
    "Detach",
    "TruncateTable",
    "Transaction",
    "Commit",
    "Rollback",
    "Grant",
]
FORBIDDEN_STMT_NODES: tuple[type[exp.Expression], ...] = tuple(
    getattr(exp, name) for name in _FORBIDDEN_STMT_NAMES if hasattr(exp, name)
)

# ---------------------------------------------------------------------------
# Forbidden function names (case-insensitive)
# These appear as exp.Anonymous nodes (name attribute) or as specific sqlglot
# expression classes (e.g. exp.ReadCSV, exp.ReadParquet).
# ---------------------------------------------------------------------------
FORBIDDEN_FUNC_NAMES: frozenset[str] = frozenset(
    [
        "read_csv",
        "read_csv_auto",
        "read_parquet",
        "read_json",
        "read_json_auto",
        "read_ndjson",
        "read_ndjson_auto",
        "read_ndjson_objects",
        "read_json_objects",
        "read_xlsx",
        "read_text",
        "read_blob",
        "glob",
        "parquet_scan",
        "query",
        "query_table",
        "getenv",
        "current_setting",
        "sniff_csv",
        "iceberg_scan",
        "delta_scan",
        "st_read",
    ]
)

# Any function whose name starts with one of these is rejected (file / metadata readers).
FORBIDDEN_FUNC_PREFIXES: tuple[str, ...] = (
    "pragma_",
    "duckdb_",
    "read_",
    "parquet_",
    "sniff_",
    "iceberg_",
    "delta_",
)


def _is_forbidden_name(name: str) -> bool:
    return name in FORBIDDEN_FUNC_NAMES or name.startswith(FORBIDDEN_FUNC_PREFIXES)


# Specific sqlglot expression classes that map to forbidden DuckDB functions.
# These appear as named classes rather than Anonymous nodes.
_FORBIDDEN_CLASS_NAMES: list[str] = [
    "ReadCSV",
    "ReadParquet",
    "Glob",
]
FORBIDDEN_FUNC_CLASSES: tuple[type[exp.Expression], ...] = tuple(
    getattr(exp, name) for name in _FORBIDDEN_CLASS_NAMES if hasattr(exp, name)
)


def _fail(code: str, detail: str = "") -> ValidationResult:
    reason = code if not detail else f"{code}: {detail}"
    return ValidationResult(ok=False, reasons=[reason])


def _ok(normalized_sql: str) -> ValidationResult:
    return ValidationResult(ok=True, normalized_sql=normalized_sql, reasons=[])


def _check_forbidden_functions(tree: exp.Expression | exp.Query) -> str | None:
    """Return a reason string if any forbidden function is found, else None."""
    # Check known sqlglot expression classes (e.g. ReadCSV, ReadParquet)
    for node in tree.walk():
        if isinstance(node, FORBIDDEN_FUNC_CLASSES):
            return f"forbidden_function:{type(node).__name__}"

    # Check Anonymous nodes and generic Func nodes by name
    for node in tree.find_all(exp.Anonymous):
        name = node.name.lower()
        if _is_forbidden_name(name):
            return f"forbidden_function:{name}"

    # Also check exp.Func subclasses that expose .name
    for node in tree.find_all(exp.Func):
        name = getattr(node, "name", "") or ""
        name_lower = name.lower()
        if _is_forbidden_name(name_lower):
            return f"forbidden_function:{name_lower}"

    return None


def _wrap_with_limit(tree: exp.Query, max_limit: int) -> exp.Query:
    """Wrap a query as `SELECT * FROM (<query>) AS q LIMIT n`."""
    return exp.select("*").from_(exp.Subquery(this=tree, alias="q")).limit(max_limit)


def _outer_limit_state(tree: exp.Select) -> tuple[str, int | None]:
    """Inspect ONLY the outermost LIMIT of a Select (never an inner subquery's).

    Returns (state, value) where state is one of:
    - "none": no LIMIT on the outer query
    - "literal": an integer-literal LIMIT with the given value
    - "other": LIMIT is an expression/subquery, or a non-LIMIT row clause (e.g. FETCH)
    """
    limit_arg = tree.args.get("limit")
    if limit_arg is None:
        return "none", None
    if not isinstance(limit_arg, exp.Limit):
        return "other", None
    expr = limit_arg.expression
    if isinstance(expr, exp.Literal) and not expr.is_string:
        try:
            return "literal", int(expr.this)
        except ValueError:
            return "other", None
    return "other", None


def _enforce_limit(tree: exp.Query, max_limit: int) -> exp.Query:
    """Enforce max_limit on the OUTER query.

    - Select with no outer LIMIT → add LIMIT max_limit.
    - Select with literal LIMIT > max_limit → lower to max_limit.
    - Select with literal LIMIT <= max_limit → keep as is.
    - Select with a non-literal LIMIT or FETCH clause → wrap and cap.
    - Set operations (Union/Intersect/Except) → wrap in SELECT * FROM (...) LIMIT max_limit.
    """
    if not isinstance(tree, exp.Select):
        return _wrap_with_limit(tree, max_limit)

    state, value = _outer_limit_state(tree)
    if state == "none":
        return tree.limit(max_limit, copy=True)
    if state == "literal":
        assert value is not None
        return tree if value <= max_limit else tree.limit(max_limit, copy=True)
    return _wrap_with_limit(tree, max_limit)


def validate_sql(
    sql: str,
    allowed_tables: set[str] | None = None,
    max_limit: int = 500,
) -> ValidationResult:
    """Validate a SQL string for safety and correctness.

    Args:
        sql: Raw SQL string from LLM output.
        allowed_tables: Set of permitted table names (lowercase). Defaults to {"reviews"}.
        max_limit: Maximum LIMIT rows enforced. Defaults to 500.

    Returns:
        ValidationResult with ok=True and normalized_sql, or ok=False with reasons.
    """
    if allowed_tables is None:
        allowed_tables = {"reviews"}
    allowed_lower = {t.lower() for t in allowed_tables}

    # -----------------------------------------------------------------------
    # Step 1: Strip and normalize input
    # -----------------------------------------------------------------------
    sql = sql.strip()
    # Remove trailing semicolons (one or more)
    while sql.endswith(";"):
        sql = sql[:-1].rstrip()
    sql = sql.strip()

    if not sql:
        return _fail("empty_input", "SQL string is empty")

    # -----------------------------------------------------------------------
    # Step 2: Parse
    # -----------------------------------------------------------------------
    try:
        stmts = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None]
    except sqlglot.errors.ParseError as exc:
        return _fail("parse_error", str(exc)[:200])

    if not stmts:
        return _fail("parse_error", "No statements parsed")

    # -----------------------------------------------------------------------
    # Step 3: Exactly one statement
    # -----------------------------------------------------------------------
    if len(stmts) != 1:
        return _fail(
            "multiple_statements",
            f"Expected 1 statement, got {len(stmts)}",
        )

    tree = stmts[0]

    # -----------------------------------------------------------------------
    # Step 4: Root must be a query (SELECT or set operation)
    # -----------------------------------------------------------------------
    if not isinstance(tree, exp.Query):
        return _fail(
            "not_a_select",
            f"Root node is {type(tree).__name__}, expected a SELECT/query",
        )

    # -----------------------------------------------------------------------
    # Step 5: Reject forbidden statement nodes anywhere in the tree
    # -----------------------------------------------------------------------
    if FORBIDDEN_STMT_NODES:
        found = tree.find(*FORBIDDEN_STMT_NODES)
        if found is not None:
            return _fail(
                "forbidden_statement",
                f"Contains forbidden node: {type(found).__name__}",
            )

    # -----------------------------------------------------------------------
    # Step 5b: Reject SELECT ... INTO
    # -----------------------------------------------------------------------
    into_node = tree.find(exp.Into)
    if into_node is not None:
        return _fail("forbidden_statement", "SELECT ... INTO is not allowed")

    # -----------------------------------------------------------------------
    # Step 6: Reject forbidden functions
    # -----------------------------------------------------------------------
    reason = _check_forbidden_functions(tree)
    if reason is not None:
        return _fail(reason)

    # -----------------------------------------------------------------------
    # Step 7: Table allowlist (minus CTE aliases)
    # -----------------------------------------------------------------------
    cte_aliases: set[str] = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}

    for tbl in tree.find_all(exp.Table):
        # Reject table-valued functions in FROM (read_csv(...), glob(...), ...). Only plain
        # identifiers may be used as tables.
        if not isinstance(tbl.this, exp.Identifier):
            return _fail(
                "table_function",
                f"Table functions are not allowed in FROM: {tbl.sql()[:80]}",
            )
        # Reject qualified names (db.table or catalog.db.table)
        if tbl.args.get("db") or tbl.args.get("catalog"):
            return _fail(
                "qualified_table",
                f"Qualified table reference not allowed: {tbl.sql()}",
            )
        name = tbl.name.lower()
        if not name:
            # Could be a table-valued function like read_parquet(...) — already caught above
            continue
        if name not in cte_aliases and name not in allowed_lower:
            return _fail(
                f"table_not_allowed:{tbl.name}",
                f"Table '{tbl.name}' is not in the allowed list {allowed_lower}",
            )

    # -----------------------------------------------------------------------
    # Step 8: Enforce row cap
    # -----------------------------------------------------------------------
    tree = _enforce_limit(tree, max_limit)

    # -----------------------------------------------------------------------
    # Step 9: Normalize and return
    # -----------------------------------------------------------------------
    normalized = tree.sql(dialect="duckdb")
    return _ok(normalized)
