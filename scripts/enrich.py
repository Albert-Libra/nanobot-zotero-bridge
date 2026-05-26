#!/usr/bin/env python
"""Abstract enrichment: fetch missing abstracts from Semantic Scholar / Crossref.

Three-tier fallback:
  1. Semantic Scholar by DOI    (fastest, structured)
  2. Crossref by DOI             (broad DOI coverage)
  3. Semantic Scholar by title   (last resort)

Rate limiting: polite delays to avoid 429 responses.
"""
import argparse
import io
import json
import re
import sys
import time
from pathlib import Path

import requests

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from db import get_db, init_db, set_db_path, update_abstract
from config import load_config, get_data_dir

S2_API = "https://api.semanticscholar.org/graph/v1"
CROSSREF_API = "https://api.crossref.org/works"
DEFAULT_TIMEOUT = 15


def fetch_abstract_s2_by_doi(doi: str) -> str | None:
    """Try Semantic Scholar by DOI."""
    try:
        resp = requests.get(
            f"{S2_API}/paper/DOI:{doi}?fields=abstract",
            timeout=DEFAULT_TIMEOUT,
        )
        if resp.status_code == 200:
            abstract = resp.json().get("abstract")
            if abstract and len(abstract.strip()) > 20:
                return abstract.strip()
    except Exception:
        pass
    return None


def fetch_abstract_crossref(doi: str) -> str | None:
    """Try Crossref by DOI. Strips HTML/jats tags from returned abstracts."""
    try:
        resp = requests.get(f"{CROSSREF_API}/{doi}", timeout=DEFAULT_TIMEOUT)
        if resp.status_code == 200:
            abstract = resp.json().get("message", {}).get("abstract")
            if abstract:
                # Strip HTML/XML tags
                abstract = re.sub(r"<[^>]+>", " ", abstract)
                abstract = re.sub(r"\s+", " ", abstract).strip()
                if len(abstract) > 50:
                    return abstract
    except Exception:
        pass
    return None


def fetch_abstract_s2_by_title(title: str) -> str | None:
    """Try Semantic Scholar by title search."""
    try:
        resp = requests.get(
            f"{S2_API}/paper/search",
            params={"query": title, "limit": 1, "fields": "abstract"},
            timeout=DEFAULT_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            if data:
                abstract = data[0].get("abstract")
                if abstract and len(abstract.strip()) > 20:
                    return abstract.strip()
    except Exception:
        pass
    return None


class EnrichState:
    """Mutable container to collect enrichment results across the loop."""
    def __init__(self):
        self.failed_papers: list[dict] = []


def enrich_abstracts(config: dict, keys: list[str] | None = None,
                     dry_run: bool = False, limit: int = 0) -> tuple[int, int]:
    """Enrich items missing abstracts.

    Args:
        config: Bridge config dict.
        keys: Specific paper keys to process. None = all missing.
        dry_run: Preview only, don't write to DB.
        limit: Max items to process (0 = unlimited).

    Returns:
        (enriched_count, skipped_count)
    """
    data_dir = get_data_dir(config)
    set_db_path(data_dir)
    conn = get_db()
    init_db(conn)

    state = EnrichState()

    if keys:
        placeholders = ",".join("?" * len(keys))
        rows = conn.execute(
            f"SELECT key, title, doi FROM items WHERE key IN ({placeholders})",
            keys,
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT key, title, doi FROM items WHERE abstract IS NULL OR abstract = ''"
        ).fetchall()

    if limit > 0:
        rows = rows[:limit]

    total = len(rows)
    enriched = 0
    skipped = 0

    print(f"Abstract enrichment: {total} items without abstracts")

    for i, row in enumerate(rows):
        key = row["key"]
        title = row["title"]
        doi = row["doi"]

        label = (title or "Untitled")[:70]
        print(f"  [{i + 1}/{total}] {label}...", end=" ")

        abstract = None
        source = None

        # Tier 1: DOI → Semantic Scholar
        if doi:
            abstract = fetch_abstract_s2_by_doi(doi)
            if abstract:
                source = "Semantic Scholar (DOI)"

        # Tier 2: DOI → Crossref
        if doi and not abstract:
            abstract = fetch_abstract_crossref(doi)
            if abstract:
                source = "Crossref (DOI)"

        # Tier 3: Title → Semantic Scholar
        if not abstract and title:
            abstract = fetch_abstract_s2_by_title(title)
            if abstract:
                source = "Semantic Scholar (title)"

        if abstract:
            if not dry_run:
                update_abstract(conn, key, abstract)
            enriched += 1
            preview = abstract[:120].replace("\n", " ")
            print(f"✓ [{source}] {preview}…")
        else:
            skipped += 1
            print("✗ (no abstract found)")
            state.failed_papers.append({
                "key": key,
                "title": title or "Untitled",
                "doi": doi or "",
            })

        # Polite rate limiting: brief pause every 5 requests
        if (i + 1) % 5 == 0:
            time.sleep(0.5)

    # Collect failed papers for user action
    if state.failed_papers:
        print(f"\n{'─' * 60}")
        print(f"  ⚠  ACTION REQUIRED: {len(state.failed_papers)} paper(s) need manual abstracts")
        print(f"{'─' * 60}")
        for fp in state.failed_papers:
            print(f"  [{fp['key']}] {fp['title'][:80]}")
            if fp.get("doi"):
                print(f"    DOI: {fp['doi']}")
            print(f"    手动设置: python enrich.py --set-abstract {fp['key']} \"<paste abstract here>\"")
            print(f"    或从文件: python enrich.py --set-abstract-file {fp['key']} <path.txt>")
            print()

    print(f"\n  Done: {enriched} enriched, {skipped} skipped, {total} total")
    conn.close()
    return enriched, skipped


def set_abstract_manually(config, key: str, abstract: str) -> bool:
    """Set an abstract from user-provided text."""
    data_dir = get_data_dir(config)
    set_db_path(data_dir)
    conn = get_db()
    init_db(conn)

    row = conn.execute("SELECT title FROM items WHERE key = ?", (key,)).fetchone()
    if not row:
        print(f"  ✗ Key not found: {key}")
        conn.close()
        return False

    from db import update_abstract
    update_abstract(conn, key, abstract)
    title = (row["title"] or "?")[:60]
    print(f"  ✓ [{key}] {title} — abstract set manually")
    conn.close()
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Enrich missing abstracts from Semantic Scholar / Crossref"
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Process all items without abstracts",
    )
    parser.add_argument("--key", type=str, help="Enrich a single paper by key")
    parser.add_argument(
        "--keys", type=str, nargs="+", help="Enrich multiple papers by key"
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview without saving")
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Max items to process (0 = unlimited; useful for testing)",
    )
    parser.add_argument(
        "--set-abstract", nargs=2, metavar=("KEY", "TEXT"),
        help="Manually set abstract for a paper (KEY 'abstract text')",
    )
    parser.add_argument(
        "--set-abstract-file", nargs=2, metavar=("KEY", "PATH"),
        help="Manually set abstract from a text file (KEY path.txt)",
    )
    args = parser.parse_args()

    config = load_config()

    # ── Manual abstract setting modes ──
    if args.set_abstract:
        key, text = args.set_abstract
        ok = set_abstract_manually(config, key, text)
        return 0 if ok else 1

    if args.set_abstract_file:
        key, path = args.set_abstract_file
        try:
            text = Path(path).read_text(encoding="utf-8")
            ok = set_abstract_manually(config, key, text)
            return 0 if ok else 1
        except FileNotFoundError:
            print(f"  ✗ File not found: {path}")
            return 1
        except Exception as e:
            print(f"  ✗ Error reading file: {e}")
            return 1

    if args.key:
        enrich_abstracts(config, keys=[args.key], dry_run=args.dry_run)
    elif args.keys:
        enrich_abstracts(config, keys=args.keys, dry_run=args.dry_run)
    elif args.all:
        enrich_abstracts(config, dry_run=args.dry_run, limit=args.limit)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
