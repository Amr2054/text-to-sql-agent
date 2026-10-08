import sqlite3
from pathlib import Path
from typing import Any, TypedDict
from langchain_core.tools import tool

DB_PATH = Path(__file__).resolve().parents[1] / "database" / "chinook.db"

class QueryResult(TypedDict):
    ok: bool
    columns: list[str]
    rows: list[list[Any]]
    error: str | None
    retryable: bool

def get_schema(db_path: Path) -> str:

    conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        cursor = conn.execute("""
            SELECT sql
            FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
        """)
        # Each result is a tuple containing one CREATE TABLE statement.
        return  ";\n\n".join(sql for (sql,) in cursor.fetchall()) + ";"
    finally:
        conn.close()

@tool
def run_query_tool(query: str) -> QueryResult:
    """Execute SQL against Chinook in read-only mode.

    Return status, columns, rows, an error, and whether SQL repair could help.
    Queries must read database tables; constant-only placeholders are rejected.
    """
    conn = None

    try:
        # Open the existing database without allowing database writes.
        conn = sqlite3.connect(
            f"{DB_PATH.resolve().as_uri()}?mode=ro",
            uri=True,
        )

        # SQLite reports real table reads, including COUNT(*) and subqueries.
        tables_read = set()

        def authorizer(action, table, column, database, source):

            if action == sqlite3.SQLITE_READ:
                tables_read.add(table)
                return sqlite3.SQLITE_OK

            dangerous_actions = {
                sqlite3.SQLITE_INSERT,
                sqlite3.SQLITE_UPDATE,
                sqlite3.SQLITE_DELETE,
                sqlite3.SQLITE_DROP_TABLE,
                sqlite3.SQLITE_ALTER_TABLE,
                sqlite3.SQLITE_CREATE_TABLE,
                sqlite3.SQLITE_ATTACH,
                sqlite3.SQLITE_DETACH,
            }

            if action in dangerous_actions:
                return sqlite3.SQLITE_DENY

            return sqlite3.SQLITE_OK

        conn.set_authorizer(authorizer)
        cursor = conn.execute(query)
        if not tables_read or cursor.description is None:
            return {
                "ok": False,
                "columns": [],
                "rows": [],
                "error": "The query must read database tables. Do not use constant-only placeholder queries such as SELECT NULL.",
                "retryable": True,
            }

        # description contains metadata for each result column.
        columns = [
            column[0]
            for column in (cursor.description or [])
        ]

        rows = [list(row) for row in cursor.fetchall()]

        return {
            "ok": True,
            "columns": columns,
            "rows": rows,
            "error": None,
            "retryable": False,
        }

    except sqlite3.Error as error:
        return {
            "ok": False,
            "columns": [],
            "rows": [],
            "error": str(error),
            # A connection failure needs configuration repair, not new SQL.
            "retryable": conn is not None,
        }

    finally:
        if conn is not None:
            conn.close()
