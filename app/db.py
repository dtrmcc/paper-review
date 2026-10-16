"""SQLite storage. One file: $DATA_DIR/review.sqlite3. Manuscripts live in $DATA_DIR/files/."""
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

DATA_DIR = os.environ.get("DATA_DIR", "/data")
DB_PATH = os.path.join(DATA_DIR, "review.sqlite3")
FILES_DIR = os.path.join(DATA_DIR, "files")

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, pw_hash TEXT NOT NULL, created TEXT);
CREATE TABLE IF NOT EXISTS sessions (
  token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created TEXT, expires TEXT);
CREATE TABLE IF NOT EXISTS login_failures (ip TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  journal TEXT, journal_url TEXT, manuscript_id TEXT,
  deadline TEXT, received TEXT,
  status TEXT DEFAULT 'new',              -- new / reading / drafting / submitted / archived
  ai_policy TEXT DEFAULT 'local',         -- none / local / remote
  recommendation TEXT,                    -- accept / minor / major / reject / ''
  blind TEXT DEFAULT 'unknown',           -- single / double / open / unknown
  author_names TEXT,                      -- for self-citation check (comma separated)
  memo TEXT,
  ai_context TEXT,                        -- redacted manuscript text the user approved for AI
  ai_context_updated TEXT,
  created TEXT, updated TEXT);

CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL,
  label TEXT,                             -- Original / R1 / R2 / Response letter / Supplement
  kind TEXT DEFAULT 'manuscript',         -- manuscript / response / supplement
  filename TEXT, orig_path TEXT, pdf_path TEXT,
  pages INTEGER, text_path TEXT, tei_path TEXT,
  grobid_status TEXT,                     -- pending / done / failed
  meta TEXT, uploaded TEXT);

CREATE TABLE IF NOT EXISTS annotations (
  id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL, page INTEGER,
  type TEXT, color TEXT, rects TEXT, quote TEXT, comment TEXT, created TEXT, updated TEXT);

CREATE TABLE IF NOT EXISTS notes (project_id INTEGER PRIMARY KEY, content TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS note_history (
  id INTEGER PRIMARY KEY, project_id INTEGER, content TEXT, saved_at TEXT);

CREATE TABLE IF NOT EXISTS chats (
  id INTEGER PRIMARY KEY, project_id INTEGER, role TEXT, content TEXT,
  provider TEXT, model TEXT, included_manuscript INTEGER DEFAULT 0, created TEXT);

CREATE TABLE IF NOT EXISTS reviews (
  id INTEGER PRIMARY KEY, project_id INTEGER, kind TEXT,  -- authors / editor
  content TEXT, provider TEXT, model TEXT, edited INTEGER DEFAULT 0, created TEXT);

CREATE TABLE IF NOT EXISTS refs (
  id INTEGER PRIMARY KEY, document_id INTEGER, idx INTEGER, xml_id TEXT,
  raw TEXT, title TEXT, authors TEXT, year TEXT, venue TEXT, doi TEXT,
  status TEXT DEFAULT 'pending',          -- pending / ok / mismatch / notfound / retracted / error
  result TEXT, user_flag TEXT, user_note TEXT, checked_at TEXT);

CREATE TABLE IF NOT EXISTS tool_results (
  id INTEGER PRIMARY KEY, project_id INTEGER, document_id INTEGER, tool TEXT,
  result TEXT, created TEXT);

CREATE TABLE IF NOT EXISTS response_items (
  id INTEGER PRIMARY KEY, project_id INTEGER, position INTEGER,
  comment TEXT, response TEXT, status TEXT DEFAULT 'open', my_note TEXT);

CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY, project_id INTEGER, document_id INTEGER, kind TEXT,
  status TEXT, progress TEXT, error TEXT, created TEXT, updated TEXT);

CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY, project_id INTEGER, at TEXT, provider TEXT, model TEXT,
  purpose TEXT, included_manuscript INTEGER, chars INTEGER, payload TEXT);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def conn():
    c = getattr(_local, "conn", None)
    if c is None:
        c = sqlite3.connect(DB_PATH, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        _local.conn = c
    return c


def init():
    os.makedirs(FILES_DIR, exist_ok=True)
    c = conn()
    c.executescript(SCHEMA)
    c.commit()


def q(sql, args=()):
    return [dict(r) for r in conn().execute(sql, args).fetchall()]


def q1(sql, args=()):
    r = conn().execute(sql, args).fetchone()
    return dict(r) if r else None


def ex(sql, args=()):
    c = conn()
    cur = c.execute(sql, args)
    c.commit()
    return cur.lastrowid


def get_setting(key, default=None):
    r = q1("SELECT value FROM settings WHERE key=?", (key,))
    return r["value"] if r and r["value"] not in (None, "") else default


def set_setting(key, value):
    ex("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
       (key, value))


def jdump(o):
    return json.dumps(o, ensure_ascii=False, default=lambda x: x.item() if hasattr(x, "item") else str(x))


def jload(s, default=None):
    try:
        return json.loads(s) if s else default
    except Exception:
        return default
