"""SGIC DMS database layer.

Database compatibility layer for SGIC DMS.

Supported engines: SQLite (default), Microsoft SQL Server, and MySQL/MariaDB.
The application continues to use a small SQLite-style API; SQL is translated for each server backend.
"""
import configparser
import os
import re
import sqlite3

BASE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_SQL = os.path.join(BASE, "schema_sqlserver.sql")

DEFAULTS = {
    "engine": "sqlite",                    # sqlite | sqlserver | mysql
    "server": r"localhost\SQLEXPRESS",
    "database": "SGIC_DMS",
    "authentication": "windows",           # windows | sql
    "username": "",
    "password": "",
    "trust_server_certificate": "yes",
    "driver": "",                          # empty = pick the newest "ODBC Driver ... for SQL Server"
    "host": "localhost",
    "port": "3306",
    "mysql_user": "sgic_app",
    "mysql_password": "",
    "charset": "utf8",
}
ENV_NAMES = {"engine": "SGIC_DB_ENGINE", "server": "SGIC_SQL_SERVER", "database": "SGIC_SQL_DATABASE",
             "authentication": "SGIC_SQL_AUTH", "username": "SGIC_SQL_USER", "password": "SGIC_SQL_PASSWORD",
             "trust_server_certificate": "SGIC_SQL_TRUST_CERT", "driver": "SGIC_SQL_DRIVER",
             "host": "SGIC_MYSQL_HOST", "port": "SGIC_MYSQL_PORT", "mysql_user": "SGIC_MYSQL_USER",
             "mysql_password": "SGIC_MYSQL_PASSWORD", "charset": "SGIC_MYSQL_CHARSET"}


def load_config():
    cfg = dict(DEFAULTS)
    path = os.path.join(BASE, "database.ini")
    if os.path.exists(path):
        parser = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
        parser.read(path, encoding="utf-8")
        if parser.has_section("database"):
            for key in cfg:
                if parser.has_option("database", key):
                    cfg[key] = parser.get("database", key).strip()
    for key, env in ENV_NAMES.items():
        if os.environ.get(env):
            cfg[key] = os.environ[env].strip()
    cfg["engine"] = cfg["engine"].lower().replace(" ", "")
    if cfg["engine"] in ("mssql", "sql_server", "sqlserver"):
        cfg["engine"] = "sqlserver"
    if cfg["engine"] in ("mariadb", "mysql8"):
        cfg["engine"] = "mysql"
    return cfg


CONFIG = load_config()


def engine():
    return CONFIG["engine"]


def describe():
    if engine() == "sqlserver":
        return f"SQL Server  {CONFIG['server']}  /  database {CONFIG['database']}"
    if engine() == "mysql":
        return f"MySQL  {CONFIG['host']}:{CONFIG['port']}  /  database {CONFIG['database']}  /  user {CONFIG['mysql_user']}"
    return "SQLite file"


# ---------------------------------------------------------------------------------------------
# Row object (SQL Server): works like sqlite3.Row - by name, by position, unpacking, dict(row)
# ---------------------------------------------------------------------------------------------
class Row:
    __slots__ = ("_cols", "_vals")

    def __init__(self, cols, vals):
        self._cols, self._vals = cols, tuple(vals)

    def __getitem__(self, key):
        if isinstance(key, (int, slice)):
            return self._vals[key]
        try:
            return self._vals[self._cols.index(key)]
        except ValueError:
            low = [c.lower() for c in self._cols]
            if str(key).lower() in low:
                return self._vals[low.index(str(key).lower())]
            raise KeyError(key)

    def __iter__(self):
        return iter(self._vals)

    def __len__(self):
        return len(self._vals)

    def keys(self):
        return list(self._cols)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def __repr__(self):
        return f"Row({dict(zip(self._cols, self._vals))})"


# ---------------------------------------------------------------------------------------------
# SQLite -> T-SQL translation (only the constructs this application uses)
# ---------------------------------------------------------------------------------------------
_LITERAL = re.compile(r"('(?:[^']|'')*')")


def _outside_literals(sql, fn):
    parts = _LITERAL.split(sql)
    return "".join(p if i % 2 else fn(p) for i, p in enumerate(parts))


def _limit_to_top(sql):
    """'SELECT ... LIMIT n'  ->  'SELECT TOP (n) ...'  (also inside sub-queries)."""
    while True:
        m = re.search(r"\s+LIMIT\s+(\d+)", sql, re.I)
        if not m:
            return sql
        n, depth, i, sel = m.group(1), 0, m.start() - 1, -1
        while i >= 0:
            ch = sql[i]
            if ch == ")":
                depth += 1
            elif ch == "(":
                if depth == 0:
                    break
                depth -= 1
            elif depth == 0 and sql[i:i + 6].upper() == "SELECT" and (i == 0 or not (sql[i - 1].isalnum() or sql[i - 1] == "_")):
                sel = i
                break
            i -= 1
        if sel < 0:
            raise ValueError("Cannot translate LIMIT in: " + sql)
        after = sel + 6
        d = re.match(r"\s+DISTINCT", sql[after:], re.I)
        if d:
            after += d.end()
        sql = sql[:after] + f" TOP ({n})" + sql[after:m.start()] + sql[m.end():]


def to_tsql(sql):
    """Translate one SQLite-style statement into T-SQL."""
    sql = re.sub(r"\s+COLLATE\s+NOCASE\b", "", sql, flags=re.I)      # SQL Server's default collation is case-insensitive
    sql = _limit_to_top(sql)
    sql = _outside_literals(sql, lambda s: re.sub(r"(?<![\[\w])timestamp(?![\]\w])", "[timestamp]", s))
    sql = _outside_literals(sql, lambda s: s.replace("||", "+"))
    return sql


def sqlite_decl_to_tsql(decl):
    d = re.sub(r"\bTEXT\b", "NVARCHAR(MAX)", decl, flags=re.I)
    return re.sub(r"\bINTEGER\b", "INT", d, flags=re.I)


# ---------------------------------------------------------------------------------------------
# SQL Server connection wrapper with the small sqlite3-style API the application uses
# ---------------------------------------------------------------------------------------------
class MssqlCursor:
    def __init__(self, cur):
        self._cur = cur

    @property
    def rowcount(self):
        return self._cur.rowcount

    def _cols(self):
        return [d[0] for d in self._cur.description] if self._cur.description else []

    def fetchone(self):
        r = self._cur.fetchone()
        return Row(self._cols(), r) if r is not None else None

    def fetchall(self):
        cols = self._cols()
        return [Row(cols, r) for r in self._cur.fetchall()]

    def __iter__(self):
        return iter(self.fetchall())


class MssqlConn:
    is_mssql = True

    def __init__(self, raw):
        self._raw = raw
        self.row_factory = None   # accepted for API compatibility; rows are always Row objects

    def execute(self, sql, params=()):
        cur = self._raw.cursor()
        cur.execute(to_tsql(sql), tuple(params))
        return MssqlCursor(cur)

    def execute_raw(self, sql):
        cur = self._raw.cursor()
        cur.execute(sql)
        return MssqlCursor(cur)

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        try:
            self._raw.close()
        except Exception:
            pass


def to_mysql(sql):
    """Translate the SQLite-style SQL used by SGIC DMS into MySQL-compatible SQL."""
    sql = re.sub(r"\s+COLLATE\s+NOCASE\b", "", sql, flags=re.I)
    # TIMESTAMP is used as an ordinary audit column name in the existing app.
    sql = _outside_literals(sql, lambda s: re.sub(r"(?<![`\w])timestamp(?![`\w])", "`timestamp`", s, flags=re.I))
    # qmark placeholders are SQLite/pyodbc style; PyMySQL uses %s.
    sql = _outside_literals(sql, lambda s: s.replace("?", "%s"))
    return sql


class MysqlCursor:
    def __init__(self, cur):
        self._cur = cur

    @property
    def rowcount(self):
        return self._cur.rowcount

    def _cols(self):
        return [d[0] for d in self._cur.description] if self._cur.description else []

    def fetchone(self):
        r = self._cur.fetchone()
        if r is None:
            return None
        if isinstance(r, dict):
            return Row(list(r.keys()), list(r.values()))
        return Row(self._cols(), r)

    def fetchall(self):
        cols = self._cols()
        rows = self._cur.fetchall()
        return [Row(list(r.keys()), list(r.values())) if isinstance(r, dict) else Row(cols, r) for r in rows]

    def __iter__(self):
        return iter(self.fetchall())


class MysqlConn:
    is_mysql = True

    def __init__(self, raw):
        self._raw = raw
        self.row_factory = None  # accepted for compatibility; rows are always Row objects

    def execute(self, sql, params=()):
        cur = self._raw.cursor()
        cur.execute(to_mysql(sql), tuple(params) if params else None)
        return MysqlCursor(cur)

    def execute_raw(self, sql):
        cur = self._raw.cursor()
        cur.execute(sql)
        return MysqlCursor(cur)

    def executescript(self, script):
        # Schema contains simple CREATE TABLE statements separated by semicolons.
        for statement in script.split(";"):
            statement = statement.strip()
            if statement and not statement.startswith("--"):
                self.execute_raw(statement)
        self.commit()

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        try:
            self._raw.close()
        except Exception:
            pass


def _pick_driver(pyodbc):
    if CONFIG["driver"]:
        return CONFIG["driver"]
    names = [d for d in pyodbc.drivers() if "SQL Server" in d]
    ranked = sorted((d for d in names if d.startswith("ODBC Driver")), reverse=True)
    if ranked:
        return ranked[0]
    if names:
        return names[0]
    raise RuntimeError("No SQL Server ODBC driver found. Install 'ODBC Driver 18 for SQL Server' from Microsoft.")


def mssql_connection_string(database=None):
    import pyodbc
    parts = [f"DRIVER={{{_pick_driver(pyodbc)}}}", f"SERVER={CONFIG['server']}",
             f"DATABASE={database or CONFIG['database']}"]
    if CONFIG["authentication"].lower() == "sql":
        parts += [f"UID={CONFIG['username']}", f"PWD={CONFIG['password']}"]
    else:
        parts.append("Trusted_Connection=yes")
    if CONFIG["trust_server_certificate"].lower() in ("yes", "true", "1"):
        parts.append("TrustServerCertificate=yes")
    parts.append("Application Name=SGIC-DMS")
    return ";".join(parts)


def connect(sqlite_path, row_factory=sqlite3.Row, autocommit=False, database=None):
    """Open a connection to whichever database is configured."""
    if engine() == "sqlserver":
        try:
            import pyodbc
        except ImportError:
            raise RuntimeError("SQL Server mode needs pyodbc; run install_sqlserver_support.bat.")
        return MssqlConn(pyodbc.connect(mssql_connection_string(database), autocommit=autocommit, timeout=15))
    if engine() == "mysql":
        try:
            import pymysql
        except ImportError:
            raise RuntimeError("MySQL mode needs PyMySQL; run install_mysql_support.bat.")
        target_db = database or CONFIG["database"]
        try:
            raw = pymysql.connect(host=CONFIG["host"], port=int(CONFIG["port"]), user=CONFIG["mysql_user"],
                                  password=CONFIG["mysql_password"], database=target_db or None,
                                  charset=CONFIG["charset"], autocommit=autocommit, connect_timeout=15,
                                  cursorclass=pymysql.cursors.DictCursor)
        except Exception as exc:
            raise RuntimeError(f"Cannot connect to MySQL at {CONFIG['host']}:{CONFIG['port']} as {CONFIG['mysql_user']}: {exc}") from exc
        return MysqlConn(raw)
    conn = sqlite3.connect(sqlite_path, timeout=15)
    conn.row_factory = row_factory
    return conn

def is_mssql(conn):
    return getattr(conn, "is_mssql", False)


def is_mysql(conn):
    return getattr(conn, "is_mysql", False)


# ---------------------------------------------------------------------------------------------
# Schema helpers used by the application's start-up (init_db)
# ---------------------------------------------------------------------------------------------
def table_columns(conn, table):
    if is_mssql(conn):
        return [r[0] for r in conn.execute(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME=?", (table,)).fetchall()]
    if is_mysql(conn):
        return [r[0] for r in conn.execute(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=?", (table,)).fetchall()]
    return [c[1] for c in conn.execute(f"PRAGMA table_info({table})")]


def add_column(conn, table, column, decl):
    """decl is written SQLite-style, e.g. TEXT DEFAULT '' or INTEGER DEFAULT 0."""
    if is_mssql(conn):
        conn.execute_raw(f"ALTER TABLE dbo.[{table}] ADD [{column}] {sqlite_decl_to_tsql(decl)}")
    elif is_mysql(conn):
        # MySQL versions before 8.0.13 do not allow literal defaults on TEXT/BLOB columns.
        # The application's added TEXT columns are short labels (e.g. document_type/request_type).
        text_type = "VARCHAR(200)" if re.search(r"DEFAULT\s+''", decl, flags=re.I) else "LONGTEXT"
        mysql_decl = re.sub(r"\bTEXT\b", text_type, decl, flags=re.I)
        mysql_decl = re.sub(r"\bINTEGER\b", "INT", mysql_decl, flags=re.I)
        conn.execute_raw(f"ALTER TABLE `{table}` ADD COLUMN `{column}` {mysql_decl}")
    else:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def split_batches(script):
    """Split a T-SQL script on lines that contain only GO."""
    return [b.strip() for b in re.split(r"^\s*GO\s*$", script, flags=re.I | re.M) if b.strip()]


def init_schema(conn, sqlite_schema, log=None):
    if is_mysql(conn):
        with open(os.path.join(BASE, "schema_mysql.sql"), encoding="utf-8") as fh:
            conn.executescript(fh.read())
        return
    if not is_mssql(conn):
        conn.executescript(sqlite_schema)
        return
    with open(SCHEMA_SQL, encoding="utf-8") as fh:
        batches = split_batches(fh.read())
    for batch in batches:
        optional = batch.upper().startswith("CREATE OR ALTER VIEW")   # friendly views must never stop the server
        try:
            conn.execute_raw(batch)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            if not optional:
                raise
            if log:
                log.warning("Could not create a SQL Server view: %s", exc)
