"""Pre-deployment self-check for SGIC DMS. Run check_install.bat (it prints OK / WARN / FAIL)."""
import os
import socket
import sqlite3
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("SGIC_PORT") or os.environ.get("SGIC_DMS_PORT", "5000"))
bad = 0


def line(level, text):
    global bad
    if level == "FAIL":
        bad += 1
    print(f"[{level:4}] {text}")


line("OK" if sys.version_info >= (3, 9) else "FAIL", f"Python {sys.version.split()[0]} (3.9 or newer needed)")
for mod in ("flask", "waitress"):
    try:
        __import__(mod)
        try:
            from importlib.metadata import version
            line("OK", f"{mod} {version(mod)} installed")
        except Exception:
            line("OK", f"{mod} installed")
    except ImportError:
        line("FAIL", f"{mod} is not installed - run install_server.bat")
for name in ("app.py", "serve.py", "dbcompat.py", "database.ini", "background.png", "requirements.txt"):
    line("OK" if os.path.exists(os.path.join(BASE, name)) else "FAIL", f"{name} present")
try:
    probe = os.path.join(BASE, ".write_test")
    open(probe, "w").write("x")
    os.remove(probe)
    line("OK", "program folder is writable")
except OSError:
    line("FAIL", "program folder is NOT writable (database, uploads and PDFs are saved here)")
if BASE.startswith("\\\\"):
    line("WARN", "running from a network share - move to a local folder such as C:\\SGIC_DMS (SQLite is not safe on shares)")
if len(BASE) > 100:
    line("WARN", f"folder path is {len(BASE)} characters long - a short folder such as C:\\SGIC_DMS is safer")
s = socket.socket()
s.settimeout(1)
if s.connect_ex(("127.0.0.1", PORT)) == 0:
    line("WARN", f"port {PORT} is already in use (is the server already running?)")
else:
    line("OK", f"port {PORT} is free")
s.close()
import dbcompat
if dbcompat.engine() == "sqlserver":
    try:
        __import__("pyodbc")
        line("OK", f"database engine: {dbcompat.describe()}  ")
    except ImportError:
        line("FAIL", "database.ini says SQL Server but pyodbc is not installed - run install_sqlserver_support.bat")
elif dbcompat.engine() == "mysql":
    try:
        __import__("pymysql")
        line("OK", f"database engine: {dbcompat.describe()}  (run check_mysql.bat to test the connection)")
    except ImportError:
        line("FAIL", "database.ini says MySQL but PyMySQL is not installed - run install_mysql_support.bat")
else:
    line("OK", "database engine: SQLite file (sgic_dms.db)")
db = os.path.join(BASE, "sgic_dms.db")
if dbcompat.engine() == "sqlite" and os.path.exists(db):
    try:
        c = sqlite3.connect(db)
        n = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        must = c.execute("SELECT COUNT(*) FROM users WHERE must_change=1").fetchone()[0]
        line("OK", f"database opens ({n} user accounts)")
        if must:
            line("WARN", f"{must} account(s) still have a temporary/demo password and must change it at next login")
    except sqlite3.Error as e:
        line("FAIL", f"database problem: {e}")
elif dbcompat.engine() == "mysql":
    line("OK", "SQLite source file check skipped because MySQL is selected")
else:
    line("OK", "no database yet - it is created on the first start")
print()
print("RESULT: " + ("NOT READY - fix the FAIL lines above." if bad else "ready to start (read any WARN lines)."))
