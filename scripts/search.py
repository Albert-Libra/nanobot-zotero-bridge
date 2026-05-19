#!/usr/bin/env python
"""Search local Zotero database with FTS5. Supports RAG context building."""
import argparse
import json
import sys
from db import get_db, init_db, search_fts, get_citations, get_stats, set_db_path
from config import load_config, get_data_dir


def format_item(item: dict, mode: str = "brief") -> str:
    if mode == "brief":
        authors = item.get("creators", "[]")
        if isinstance(authors, str):
            try:
                authors = json.loads(authors)
            except (json.JSONDecodeError, TypeError):
                authors = []
        author_str = ""
        if authors:
            names = [f"{a.get('lastName', a.get('name', ''))}" for a in authors[:3]]
            author_str = ", ".join(names)
            if len(authors) > 3:
                author_str += " et al."
        return (
            f"[{item.get('date', 'N/A')}] {item.get('title', 'Untitled')}\n"
            f"  Authors: {author_str or 'N/A'}\n"
            f"  Journal: {item.get('publication', 'N/A')}  DOI: {item.get('doi', 'N/A')}\n"
            f"  Level: L{item.get('processing_level', 0)}  Key: {item.get('key', '')}"
        )

    elif mode == "rag":
        parts = [f"Title: {item.get('title', 'Untitled')}"]
        authors = item.get("creators", "[]")
        if isinstance(authors, str):
            try:
                authors = json.loads(authors)
            except (json.JSONDecodeError, TypeError):
                authors = []
        if authors:
            parts.append(f"Authors: {', '.join(a.get('lastName', a.get('name', '')) for a in authors)}")
        parts.append(f"Year: {item.get('date', 'N/A')}")
        parts.append(f"Journal: {item.get('publication', 'N/A')}")
        parts.append(f"DOI: {item.get('doi', 'N/A')}")
        if item.get("abstract"):
            parts.append(f"Abstract: {item['abstract']}")
        if item.get("local_summary") and item.get("local_summary") not in ("null", None, "None"):
            parts.append(f"AI Summary: {item['local_summary']}")
        return "\n".join(parts)

    elif mode == "json":
        return json.dumps(item, ensure_ascii=False, indent=2)

    return str(item)


def build_rag_context(results: list[dict]) -> str:
    if not results:
        return "No relevant papers found in Zotero library."
    lines = [f"Found {len(results)} relevant papers in your Zotero library:\n"]
    for i, item in enumerate(results, 1):
        lines.append(f"--- Paper {i} ---")
        lines.append(format_item(item, "rag"))
        lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Search local Zotero database")
    parser.add_argument("query", nargs="+", help="Search query")
    parser.add_argument("--top-k", type=int, default=10, help="Max results (default 10)")
    parser.add_argument("--mode", choices=["brief", "rag", "json"], default="brief",
                        help="Output mode: brief (default), rag (context injection), json")
    parser.add_argument("--level-min", type=int, default=0,
                        help="Minimum processing level filter")
    args = parser.parse_args()

    config = load_config()
    data_dir = get_data_dir(config)
    set_db_path(data_dir)

    conn = get_db()
    init_db(conn)

    query = " ".join(args.query)
    results = search_fts(conn, query, top_k=args.top_k, level_min=args.level_min)

    if not results:
        print(f"No results found for: {query}")
        conn.close()
        sys.exit(0)

    if args.mode == "rag":
        output = build_rag_context(results)
    else:
        output = f"Found {len(results)} results for: {query}\n"
        for i, item in enumerate(results, 1):
            output += f"\n--- Result {i} ---\n"
            output += format_item(item, args.mode)

    print(output)
    conn.close()


if __name__ == "__main__":
    main()
