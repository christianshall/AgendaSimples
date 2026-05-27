"""
Camada única de acesso ao banco: Turso (SQLite/libsql) em produção ou SQL Server local.

O app escreve SQL no dialeto SQLite; execute_query traduz automaticamente para T-SQL
quando o backend local for SQL Server.
"""
from __future__ import annotations

import os
import re
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterable, Literal, Optional

try:
    import pyodbc
except ImportError:
    pyodbc = None

from database import (
    _valor_linha,
    _get_connection_sqlite_turso,
    safe_close,
    safe_commit,
    safe_rollback,
    usar_banco_remoto,
)

Backend = Literal["turso", "sqlite", "sqlserver"]

_LOGGED_BACKEND: Optional[str] = None


def get_backend() -> Backend:
    """turso | sqlite | sqlserver conforme variáveis de ambiente."""
    if usar_banco_remoto() or (os.environ.get("TURSO_DATABASE_URL") or "").strip():
        url = (os.environ.get("TURSO_DATABASE_URL") or os.environ.get("DATABASE_URL") or "").strip()
        token = (os.environ.get("TURSO_AUTH_TOKEN") or "").strip()
        if url and token:
            return "turso"
    odbc = _sql_server_connection_string()
    if odbc and pyodbc is not None:
        return "sqlserver"
    if odbc and pyodbc is None:
        print(
            "AVISO: SQL_SERVER_ODBC configurado mas pyodbc não está instalado; "
            "usando SQLite local."
        )
    return "sqlite"


def _sql_server_connection_string() -> str:
    explicit = (os.environ.get("SQL_SERVER_ODBC") or os.environ.get("SQLSERVER_ODBC") or "").strip()
    if explicit:
        return explicit
    if os.environ.get("USE_SQL_SERVER", "").lower() in ("1", "true", "yes"):
        server = os.environ.get("SQL_SERVER_HOST", "localhost")
        database = os.environ.get("SQL_SERVER_DATABASE", "AgendaSimples")
        driver = os.environ.get("SQL_SERVER_DRIVER", "ODBC Driver 17 for SQL Server")
        trusted = os.environ.get("SQL_SERVER_TRUSTED", "yes").lower() in ("1", "true", "yes")
        if trusted:
            return (
                f"Driver={{{driver}}};Server={server};Database={database};"
                "Trusted_Connection=yes;"
            )
        user = os.environ.get("SQL_SERVER_USER", "")
        pwd = os.environ.get("SQL_SERVER_PASSWORD", "")
        return (
            f"Driver={{{driver}}};Server={server};Database={database};"
            f"UID={user};PWD={pwd};"
        )
    return ""


def _log_backend_once() -> None:
    global _LOGGED_BACKEND
    backend = get_backend()
    if _LOGGED_BACKEND == backend:
        return
    _LOGGED_BACKEND = backend
    if backend == "turso":
        print("Conectado ao Turso em Produção")
    elif backend == "sqlserver":
        print("Usando SQL Server em Desenvolvimento")
    else:
        print("Usando Banco Local em Desenvolvimento (SQLite)")


def _limits_to_top(sql: str, max_passes: int = 24) -> str:
    """Converte cada LIMIT N no SELECT mais próximo anterior (subqueries inclusas)."""
    out = sql
    for _ in range(max_passes):
        match = re.search(r"\s+LIMIT\s+(\d+)\b", out, re.IGNORECASE)
        if not match:
            break
        n = match.group(1)
        prefix = out[: match.start()]
        selects = list(re.finditer(r"\bSELECT\b", prefix, re.IGNORECASE))
        if not selects:
            out = out[: match.start()] + out[match.end() :]
            continue
        sel = selects[-1]
        insert_at = sel.end()
        between = out[insert_at : match.start()]
        if re.search(r"\bTOP\s+\d+\b", between, re.IGNORECASE):
            out = out[: match.start()] + out[match.end() :]
            continue
        out = (
            out[:insert_at]
            + f" TOP {n} "
            + out[insert_at : match.start()]
            + out[match.end() :]
        )
    return out


def translate_sql(sql: str, backend: Optional[Backend] = None) -> str:
    """
    Traduz SQL canônico (SQLite) para o dialeto do backend ativo.
  - LIMIT n  <->  TOP n
  - substr(COALESCE(col,''),1,10)  ->  CONVERT(VARCHAR(10), col, 23) no SQL Server
  - [Coluna]  ->  Coluna
  - IFNULL  ->  ISNULL (SQL Server)
  - ADD COLUMN  ->  ADD (SQL Server)
    """
    backend = backend or get_backend()
    out = sql.strip()

    if backend == "sqlserver":
        out = re.sub(r"\[([^\]]+)\]", r"\1", out)
        out = re.sub(
            r"substr\s*\(\s*COALESCE\s*\(\s*([^,]+?)\s*,\s*''\s*\)\s*,\s*1\s*,\s*10\s*\)",
            r"CONVERT(VARCHAR(10), \1, 23)",
            out,
            flags=re.IGNORECASE,
        )
        out = re.sub(r"\bIFNULL\s*\(", "ISNULL(", out, flags=re.IGNORECASE)
        out = re.sub(r"\bADD\s+COLUMN\b", "ADD", out, flags=re.IGNORECASE)

        out = _limits_to_top(out)
        return out

    # turso / sqlite: aceitar SQL Server legado
    top_match = re.search(r"^\s*SELECT\s+TOP\s+(\d+)\s+", out, re.IGNORECASE)
    if top_match:
        n = top_match.group(1)
        out = re.sub(r"^\s*SELECT\s+TOP\s+\d+\s+", "SELECT ", out, count=1, flags=re.IGNORECASE)
        if not re.search(r"\s+LIMIT\s+\d+\s*;?\s*$", out, re.IGNORECASE):
            out = out.rstrip().rstrip(";") + f" LIMIT {n}"
    out = re.sub(r"\bISNULL\s*\(", "IFNULL(", out, flags=re.IGNORECASE)
    return out


def open_sqlserver_connection():
    if pyodbc is None:
        raise RuntimeError("pyodbc não instalado; configure Turso ou instale pyodbc.")
    conn = pyodbc.connect(_sql_server_connection_string())
    conn.autocommit = False
    return conn


def get_connection():
    """Retorna conexão do backend ativo (pyodbc ou SQLite/Turso)."""
    _log_backend_once()
    if get_backend() == "sqlserver":
        return open_sqlserver_connection()
    return _get_connection_sqlite_turso()


def _row_to_dict(cursor, row) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return row
    try:
        cols = [d[0] for d in cursor.description]
        return {cols[i]: row[i] for i in range(len(cols))}
    except Exception:
        pass
    if hasattr(row, "keys"):
        return {k: row[k] for k in row.keys()}
    return {"_0": row}


def _rows_to_dicts(cursor, rows) -> list[dict[str, Any]]:
    result = []
    for row in rows or []:
        if isinstance(row, dict):
            result.append(row)
        else:
            result.append(_row_to_dict(cursor, row))
    return result


def execute_query(
    sql: str,
    params: Optional[Iterable[Any]] = None,
    *,
    fetch: Literal["all", "one", "none"] = "all",
    conn=None,
    commit: bool = False,
) -> Any:
    """
    Executa SQL traduzido para o backend atual.
    Retorna lista de dicts (all), um dict/None (one) ou rowcount (none).
    """
    backend = get_backend()
    sql_exec = translate_sql(sql, backend)
    params_tuple = tuple(params) if params is not None else ()

    owns_conn = conn is None
    if owns_conn:
        conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(sql_exec, params_tuple)
        if fetch == "none":
            if commit:
                safe_commit(conn)
            return cursor.rowcount
        if fetch == "one":
            row = cursor.fetchone()
            if commit:
                safe_commit(conn)
            if row is None:
                return None
            if isinstance(row, dict):
                return row
            return _row_to_dict(cursor, row)
        rows = cursor.fetchall()
        if commit:
            safe_commit(conn)
        return _rows_to_dicts(cursor, rows)
    except Exception:
        if owns_conn:
            safe_rollback(conn)
        raise
    finally:
        if owns_conn:
            safe_close(conn)


def execute_write(sql: str, params: Optional[Iterable[Any]] = None, *, conn=None) -> int:
    return int(
        execute_query(sql, params, fetch="none", conn=conn, commit=True) or 0
    )


def coluna_existe(tabela: str, coluna: str, *, conn=None) -> bool:
    backend = get_backend()
    owns_conn = conn is None
    if owns_conn:
        conn = get_connection()
    try:
        cursor = conn.cursor()
        if backend == "sqlserver":
            cursor.execute(
                """
                SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_NAME = ? AND COLUMN_NAME = ?
                """,
                (tabela, coluna),
            )
            return cursor.fetchone() is not None
        try:
            cursor.execute(f"PRAGMA table_info({tabela})")
            for row in cursor.fetchall() or []:
                if _valor_linha(row, 1, "name") == coluna:
                    return True
        except Exception:
            pass
        try:
            probe = translate_sql(f"SELECT {coluna} FROM {tabela} LIMIT 0", backend)
            cursor.execute(probe)
            return True
        except Exception:
            return False
    finally:
        if owns_conn:
            safe_close(conn)


def expr_data_yyyy_mm_dd(column_sql: str) -> str:
    """Expressão de data YYYY-MM-DD compatível com SQLite/Turso e SQL Server."""
    col = column_sql.strip()
    if get_backend() == "sqlserver":
        return f"CONVERT(VARCHAR(10), {col}, 23)"
    return f"substr(COALESCE({col}, ''), 1, 10)"


@contextmanager
def connection_scope():
    conn = get_connection()
    try:
        yield conn
        safe_commit(conn)
    except Exception:
        safe_rollback(conn)
        raise
    finally:
        safe_close(conn)


def ensure_financeiro_schema(*, conn=None) -> None:
    """Garante tabelas/colunas críticas do financeiro no backend ativo."""
    ensure_app_schema(conn)


def ensure_app_schema(*, conn=None) -> None:
    """Garante schema completo conforme backend (Turso/SQLite ou SQL Server)."""
    from database import initialize_database_schema

    initialize_database_schema(conn)


def row_get(row: Any, *keys, index: int = 0, default=None):
    if row is None:
        return default
    if isinstance(row, dict):
        for k in keys:
            if k and k in row:
                return row[k]
        vals = list(row.values())
        if index < len(vals):
            return vals[index]
        return default
    return _valor_linha(row, index, keys[0] if keys else None) or default


class AdapterCursor:
    """Cursor compatível que traduz SQL em cada execute (para código legado)."""

    def __init__(self, conn):
        self._conn = conn
        self._cursor = conn.cursor()
        self.lastrowid = None
        self.rowcount = 0

    def execute(self, sql, params=()):
        sql_exec = translate_sql(sql)
        self._cursor.execute(sql_exec, tuple(params) if params is not None else ())
        self.rowcount = getattr(self._cursor, "rowcount", 0) or 0
        self.lastrowid = getattr(self._cursor, "lastrowid", None)
        return self

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    @property
    def description(self):
        return self._cursor.description


def cursor(conn) -> AdapterCursor:
    return AdapterCursor(conn)
