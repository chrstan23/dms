"""Test MySQL connectivity and show SGIC DMS table counts without changing data."""
import dbcompat

if dbcompat.engine() != "mysql":
    raise SystemExit("database.ini must say engine = mysql")
conn = dbcompat.connect(None, row_factory=None)
try:
    print("Connected:", dbcompat.describe())
    print("Server:", conn.execute("SELECT VERSION()").fetchone()[0])
    print("Account:", conn.execute("SELECT CURRENT_USER()").fetchone()[0])
    for table in ("users", "documents", "existing_procedures", "revision_requests", "audit"):
        try:
            count = conn.execute(f"SELECT COUNT(*) FROM `{table}`").fetchone()[0]
            print(f"{table:22s} {count:>8} rows")
        except Exception as exc:
            print(f"{table:22s} ERROR: {exc}")
finally:
    conn.close()
print("\nConnection test finished. No application data was changed.")
