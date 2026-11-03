"""Set a user's DMS password (works with SQLite, SQL Server, or MySQL, as set in database.ini)."""
import getpass
import os

from werkzeug.security import generate_password_hash

import dbcompat

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "sgic_dms.db")
if dbcompat.engine() == "sqlite" and not os.path.exists(DB):
    raise SystemExit("Database not found. Start SGIC DMS once before changing passwords.")
conn = dbcompat.connect(DB, row_factory=None)
try:
    users = [r[0] for r in conn.execute("SELECT username FROM users ORDER BY username").fetchall()]
    print("Existing users:", ", ".join(users))
    username = input("Username to update: ").strip()
    if username not in users:
        raise SystemExit("That username does not exist.")
    password = getpass.getpass("New password (minimum 12 characters recommended): ")
    confirm = getpass.getpass("Confirm new password: ")
    if len(password) < 8:
        raise SystemExit("Password must be at least 8 characters.")
    if password != confirm:
        raise SystemExit("Passwords do not match.")
    conn.execute("UPDATE users SET password_hash=?, must_change=0 WHERE username=?",
                 (generate_password_hash(password), username))
    conn.commit()
finally:
    conn.close()
print(f"Password updated for {username}. Store it securely.")
