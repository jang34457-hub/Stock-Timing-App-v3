"""Production DB: SQLite by default, PostgreSQL (Supabase) when configured.

Backtest still uses `backtest_db.py` / SQLite only. Tests that point
`prices_db.DB_PATH` at a temp file always stay on SQLite.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_SQLITE = ROOT / "data" / "prices.db"
SCHEMA_SQL = ROOT.parent / "database" / "003_runtime_compat.sql"


def _load_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _is_production() -> bool:
    return os.getenv("APP_ENV", "development").strip().lower() == "production"


def database_url() -> str:
    return (
        os.getenv("DATABASE_URL")
        or os.getenv("SUPABASE_DB_URL")
        or os.getenv("POSTGRES_URL")
        or ""
    ).strip()


def sqlite_path_is_default(db_path: Path) -> bool:
    try:
        return db_path.resolve() == DEFAULT_SQLITE.resolve()
    except OSError:
        return False


def use_postgres(db_path: Path) -> bool:
    """Postgres only for the default production path + URL + production (or explicit flag)."""
    if _flag("STA_FORCE_SQLITE"):
        return False
    if not sqlite_path_is_default(db_path):
        return False
    url = database_url()
    if not url:
        if _is_production():
            raise RuntimeError(
                "APP_ENV=production 이지만 DATABASE_URL / SUPABASE_DB_URL 이 없습니다."
            )
        return False
    return _is_production() or _flag("STA_USE_POSTGRES")


def coerce_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def date_str(value) -> str:
    return coerce_date(value).isoformat()


def sql_for_postgres(sql: str) -> str:
    text = sql.strip()
    ignore = "INSERT OR IGNORE INTO"
    upper = text.upper()
    if ignore in upper:
        idx = upper.find(ignore)
        text = text[:idx] + "INSERT INTO" + text[idx + len(ignore) :]
        if "ON CONFLICT" not in text.upper():
            text = text.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    out: list[str] = []
    in_str = False
    quote = ""
    i = 0
    while i < len(text):
        ch = text[i]
        if in_str:
            out.append(ch)
            if ch == quote:
                in_str = False
            elif ch == "\\" and quote == "'":
                if i + 1 < len(text):
                    out.append(text[i + 1])
                    i += 1
            i += 1
            continue
        if ch in ("'", '"'):
            in_str = True
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "?":
            out.append("%s")
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _as_tuple(row):
    if row is None:
        return None
    if isinstance(row, dict):
        return tuple(row.values())
    return row


class _PgCursor:
    def __init__(self, cur, as_dict: bool):
        self._cur = cur
        self.rowcount = cur.rowcount if cur.rowcount is not None else -1
        self._as_dict = as_dict

    def fetchone(self):
        row = self._cur.fetchone()
        if row is None:
            return None
        if self._as_dict:
            return dict(row)
        return _as_tuple(row)

    def fetchall(self):
        rows = self._cur.fetchall()
        if self._as_dict:
            return [dict(r) for r in rows]
        return [_as_tuple(r) for r in rows]


class PgConnection:
    """sqlite3-like surface used by prices_db (execute, executemany, row_factory)."""

    def __init__(self, raw):
        self._raw = raw
        self.row_factory = None
        self.total_changes = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self._raw.commit()
        else:
            self._raw.rollback()
        self._raw.close()
        return False

    def execute(self, sql: str, params=()):
        cur = self._raw.cursor()
        cur.execute(sql_for_postgres(sql), params or ())
        if cur.rowcount and cur.rowcount > 0:
            self.total_changes += cur.rowcount
        return _PgCursor(cur, as_dict=self.row_factory is not None)

    def executemany(self, sql: str, seq):
        cur = self._raw.cursor()
        rows = list(seq)
        cur.executemany(sql_for_postgres(sql), rows)
        n = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        self.total_changes += n
        return _PgCursor(cur, as_dict=self.row_factory is not None)

    def commit(self):
        self._raw.commit()

    def close(self):
        self._raw.close()


def open_postgres() -> PgConnection:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    raw = psycopg2.connect(database_url(), cursor_factory=RealDictCursor)
    raw.autocommit = False
    return PgConnection(raw)


def apply_runtime_schema(conn) -> None:
    sql = SCHEMA_SQL.read_text(encoding="utf-8")
    if isinstance(conn, PgConnection):
        cur = conn._raw.cursor()
        cur.execute(sql)
        return
    conn.executescript(sql)


def ping_postgres() -> dict:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    raw = psycopg2.connect(database_url(), cursor_factory=RealDictCursor)
    try:
        with raw.cursor() as cur:
            cur.execute("SELECT current_database() AS db, current_user AS usr")
            info = dict(cur.fetchone())
            cur.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name IN (
                    'daily_prices', 'stocks', 'top20_history',
                    'user_settings', 'user_watchlist', 'signal_history'
                  )
                ORDER BY table_name
                """
            )
            info["tables"] = [r["table_name"] for r in cur.fetchall()]
        return info
    finally:
        raw.close()
