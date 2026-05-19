#!/usr/bin/env python
"""Sync engine: pull items from Zotero Web API into local SQLite database."""
import argparse
import sys
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
    parser = argparse.ArgumentParser(description="Sync Zotero library to local DB")
    parser.add_argument("--depth", type=int, default=1)
    parser.add_argument("--full", action="store_true",
                        help="Force full sync (ignore incremental state)")
    args = parser.parse_args()

    config = load_config()
    data_dir = get_data_dir(config)
    set_db_path(data_dir)

    zot_config = config["zotero"]
    zot = Zotero(zot_config["library_id"], zot_config["library_type"], zot_config["api_key"])

    conn = get_db()
    init_db(conn)

    print(f"Zotero Bridge — Sync")
    print(f"  Library: {zot_config['library_id']} ({zot_config['library_type']})")

    try:
        sync_items(zot, conn, incremental=not args.full)
    except Exception as e:
        print(f"  Error: {e}")
        conn.close()
        sys.exit(1)

    stats = get_stats(conn)
    print(f"\n  Library: {stats['total']} items | "
          f"L1:{stats['L1_abstract']} L2:{stats['L2_fulltext']} L3:{stats['L3_summarized']} | "
          f"PDFs:{stats['with_pdf']}")
    conn.close()

if __name__ == "__main__":
    main()
