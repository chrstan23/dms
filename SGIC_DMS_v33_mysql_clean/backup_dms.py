"""Create a timestamped backup: the database + uploaded files + controlled PDFs/editable files.
Works with SQLite, SQL Server, and MySQL (as set in database.ini)."""
import os
import shutil
import sqlite3
import zipfile
import subprocess
from datetime import datetime

import dbcompat

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "sgic_dms.db")
UPLOADS = os.path.join(BASE, "uploads")
PDFS = os.path.join(BASE, "pdf_documents")
BACKUPS = os.path.join(BASE, "backups")
os.makedirs(BACKUPS, exist_ok=True)
stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
out = os.path.join(BACKUPS, f"sgic_dms_backup_{stamp}.zip")
db_file = None   # database file to put in the zip (None = it is kept by SQL Server itself)

if dbcompat.engine() == "mysql":
    import shutil as _shutil
    dump_tool = _shutil.which("mysqldump") or _shutil.which("mysqldump.exe")
    if not dump_tool:
        raise SystemExit("MySQL backup needs mysqldump.exe (install MySQL Server/client tools and add its bin folder to PATH). "
                         "No backup was made; install the tool and retry.")
    db_file = os.path.join(BACKUPS, f"SGIC_DMS_{stamp}.sql")
    env = os.environ.copy()
    env["MYSQL_PWD"] = dbcompat.CONFIG["mysql_password"]
    cmd = [dump_tool, "--host", dbcompat.CONFIG["host"], "--port", str(dbcompat.CONFIG["port"]),
           "--user", dbcompat.CONFIG["mysql_user"], "--single-transaction", "--routines", "--triggers",
           "--default-character-set=" + dbcompat.CONFIG["charset"], dbcompat.CONFIG["database"]]
    try:
        with open(db_file, "wb") as dump:
            subprocess.run(cmd, env=env, stdout=dump, stderr=subprocess.PIPE, check=True, timeout=300)
    except Exception as exc:
        try:
            if os.path.exists(db_file): os.remove(db_file)
        except OSError:
            pass
        detail = getattr(exc, "stderr", b"")
        if isinstance(detail, bytes): detail = detail.decode(errors="replace")
        raise SystemExit(f"MySQL dump failed: {detail or exc}")
elif dbcompat.engine() == "sqlserver":
    # SQL Server writes the .bak itself (it needs permission on the target folder).
    name = dbcompat.CONFIG["database"]
    conn = dbcompat.connect(None, row_factory=None, autocommit=True)
    raw = conn._raw

    def run_backup(path):
        cur = raw.cursor()
        cur.execute(f"BACKUP DATABASE [{name}] TO DISK = N'{path}' WITH INIT, FORMAT")
        while cur.nextset():   # BACKUP reports progress as extra result sets; it finishes when they are consumed
            pass

    try:
        bak = os.path.join(BACKUPS, f"SGIC_DMS_{stamp}.bak")
        try:
            run_backup(bak)
            db_file = bak
        except Exception as first:
            folder = conn.execute_raw("SELECT CAST(SERVERPROPERTY('InstanceDefaultBackupPath') AS NVARCHAR(500))").fetchone()[0]
            if not folder:
                raise first
            bak = os.path.join(folder, f"SGIC_DMS_{stamp}.bak")
            run_backup(bak)
            print(f"SQL Server could not write into the backups folder, so its own backup folder was used:\n  {bak}\n"
                  "(Give the 'SQL Server' service account write access to the backups folder to keep everything together.)")
    except Exception as exc:
        raise SystemExit(f"SQL Server backup failed: {exc}")
    finally:
        conn.close()
else:
    if not os.path.exists(DB):
        raise SystemExit("Database not found. Start SGIC DMS once to initialize it.")
    db_file = os.path.join(BACKUPS, f"sgic_dms_{stamp}.db")
    # SQLite backup API gives a consistent database copy while the server is running.
    with sqlite3.connect(DB) as source, sqlite3.connect(db_file) as target:
        source.backup(target)

try:
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        if db_file and os.path.dirname(db_file) == BACKUPS:
            z.write(db_file, "sgic_dms.db" if db_file.endswith(".db") else os.path.basename(db_file))
        if os.path.exists(os.path.join(BASE, ".secret_key")):
            z.write(os.path.join(BASE, ".secret_key"), ".secret_key")
        for folder in (UPLOADS, PDFS):
            if os.path.isdir(folder):
                for root, _, files in os.walk(folder):
                    for name in files:
                        full = os.path.join(root, name)
                        z.write(full, os.path.relpath(full, BASE))
    print(f"Backup created: {out}")
finally:
    if db_file and os.path.dirname(db_file) == BACKUPS and db_file.endswith(".db") and os.path.exists(db_file):
        os.remove(db_file)   # (a SQL Server .bak stays next to the zip as well)
# Keep the newest 30 ZIP backups. The matching SQL Server .bak / MySQL .sql dump is retained beside each ZIP.
old = sorted(f for f in os.listdir(BACKUPS) if f.startswith("sgic_dms_backup_") and f.endswith(".zip"))
for name in old[:-30]:
    os.remove(os.path.join(BACKUPS, name))
