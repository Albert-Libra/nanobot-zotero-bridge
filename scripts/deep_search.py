#!/usr/bin/env python
"""Deep search: combines Zotero local search + web search for comprehensive results."""
import argparse
import io
import sys

# Fix Unicode output on Windows GBK console
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from db import get_db, init_db, search_fts, set_db_path
from config import load_config, get_data_dir


def search_local(query: str, top_k: int = 10) -> list[dict]:
    """Search local Zotero database."""
    config = load_config()
    data_dir = get_data_dir(config)
    set_db_path(data_dir)
    conn = get_db()
    init_db(conn)
    results = search_fts(conn, query, top_k=top_k)
    conn.close()
    return results


def format_deep_results(query: str, local_results: list[dict]) -> str:
    """Format results for agent consumption, with web search guidance."""
    import json as _json

    def _fmt_authors(creators_raw):
        """Parse creators JSON and return formatted string."""
        if not creators_raw:
            return "N/A"
        if isinstance(creators_raw, str):
            try:
                creators = _json.loads(creators_raw)
            except (_json.JSONDecodeError, TypeError):
                return creators_raw
        elif isinstance(creators_raw, list):
            creators = creators_raw
        else:
            return "N/A"
        names = []
        for c in creators:
            if isinstance(c, dict):
                last = c.get("lastName", "")
                first = c.get("firstName", "")
                if last:
                    names.append(f"{last}{', ' + first if first else ''}")
            elif isinstance(c, str):
                names.append(c)
        return "; ".join(names) if names else "N/A"

    lines = [f"# Deep Search: \"{query}\"\n"]

    lines.append("## Zotero Library Results\n")
    if local_results:
        lines.append(f"Found {len(local_results)} papers in your library:\n")
        for i, item in enumerate(local_results, 1):
            lines.append(f"### {i}. {item.get('title', 'Untitled')}")
            lines.append(f"- **Authors**: {_fmt_authors(item.get('creators'))}")
            lines.append(
                f"- **Year**: {item.get('date', 'N/A')}  "
                f"**Journal**: {item.get('publication', 'N/A')}"
            )
            lines.append(
                f"- **DOI**: {item.get('doi', 'N/A')}  "
                f"**Level**: L{item.get('processing_level', 0)}"
            )
            if item.get("abstract"):
                lines.append(f"- **Abstract**: {item['abstract'][:300]}...")
            if (
                item.get("local_summary")
                and item.get("local_summary") not in ("null", None, "None")
            ):
                lines.append(
                    f"- **AI Summary**: {item['local_summary'][:200]}..."
                )
            lines.append("")
    else:
        lines.append("No matching papers found in your Zotero library.\n")

    lines.append("---")
    lines.append("## Web Search Guidance")
    lines.append("Based on the above, here are recommended web search queries:")
    lines.append(f'1. "{query} review" — for recent reviews')
    lines.append(f'2. "{query} 2024 2025" — for latest papers')
    if local_results:
        # Extract key terms from top result
        top = local_results[0]
        title_words = top.get("title", "").split()[:3]
        if title_words:
            lines.append(f'3. "{" ".join(title_words)}" related — for similar work')
    lines.append("")
    lines.append("> Note: Zotero library is the priority source. Web search supplements for completeness.")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Deep search: Zotero + web search guidance")
    parser.add_argument("query", nargs="+", help="Search query")
    parser.add_argument("--top-k", type=int, default=10)
    args = parser.parse_args()

    query = " ".join(args.query)
    local = search_local(query, top_k=args.top_k)
    output = format_deep_results(query, local)
    print(output)


if __name__ == "__main__":
    main()
