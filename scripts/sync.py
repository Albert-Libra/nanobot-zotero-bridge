#!/usr/bin/env python
"""Sync engine: pull items from Zotero Web API into local SQLite database.

Depth levels:
  0 — metadata only
  1 — +abstract
  2 — +fulltext (auto-download PDFs via Unpaywall / Sci-Hub)
  3 — +LLM summary + citation graph
"""
import argparse
import io
import sys

# Fix Unicode output on Windows GBK console
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from pyzotero import Zotero
from db import get_db, init_db, upsert_item, get_stats, load_sync_state, save_sync_state, set_db_path
from config import load_config, get_data_dir


def sync_items(zot: Zotero, conn, incremental: bool = True, limit: int = 100):
    if incremental:
        state = load_sync_state(conn)
    else:
        state = {"last_version": 0, "total_synced": 0}

    new_count = 0
    updated_count = 0
    max_version = state["last_version"]

    if incremental and state["last_version"] > 0:
        # Incremental: get items modified since last version
        items = zot.items(since=state["last_version"], limit=None)
        for item in items:
            upsert_item(conn, item)
            v = item.get("data", item).get("version", 0)
            if v > max_version:
                max_version = v
            updated_count += 1
        print(f"  Updated {updated_count} items")
    else:
        # Full sync: paginate through all items
        start = 0
        while True:
            items = zot.top(limit=limit, start=start)
            if not items:
                break
            for item in items:
                upsert_item(conn, item)
                v = item.get("data", item).get("version", 0)
                if v > max_version:
                    max_version = v
                new_count += 1
            start += limit
            print(f"  Synced {start} items so far...", end="\r")
        print(f"\n  Synced {new_count} new items")

    total = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    save_sync_state(conn, max_version, total)
    return new_count, updated_count


def main():
    parser = argparse.ArgumentParser(
        description="Sync Zotero library to local DB")
    parser.add_argument("--depth", type=int, default=1,
                        help="0=metadata, 1=+abstract, 2=+fulltext(download PDFs), 3=+summary")
    parser.add_argument("--full", action="store_true",
                        help="Force full sync (ignore incremental state)")
    parser.add_argument("--limit", type=int, default=0,
                        help="Max PDFs to download at depth >= 2 (0 = all)")
    args = parser.parse_args()

    config = load_config()
    data_dir = get_data_dir(config)
    set_db_path(data_dir)

    zot_config = config["zotero"]
    zot = Zotero(zot_config["library_id"], zot_config["library_type"],
                 zot_config["api_key"])

    conn = get_db()
    init_db(conn)

    print(f"Zotero Bridge — Sync (depth={args.depth})")
    print(f"  Library: {zot_config['library_id']} ({zot_config['library_type']})")

    # --- Depth 0-1: metadata sync ---
    try:
        sync_items(zot, conn, incremental=not args.full)
    except Exception as e:
        print(f"  Error: {e}")
        conn.close()
        sys.exit(1)

    # --- Depth 2+: full-text PDF acquisition ---
    if args.depth >= 2:
        from ingest import batch_ingest
        print(f"\n  --- Depth 2: PDF acquisition ---")
        batch_ingest(config, limit=args.limit)

    # --- Depth 3+: LLM summaries ---
    if args.depth >= 3:
        print(f"\n  --- Depth 3: LLM summarization ---")
        # TODO: call summarize.py batch
        print("  (summarization not yet integrated — use summarize.py directly)")

    stats = get_stats(conn)
    print(f"\n  Library: {stats['total']} items | "
          f"L1:{stats['L1_abstract']} L2:{stats['L2_fulltext']} L3:{stats['L3_summarized']} | "
          f"PDFs:{stats['with_pdf']}")
    conn.close()


if __name__ == "__main__":
    main()
