"""SQLite database layer for Zotero Bridge — schema, FTS5, CRUD."""
import sqlite3
import json
from pathlib import Path

# These are patched at runtime by config
DB_DIR = None
DB_PATH = None


def set_db_path(data_dir: Path):
    global DB_DIR, DB_PATH
    DB_DIR = data_dir
    DB_PATH = data_dir / "zotero.db"


def get_db() -> sqlite3.Connection:
    if DB_PATH is None:
        # Fallback: default relative path
        default = Path(__file__).parent.parent.parent / "zotero-data" / "zotero.db"
        set_db_path(default.parent)
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT UNIQUE NOT NULL,
            item_type TEXT,
            title TEXT,
            creators TEXT,
            date TEXT,
            publication TEXT,
            doi TEXT,
            url TEXT,
            abstract TEXT,
            tags TEXT,
            collections TEXT,
            date_added TEXT,
            date_modified TEXT,
            version INTEGER DEFAULT 0,
            processing_level INTEGER DEFAULT 0,
            has_pdf INTEGER DEFAULT 0,
            local_summary TEXT,
            raw_json TEXT
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
            title, abstract, full_text, summary
        );

        CREATE TABLE IF NOT EXISTS citations (
            paper_key TEXT NOT NULL,
            cited_key TEXT NOT NULL,
            relation TEXT NOT NULL CHECK(relation IN ('cites', 'cited_by')),
            title TEXT,
            year TEXT,
            doi TEXT,
            PRIMARY KEY (paper_key, cited_key, relation)
        );

        CREATE TABLE IF NOT EXISTS sync_state (
            id INTEGER PRIMARY KEY CHECK(id=1),
            last_version INTEGER DEFAULT 0,
            total_synced INTEGER DEFAULT 0,
            last_sync TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_items_key ON items(key);
        CREATE INDEX IF NOT EXISTS idx_items_doi ON items(doi);
        CREATE INDEX IF NOT EXISTS idx_items_processing ON items(processing_level);
        CREATE INDEX IF NOT EXISTS idx_citations_paper ON citations(paper_key);
    """)
    conn.commit()


# ── Items CRUD ──────────────────────────────────────────────

def upsert_item(conn: sqlite3.Connection, item_data: dict) -> int:
    data = item_data.get("data", item_data)
    key = data["key"]
    rowid = conn.execute("SELECT id FROM items WHERE key = ?", (key,)).fetchone()

    creators = json.dumps(data.get("creators", []), ensure_ascii=False)
    tags_raw = data.get("tags", [])
    tags_json = json.dumps(
        [t.get("tag", t) if isinstance(t, dict) else t for t in tags_raw],
        ensure_ascii=False
    )
    collections = json.dumps(
        data.get("collections", []), ensure_ascii=False
    )

    if rowid:
        conn.execute("""
            UPDATE items SET item_type=?, title=?, creators=?, date=?,
                publication=?, doi=?, url=?, abstract=?, tags=?,
                collections=?, version=?, date_added=?, date_modified=?, raw_json=?
            WHERE key=?
        """, (
            data.get("itemType"), data.get("title"), creators, data.get("date"),
            data.get("publicationTitle") or data.get("repository"),
            data.get("DOI"), data.get("url"), data.get("abstractNote"),
            tags_json, collections, data.get("version", 0),
            data.get("dateAdded"), data.get("dateModified"),
            json.dumps(item_data, ensure_ascii=False), key
        ))
        rid = rowid["id"]
        # Refresh FTS (content-sync uses rowid)
        conn.execute("DELETE FROM items_fts WHERE rowid=?", (rid,))
        conn.execute("INSERT INTO items_fts(rowid, title, abstract) VALUES (?,?,?)",
                     (rid, data.get("title", ""), data.get("abstractNote", "")))
    else:
        c = conn.execute("""
            INSERT INTO items (key,item_type,title,creators,date,publication,
                doi,url,abstract,tags,collections,version,date_added,date_modified,raw_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            key, data.get("itemType"), data.get("title"), creators, data.get("date"),
            data.get("publicationTitle") or data.get("repository"),
            data.get("DOI"), data.get("url"), data.get("abstractNote"),
            tags_json, collections, data.get("version", 0),
            data.get("dateAdded"), data.get("dateModified"),
            json.dumps(item_data, ensure_ascii=False)
        ))
        rid = c.lastrowid
        conn.execute("INSERT INTO items_fts(rowid, title, abstract) VALUES (?,?,?)",
                     (rid, data.get("title", ""), data.get("abstractNote", "")))
    conn.commit()
    return rid


def get_item_by_key(conn, key: str) -> dict | None:
    row = conn.execute("SELECT * FROM items WHERE key = ?", (key,)).fetchone()
    return dict(row) if row else None


def get_all_items(conn, limit: int = None, offset: int = 0) -> list[dict]:
    if limit:
        rows = conn.execute(
            "SELECT * FROM items ORDER BY date_added DESC LIMIT ? OFFSET ?",
            (limit, offset)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM items ORDER BY date_added DESC").fetchall()
    return [dict(r) for r in rows]


def update_processing_level(conn, key: str, level: int):
    conn.execute("UPDATE items SET processing_level = MAX(processing_level, ?) WHERE key = ?",
                 (level, key))
    conn.commit()


def update_full_text(conn, key: str, full_text: str):
    """Store full text in FTS index."""
    row = conn.execute("SELECT id FROM items WHERE key = ?", (key,)).fetchone()
    if row:
        # FTS5 content-sync: delete then insert with rowid
        conn.execute("DELETE FROM items_fts WHERE rowid=?", (row["id"],))
        conn.execute("INSERT INTO items_fts(rowid, full_text) VALUES (?,?)",
                     (row["id"], full_text))
        update_processing_level(conn, key, 2)


def update_summary(conn, key: str, summary: str):
    """Store LLM summary in FTS index and items table."""
    row = conn.execute("SELECT id FROM items WHERE key = ?", (key,)).fetchone()
    if row:
        # FTS5 content-sync: delete then insert with rowid
        conn.execute("DELETE FROM items_fts WHERE rowid=?", (row["id"],))
        conn.execute("INSERT INTO items_fts(rowid, summary) VALUES (?,?)",
                     (row["id"], summary))
        conn.execute("UPDATE items SET local_summary = ? WHERE key = ?", (summary, key))
        update_processing_level(conn, key, 3)


# ── Search ──────────────────────────────────────────────────

def search_fts(conn, query: str, top_k: int = 10, level_min: int = 0) -> list[dict]:
    """Full-text search across title, abstract, full_text, summary."""
    rows = conn.execute("""
        SELECT items.*, items_fts.rank
        FROM items_fts
        JOIN items ON items_fts.rowid = items.id
        WHERE items_fts MATCH ? AND items.processing_level >= ?
        ORDER BY rank
        LIMIT ?
    """, (query, level_min, top_k)).fetchall()
    return [dict(r) for r in rows]


# ── Citations ───────────────────────────────────────────────

def upsert_citation(conn, paper_key: str, cited_key: str, relation: str,
                    title: str = None, year: str = None, doi: str = None):
    conn.execute("""
        INSERT OR REPLACE INTO citations (paper_key, cited_key, relation, title, year, doi)
        VALUES (?,?,?,?,?,?)
    """, (paper_key, cited_key, relation, title, year, doi))
    conn.commit()


def get_citations(conn, paper_key: str, relation: str = None) -> list[dict]:
    if relation:
        rows = conn.execute(
            "SELECT * FROM citations WHERE paper_key=? AND relation=?",
            (paper_key, relation)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM citations WHERE paper_key=?", (paper_key,)
        ).fetchall()
    return [dict(r) for r in rows]


# ── Stats ───────────────────────────────────────────────────

def get_stats(conn) -> dict:
    total = conn.execute("SELECT COUNT(*) as n FROM items").fetchone()["n"]
    l1 = conn.execute("SELECT COUNT(*) as n FROM items WHERE processing_level>=1").fetchone()["n"]
    l2 = conn.execute("SELECT COUNT(*) as n FROM items WHERE processing_level>=2").fetchone()["n"]
    l3 = conn.execute("SELECT COUNT(*) as n FROM items WHERE processing_level>=3").fetchone()["n"]
    pdf = conn.execute("SELECT COUNT(*) as n FROM items WHERE has_pdf=1").fetchone()["n"]
    return {"total": total, "L1_abstract": l1, "L2_fulltext": l2,
            "L3_summarized": l3, "with_pdf": pdf}


# ── Sync State ──────────────────────────────────────────────

def load_sync_state(conn) -> dict:
    row = conn.execute("SELECT * FROM sync_state WHERE id=1").fetchone()
    if row:
        return {"last_version": row["last_version"], "total_synced": row["total_synced"]}
    return {"last_version": 0, "total_synced": 0}


def save_sync_state(conn, version: int, total: int):
    conn.execute("""
        INSERT INTO sync_state (id, last_version, total_synced, last_sync)
        VALUES (1, ?, ?, datetime('now'))
        ON CONFLICT(id) DO UPDATE SET
            last_version=excluded.last_version,
            total_synced=excluded.total_synced,
            last_sync=excluded.last_sync
    """, (version, total))
    conn.commit()
