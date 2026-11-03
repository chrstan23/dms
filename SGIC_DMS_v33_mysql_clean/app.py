"""
SGIC Document Management System - Python (Flask + SQLite) version.

Run:
    pip install flask
    python app.py
Then open http://<host-ip>:5000

Demo accounts (change them before real use):
    admin/admin123  dcc/dcc123  author/author123
    requestor/requestor123  admr/admr123  agmr/agmr123
"""
import csv
import dbcompat
import io
import json
import os
import re
import secrets
import shutil
import sqlite3
import time
import logging
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from urllib.parse import urlparse

from flask import (Flask, Response, abort, flash, g, redirect, render_template,
                   request, send_file, session, url_for)
from jinja2 import DictLoader
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "sgic_dms.db")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
# PDF copies of controlled documents are kept as real files in this folder, named by
# Document No. (e.g. PR-QA01000-00.pdf). Replaced revisions are kept in pdf_documents/archive.
PDF_DIR = os.path.join(os.path.dirname(UPLOAD_DIR), "pdf_documents")
os.makedirs(os.path.join(PDF_DIR, "archive"), exist_ok=True)

DEFAULT_USERS = [
    ("admin", "admin123", "Administrator"),
    ("dcc", "dcc123", "DCC"),
    ("author", "author123", "Author"),
    ("requestor", "requestor123", "Requestor"),
    ("admr", "admr123", "ADMR"),      # Assistant Department Manager
    ("agmr", "agmr123", "AGMR"),      # Assistant General Manager - final approval
]
DEPARTMENTS = ["MANAGEMENT", "PCBA-QUALITY ASSURANCE", "PCBA-PRODUCTION", "PCBA-IE", "ATM-QUALITY ASSURANCE",
               "ATM-PRODUCTION", "ATM-IE", "DOCUMENT CONTROL CENTER", "ENVIRONMENT", "ESM", "FACILITIES",
               "GENERAL AFFAIRS", "IMPORT-EXPORT", "MATERIAL CONTROL", "PROCESS", "PURCHASING", "QUALITY CONTROL",
               "TRAINING", "WAREHOUSE"]
SECTIONS = ["MANAGEMENT", "PCBA-QPE", "PCBA-PRODUCTION", "PCBA-IE", "ATM-QPE",
               "ATM-PRODUCTION", "ATM-IE", "EME", "ESM"]
DOC_TYPES = ["INSPECTION GUIDE", "INTEGRATED MANUAL", "PROCEDURE TEMPLATE", "PROCESS FLOWCHART", "VISUAL GUIDE",
             "WORK INSTRUCTION", "EXTERNAL DOCUMENTS PCB", "EXTERNAL DOCUMENTS ATM"]
# Old labels that clearly map to a new one (everything else is matched ignoring upper/lower case).
LEGACY_ALIASES = {"dcc": "DOCUMENT CONTROL CENTER"}
EXISTING_STATUSES = ["Effective", "Under Review", "Obsolete"]

CREATORS = ["Author", "Requestor", "DCC", "Administrator", "ADMR"]
VERSION = "1.1.0"
PROCESSORS = ["DCC", "Administrator"]
VIEW_ALL = ["DCC", "ADMR", "Administrator"]
# AGMR (highest authority) only gets Dashboard, Revision Requests and the Document Master List,
# but must see every request to give the final approval.
REQUEST_VIEWERS = VIEW_ALL + ["AGMR"]
EDITABLE_EXT = re.compile(r"\.(doc|docx|xls|xlsx|ppt|pptx|odt|ods|odp|txt)$", re.I)
OPEN_STATUSES_CLOSED = ("Released", "Rejected")

app = Flask(__name__)
key_file = os.path.join(BASE_DIR, ".secret_key")
if not os.path.exists(key_file):
    with open(key_file, "w") as fh:
        fh.write(secrets.token_hex(32))
with open(key_file) as fh:
    app.secret_key = fh.read().strip()
try:
    os.chmod(key_file, 0o600)
except OSError:
    pass
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  PERMANENT_SESSION_LIFETIME=timedelta(hours=8))   # idle logout after 8 hours
app.config["MAX_CONTENT_LENGTH"] = None  # no application-set file size limit


# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
    username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, role TEXT NOT NULL,
    full_name TEXT DEFAULT '', section TEXT DEFAULT '', must_change INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS documents(
    id TEXT PRIMARY KEY, doc_no TEXT UNIQUE, title TEXT, department TEXT,
    revision TEXT, status TEXT, created_by TEXT, date_created TEXT,
    revision_history TEXT DEFAULT '[]', current_attachment TEXT, document_type TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS existing_procedures(
    id TEXT PRIMARY KEY, doc_no TEXT UNIQUE COLLATE NOCASE, title TEXT,
    department TEXT, document_type TEXT, revision TEXT, effective_date TEXT,
    status TEXT, file_reference TEXT, remarks TEXT, registered_by TEXT,
    registered_at TEXT, revision_in_progress INTEGER DEFAULT 0,
    last_revision_reason TEXT, last_revision_released_by TEXT,
    last_revision_attachment TEXT);
CREATE TABLE IF NOT EXISTS revision_requests(
    id TEXT PRIMARY KEY, source_type TEXT, source_id TEXT, doc_no TEXT,
    title TEXT, department TEXT, current_revision TEXT, proposed_revision TEXT,
    reason TEXT, status TEXT, requested_by TEXT, requested_at TEXT,
    updated_at TEXT, attachment TEXT, attachment_added_by TEXT,
    attachment_added_at TEXT, comments TEXT DEFAULT '[]',
    history TEXT DEFAULT '[]', request_type TEXT DEFAULT 'revision');
CREATE TABLE IF NOT EXISTS audit(
    id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, activity TEXT,
    timestamp TEXT);
"""


def db():
    if "db" not in g:
        g.db = dbcompat.connect(DB_PATH)   # SQLite file or SQL Server, as set in database.ini
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn:
        conn.close()


PENDING_PREFIX = "PENDING-"   # temporary Document No. until DCC assigns the real one
MASTER_STATUS = {"Effective": "Approved", "Under Review": "Under Review", "Obsolete": "Obsolete"}


def master_insert(conn, doc_no, title, department, revision, ep_status, by, ts, attachment=None, document_type=""):
    """Record a registered existing procedure in the Document Master List."""
    doc_id = f"{int(time.time() * 1000)}-{secrets.token_hex(3)}"
    conn.execute(
        "INSERT INTO documents(id, doc_no, title, department, revision, status, created_by, date_created,"
        " current_attachment, document_type) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (doc_id, doc_no, title, department, revision, MASTER_STATUS.get(ep_status, ep_status),
         by, ts, json.dumps(attachment) if attachment else None, document_type or ""))
    return doc_id


def _clean_part(text, limit):
    text = re.sub(r'[\\/:*?"<>|]+', " ", str(text or ""))
    return re.sub(r"\s+", " ", text).strip(" .")[:limit]


def pdf_basename(doc_no, title, revision):
    """PDF file name: <Document No>_<Title of the Document>_<Revision>.pdf"""
    parts = [_clean_part(doc_no, 60), _clean_part(title, 80), _clean_part(revision, 20)]
    return "_".join(x for x in parts if x) + ".pdf"


def lp(path):
    """Windows: very long paths (over ~200 characters) are used in the extended-length form so the
    260-character limit does not break saving/reading a PDF. No effect on other systems or short paths."""
    if os.name != "nt" or len(path) < 200 or path.startswith("\\\\?\\"):
        return path
    full = os.path.normpath(os.path.abspath(path))
    return ("\\\\?\\UNC\\" + full[2:]) if full.startswith("\\\\") else ("\\\\?\\" + full)


def clean_upload_name(name):
    """The file name exactly as chosen, minus characters Windows does not allow in file names."""
    name = os.path.basename(str(name or "").replace("\\", "/"))
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", name).strip(" .")
    base, ext = os.path.splitext(name)
    return (base[:120] + ext) if name else ""


def unique_upload_name(name):
    """Name used inside the uploads folder: the original name; '(2)', '(3)'... only if it is already taken."""
    name = clean_upload_name(name) or "attachment"
    if name.lower().startswith("tmp-"):
        name = "file-" + name
    base, ext = os.path.splitext(name)
    candidate, n = name, 1
    while os.path.exists(lp(os.path.join(UPLOAD_DIR, candidate))):
        n += 1
        candidate = f"{base} ({n}){ext}"
    return candidate


def _folder_part(text, fallback):
    return _clean_part(text, 60) or fallback


def pdf_rel_path(doc_type, department, doc_no, title, revision):
    """Relative location of a document's PDF: <Document Type>/<Department>/<DocNo>_<Title>_<Rev>.pdf"""
    return "/".join([_folder_part(doc_type, "Unclassified"), _folder_part(department, "No Department"),
                     pdf_basename(doc_no, title, revision)])


def att_path(att):
    rel = att.get("pdfFile")
    if rel:
        root = os.path.normpath(PDF_DIR)
        full = os.path.normpath(os.path.join(root, *[x for x in re.split(r"[\\/]+", rel) if x not in ("", ".", "..")]))
        return lp(full if full.startswith(root + os.sep) else os.path.join(root, "__invalid__"))
    return lp(os.path.join(UPLOAD_DIR, os.path.basename(att.get("blobKey", ""))))


def archive_pdf(att):
    """Move a document's PDF into the 'archive' folder next to it (kept for history)."""
    if not att or not att.get("pdfFile"):
        return
    src = att_path(att)
    if os.path.exists(src):
        arch = os.path.join(os.path.dirname(src), "archive")
        os.makedirs(arch, exist_ok=True)
        dst = os.path.join(arch, os.path.basename(src))
        if os.path.exists(dst):
            dst = dst[:-4] + f"_{int(time.time())}.pdf"
        shutil.move(src, dst)


def put_pdf_in_folder(doc_no, title, revision, doc_type, department, src_path, by, role,
                      previous=None, move=False):
    """Save a PDF as pdf_documents/<Document Type>/<Department>/<DocNo>_<Title>_<Revision>.pdf.
    The document's previous PDF (an older revision) moves to that folder's 'archive' subfolder."""
    rel = pdf_rel_path(doc_type, department, doc_no, title, revision)
    target = att_path({"pdfFile": rel})
    os.makedirs(os.path.dirname(target), exist_ok=True)
    archive_pdf(previous)
    if os.path.exists(target):
        archive_pdf({"pdfFile": rel})
    (shutil.move if move else shutil.copyfile)(src_path, target)
    return {"kind": "master", "name": os.path.basename(target), "type": "application/pdf",
            "size": os.path.getsize(target), "pdfFile": rel, "by": by, "role": role, "at": now()}


def save_master_pdf(upload, by, role, doc_no, title, revision, doc_type="", department=""):
    """Validate an uploaded PDF and store it in the PDF folder. Returns (attachment, error)."""
    if not (upload.filename or "").lower().endswith(".pdf"):
        return None, "Only PDF files can be attached for viewing."
    tmp = os.path.join(UPLOAD_DIR, f"tmp-{secrets.token_hex(6)}")
    upload.save(tmp)
    with open(tmp, "rb") as fh:
        ok = fh.read(5).startswith(b"%PDF")
    if not ok:
        os.remove(tmp)
        return None, "That file is not a valid PDF."
    try:
        return put_pdf_in_folder(doc_no, title, revision, doc_type, department, tmp, by, role, move=True), None
    except OSError as e:
        if os.path.exists(tmp):
            os.remove(tmp)
        return None, (f"The PDF could not be saved ({e.strerror or e}). The folder path is probably too long or not "
                      "writable. Move the program to a short local folder such as C:\\SGIC_DMS and try again.")


def check_upload(upload, kind):
    """Return an error message if this uploaded file is not acceptable for its purpose, else None."""
    name = upload.filename or ""
    if kind == "submission":
        if not name.lower().endswith(".pdf"):
            return "Please submit your completed document as a PDF file."
        head = upload.stream.read(5)
        upload.stream.seek(0)
        if not head.startswith(b"%PDF"):
            return "That file is not a valid PDF. Please export or save your document as PDF and try again."
    elif kind in ("template", "editable"):
        m_ext = RELEASE_EXT.search(name)
        if not m_ext:
            return (f"'{name}': only editable Microsoft Word (.docx / .doc) or Excel (.xlsx / .xls) files "
                    "can be attached here.")
        head = upload.stream.read(8)
        upload.stream.seek(0)
        old_format = m_ext.group(1).lower() in ("doc", "xls")   # old binary Office files
        if not (head.startswith(b"\xd0\xcf\x11\xe0") if old_format else head.startswith(b"PK\x03\x04")):
            return (f"'{name}' is not a valid Word or Excel file. Save it again from Microsoft Office "
                    "(.docx / .xlsx) and try again.")
    elif not REVIEW_EXT.search(name):
        return "Please attach an editable Word, Excel, PowerPoint, OpenDocument or TXT file (PDF is also accepted)."
    return None


def file_label(roles, i):
    if i < len(roles):
        return roles[i] if roles[i] in FILE_LABELS else "Other"
    return ""


def editable_dir_rel(doc):
    """Where a document's final editable files live: <Type>/<Department>/Editable/<DocNo> (inside pdf_documents)."""
    return "/".join([_folder_part(doc.get("document_type"), "Unclassified"),
                     _folder_part(doc.get("department"), "No Department"), "Editable",
                     _folder_part(doc.get("doc_no"), "document")])


def _folder_of(rel_dir):
    return os.path.dirname(att_path({"pdfFile": rel_dir + "/_"}))


def store_editable_files(doc, uploads, roles, user, ts):
    """Save the requestor's final editable files in the document's department folder (same names as chosen).
    Files from an earlier revision are moved to an 'archive' subfolder first."""
    rel_dir = editable_dir_rel(doc)
    folder = _folder_of(rel_dir)
    os.makedirs(lp(folder), exist_ok=True)
    old = [f for f in os.listdir(lp(folder)) if os.path.isfile(os.path.join(lp(folder), f))]
    if old:
        arch = os.path.join(folder, "archive", datetime.now().strftime("%Y-%m-%d %H-%M-%S"))
        os.makedirs(lp(arch), exist_ok=True)
        for f in old:
            shutil.move(lp(os.path.join(folder, f)), lp(os.path.join(arch, f)))
    saved = []
    for i, up in enumerate(uploads):
        name = clean_upload_name(up.filename) or "attachment"
        base, ext = os.path.splitext(name)
        candidate, n = name, 1
        while os.path.exists(lp(os.path.join(folder, candidate))):
            n += 1
            candidate = f"{base} ({n}){ext}"
        up.save(lp(os.path.join(folder, candidate)))
        saved.append({"kind": "editable", "name": name, "type": up.mimetype or "application/octet-stream",
                      "size": os.path.getsize(lp(os.path.join(folder, candidate))),
                      "pdfFile": rel_dir + "/" + candidate, "label": file_label(roles, i),
                      "by": user["username"], "role": user["role"], "at": ts})
    return saved


def move_editable_dir(conn, doc_id, old_rel, new_rel):
    """The document was re-classified: move its editable files and fix the stored paths."""
    if old_rel == new_rel:
        return
    src, dst = _folder_of(old_rel), _folder_of(new_rel)
    if os.path.isdir(lp(src)) and not os.path.exists(lp(dst)):
        os.makedirs(lp(os.path.dirname(dst)), exist_ok=True)
        shutil.move(lp(src), lp(dst))
        for row in conn.execute("SELECT id, attachment FROM revision_requests WHERE source_id=?", (doc_id,)).fetchall():
            try:
                files = json.loads(row[1] or "[]")
            except ValueError:
                continue
            changed = False
            for f in files if isinstance(files, list) else []:
                if str(f.get("pdfFile", "")).startswith(old_rel + "/"):
                    f["pdfFile"] = new_rel + f["pdfFile"][len(old_rel):]
                    changed = True
            if changed:
                conn.execute("UPDATE revision_requests SET attachment=? WHERE id=?", (json.dumps(files), row[0]))


def refile_document_pdf(conn, doc):
    """Put a document's current PDF in the right Type/Department folder with the right name."""
    try:
        att = json.loads(doc["current_attachment"] or "null")
    except ValueError:
        return
    if not att or not str(att.get("name", "")).lower().endswith(".pdf") or str(doc["doc_no"]).startswith(PENDING_PREFIX):
        return
    want = pdf_rel_path(doc["document_type"], doc["department"], doc["doc_no"], doc["title"], doc["revision"])
    if att.get("pdfFile"):
        if att["pdfFile"] == want:
            return
        src, dst = att_path(att), att_path({"pdfFile": want})
        if os.path.exists(src) and not os.path.exists(dst):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(src, dst)
            att.update(name=os.path.basename(dst), pdfFile=want)
            conn.execute("UPDATE documents SET current_attachment=? WHERE id=?", (json.dumps(att), doc["id"]))
    else:
        src = att_path(att)
        if os.path.exists(src):
            new = put_pdf_in_folder(doc["doc_no"], doc["title"], doc["revision"], doc["document_type"],
                                    doc["department"], src, att.get("by", "system"), att.get("role", ""))
            conn.execute("UPDATE documents SET current_attachment=? WHERE id=?", (json.dumps(new), doc["id"]))


def pdf_attachment(doc):
    """The document's current attachment if it is a PDF that exists on disk, else None."""
    try:
        att = json.loads(doc.get("current_attachment") or "null")
    except ValueError:
        return None
    if not att or not str(att.get("name", "")).lower().endswith(".pdf"):
        return None
    if not os.path.exists(att_path(att)):
        return None
    return att


# Password required to DOWNLOAD a PDF (viewing needs no password).
# Stored as a hash. To change it, set the SGIC_PDF_PASSWORD environment variable.
PDF_DOWNLOAD_HASH = "pbkdf2:sha256:1000000$N7xucpdLjFvouSZo$e282543a93f978ff443edc4397d65e9e9eb6db4348130ef2d423009e00b33f5b"
_pdf_fails = {}


def pdf_password_ok(candidate):
    env = os.environ.get("SGIC_PDF_PASSWORD")
    if env:
        return secrets.compare_digest(candidate.encode(), env.encode())
    return check_password_hash(PDF_DOWNLOAD_HASH, candidate)


def pdf_locked(username):
    recent = [t for t in _pdf_fails.get(username, []) if time.time() - t < 600]
    _pdf_fails[username] = recent
    return len(recent) >= 5


def pdf_attachment(doc):
    """The document's current attachment if it is a PDF that exists on disk, else None."""
    try:
        att = json.loads(doc.get("current_attachment") or "null")
    except ValueError:
        return None
    if not att or not str(att.get("name", "")).lower().endswith(".pdf"):
        return None
    if not os.path.exists(os.path.join(UPLOAD_DIR, os.path.basename(att.get("blobKey", "")))):
        return None
    return att


def init_db():
    conn = dbcompat.connect(DB_PATH, row_factory=None)
    dbcompat.init_schema(conn, SCHEMA, log)
    if "request_type" not in dbcompat.table_columns(conn, "revision_requests"):  # upgrade older databases
        dbcompat.add_column(conn, "revision_requests", "request_type", "TEXT DEFAULT 'revision'")
    if "document_type" not in dbcompat.table_columns(conn, "documents"):
        dbcompat.add_column(conn, "documents", "document_type", "TEXT DEFAULT ''")
    # Existing procedures registered earlier must also appear in the Document Master List.
    for _id, dno, title, dept, rev, st, by, at, dtype in conn.execute(
            "SELECT id, doc_no, title, department, revision, status, registered_by, registered_at, document_type "
            "FROM existing_procedures").fetchall():
        if not conn.execute("SELECT 1 FROM documents WHERE doc_no=? COLLATE NOCASE", (dno,)).fetchone():
            master_insert(conn, dno, title, dept, rev, st, by or "system", at or now(), None, dtype)
    conn.execute("UPDATE documents SET document_type=(SELECT ep.document_type FROM existing_procedures ep "
                 "WHERE ep.doc_no=documents.doc_no COLLATE NOCASE LIMIT 1) "
                 "WHERE COALESCE(document_type,'')='' AND EXISTS (SELECT 1 FROM existing_procedures ep "
                 "WHERE ep.doc_no=documents.doc_no COLLATE NOCASE)")
    for table, column, allowed in (("documents", "department", DEPARTMENTS), ("documents", "document_type", DOC_TYPES),
                                   ("existing_procedures", "department", DEPARTMENTS),
                                   ("existing_procedures", "document_type", DOC_TYPES),
                                   ("revision_requests", "department", DEPARTMENTS)):
        canon = {x.lower(): x for x in allowed}
        for (val,) in conn.execute(f"SELECT DISTINCT {column} FROM {table}").fetchall():
            new = canon.get(str(val or "").strip().lower()) or (
                LEGACY_ALIASES.get(str(val or "").strip().lower()) if column == "department" else None)
            if new and new != val:
                conn.execute(f"UPDATE {table} SET {column}=? WHERE {column}=?", (new, val))
    conn.row_factory = sqlite3.Row
    for d in conn.execute("SELECT * FROM documents WHERE current_attachment IS NOT NULL").fetchall():
        refile_document_pdf(conn, d)
    conn.row_factory = None
    conn.execute("UPDATE users SET role='ADMR' WHERE role='Approver'")
    conn.execute("UPDATE revision_requests SET status='Pending ADMR' WHERE status='Pending Approver'")
    ucols = dbcompat.table_columns(conn, "users")
    for col in ("full_name", "section"):
        if col not in ucols:  # upgrade older databases
            dbcompat.add_column(conn, "users", col, "TEXT DEFAULT ''")
    if "must_change" not in dbcompat.table_columns(conn, "users"):
        dbcompat.add_column(conn, "users", "must_change", "INTEGER DEFAULT 0")
    # Demo accounts are created only on the very first start, so removed accounts stay removed.
    if not conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        for username, password, role in DEFAULT_USERS:
            conn.execute(
                "INSERT INTO users(username, password_hash, role, must_change) VALUES (?,?,?,1)",
                (username, generate_password_hash(password), role))
    # Any account still using a well-known demo password must choose a new one at its next login.
    for demo_user, demo_pw in [(u, pw) for u, pw, _r in DEFAULT_USERS] + [("approver", "approver123")]:
        row = conn.execute("SELECT password_hash FROM users WHERE username=?", (demo_user,)).fetchone()
        if row and check_password_hash(row[0], demo_pw):
            conn.execute("UPDATE users SET must_change=1 WHERE username=?", (demo_user,))
    conn.commit()
    conn.close()


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log_activity(activity):
    if "username" in session:
        db().execute("INSERT INTO audit(username, activity, timestamp) VALUES (?,?,?)",
                     (session["username"], activity, now()))
        db().commit()


def rows(sql, args=()):
    return [dict(r) for r in db().execute(sql, args).fetchall()]


def one(sql, args=()):
    r = db().execute(sql, args).fetchone()
    return dict(r) if r else None


def load_request(row):
    if row:
        for f in ("comments", "history"):
            row[f] = json.loads(row[f]) if row[f] else []
        files = json.loads(row["attachment"]) if row["attachment"] else []
        if isinstance(files, dict):  # older single-attachment format
            files = [dict(files, kind="template", role="DCC",
                          by=row.get("attachment_added_by"), at=row.get("attachment_added_at"))]
        row["files"] = files
        row["doc_no_pending"] = str(row.get("doc_no") or "").startswith(PENDING_PREFIX)
    return row


def requests_visible(user, q=""):
    items = [load_request(r) for r in rows(
        "SELECT * FROM revision_requests ORDER BY requested_at DESC, id DESC")]
    items = [r for r in items if user["role"] in REQUEST_VIEWERS or r["requested_by"] == user["username"]]
    if q:
        q = q.lower()
        items = [r for r in items if any(
            q in str(r[k] or "").lower() for k in
            ("id", "doc_no", "title", "department", "requested_by", "status", "reason"))]
    return items


def save_request(r):
    db().execute(
        """UPDATE revision_requests SET status=?, updated_at=?, attachment=?,
           comments=?, history=? WHERE id=?""",
        (r["status"], r.get("updated_at"), json.dumps(r["files"]) if r["files"] else None,
         json.dumps(r["comments"]), json.dumps(r["history"]), r["id"]))
    db().commit()


# --------------------------------------------------------------------------
# Helpers / auth
# --------------------------------------------------------------------------
@app.before_request
def check_account_still_exists():
    """A removed (or role-changed) account is logged out / updated on its next request."""
    g.account = None
    if "username" in session:
        acc = one("SELECT username, role, full_name, section, must_change FROM users WHERE username=?",
                  (session["username"],))
        if not acc:
            session.clear()
        else:
            session["role"] = acc["role"]
            g.account = acc
            if acc["must_change"] and request.path not in ("/account/password", "/logout", "/style.css",
                                                           "/background.png", "/healthz"):
                flash("Please set your own password before continuing.", "error")
                return redirect(url_for("change_password_page"))


def current_user():
    if "username" in session:
        acc = getattr(g, "account", None) or {}
        return {"username": session["username"], "role": session["role"],
                "full_name": acc.get("full_name") or "", "section": acc.get("section") or ""}
    return None


def require(*roles):
    """Decorator: login required, optionally limited to certain roles."""
    def wrap(fn):
        def inner(*a, **kw):
            user = current_user()
            if not user:
                return redirect(url_for("login"))
            if roles and user["role"] not in roles:
                flash("You do not have permission to access this section.", "error")
                return redirect(url_for("dashboard"))
            return fn(*a, **kw)
        inner.__name__ = fn.__name__
        return inner
    return wrap


@app.context_processor
def inject():
    return {"user": current_user(), "VERSION": VERSION, "CREATORS": CREATORS, "PROCESSORS": PROCESSORS,
            "DEPARTMENTS": DEPARTMENTS, "DOC_TYPES": DOC_TYPES,
            "EXISTING_STATUSES": EXISTING_STATUSES, "VIEW_ALL": VIEW_ALL}


STATUS_LABELS = {
    "Pending DCC Check": "For DCC Check",
    "Released to Requestor": "Released to Requestor",
    "Pending ADMR": "For ADMR Approval",
    "Pending AGMR": "For AGMR Final Approval",
    "Pending Editable Files": "Waiting for Editable Files",
    "Completed": "Completed",
    "Rejected": "Rejected – For Revision",
    "Approved": "Approved",
    "Returned to Requestor": "Returned by DCC",
    "Returned to Author": "Returned by DCC",
    "Approved - Pending Release": "Approved – For Release",
    "Released": "Released",
}
STEPS = ["Requested", "DCC Check", "Released to Requestor", "ADMR Approval", "AGMR Final Approval",
         "Editable Files", "Completed"]
STAGE = {"Pending DCC Check": 1, "Released to Requestor": 2, "Pending ADMR": 3, "Pending AGMR": 4,
         "Pending Editable Files": 5, "Completed": 7, "Approved": 7, "Released": 7}


# Progress colours: green = done, yellow = ongoing, grey = not finished.
STATUS_GROUP = {
    "Approved": "done", "Released": "done", "Effective": "done", "Completed": "done",
    "Pending DCC Check": "ongoing", "Released to Requestor": "ongoing", "Pending ADMR": "ongoing", "Pending AGMR": "ongoing", "Pending Editable Files": "ongoing",
    "Approved - Pending Release": "ongoing", "Under Review": "ongoing",
    "Draft": "notfinished", "Rejected": "notfinished", "Returned to Requestor": "notfinished",
    "Returned to Author": "notfinished", "Correction Required": "notfinished", "Obsolete": "notfinished",
}


@app.template_filter("group")
def group_filter(status):
    return STATUS_GROUP.get(status or "", "notfinished")


@app.template_filter("badge")
def badge_filter(status):
    return re.sub(r"[^A-Za-z0-9]+", "-", status or "").strip("-")


@app.template_filter("label")
def label_filter(status):
    return STATUS_LABELS.get(status, status)


def request_stage(r):
    """Progress position for the tracker (None for returned/rejected requests)."""
    return STAGE.get(r["status"])


def latest_revision_status():
    """doc_no -> status of the most recent revision request for that document."""
    out = {}
    for r in rows("SELECT doc_no, status FROM revision_requests ORDER BY requested_at, id"):
        out[r["doc_no"]] = r["status"]
    return out


@app.template_filter("fmt")
def fmt_filter(value):
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return value or ""


def next_revision(current):
    rev = str(current or "00").strip()
    m = re.match(r"^(.*?)(\d+)(\s*)$", rev)
    if m:
        return m.group(1) + str(int(m.group(2)) + 1).zfill(len(m.group(2))) + m.group(3)
    return rev + " 01" if rev else "01"


def generate_doc_no():
    highest = 0
    for d in rows("SELECT doc_no FROM documents"):
        m = re.match(r"^DOC-(\d+)$", d["doc_no"])
        if m:
            highest = max(highest, int(m.group(1)))
    return f"DOC-{highest + 1:05d}"


def _squash(text):
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def search_filter(items, keys, q):
    """Every word typed must match. Document No. matches rank first (starts with, then contains,
    ignoring dashes/spaces so 'PR-EM', 'pr em' and 'prem' all find PR-EM01000-00); other fields rank last."""
    terms = [t for t in (q or "").lower().split() if t]
    if not terms:
        return list(items)
    primary = "doc_no" if "doc_no" in keys else keys[0]
    scored = []
    for pos, item in enumerate(items):
        dn = str(item.get(primary) or "").lower()
        dn_sq = _squash(dn)
        total = 0
        for t in terms:
            sq = _squash(t)
            if dn.startswith(t) or (sq and dn_sq.startswith(sq)):
                best = 0
            elif t in dn or (sq and sq in dn_sq):
                best = 1
            elif any(t in str(item.get(k) or "").lower() for k in keys if k != primary):
                best = 2
            else:
                total = None
                break
            total += best
        if total is not None:
            scored.append((total, pos, item))
    scored.sort(key=lambda x: (x[0], x[1]))
    return [x[2] for x in scored]


FILE_KINDS = {"template": "DCC editable file",
              "submission": "Requestor's completed document",
              "review": "Management's revision file",
              "editable": "Final editable file"}
FILE_FOR_ACTION = {"release": "template", "submitManagement": "submission", "approve": "review",
                   "finalApprove": "review", "reject": "review", "sendEditable": "editable"}
FILE_LABELS = ("Main procedure", "Mother procedure", "Form", "Other")
MAX_FILES = 20
HOLD_STATUSES = ("Pending DCC Check", "Returned to Requestor", "Returned to Author")
RELEASE_EXT = re.compile(r"\.(docx|doc|xlsx|xls)$", re.I)   # editable Word / Excel files (release and final editable files)
REVIEW_EXT = re.compile(r"\.(doc|docx|xls|xlsx|ppt|pptx|odt|ods|odp|txt|pdf)$", re.I)


def available_actions(r, user):
    """Workflow: Requestor asks -> DCC releases editable file -> Requestor completes and
    submits -> ADMR approves (forwards) or rejects -> AGMR gives the final approval or rejects."""
    role, st, acts = user["role"], r["status"], []
    if role == "DCC" and st == "Pending DCC Check":
        acts += [("release", "Accept & Assign Document No." if r.get("request_type") == "new"
                  else "Release to Requestor", "success"),
                 ("returnAuthor", "Return to Requestor", "warning")]
    if role == "ADMR" and st == "Pending ADMR":
        acts += [("approve", "Approve & Send to AGMR", "success"), ("reject", "Reject", "danger")]
    if role == "AGMR" and st == "Pending AGMR":
        acts += [("finalApprove", "Final Approve", "success"), ("reject", "Reject", "danger")]
    if role == "DCC" and st == "Pending Editable Files":
        acts += [("closeNoEditable", "Close without editable files", "warning")]
    if r["requested_by"] == user["username"]:
        if st == "Released to Requestor":
            acts += [("submitManagement", "Submit to Management for Approval", "success")]
        if st == "Rejected":
            acts += [("submitManagement", "Resubmit to Management", "success")]
        if st == "Pending Editable Files":
            acts += [("sendEditable", "Send Editable Files", "success")]
        if st in ("Returned to Requestor", "Returned to Author"):
            acts += [("resubmit", "Resubmit to DCC", "success")]
    return acts


def takes_file(acts):
    return any(a[0] in FILE_FOR_ACTION for a in acts)


def may_download(r, f, user):
    if user["role"] in REQUEST_VIEWERS:
        return True
    if r["requested_by"] != user["username"]:
        return False
    return not (f.get("kind") == "template" and r["status"] in HOLD_STATUSES)


app.jinja_env.globals.update(available_actions=available_actions,
                             may_download=may_download, takes_file=takes_file,
                             FILE_KINDS=FILE_KINDS,
                             stage=request_stage, STEPS=STEPS)


# --------------------------------------------------------------------------
# Auth routes
# --------------------------------------------------------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        key = (request.remote_addr or "?", username.lower())
        if login_locked(key):
            flash("Too many failed attempts. Please wait 10 minutes and try again.", "error")
            return render_template("login.html")
        u = one("SELECT * FROM users WHERE username=?", (username,))
        if u and check_password_hash(u["password_hash"], request.form.get("password", "")):
            _login_fails.pop(key, None)
            session.clear()
            session.permanent = True
            session["username"], session["role"] = u["username"], u["role"]
            log_activity("Logged In")
            log.info("Login ok: %s (%s) from %s", u["username"], u["role"], request.remote_addr)
            if u["must_change"]:
                flash("Welcome! Please set your own password to continue.")
                return redirect(url_for("change_password_page"))
            return redirect(url_for("dashboard"))
        _login_fails.setdefault(key, []).append(time.time())
        log.warning("Login failed for '%s' from %s", username, request.remote_addr)
        flash("Invalid username or password.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    if current_user():
        log_activity("Logged Out")
    session.clear()
    return redirect(url_for("login"))


# --------------------------------------------------------------------------
# Dashboard + document workflow
# --------------------------------------------------------------------------
@app.route("/")
@require()
def dashboard():
    user, q = current_user(), request.args.get("q", "")
    docs = rows("SELECT * FROM documents ORDER BY date_created")
    count = lambda s: sum(1 for d in docs if d["status"] == s)
    shown = search_filter(docs, ("doc_no", "title", "department", "revision", "status", "created_by"), q)
    reqs = requests_visible(user)
    c = lambda s: sum(1 for r in reqs if r["status"] == s)
    wf = {"dcc": c("Pending DCC Check"), "admr": c("Pending ADMR"), "agmr": c("Pending AGMR"), "editable": c("Pending Editable Files"),
          "release": c("Released to Requestor"),
          "returned": sum(1 for r in reqs if r["status"] in
                          ("Returned to Author", "Returned to Requestor", "Rejected"))}
    return render_template("dashboard.html", docs=shown, total=len(docs),
                           draft=count("Draft"), review=count("Under Review"),
                           approved=count("Approved"), q=q, wf=wf, reqs=reqs[:8],
                           rev_status=latest_revision_status())


@app.route("/create", methods=["GET", "POST"])
@require(*CREATORS)
def create():
    """Requesting a new document: creates the record (Draft) and opens a request to DCC."""
    user = current_user()
    if request.method == "POST":
        title = request.form.get("title", "").strip()[:200]
        dept = request.form.get("department", "")
        dtype = request.form.get("document_type", "")
        purpose = request.form.get("purpose", "").strip()[:2000]
        if not title or dept not in DEPARTMENTS or dtype not in DOC_TYPES or not purpose:
            flash("Please complete all required fields.", "error")
            return redirect(url_for("create"))
        doc_no = PENDING_PREFIX + secrets.token_hex(3).upper()  # DCC assigns the real number
        ts = now()
        doc_id = f"{int(time.time() * 1000)}-{secrets.token_hex(3)}"
        rid = f"RR-{int(time.time() * 1000)}"
        history = [{"status": "Pending DCC Check", "by": user["username"], "at": ts,
                    "comment": "New document requested. Awaiting Document No. from DCC."}]
        db().execute(
            "INSERT INTO documents(id, doc_no, title, department, revision, status, created_by, date_created,"
            " document_type) VALUES (?,?,?,?,?,?,?,?,?)",
            (doc_id, doc_no, title, dept, "00", "Draft", user["username"], ts, dtype))
        db().execute(
            """INSERT INTO revision_requests(id, source_type, source_id, doc_no, title, department,
               current_revision, proposed_revision, reason, status, requested_by, requested_at,
               comments, history, request_type) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (rid, "doc", doc_id, doc_no, title, dept, "New", "00", purpose, "Pending DCC Check",
             user["username"], ts, "[]", json.dumps(history), "new"))
        db().commit()
        log_activity(f"Requested new document \"{title}\" (request {rid})")
        flash(f"Request {rid} sent to DCC. DCC will assign the Document No. when it accepts the request.")
        return redirect(url_for("requests_page"))
    return render_template("create.html")


# --------------------------------------------------------------------------
# Revision requests
# --------------------------------------------------------------------------
def eligible_sources():
    open_ids = {r["source_id"] for r in rows(
        "SELECT source_id FROM revision_requests WHERE source_type='doc' "
        "AND status NOT IN ('Approved','Released','Completed')")}
    out = []
    for d in rows("SELECT * FROM documents WHERE status='Approved'"):
        if d["id"] not in open_ids:
            out.append({"value": "doc:" + d["id"], "doc_no": d["doc_no"], "title": d["title"],
                        "department": d["department"], "revision": d["revision"],
                        "label": f"{d['doc_no']} — {d['title']} (Rev. {d['revision']})"})
    for e in rows("SELECT * FROM existing_procedures WHERE status='Effective' "
                  "AND revision_in_progress=0"):
        if one("SELECT 1 FROM documents WHERE doc_no=? COLLATE NOCASE", (e["doc_no"],)):
            continue  # already listed above through the Document Master List
        out.append({"value": "existing:" + e["id"], "doc_no": e["doc_no"], "title": e["title"],
                    "department": e["department"], "revision": e["revision"],
                    "label": f"{e['doc_no']} — {e['title']} (Rev. {e['revision']}) [Existing Procedure]"})
    return sorted(out, key=lambda x: x["doc_no"])


@app.route("/revision", methods=["GET", "POST"])
@require(*CREATORS)
def revision():
    user = current_user()
    if request.method == "POST":
        selection = request.form.get("source", "")
        reason = request.form.get("reason", "").strip()[:2000]
        if ":" not in selection or not reason:
            flash("Select a document and enter the reason for change.", "error")
            return redirect(url_for("revision"))
        stype, sid = selection.split(":", 1)
        if stype == "doc":
            src = one("SELECT * FROM documents WHERE id=? AND status='Approved'", (sid,))
        elif stype == "existing":
            src = one("SELECT * FROM existing_procedures WHERE id=? AND status='Effective' "
                      "AND revision_in_progress=0", (sid,))
        else:
            src = None
        if not src:
            flash("Selected document is unavailable for revision.", "error")
            return redirect(url_for("revision"))
        if one("SELECT 1 FROM revision_requests WHERE source_type=? AND source_id=? "
               "AND status NOT IN ('Approved','Released','Completed')", (stype, sid)):
            flash("There is already an open revision request for this document.", "error")
            return redirect(url_for("revision"))
        ts, rid = now(), f"RR-{int(time.time() * 1000)}"
        history = [{"status": "Pending DCC Check", "by": user["username"], "at": ts,
                    "comment": "Revision request submitted."}]
        db().execute(
            """INSERT INTO revision_requests(id, source_type, source_id, doc_no, title, department,
               current_revision, proposed_revision, reason, status, requested_by, requested_at,
               comments, history) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (rid, stype, sid, src["doc_no"], src["title"], src["department"], src["revision"],
             next_revision(src["revision"]), reason, "Pending DCC Check", user["username"], ts,
             "[]", json.dumps(history)))
        if stype == "existing":
            db().execute("UPDATE existing_procedures SET revision_in_progress=1 WHERE id=?", (sid,))
        db().commit()
        log_activity(f"Submitted revision request {rid} for {src['doc_no']}")
        flash(f"Revision request {rid} submitted for DCC checking.")
        return redirect(url_for("requests_page"))
    q = request.args.get("q", "").strip().lower()
    all_src = eligible_sources()
    shown = [s for s in all_src if not q or q in " ".join(
        [s["doc_no"], s["title"], s["department"], s["revision"]]).lower()]
    return render_template("revision.html", options=shown, total=len(all_src), q=q)


@app.route("/requests")
@require()
def requests_page():
    q = request.args.get("q", "")
    return render_template("requests.html", reqs=requests_visible(current_user(), q), q=q)


def add_event(r, user, text, status=None):
    ts = now()
    if status:
        r["status"], r["updated_at"] = status, ts
        if text:
            r["comments"].append({"by": user["username"], "at": ts, "text": text})
    r["history"].append({"status": r["status"], "by": user["username"], "at": ts, "comment": text})


def set_new_doc_status(r, status):
    if r.get("request_type") == "new":
        db().execute("UPDATE documents SET status=? WHERE id=?", (status, r["source_id"]))


@app.route("/requests/<rid>/act", methods=["POST"])
@require()
def act_on_request(rid):
    user = current_user()
    r = load_request(one("SELECT * FROM revision_requests WHERE id=?", (rid,)))
    action = request.form.get("action", "")
    comment = request.form.get("comment", "").strip()[:1000]
    uploads = [u for u in request.files.getlist("file") if u and u.filename]
    upload = uploads[0] if uploads else None
    has_file = bool(uploads)
    roles_in = request.form.getlist("role")

    def fail(msg):
        flash(msg, "error")
        return redirect(url_for("requests_page"))

    if not r:
        return fail("Revision request not found.")
    if action not in [a[0] for a in available_actions(r, user)]:
        return fail("You do not have permission for this action or request status.")

    kind = FILE_FOR_ACTION.get(action)
    if action == "release" and r.get("request_type") == "new":
        kind = None  # new document: DCC only assigns the Document No., no file
    if has_file:
        if not kind:
            return fail("No file is needed for this action.")
        if len(uploads) > 1 and kind not in ("template", "editable"):
            return fail("Please attach only one file for this action.")
        if len(uploads) > MAX_FILES:
            return fail(f"Please attach at most {MAX_FILES} files at a time.")
        for up in uploads:
            problem = check_upload(up, kind)
            if problem:
                return fail(problem)
    if action == "finalApprove" and has_file:
        head = upload.stream.read(5)
        upload.stream.seek(0)
        if not upload.filename.lower().endswith(".pdf") or not head.startswith(b"%PDF"):
            return fail("Please attach the approved document as a PDF file.")
    if action == "sendEditable" and not has_file:
        return fail("Attach all the editable Word / Excel files of the approved document.")
    if action in ("returnAuthor", "reject", "closeNoEditable") and not comment:
        return fail("Enter a reason or comment before continuing.")
    if action == "release" and r.get("request_type") != "new" and \
            not (has_file or any(f["kind"] == "template" for f in r["files"])):
        return fail("Attach the Word or Excel file before releasing it to the requestor.")
    if action == "submitManagement" and not has_file:
        return fail("Attach your completed document before submitting it to management.")
    new_doc_no = ""
    if action == "release" and r.get("request_type") == "new" and r["doc_no_pending"]:
        new_doc_no = re.sub(r"\s+", " ", request.form.get("doc_no", "")).strip()[:100]
        if not new_doc_no:
            return fail("Enter the Document No. before accepting this new document.")
        if new_doc_no.upper().startswith(PENDING_PREFIX):
            return fail("Please enter the actual Document No.")
        if one("SELECT 1 FROM documents WHERE doc_no=? COLLATE NOCASE AND id<>?", (new_doc_no, r["source_id"])) or \
           one("SELECT 1 FROM existing_procedures WHERE doc_no=? COLLATE NOCASE", (new_doc_no,)):
            return fail(f"Document No. {new_doc_no} is already in use.")
    source = None
    if action in ("finalApprove", "sendEditable"):
        if r["source_type"] == "doc":
            source = one("SELECT * FROM documents WHERE id=?", (r["source_id"],))
        else:
            source = one("SELECT * FROM existing_procedures WHERE id=?", (r["source_id"],))
        if not source:
            return fail("Source document not found.")

    ts, saved, saved_list = now(), None, []
    if has_file:
        if kind == "editable":
            saved_list = store_editable_files(source, uploads, roles_in, user, ts)   # into the department folder
        else:
            for i, up in enumerate(uploads):
                key = unique_upload_name(up.filename)   # saved in uploads\ under the same name as the chosen file
                up.save(lp(os.path.join(UPLOAD_DIR, key)))
                saved_list.append({"kind": kind, "name": clean_upload_name(up.filename) or "attachment",
                                   "type": up.mimetype or "application/octet-stream",
                                   "size": os.path.getsize(lp(os.path.join(UPLOAD_DIR, key))),
                                   "blobKey": key, "label": file_label(roles_in, i) if kind == "template" else "",
                                   "by": user["username"], "role": user["role"], "at": ts})
        r["files"].extend(saved_list)
        saved = saved_list[-1]

    def note(default):
        if len(saved_list) > 1:
            extra = f" [{len(saved_list)} files attached: " + ", ".join(f["name"] for f in saved_list) + "]"
        elif saved:
            extra = f" [File attached: {saved['name']}]"
        else:
            extra = ""
        return (comment or default) + extra

    if action == "returnAuthor":
        add_event(r, user, note(""), "Returned to Requestor")
    elif action == "resubmit":
        add_event(r, user, note("Requestor resubmitted the request."), "Pending DCC Check")
    elif action == "release":
        default = (f"DCC released {len(saved_list)} editable files to the requestor." if len(saved_list) > 1
                   else "DCC released the editable file to the requestor.")
        if r.get("request_type") == "new":
            if new_doc_no:
                db().execute("UPDATE documents SET doc_no=? WHERE id=?", (new_doc_no, r["source_id"]))
                db().execute("UPDATE revision_requests SET doc_no=? WHERE source_type='doc' AND source_id=?",
                             (new_doc_no, r["source_id"]))
                r["doc_no"] = new_doc_no
            default = (f"DCC accepted the new document and assigned Document No. {r['doc_no']}. "
                       f"The requestor may now prepare and submit the document.")
        add_event(r, user, note(default), "Released to Requestor")
    elif action == "submitManagement":
        set_new_doc_status(r, "Under Review")
        add_event(r, user, note("Requestor submitted the completed document for management approval."),
                  "Pending ADMR")
    elif action == "reject":
        set_new_doc_status(r, "Correction Required")
        add_event(r, user, note(""), "Rejected")
    elif action == "approve":   # ADMR: first-level approval, forwarded to AGMR
        add_event(r, user, note("ADMR approved and forwarded the document to AGMR for final approval."),
                  "Pending AGMR")
    elif action == "finalApprove":   # AGMR: final approval; the attached PDF becomes the controlled copy
        latest = next((f for f in reversed(r["files"]) if f["kind"] == "submission"), None)
        is_pdf = lambda f: bool(f) and str(f.get("name", "")).lower().endswith(".pdf")
        # The approved revision replaces the current copy: a PDF attached by management at
        # approval wins, otherwise the requestor's latest submission if it is a PDF.
        if saved and saved["kind"] == "review" and is_pdf(saved):
            latest = saved
        elif not is_pdf(latest):
            if r["source_type"] == "doc":
                try:
                    archive_pdf(json.loads(source["current_attachment"] or "null"))
                except ValueError:
                    pass
            flash("Approved, but no PDF came with this revision, so the Master List has no viewable PDF "
                  "for it. Submit (or approve with) a PDF to have it replace the viewable copy.")
        if is_pdf(latest) and r["source_type"] == "doc" and latest.get("blobKey"):
            src = os.path.join(UPLOAD_DIR, os.path.basename(latest["blobKey"]))
            if os.path.exists(src):
                try:
                    prev = json.loads(source["current_attachment"] or "null")
                except ValueError:
                    prev = None
                new_rev = source["revision"] if r.get("request_type") == "new" else r["proposed_revision"]
                latest = put_pdf_in_folder(source["doc_no"], source["title"], new_rev,
                                           source.get("document_type"), source["department"], src,
                                           user["username"], user["role"], previous=prev)
        latest_json = json.dumps(latest) if latest else None
        if r.get("request_type") == "new":
            db().execute("UPDATE documents SET status='Approved', current_attachment=? WHERE id=?",
                         (latest_json, source["id"]))
        elif r["source_type"] == "doc":
            hist = json.loads(source["revision_history"] or "[]")
            hist.append({"previousRevision": source["revision"], "revision": r["proposed_revision"],
                         "reason": r["reason"], "requestedBy": r["requested_by"], "date": ts,
                         "status": "Approved", "requestId": r["id"],
                         "attachmentName": (latest or {}).get("name", "")})
            db().execute(
                "UPDATE documents SET revision=?, status='Approved', revision_history=?, "
                "current_attachment=? WHERE id=?",
                (r["proposed_revision"], json.dumps(hist), latest_json, source["id"]))
            db().execute("UPDATE existing_procedures SET revision=?, effective_date=?, status='Effective' "
                         "WHERE doc_no=? COLLATE NOCASE",
                         (r["proposed_revision"], ts[:10], source["doc_no"]))
        else:
            db().execute(
                """UPDATE existing_procedures SET revision=?, status='Effective', effective_date=?,
                   revision_in_progress=0, last_revision_reason=?, last_revision_released_by=?,
                   last_revision_attachment=? WHERE id=?""",
                (r["proposed_revision"], ts[:10], r["reason"], user["username"],
                 latest_json, source["id"]))
        add_event(r, user, note("Management approved the document. The requestor must now send all the editable "
                                "Word / Excel files."), "Pending Editable Files")
    elif action == "sendEditable":   # requestor: final editable files, filed with the document
        add_event(r, user, note("Requestor sent the editable files."), "Completed")
    elif action == "closeNoEditable":   # DCC safety valve
        add_event(r, user, note(""), "Completed")
    save_request(r)
    log_activity(f"{action} {r['id']} ({r['doc_no']}) — {r['status']}"
                 + (f" — assigned Document No. {new_doc_no}" if new_doc_no else ""))
    flash(f"{r['id']} updated: {STATUS_LABELS.get(r['status'], r['status'])}.")
    return redirect(url_for("requests_page"))


@app.route("/requests/<rid>/download/<int:idx>")
@require()
def download_attachment(rid, idx):
    r = load_request(one("SELECT * FROM revision_requests WHERE id=?", (rid,)))
    if not r or idx < 0 or idx >= len(r["files"]) or not may_download(r, r["files"][idx], current_user()):
        flash("Attachment download is not available for this request.", "error")
        return redirect(url_for("requests_page"))
    f = r["files"][idx]
    path = att_path(f)
    if not os.path.exists(path):
        flash("Attachment data was not found on the server.", "error")
        return redirect(url_for("requests_page"))
    return send_file(path, as_attachment=True, download_name=f["name"])


# --------------------------------------------------------------------------
# Existing procedures
# --------------------------------------------------------------------------
@app.route("/existing", methods=["GET", "POST"])
@require(*VIEW_ALL)
def existing():
    if request.method == "POST":
        back = url_for("master") if request.form.get("next") == "master" else url_for("existing")
        if session["role"] not in PROCESSORS:
            flash("Only DCC or Administrator can register existing procedures.", "error")
            return redirect(back)
        f = request.form
        rec = {k: f.get(k, "").strip() for k in
               ("doc_no", "title", "department", "document_type", "revision",
                "effective_date", "status", "file_reference", "remarks")}
        if not all(rec[k] for k in ("doc_no", "title", "department", "document_type",
                                    "revision", "status")):
            flash("Please complete all required fields.", "error")
        elif rec["department"] not in DEPARTMENTS or rec["document_type"] not in DOC_TYPES:
            flash("Choose the Department / Process Owner and the Document Type from the lists.", "error")
        elif one("SELECT 1 FROM existing_procedures WHERE doc_no=? COLLATE NOCASE", (rec["doc_no"],)):
            flash("This document number is already registered. Check the Existing Procedure Register.", "error")
        elif one("SELECT 1 FROM documents WHERE doc_no=? COLLATE NOCASE", (rec["doc_no"],)):
            flash("This document number already exists in the Document Master List.", "error")
        elif (f_pdf := request.files.get("pdf")) and f_pdf.filename and \
                (att_result := save_master_pdf(f_pdf, session["username"], session["role"], rec["doc_no"],
                                          rec["title"], rec["revision"], rec["document_type"],
                                          rec["department"]))[1]:
            flash(att_result[1], "error")
        else:
            attachment = att_result[0] if (f_pdf := request.files.get("pdf")) and f_pdf.filename else None
            db().execute(
                """INSERT INTO existing_procedures(id, doc_no, title, department, document_type,
                   revision, effective_date, status, file_reference, remarks, registered_by,
                   registered_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (f"{int(time.time() * 1000)}-{secrets.token_hex(3)}", rec["doc_no"], rec["title"],
                 rec["department"], rec["document_type"], rec["revision"], rec["effective_date"],
                 rec["status"], rec["file_reference"], rec["remarks"], session["username"], now()))
            master_insert(db(), rec["doc_no"], rec["title"], rec["department"], rec["revision"],
                          rec["status"], session["username"], now(), attachment, rec["document_type"])
            db().commit()
            log_activity(f"Registered existing procedure {rec['doc_no']} — {rec['title']} (recorded in Document Master List)")
            flash(f"Existing procedure {rec['doc_no']} was registered and recorded in the Document Master List.")
        return redirect(back)
    q = request.args.get("q", "")
    items = rows("SELECT * FROM existing_procedures ORDER BY doc_no")
    items = search_filter(items, ("doc_no", "title", "department", "document_type", "revision",
                                  "effective_date", "status", "registered_by",
                                  "file_reference", "remarks"), q)
    return render_template("existing.html", items=items, q=q)


def csv_response(headers, data, filename):
    def cell(v):
        text = str(v if v is not None else "")
        return "'" + text if re.match(r"^[=+\-@\t\r]", text) else text  # formula-injection guard
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    w.writerow(headers)
    for row in data:
        w.writerow([cell(c) for c in row])
    return Response("\ufeff" + buf.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})


@app.route("/existing/export")
@require(*PROCESSORS)
def export_existing():
    items = rows("SELECT * FROM existing_procedures ORDER BY doc_no")
    log_activity("Exported Existing Procedure Register CSV")
    return csv_response(
        ["Document No.", "Title", "Department", "Document Type", "Revision", "Effective Date",
         "Control Status", "File Name / Storage Location", "Remarks", "Registered By",
         "Registered At"],
        [[i["doc_no"], i["title"], i["department"], i["document_type"], i["revision"],
          i["effective_date"], i["status"], i["file_reference"], i["remarks"],
          i["registered_by"], fmt_filter(i["registered_at"])] for i in items],
        "SGIC_Existing_Procedure_Register.csv")


# --------------------------------------------------------------------------
# Master list + audit
# --------------------------------------------------------------------------
@app.route("/master")
@require()
def master():
    q = request.args.get("q", "")
    docs = rows("SELECT * FROM documents ORDER BY doc_no")
    docs = search_filter(docs, ("doc_no", "title", "department", "document_type", "revision", "status", "created_by"), q)
    for d in docs:
        d["has_pdf"] = bool(pdf_attachment(d))
    return render_template("master.html", docs=docs, q=q, rev_status=latest_revision_status())


@app.route("/master/<doc_id>/view")
@require()
def view_document(doc_id):
    doc = one("SELECT * FROM documents WHERE id=?", (doc_id,))
    if not doc:
        flash("Document not found.", "error")
        return redirect(url_for("master"))
    att = pdf_attachment(doc)
    log_activity(f"Viewed {doc['doc_no']} — {doc['title']}")
    return render_template("docview.html", d=doc, pdf=att, rev_status=latest_revision_status())


@app.route("/master/<doc_id>/type", methods=["POST"])
@require(*PROCESSORS)
def set_document_type(doc_id):
    """Change a document's Department / Process Owner and/or Document Type; its PDF is re-filed."""
    doc = one("SELECT * FROM documents WHERE id=?", (doc_id,))
    dept = request.form.get("department", "")
    dtype = request.form.get("document_type", "")
    if not doc:
        flash("Document not found.", "error")
        return redirect(url_for("master"))
    if (dept and dept not in DEPARTMENTS) or (dtype and dtype not in DOC_TYPES):
        flash("Choose the Department and Document Type from the lists.", "error")
    elif not dept and not dtype:
        flash("Nothing changed. Pick a new Department and/or Document Type.")
    else:
        dept, dtype = dept or doc["department"], dtype or doc["document_type"]
        db().execute("UPDATE documents SET department=?, document_type=? WHERE id=?", (dept, dtype, doc_id))
        db().execute("UPDATE existing_procedures SET department=?, document_type=? WHERE doc_no=? COLLATE NOCASE",
                     (dept, dtype, doc["doc_no"]))
        db().execute("UPDATE revision_requests SET department=? WHERE source_type='doc' AND source_id=?",
                     (dept, doc_id))
        d2 = dict(doc, department=dept, document_type=dtype)
        conn = db()
        conn.row_factory = sqlite3.Row
        move_editable_dir(conn, doc_id, editable_dir_rel(doc), editable_dir_rel(d2))
        refile_document_pdf(conn, d2)
        conn.commit()
        log_activity(f"Changed filing of {doc['doc_no']} to {dtype} / {dept}")
        flash(f"Filed under {dtype} / {dept}. The PDF was moved to that folder.")
    return redirect(url_for("view_document", doc_id=doc_id))


@app.route("/master/<doc_id>/pdf")
@require()
def document_pdf(doc_id):
    doc = one("SELECT * FROM documents WHERE id=?", (doc_id,))
    att = pdf_attachment(doc) if doc else None
    if not att:
        flash("No PDF is attached to this document.", "error")
        return redirect(url_for("master"))
    path = att_path(att)
    resp = send_file(path, mimetype="application/pdf", as_attachment=False,
                     download_name=att["name"], conditional=True)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Cache-Control"] = "private, max-age=0, must-revalidate"
    return resp


@app.route("/master/<doc_id>/download", methods=["GET", "POST"])
@require()
def download_document_pdf(doc_id):
    doc = one("SELECT * FROM documents WHERE id=?", (doc_id,))
    att = pdf_attachment(doc) if doc else None
    if not att:
        flash("No PDF is attached to this document.", "error")
        return redirect(url_for("master"))
    back = redirect(url_for("view_document", doc_id=doc_id))
    if request.method != "POST":
        return back
    me = session["username"]
    if pdf_locked(me):
        flash("Too many wrong passwords. Try again in 10 minutes.", "error")
        return back
    if not pdf_password_ok(request.form.get("password", "")):
        _pdf_fails.setdefault(me, []).append(time.time())
        log_activity(f"Wrong download password for {doc['doc_no']}")
        flash("Incorrect download password. You can still view the document.", "error")
        return back
    _pdf_fails.pop(me, None)
    path = att_path(att)
    log_activity(f"Downloaded PDF {att['name']} of {doc['doc_no']}")
    resp = send_file(path, mimetype="application/pdf", as_attachment=True,
                     download_name=att["name"], conditional=False)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


@app.route("/master/export")
@require()
def export_master():
    docs = rows("SELECT * FROM documents ORDER BY doc_no")
    log_activity("Exported Document Master List CSV")
    return csv_response(
        ["Document No.", "Title", "Department", "Revision", "Status", "Created By", "Date Created"],
        [[d["doc_no"], d["title"], d["department"], d["revision"], d["status"],
          d["created_by"], fmt_filter(d["date_created"])] for d in docs],
        "SGIC_Document_Master_List.csv")


@app.route("/audit")
@require(*VIEW_ALL)
def audit():
    q = request.args.get("q", "")
    logs = search_filter(rows("SELECT * FROM audit ORDER BY id DESC LIMIT 5000"),
                         ("username", "activity", "timestamp"), q)
    return render_template("audit.html", logs=logs, q=q)


ALL_ROLES = ["Administrator", "DCC", "Author", "Requestor", "ADMR", "AGMR"]
# Roles a DCC user may create. Edit this list to allow or block more roles.
DCC_CREATABLE_ROLES = ["Requestor", "Author", "ADMR"]   # only Administrator can create AGMR


@app.route("/users", methods=["GET", "POST"])
@require(*VIEW_ALL)
def users_page():
    """DCC may create the roles in DCC_CREATABLE_ROLES; Administrator may create any role."""
    user = current_user()
    if user["role"] == "Administrator":
        creatable = ALL_ROLES
    elif user["role"] == "DCC":
        creatable = DCC_CREATABLE_ROLES
    else:
        creatable = []  # ADMR: can view the list only
    if request.method == "POST":
        if not creatable:
            flash("You do not have permission to create accounts.", "error")
            return redirect(url_for("users_page"))
        username = request.form.get("username", "").strip()
        full_name = re.sub(r"\s+", " ", request.form.get("full_name", "")).strip()[:100]
        section = re.sub(r"\s+", " ", request.form.get("section", "")).strip()[:80]
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        role = request.form.get("role", "Requestor")
        if not full_name or not section:
            flash("Please enter the Name of the Person and the Section.", "error")
        elif role not in creatable:
            flash(f"You can only create these account types: {', '.join(creatable)}.", "error")
        elif not re.fullmatch(r"[A-Za-z0-9._-]{3,30}", username):
            flash("Username must be 3-30 characters: letters, numbers, dot, dash or underscore.", "error")
        elif len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
        elif password != confirm:
            flash("Passwords do not match.", "error")
        elif one("SELECT 1 FROM users WHERE lower(username)=lower(?)", (username,)):
            flash("That username already exists.", "error")
        else:
            db().execute("INSERT INTO users(username, password_hash, role, full_name, section, must_change) VALUES (?,?,?,?,?,1)",
                         (username, generate_password_hash(password), role, full_name, section))
            db().commit()
            log_activity(f"Created {role} account '{username}' for {full_name} ({section})")
            flash(f"{role} account '{username}' created for {full_name}. They must set their own password at first login.")
        return redirect(url_for("users_page"))
    # Everyone allowed on this page sees ALL accounts (removing is still limited by role).
    order = ["Administrator", "AGMR", "ADMR", "DCC", "Author", "Requestor"]
    accounts = sorted(rows("SELECT username, role, full_name, section FROM users"),
                      key=lambda a: (order.index(a["role"]) if a["role"] in order else len(order), a["username"].lower()))
    return render_template("users.html", accounts=accounts, creatable=creatable)


@app.route("/users/<username>/delete", methods=["POST"])
@require(*PROCESSORS)
def delete_user(username):
    """Remove the account of a person who is no longer available. History keeps their name as text."""
    me = current_user()
    target = one("SELECT username, role, full_name FROM users WHERE username=?", (username,))
    if not target:
        flash("Account not found.", "error")
    elif target["username"] == me["username"]:
        flash("You cannot remove the account you are logged in with.", "error")
    elif me["role"] != "Administrator" and target["role"] not in DCC_CREATABLE_ROLES:
        flash(f"DCC can only remove {', '.join(DCC_CREATABLE_ROLES)} accounts.", "error")
    elif target["role"] == "Administrator" and \
            one("SELECT COUNT(*) AS n FROM users WHERE role='Administrator'")["n"] <= 1:
        flash("The last Administrator account cannot be removed.", "error")
    else:
        db().execute("DELETE FROM users WHERE username=?", (username,))
        db().commit()
        log_activity(f"Removed {target['role']} account '{username}' ({target['full_name'] or 'no name'})")
        flash(f"Account '{username}' was removed.")
    return redirect(url_for("users_page"))


# --------------------------------------------------------------------------
# Production hardening: logging, safer defaults, friendly errors, health check
# --------------------------------------------------------------------------
LOG_DIR = os.path.join(os.path.dirname(UPLOAD_DIR), "logs")
log = logging.getLogger("sgic")


def setup_logging():
    if getattr(app, "_sgic_logging", False):
        return
    app._sgic_logging = True
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        handler = RotatingFileHandler(os.path.join(LOG_DIR, "dms.log"), maxBytes=2_000_000,
                                      backupCount=5, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        for name in ("sgic", "waitress"):
            lg = logging.getLogger(name)
            lg.setLevel(logging.INFO)
            lg.addHandler(handler)
        app.logger.addHandler(handler)
        app.logger.setLevel(logging.INFO)
    except OSError:
        pass   # logging must never stop the server


setup_logging()

_login_fails = {}


def login_locked(key):
    recent = [t for t in _login_fails.get(key, []) if time.time() - t < 600]
    _login_fails[key] = recent
    return len(recent) >= 5


@app.before_request
def block_cross_site_posts():
    """Refuse form posts that come from another website (CSRF protection)."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        src = request.headers.get("Origin") or request.headers.get("Referer")
        if src and urlparse(src).netloc not in ("", request.host):
            log.warning("Blocked cross-site %s to %s from %s", request.method, request.path, src)
            abort(403)


@app.after_request
def add_security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    if os.environ.get("SGIC_EMBEDDED") != "1":
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    if resp.mimetype == "text/html" and "Cache-Control" not in resp.headers:
        resp.headers["Cache-Control"] = "no-store"
    return resp


def render_error(code, title, text):
    ref = secrets.token_hex(3).upper()
    try:
        return render_template("error.html", code=code, title=title, text=text, ref=ref), code
    except Exception:   # never fail while reporting a failure
        return (f"<h2>{code} {title}</h2><p>{text}</p><p><a href='/'>Back to the dashboard</a></p>", code)


@app.errorhandler(403)
def forbidden(_e):
    return render_error(403, "Not allowed", "You are not allowed to do that.")


@app.errorhandler(404)
def not_found(_e):
    return render_error(404, "Page not found", "The page you asked for does not exist.")


@app.errorhandler(413)
def too_large(_e):
    return render_error(413, "File too large", "That file is too large to upload.")


@app.errorhandler(500)
def server_error(e):
    ref = secrets.token_hex(3).upper()
    log.error("Unhandled error ref=%s on %s", ref, request.path, exc_info=getattr(e, "original_exception", e))
    try:
        return render_template("error.html", code=500, title="Something went wrong",
                               text="The problem was recorded. Please try again; if it keeps happening, "
                                    "tell the system administrator.", ref=ref), 500
    except Exception:
        return (f"<h2>Something went wrong</h2><p>Reference {ref}. <a href='/'>Back</a></p>", 500)


@app.route("/healthz")
def healthz():
    try:
        one("SELECT 1 AS ok")
        return {"status": "ok", "version": VERSION, "database": dbcompat.engine()}
    except Exception:
        return {"status": "database error"}, 503


@app.route("/account/password", methods=["GET", "POST"])
@require()
def change_password_page():
    user = current_user()
    if request.method == "POST":
        current = request.form.get("current", "")
        new = request.form.get("new", "")
        confirm = request.form.get("confirm", "")
        u = one("SELECT * FROM users WHERE username=?", (user["username"],))
        if not check_password_hash(u["password_hash"], current):
            flash("Your current password is not correct.", "error")
        elif len(new) < 8:
            flash("The new password must be at least 8 characters.", "error")
        elif new == current:
            flash("Choose a password different from the current one.", "error")
        elif new.lower() == user["username"].lower():
            flash("The password cannot be the same as the username.", "error")
        elif new != confirm:
            flash("The new passwords do not match.", "error")
        else:
            db().execute("UPDATE users SET password_hash=?, must_change=0 WHERE username=?",
                         (generate_password_hash(new), user["username"]))
            db().commit()
            log_activity("Changed own password")
            flash("Your password was changed.")
            return redirect(url_for("dashboard"))
    return render_template("password.html", forced=bool(g.account and g.account["must_change"]))


@app.route("/users/<username>/reset", methods=["POST"])
@require(*PROCESSORS)
def reset_user_password(username):
    """Give a person a new temporary password (shown once); they must change it at the next login."""
    me = current_user()
    target = one("SELECT username, role, full_name FROM users WHERE username=?", (username,))
    if not target:
        flash("Account not found.", "error")
    elif target["username"] == me["username"]:
        flash("Use Change Password (top right) for your own account.", "error")
    elif me["role"] != "Administrator" and target["role"] not in DCC_CREATABLE_ROLES:
        flash(f"DCC can only reset {', '.join(DCC_CREATABLE_ROLES)} accounts.", "error")
    else:
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
        temp = "".join(secrets.choice(alphabet) for _ in range(10))
        db().execute("UPDATE users SET password_hash=?, must_change=1 WHERE username=?",
                     (generate_password_hash(temp), username))
        db().commit()
        _login_fails.clear()
        log_activity(f"Reset password of '{username}'")
        flash(f"Temporary password for '{username}': {temp}   (shown only once - give it to "
              f"{target['full_name'] or username}; they must set their own at the next login)")
    return redirect(url_for("users_page"))


# --------------------------------------------------------------------------
# Styles (same look as the original HTML version)
# --------------------------------------------------------------------------
CSS = """
*{box-sizing:border-box}body{margin:0;background:#f1f5f9 url('/background.png') center center/cover no-repeat fixed;color:#1e293b;font:14px Arial,sans-serif}
button,input,select,textarea{font:inherit}
button{cursor:pointer;border:0;border-radius:5px;padding:9px 13px;background:#174a7e;color:#fff}
button:hover{opacity:.88}
.secondary{background:#64748b}.success{background:#15803d}.warning{background:#b45309}.danger{background:#b91c1c}
.container{max-width:1250px;margin:auto;padding:20px}
.container.app{background:rgba(255,255,255,.94);border-radius:12px;margin:18px auto;box-shadow:0 4px 18px #00000040}
.container:not(.app) footer{background:rgba(255,255,255,.92);color:#334155;border-radius:6px;padding:8px 14px;width:fit-content;max-width:100%;margin:0 auto}
header{background:#12345a;color:#fff;padding:22px;border-radius:10px;display:flex;align-items:center;justify-content:space-between;gap:15px;flex-wrap:wrap;margin-bottom:20px}
header h1{margin:0 0 6px;font-size:24px}header p{margin:0;color:#dbeafe}
.panel{background:#fff;padding:20px;border-radius:9px;box-shadow:0 2px 8px #0000000c;margin-bottom:20px}
.login-panel{max-width:430px;margin:8vh auto}.login-panel h2{color:#12345a}
label{display:block;font-weight:bold;margin:12px 0 6px}
input,select,textarea{width:100%;padding:11px;border:1px solid #cbd5e1;border-radius:5px;background:#fff}
textarea{min-height:110px;resize:vertical}
.form-row{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:15px}
nav{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:20px}
nav a{background:#e2e8f0;color:#1e293b;padding:9px 13px;border-radius:5px;text-decoration:none}
nav a.active{background:#174a7e;color:#fff}
.stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:15px;margin-bottom:20px}
.stat{background:#fff;padding:18px;border-radius:8px;border-left:4px solid #174a7e}
.stat span{display:block;color:#64748b;margin-bottom:8px}.stat strong{font-size:26px}
.workflow-stats{margin:12px 0 18px}
.table-wrap{overflow-x:auto;width:100%}
table{width:100%;border-collapse:collapse;min-width:750px}
th,td{padding:11px;border-bottom:1px solid #e2e8f0;text-align:left;vertical-align:middle}
th{background:#eaf0f7;color:#12345a;white-space:nowrap}
td.actions{min-width:270px}.actions button,.actions form{margin:2px;display:inline}
.actions button{padding:6px 9px;font-size:12px}
.badge{display:inline-block;padding:5px 8px;border-radius:20px;background:#e2e8f0;font-size:12px;white-space:nowrap}
.badge.Approved{background:#dcfce7;color:#166534}.badge.Draft{background:#dbeafe;color:#1d4ed8}
.badge.Under-Review{background:#fef3c7;color:#92400e}.badge.Correction-Required{background:#fee2e2;color:#991b1b}
.toolbar{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:15px}
.toolbar input{max-width:350px}
.message{padding:12px;border-radius:5px;background:#dcfce7;color:#166534;margin-bottom:15px}
.error{background:#fee2e2;color:#991b1b}
.small{font-size:12px;color:#64748b}
footer{text-align:center;color:#64748b;padding:20px}
a.btn{display:inline-block;background:#174a7e;color:#fff;padding:9px 13px;border-radius:5px;text-decoration:none}
a.btn.secondary{background:#64748b}
.badge.Released-to-Requestor{background:#ccfbf1;color:#115e59}.badge.Pending-DCC-Check{background:#dbeafe;color:#1d4ed8}.badge.Pending-Approver{background:#ede9fe;color:#5b21b6}
.badge.Approved-Pending-Release{background:#ccfbf1;color:#115e59}.badge.Released{background:#bbf7d0;color:#14532d}
.badge.Rejected{background:#fecaca;color:#991b1b}.badge.Returned-to-Requestor,.badge.Returned-to-Author{background:#fed7aa;color:#9a3412}
.badge.grp-done{background:#bbf7d0;color:#14532d}.badge.grp-ongoing{background:#fde68a;color:#78350f}.badge.grp-notfinished{background:#e5e7eb;color:#374151}
.badge:before{content:"";display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;background:currentColor;opacity:.75}
.legend{font-size:12px;color:#475569;margin:0 0 10px}.legend .badge{margin-right:10px}
.track{display:flex;gap:4px;flex-wrap:wrap;margin-top:7px}
.track span{font-size:11px;padding:3px 7px;border-radius:12px;background:#e2e8f0;color:#64748b}
.track .done{background:#dcfce7;color:#166534}.track .current{background:#fef3c7;color:#92400e;font-weight:bold}
details{margin-top:6px}details summary{cursor:pointer;color:#174a7e;font-size:12px}
details ul{margin:6px 0 0;padding-left:16px;font-size:12px;color:#475569}
@media(max-width:760px){.stats{grid-template-columns:repeat(2,minmax(0,1fr))}.form-row{grid-template-columns:1fr}.container{padding:12px}header h1{font-size:20px}}
"""


@app.route("/background.png")
def background_image():
    resp = send_file(os.path.join(BASE_DIR, "background.png"), mimetype="image/png", conditional=True)
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@app.route("/style.css")
def style():
    return Response(CSS, mimetype="text/css")


# --------------------------------------------------------------------------
# Templates (inline so the project stays a single file)
# --------------------------------------------------------------------------
BADGE = ("{% macro badge(s) %}<span class=\"badge {{ s|badge }} grp-{{ s|group }}\">{{ s|label }}</span>{% endmacro %}"
         "{% macro legend() %}<div class=\"legend\"><span class=\"badge grp-done\">Green &ndash; done</span>"
         "<span class=\"badge grp-ongoing\">Yellow &ndash; ongoing</span>"
         "<span class=\"badge grp-notfinished\">Grey &ndash; not finished</span></div>{% endmacro %}")
SEARCH = ("{% macro search(q, ph) %}<form method=\"get\" style=\"margin:0;display:flex;gap:6px;align-items:center\">"
          "<input name=\"q\" value=\"{{ q }}\" placeholder=\"{{ ph }}\" autocomplete=\"off\" {% if q %}autofocus{% endif %} "
          "onfocus=\"var v=this.value;this.value='';this.value=v\" "
          "oninput=\"clearTimeout(window._st);var f=this.form;window._st=setTimeout(function(){f.submit()},500)\">"
          "<button type=\"submit\">Search</button>"
          "{% if q %}<a href=\"?\" class=\"small\">Clear</a>{% endif %}"
          "</form>{% endmacro %}")

TEMPLATES = {
"base.html": """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SGIC Document Management System</title>
<link rel="stylesheet" href="{{ url_for('style') }}"></head><body><div class="container{% if user %} app{% endif %}">
{% if user %}
<header><div><h1>SGIC Document Management System</h1><p>Sanritsu Great International Corporation</p></div>
<div><div>User: {% if user.full_name %}{{ user.full_name }} ({{ user.username }}){% else %}{{ user.username }}{% endif %}{% if user.section %} | Section: {{ user.section }}{% endif %} | Role: {{ user.role }}</div>
<a class="btn" style="background:#475569" href="{{ url_for('change_password_page') }}">Change Password</a> <a class="btn" style="background:#b91c1c" href="{{ url_for('logout') }}">Logout</a></div></header>
{% endif %}
{% for cat, msg in get_flashed_messages(with_categories=true) %}
<div class="message {{ 'error' if cat == 'error' }}">{{ msg }}</div>{% endfor %}
{% if user %}
{% set ep = request.endpoint %}
<nav>
<a href="{{ url_for('dashboard') }}" class="{{ 'active' if ep == 'dashboard' }}">Dashboard</a>
{% if user.role in CREATORS %}
<a href="{{ url_for('create') }}" class="{{ 'active' if ep == 'create' }}">Create Document</a>
<a href="{{ url_for('revision') }}" class="{{ 'active' if ep == 'revision' }}">Submit Revision</a>{% endif %}
<a href="{{ url_for('requests_page') }}" class="{{ 'active' if ep == 'requests_page' }}">Revision Requests</a>
{% if user.role in VIEW_ALL %}
<a href="{{ url_for('existing') }}" class="{{ 'active' if ep == 'existing' }}">Register Existing Procedure</a>{% endif %}
<a href="{{ url_for('master') }}" class="{{ 'active' if ep == 'master' }}">Document Master List</a>
{% if user.role in VIEW_ALL %}
<a href="{{ url_for('users_page') }}" class="{{ 'active' if ep == 'users_page' }}">Manage Users</a>
<a href="{{ url_for('audit') }}" class="{{ 'active' if ep == 'audit' }}">Audit Trail</a>{% endif %}
</nav>{% endif %}
{% block content %}{% endblock %}
<footer>SGIC DMS v{{ VERSION }} | Document Control Portal | Sanritsu Great International Corporation</footer>
</div><script>
document.addEventListener('change', function (e) {
  var inp = e.target;
  if (!inp.matches || !inp.matches('input[type=file][data-roles]')) return;
  var box = inp.parentNode.querySelector('.filerows');
  if (!box) return;
  box.innerHTML = '';
  Array.prototype.forEach.call(inp.files, function (f) {
    var row = document.createElement('div');
    row.className = 'small'; row.style.margin = '3px 0';
    row.appendChild(document.createTextNode(f.name + ' \u2013 '));
    var sel = document.createElement('select'); sel.name = 'role';
    ['Main procedure', 'Mother procedure', 'Form', 'Other'].forEach(function (t) {
      var o = document.createElement('option'); o.textContent = t; sel.appendChild(o);
    });
    row.appendChild(sel); box.appendChild(row);
  });
});
</script></body></html>""",

"error.html": """{% extends 'base.html' %}{% block content %}
<section class="panel"><h2>{{ title }}</h2><p>{{ text }}</p><p class="small">Reference: {{ ref }} &middot; Error {{ code }}</p>
<p><a class="btn" href="/">Back to the dashboard</a></p></section>{% endblock %}""",

"password.html": """{% extends 'base.html' %}{% block content %}
<section class="panel" style="max-width:520px"><h2>Change Password</h2>
{% if forced %}<p class="small"><b>You must set your own password before you can use the system.</b></p>{% endif %}
<form method="post"><label for="current">Current password</label><input id="current" name="current" type="password" required autocomplete="current-password">
<label for="new">New password (at least 8 characters)</label><input id="new" name="new" type="password" required minlength="8" autocomplete="new-password">
<label for="confirm">Confirm new password</label><input id="confirm" name="confirm" type="password" required minlength="8" autocomplete="new-password">
<button type="submit" style="margin-top:16px">Save new password</button></form></section>{% endblock %}""",

"login.html": """{% extends 'base.html' %}{% block content %}
<section class="panel login-panel"><h2>SGIC Document Management System</h2>
<p class="small">Document Control Portal</p>
<form method="post"><label for="username">Username</label>
<input id="username" name="username" required autocomplete="username">
<label for="password">Password</label>
<input id="password" name="password" type="password" required autocomplete="current-password">
<button type="submit" style="width:100%;margin-top:20px">Login</button></form><hr>
<p class="small">Forgot your password? Ask the DCC or the system administrator for a new one.</p></section>{% endblock %}""",

"dashboard.html": BADGE + SEARCH + """{% extends 'base.html' %}{% block content %}
{{ legend() }}
<div class="stats">
<div class="stat"><span>Total Documents</span><strong>{{ total }}</strong></div>
<div class="stat"><span>Draft</span><strong>{{ draft }}</strong></div>
<div class="stat"><span>Under Review</span><strong>{{ review }}</strong></div>
<div class="stat"><span>Approved</span><strong>{{ approved }}</strong></div></div>
<div class="panel"><div class="toolbar"><h2>Document Dashboard</h2>{{ search(q, 'Search document...') }}</div>{% if q %}<p class="small">{{ docs|length }} result(s) for "<b>{{ q }}</b>"</p>{% endif %}
<div class="table-wrap"><table><thead><tr><th>Document No.</th><th>Title</th><th>Department</th>
<th>Revision</th><th>Status</th><th>Revision Request</th><th>Created By</th><th>Actions</th></tr></thead><tbody>
{% for d in docs %}<tr><td>{{ d.doc_no }}{% if d.doc_no.startswith('PENDING-') %}<div class="small">awaiting DCC</div>{% endif %}</td><td>{{ d.title }}</td><td>{{ d.department }}</td>
<td>{{ d.revision }}</td><td>{{ badge(d.status) }}</td><td>{% set rs = rev_status.get(d.doc_no) %}{% if rs %}{{ badge(rs) }}{% else %}<span class="small">—</span>{% endif %}</td><td>{{ d.created_by }}</td>
<td><a class="btn secondary" style="padding:6px 9px;font-size:12px" href="{{ url_for('requests_page', q=d.doc_no) }}">View Requests</a></td></tr>
{% else %}<tr><td colspan="8">No documents found.</td></tr>{% endfor %}</tbody></table></div>
<p class="small">Workflow actions are restricted by the current user's role.</p></div>
<div class="panel"><div class="toolbar"><h2>Revision Request Workflow</h2>
<a class="btn" href="{{ url_for('requests_page') }}">Open Full Workflow</a></div>
<p class="small">Track requests: Requestor asks → DCC releases the editable Word/Excel file → Requestor completes and submits → ADMR approves, then AGMR gives the final approval (or rejects with a comment).</p>
<div class="stats workflow-stats">
<div class="stat"><span>For DCC Check</span><strong>{{ wf.dcc }}</strong></div>
<div class="stat"><span>For ADMR Approval</span><strong>{{ wf.admr }}</strong></div>
<div class="stat"><span>For AGMR Final Approval</span><strong>{{ wf.agmr }}</strong></div>
<div class="stat"><span>Waiting for Editable Files</span><strong>{{ wf.editable }}</strong></div>
<div class="stat"><span>With Requestor for Revision</span><strong>{{ wf.release }}</strong></div>
<div class="stat"><span>Returned / Rejected</span><strong>{{ wf.returned }}</strong></div></div>
<div class="table-wrap"><table><thead><tr><th>Request ID</th><th>Document</th><th>Revision</th>
<th>Requestor</th><th>Status</th><th>Files</th><th>Open</th></tr></thead><tbody>
{% for r in reqs %}<tr><td>{{ r.id }}<br><span class="small">{{ r.requested_at|fmt }}</span></td>
<td>{{ r.doc_no }}<br>{{ r.title }}</td><td>{{ r.current_revision }} → {{ r.proposed_revision }}</td>
<td>{{ r.requested_by }}</td><td>{{ badge(r.status) }}</td>
<td>{% if r.files %}{{ r.files[-1].name }} <span class="small">({{ r.files|length }} file(s))</span>{% else %}<span class="small">No files yet</span>{% endif %}</td>
<td><a class="btn secondary" href="{{ url_for('requests_page') }}">View / Action</a></td></tr>
{% else %}<tr><td colspan="7">No revision requests found for your account.</td></tr>{% endfor %}
</tbody></table></div></div>{% endblock %}""",

"create.html": """{% extends 'base.html' %}{% block content %}
<div class="panel"><h2>Create New Document</h2><form method="post"><div class="form-row">
<div><label for="title">Document Title</label><input id="title" name="title" maxlength="200" required></div>
<div><label for="department">Department / Process Owner</label><select id="department" name="department" required>
<option value="">Select Department</option>{% for d in DEPARTMENTS %}<option>{{ d }}</option>{% endfor %}</select></div></div>
<div class="form-row"><div><label for="document_type">Document Type</label><select id="document_type" name="document_type" required>
<option value="">Select Document Type</option>{% for d in DOC_TYPES %}<option>{{ d }}</option>{% endfor %}</select></div><div></div></div>
<label for="purpose">Purpose / Scope of the New Document</label>
<textarea id="purpose" name="purpose" maxlength="2000" required placeholder="Describe what the new document is for."></textarea>
<p class="small">This sends a request to DCC (Revision 00, Draft). You do not enter a Document No.: DCC assigns it when it accepts your request, with no file attached by DCC. You then prepare your document and submit it to management for approval.</p>
<button type="submit">Create Document</button>
<a class="btn secondary" href="{{ url_for('dashboard') }}">Cancel</a></form></div>{% endblock %}""",

"revision.html": """{% extends 'base.html' %}{% block content %}
<div class="panel"><h2>Revise Existing Document</h2>
<p class="small">Use this section only for approved documents that require a revision.</p>
<form method="get"><label for="q">Search Documents for Revision</label>
<input id="q" name="q" type="search" value="{{ q }}" placeholder="Search by document number, title, department, or revision..."></form>
<form method="post"><label for="source">Approved Document</label>
<select id="source" name="source" required><option value="">Select an approved document</option>
{% for o in options %}<option value="{{ o.value }}">{{ o.label }}</option>{% endfor %}</select>
<p class="small">{% if q %}{{ options|length }} matching document(s) found.{% else %}{{ total }} eligible document(s). Press Enter in the search box to filter.{% endif %}</p>
<label for="reason">Reason for Change / Summary of Changes</label>
<textarea id="reason" name="reason" maxlength="2000" required placeholder="Describe why the document needs revision and summarize the changes."></textarea>
<p class="small">No file is needed now. DCC will check the request and release the editable file to you. Revise it, then submit it to management for approval.</p>
<button type="submit">Submit Revision Request</button>
<a class="btn secondary" href="{{ url_for('dashboard') }}">Cancel</a></form></div>{% endblock %}""",

"requests.html": BADGE + SEARCH + """{% extends 'base.html' %}{% block content %}
{{ legend() }}
<div class="panel"><div class="toolbar"><h2>Revision Requests Workflow</h2>{{ search(q, 'Search requests...') }}</div>{% if q %}<p class="small">{{ reqs|length }} result(s) for "<b>{{ q }}</b>"</p>{% endif %}
<p class="small">Workflow: Requestor asks for a new document or revision → DCC checks and releases the editable file to the requestor → Requestor completes it and submits to Management → Management approves, or rejects and can attach what needs to be revised (the requestor then revises and resubmits).</p>
<div class="table-wrap"><table><thead><tr><th>Request</th><th>Document</th><th>Revision</th><th>Requested By</th>
<th>Status</th><th>Files</th><th>Comments / Actions</th></tr></thead><tbody>
{% for r in reqs %}{% set acts = available_actions(r, user) %}<tr>
<td>{{ r.id }}<br><span class="small">{{ r.requested_at|fmt }}</span><br><span class="small">{{ 'New document' if r.request_type == 'new' else 'Revision' }}</span></td>
<td>{{ r.doc_no }}<br>{{ r.title }}<br><span class="small">{{ r.department }}</span>{% if r.request_type == 'new' %}<div class="small">{% if r.doc_no_pending %}Document No.: awaiting DCC{% else %}Document No. assigned by DCC{% endif %}</div>{% endif %}<div class="small">Reason: {{ r.reason }}</div></td>
<td>{{ r.current_revision }} → {{ r.proposed_revision }}</td><td>{{ r.requested_by }}</td>
<td>{{ badge(r.status) }}{% set st = stage(r) %}{% if st is not none %}<div class="track">{% for name in STEPS %}<span class="{{ 'done' if loop.index0 < st else ('current' if loop.index0 == st else '') }}">{{ name }}</span>{% endfor %}</div>{% endif %}
<details><summary>Status history</summary><ul>{% for h in r.history %}<li>{{ h.at|fmt }} — <b>{{ h.status|label }}</b> by {{ h.by }}{% if h.comment %}: {{ h.comment }}{% endif %}</li>{% endfor %}</ul></details></td>
<td>{% for f in r.files %}<div style="margin-bottom:6px"><span class="small">{{ FILE_KINDS.get(f.kind, 'File') }}{% if f.label %} — <b>{{ f.label }}</b>{% endif %} · {{ f.by }}</span><br>
{% if may_download(r, f, user) %}<a class="btn secondary" style="padding:5px 8px;font-size:12px" href="{{ url_for('download_attachment', rid=r.id, idx=loop.index0) }}">{{ f.name }}</a>{% else %}{{ f.name }}<div class="small">Available after DCC releases it</div>{% endif %}</div>
{% else %}<span class="small">No files yet</span>{% endfor %}</td>
<td class="actions">{% if r.comments %}<div class="small">{% for c in r.comments[-2:] %}{{ c.by }}: {{ c.text }}<br>{% endfor %}</div>{% endif %}
{% if acts %}<form method="post" enctype="multipart/form-data" action="{{ url_for('act_on_request', rid=r.id) }}" style="display:block">
<input name="comment" placeholder="Comments / reason" maxlength="1000">
{% if r.request_type == 'new' and r.doc_no_pending and user.role == 'DCC' and acts|selectattr(0, 'equalto', 'release')|list %}<input name="doc_no" placeholder="Document No. (required)" maxlength="100">
<div class="small">Enter the Document No. for this new document. It is sent to the requestor when you Accept. No file is attached for new documents.</div>{% endif %}
{% if takes_file(acts) and not (r.request_type == 'new' and acts|selectattr(0, 'equalto', 'release')|list) %}<input type="file" name="file" {% if acts|selectattr(0, 'equalto', 'sendEditable')|list or (acts|selectattr(0, 'equalto', 'release')|list and r.request_type != 'new') %}multiple data-roles="1" {% endif %}accept="{% if acts|selectattr(0, 'equalto', 'release')|list %}.docx,.doc,.xlsx,.xls{% elif acts|selectattr(0, 'equalto', 'sendEditable')|list %}.docx,.doc,.xlsx,.xls{% elif acts|selectattr(0, 'equalto', 'approve')|list %}.doc,.docx,.xls,.xlsx,.ppt,.pptx,.odt,.ods,.odp,.txt,.pdf{% else %}.pdf,application/pdf{% endif %}" style="max-width:260px">
<div class="filerows"></div>
<div class="small">{% if acts|selectattr(0, 'equalto', 'sendEditable')|list %}Send <b>all the editable files</b> of the approved document: Word (.docx / .doc) and Excel (.xlsx / .xls) &mdash; the procedure, the mother procedure and every form. You can select several files at once and label each one. They are filed with the approved document.{% elif user.role == 'DCC' %}Attach the editable files to release (you can select <b>several</b> at once and label each: main procedure, mother procedure, forms): <b>Word</b> (.docx / .doc) or <b>Excel</b> (.xlsx / .xls), then Release to Requestor.{% elif user.role == 'ADMR' %}Optional: attach a file (when you Reject, it shows what to revise). Approve sends the request to AGMR for final approval.{% elif user.role == 'AGMR' %}Final approval: attach the approved document as a <b>PDF</b> (it becomes the controlled copy in the Master List; if you attach none, the requestor's PDF is used). To reject, enter a comment.{% else %}Attach your completed / revised document as a <b>PDF</b>. It becomes the viewable copy in the Master List once approved.{% endif %}</div>{% endif %}
{% for a, label, style in acts %}<button name="action" value="{{ a }}" class="{{ style }}">{{ label }}</button>{% endfor %}</form>
{% else %}<span class="small">No action required for your role/status.</span>{% endif %}</td></tr>
{% else %}<tr><td colspan="7">No revision requests found.</td></tr>{% endfor %}
</tbody></table></div></div>{% endblock %}""",

"existing.html": SEARCH + """{% extends 'base.html' %}{% block content %}
{% if user.role in PROCESSORS %}<div class="panel"><h2>Register Existing Procedure</h2>
<p class="small">Record procedures and controlled documents that already exist. Each record is also added to the Document Master List. This does not start a revision workflow.</p>
<form method="post" enctype="multipart/form-data"><div class="form-row">
<div><label>Existing Document No.</label><input name="doc_no" maxlength="100" required placeholder="e.g., PR-QA01000-00"></div>
<div><label>Document Title</label><input name="title" maxlength="200" required></div></div>
<div class="form-row"><div><label>Department / Process Owner</label><select name="department" required>
<option value="">Select Department</option>{% for d in DEPARTMENTS %}<option>{{ d }}</option>{% endfor %}</select></div>
<div><label>Document Type</label><select name="document_type" required>
<option value="">Select Document Type</option>{% for d in DOC_TYPES %}<option>{{ d }}</option>{% endfor %}</select></div></div>
<div class="form-row"><div><label>Current Revision</label><input name="revision" maxlength="30" required placeholder="e.g., 00, 01, Rev. 02"></div>
<div><label>Effective Date</label><input name="effective_date" type="date"></div></div>
<div class="form-row"><div><label>Control Status</label><select name="status" required>
{% for s in EXISTING_STATUSES %}<option>{{ s }}</option>{% endfor %}</select></div>
<div><label>File Name / Storage Location (optional)</label><input name="file_reference" maxlength="250"></div></div>
<label>PDF Copy (optional — lets everyone view it from the Master List)</label><input type="file" name="pdf" accept="application/pdf,.pdf">
<label>Remarks (optional)</label><textarea name="remarks" maxlength="2000"></textarea>
<button type="submit">Save Existing Procedure Record</button>
<button type="reset" class="secondary">Clear Form</button></form></div>{% endif %}
<div class="panel"><div class="toolbar"><h2>Existing Procedure Register</h2>{{ search(q, 'Search existing procedures...') }}</div>{% if q %}<p class="small">{{ items|length }} result(s) for "<b>{{ q }}</b>"</p>{% endif %}
<div class="table-wrap"><table><thead><tr><th>Document No.</th><th>Title</th><th>Department</th><th>Type</th>
<th>Revision</th><th>Effective Date</th><th>Status</th><th>Registered By</th></tr></thead><tbody>
{% for i in items %}<tr><td>{{ i.doc_no }}</td><td>{{ i.title }}</td><td>{{ i.department }}</td>
<td>{{ i.document_type }}</td><td>{{ i.revision }}</td><td>{{ i.effective_date or '' }}</td>
<td>{{ i.status }}</td><td>{{ i.registered_by }}</td></tr>
{% else %}<tr><td colspan="8">No existing procedures registered.</td></tr>{% endfor %}</tbody></table></div>
{% if user.role in PROCESSORS %}<a class="btn" href="{{ url_for('export_existing') }}">Export Existing Procedure Register CSV</a>{% endif %}</div>{% endblock %}""",

"docview.html": BADGE + """{% extends 'base.html' %}{% block content %}
<div class="panel"><div class="toolbar"><h2>{{ d.title }}</h2><a class="btn" href="{{ url_for('master') }}">&larr; Back to Master List</a></div>
<p class="small">Document No.: <b>{{ d.doc_no }}</b> &nbsp;|&nbsp; Department: {{ d.department }} &nbsp;|&nbsp; Type: {{ d.document_type or 'Unclassified' }} &nbsp;|&nbsp; Revision: {{ d.revision }} &nbsp;|&nbsp; {{ badge(d.status) }}
{% set rs = rev_status.get(d.doc_no) %}{% if rs %} &nbsp;|&nbsp; Revision request: {{ badge(rs) }}{% endif %}</p>
{% if user.role in PROCESSORS %}<form method="post" action="{{ url_for('set_document_type', doc_id=d.id) }}" style="margin:0 0 12px;display:flex;gap:6px;align-items:center;flex-wrap:wrap">
<span class="small">Change filing (decides the PDF folder):</span><select name="department" style="max-width:250px"><option value="">Department: {{ d.department }} (keep)</option>{% for x in DEPARTMENTS %}{% if x != d.department %}<option>{{ x }}</option>{% endif %}{% endfor %}</select><select name="document_type" style="max-width:250px"><option value="">Type: {{ d.document_type or 'Unclassified' }} (keep)</option>{% for x in DOC_TYPES %}{% if x != d.document_type %}<option>{{ x }}</option>{% endif %}{% endfor %}</select>
<button type="submit" class="secondary" style="padding:6px 10px">Save</button></form>{% endif %}
{% if pdf %}<p class="small">View only. Downloading this PDF needs the download password. &nbsp; <span>{{ pdf.name }} &middot; {{ (pdf.size / 1024)|round(0)|int }} KB</span></p>
<details style="margin:0 0 12px"><summary class="btn" style="display:inline-block;cursor:pointer">&#11015; Download PDF</summary>
<form method="post" action="{{ url_for('download_document_pdf', doc_id=d.id) }}" style="margin-top:10px;max-width:340px">
<label for="dlpw">Enter download password</label><input id="dlpw" type="password" name="password" required autocomplete="off">
<button type="submit">Download</button></form></details>
<iframe src="{{ url_for('document_pdf', doc_id=d.id) }}#toolbar=0&navpanes=0&view=FitH" title="{{ d.title }}" style="width:100%;height:78vh;border:1px solid #cbd5e1;border-radius:6px;background:#f1f5f9"></iframe>
{% else %}<p>No PDF is available for this document yet. A PDF appears here automatically once a revision that includes a PDF is approved.</p>{% endif %}
</div>{% endblock %}""",

"master.html": BADGE + SEARCH + """{% extends 'base.html' %}{% block content %}
{{ legend() }}
{% if user.role in PROCESSORS %}<details class="panel" {{ 'open' if request.args.get('register') }}><summary><b>Register Existing Procedure</b> <span class="small">— records it in the Existing Procedure Register and in this Master List</span></summary>
<form method="post" enctype="multipart/form-data" action="{{ url_for('existing') }}" style="margin-top:12px"><input type="hidden" name="next" value="master">
<div class="form-row">
<div><label>Existing Document No.</label><input name="doc_no" maxlength="100" required placeholder="e.g., PR-QA01000-00"></div>
<div><label>Document Title</label><input name="title" maxlength="200" required></div></div>
<div class="form-row"><div><label>Department / Process Owner</label><select name="department" required>
<option value="">Select Department</option>{% for d in DEPARTMENTS %}<option>{{ d }}</option>{% endfor %}</select></div>
<div><label>Document Type</label><select name="document_type" required>
<option value="">Select Document Type</option>{% for d in DOC_TYPES %}<option>{{ d }}</option>{% endfor %}</select></div></div>
<div class="form-row"><div><label>Current Revision</label><input name="revision" maxlength="30" required placeholder="e.g., 00, 01, Rev. 02"></div>
<div><label>Effective Date</label><input name="effective_date" type="date"></div></div>
<div class="form-row"><div><label>Control Status</label><select name="status" required>
{% for s in EXISTING_STATUSES %}<option>{{ s }}</option>{% endfor %}</select></div>
<div><label>File Name / Storage Location (optional)</label><input name="file_reference" maxlength="250"></div></div>
<label>PDF Copy (optional — lets everyone view it from the Master List)</label><input type="file" name="pdf" accept="application/pdf,.pdf">
<label>Remarks (optional)</label><textarea name="remarks" maxlength="2000"></textarea>
<button type="submit">Register &amp; Record in Master List</button></form></details>{% endif %}
<div class="panel"><div class="toolbar"><h2>Document Master List</h2>{{ search(q, 'Search master list...') }}</div>{% if q %}<p class="small">{{ docs|length }} result(s) for "<b>{{ q }}</b>"</p>{% endif %}
<div class="table-wrap"><table><thead><tr><th>Document No.</th><th>Title</th><th>Department</th><th>Type</th><th>Revision</th>
<th>Status</th><th>Revision Request</th><th>Created By</th><th>Date Created</th></tr></thead><tbody>
{% for d in docs %}<tr><td>{{ d.doc_no }}{% if d.doc_no.startswith('PENDING-') %}<div class="small">awaiting DCC</div>{% endif %}</td><td><a href="{{ url_for('view_document', doc_id=d.id) }}" title="Open document">{{ d.title }}</a> {% if d.has_pdf %}<span class="badge Approved" title="PDF available">PDF</span>{% endif %}</td><td>{{ d.department }}</td><td>{{ d.document_type or '—' }}</td>
<td>{{ d.revision }}</td><td>{{ badge(d.status) }}</td><td>{% set rs = rev_status.get(d.doc_no) %}{% if rs %}{{ badge(rs) }}{% else %}<span class="small">—</span>{% endif %}</td><td>{{ d.created_by }}</td><td>{{ d.date_created|fmt }}</td></tr>
{% else %}<tr><td colspan="9">No documents found.</td></tr>{% endfor %}</tbody></table></div>
<a class="btn" href="{{ url_for('export_master') }}">Export Master List CSV</a></div>{% endblock %}""",

"audit.html": SEARCH + """{% extends 'base.html' %}{% block content %}
<div class="panel"><div class="toolbar"><h2>Audit Trail</h2>{{ search(q, 'Search audit trail...') }}</div>
<div class="table-wrap"><table><thead><tr><th>ID</th><th>User</th><th>Activity</th><th>Date and Time</th></tr></thead><tbody>
{% for l in logs %}<tr><td>{{ l.id }}</td><td>{{ l.username }}</td><td>{{ l.activity }}</td><td>{{ l.timestamp|fmt }}</td></tr>
{% else %}<tr><td colspan="4">No audit records found.</td></tr>{% endfor %}</tbody></table></div></div>{% endblock %}""",

"users.html": """{% extends 'base.html' %}{% block content %}
{% if creatable %}<div class="panel"><h2>Create User Account</h2>
<p class="small">{% if user.role == 'Administrator' %}As Administrator you can create accounts for any role.{% else %}DCC can create {{ creatable|join(', ') }} accounts. Share the username and password securely with the user.{% endif %}</p>
<form method="post" autocomplete="off"><div class="form-row">
<div><label for="full_name">Name of the Person</label><input id="full_name" name="full_name" maxlength="100" required autocomplete="off" placeholder="e.g., Juan Dela Cruz"></div>
<div><label for="section">Section</label><input id="section" name="section" maxlength="80" required autocomplete="off" list="sections" placeholder="e.g., Quality Assurance">
<datalist id="sections">{% for d in DEPARTMENTS %}<option value="{{ d }}">{% endfor %}</datalist></div></div>
<div class="form-row">
<div><label for="username">Username</label><input id="username" name="username" maxlength="30" required autocomplete="off"></div>
<div><label for="role">Role</label><select id="role" name="role">
{% for r in creatable %}<option>{{ r }}</option>{% endfor %}</select></div></div>
<div class="form-row">
<div><label for="password">Password (min. 8 characters)</label><input id="password" name="password" type="password" minlength="8" required autocomplete="new-password"></div>
<div><label for="confirm">Confirm Password</label><input id="confirm" name="confirm" type="password" minlength="8" required autocomplete="new-password"></div></div>
<button type="submit" style="margin-top:15px">Create Account</button></form></div>{% endif %}
<div class="panel"><h2>User Accounts ({{ accounts|length }})</h2>
<div class="table-wrap"><table><thead><tr><th>Name of the Person</th><th>Section</th><th>Username</th><th>Role</th><th>Action</th></tr></thead><tbody>
{% for a in accounts %}<tr><td>{% if a.full_name %}{{ a.full_name }}{% else %}<span class="small">—</span>{% endif %}</td><td>{% if a.section %}{{ a.section }}{% else %}<span class="small">—</span>{% endif %}</td><td>{{ a.username }}</td><td>{{ a.role }}</td>
<td>{% if user.role in PROCESSORS and a.username != user.username and (user.role == 'Administrator' or a.role in ['Requestor','Author','ADMR']) %}<form method="post" action="{{ url_for('reset_user_password', username=a.username) }}" style="display:inline" onsubmit="return confirm('Create a new temporary password for {{ (a.full_name or a.username)|e }}?')"><button type="submit" class="secondary" style="padding:5px 9px;font-size:12px">Reset password</button></form> <form method="post" action="{{ url_for('delete_user', username=a.username) }}" style="display:inline" onsubmit="return confirm('Remove the account of {{ (a.full_name or a.username)|e }} ({{ a.username|e }})? They will no longer be able to log in. Their past records are kept.')"><button type="submit" class="danger" style="padding:5px 9px;font-size:12px">Remove</button></form>{% else %}<span class="small">—</span>{% endif %}</td></tr>
{% else %}<tr><td colspan="5">No accounts found.</td></tr>{% endfor %}</tbody></table></div></div>{% endblock %}""",
}

app.jinja_loader = DictLoader(TEMPLATES)

if __name__ == "__main__":
    # `python app.py` now uses the Waitress production server too, so Flask's
    # "development server" warning can never appear. (start_server.bat / serve.py do the same.)
    init_db()
    host = "0.0.0.0"
    PORT = 5000
    try:
        from waitress import serve
    except ImportError:
        raise SystemExit("Waitress (the production server) is not installed.\n"
                         "Run install_server.bat, or:  pip install -r requirements.txt")
    print(f"SGIC DMS running on port {PORT}  (Waitress production server, press Ctrl+C to stop). Use: python serve.py to see the LAN address.")
    serve(app, host=host, port=PORT, threads=8)
