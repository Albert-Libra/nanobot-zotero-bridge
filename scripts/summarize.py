#!/usr/bin/env python
"""LLM auto-summary generation (L3) + Semantic Scholar citation fetching."""
import argparse
import io
import json
import sys

# Fix Unicode output on Windows GBK console
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import requests
from db import get_db, init_db, get_item_by_key, get_all_items, update_summary, \
    upsert_citation, get_citations, set_db_path
from config import load_config, get_data_dir

# Semantic Scholar API base
S2_API = "https://api.semanticscholar.org/graph/v1"


def fetch_citations_s2(doi: str = None, paper_id: str = None) -> dict:
    """Fetch citations from Semantic Scholar for a paper."""
    if doi:
        url = f"{S2_API}/paper/DOI:{doi}?fields=citations.title,citations.paperId,citations.year,citations.externalIds,references.title,references.paperId,references.year,references.externalIds"
        resp = requests.get(url, timeout=30)
    elif paper_id:
        url = f"{S2_API}/paper/{paper_id}?fields=citations.title,citations.paperId,citations.year,citations.externalIds,references.title,references.paperId,references.year,references.externalIds"
        resp = requests.get(url, timeout=30)
    else:
        return {"citations": [], "references": []}

    if resp.status_code != 200:
        return {"citations": [], "references": []}
    return resp.json()


def fetch_s2_by_title(title: str) -> dict | None:
    """Search Semantic Scholar by title to get paper ID and citation counts."""
    resp = requests.get(f"{S2_API}/paper/search", params={
        "query": title, "limit": 1,
        "fields": "paperId,title,citationCount,externalIds"
    }, timeout=30)
    if resp.status_code == 200:
        data = resp.json().get("data", [])
        if data:
            return data[0]
    return None


def store_citations(conn, paper_key: str, citations_data: dict):
    """Store forward (cited_by) and backward (cites/references) citations."""
    # References (backward)
    refs = citations_data.get("references", [])
    for ref in refs:
        ref_doi = (ref.get("externalIds") or {}).get("DOI", "")
        upsert_citation(conn, paper_key, ref.get("paperId", ref.get("title", "")),
                        "cites", ref.get("title"), ref.get("year"), ref_doi)

    # Citations (forward)
    cites = citations_data.get("citations", [])
    for cite in cites:
        cite_doi = (cite.get("externalIds") or {}).get("DOI", "")
        upsert_citation(conn, paper_key, cite.get("paperId", cite.get("title", "")),
                        "cited_by", cite.get("title"), cite.get("year"), cite_doi)


def summarize_paper(key: str, config: dict) -> str:
    """Generate an AI summary for a paper.
    
    Uses the agent's LLM context. When run as a script, prints a structured
    template that the agent should complete. In agent mode, the agent calls
    this and then fills in the summary based on available content (abstract,
    full text).
    """
    data_dir = get_data_dir(config)
    set_db_path(data_dir)
    conn = get_db()
    init_db(conn)

    item = get_item_by_key(conn, key)
    if not item:
        print(f"Paper not found: {key}")
        conn.close()
        return ""

    title = item.get("title", "Unknown")
    print(f"Summarizing: {title[:80]}")

    # Gather available content
    abstract = item.get("abstract") or ""
    full_text = ""
    fulltext_path = data_dir / "fulltext" / f"{key}.md"
    if fulltext_path.exists():
        full_text = fulltext_path.read_text(encoding="utf-8")[:8000]  # truncate for LLM

    # Fetch citations
    doi = item.get("doi")
    if doi:
        print(f"  Fetching citations from Semantic Scholar...")
        s2_data = fetch_citations_s2(doi=doi)
        store_citations(conn, key, s2_data)
        cites_count = len(s2_data.get("citations", []))
        refs_count = len(s2_data.get("references", []))
        print(f"  Citations: {cites_count} forward, {refs_count} backward")
    else:
        # Try by title
        s2_result = fetch_s2_by_title(title)
        if s2_result and s2_result.get("paperId"):
            s2_data = fetch_citations_s2(paper_id=s2_result["paperId"])
            store_citations(conn, key, s2_data)

    # ── Output structured summary template for agent to fill ──
    # When running in an agent context, the agent reads this and completes the summary.
    template = f"""
=== SUMMARY TEMPLATE for "{title}" ===
KEY: {key}
DOI: {doi or 'N/A'}

ABSTRACT:
{abstract}

FULL TEXT (first 8000 chars):
{full_text}

Please complete:
1. **Main Finding** (1-2 sentences):
2. **Methods** (key techniques used):
3. **Key Results** (most important data/figures):
4. **Limitations** (stated or apparent):
5. **Relevance** (to your research):
6. **Tags** (suggested keywords):
"""
    # Save template
    summaries_dir = data_dir / "summaries"
    summaries_dir.mkdir(parents=True, exist_ok=True)
    template_path = summaries_dir / f"{key}.md"
    template_path.write_text(template, encoding="utf-8")

    print(f"  Summary template saved: {template_path}")
    print(template)
    conn.close()
    return template


def save_summary(key: str, summary_text: str, config: dict):
    """Save a completed summary to DB."""
    data_dir = get_data_dir(config)
    set_db_path(data_dir)
    conn = get_db()
    init_db(conn)

    update_summary(conn, key, summary_text)

    # Also save to file
    summaries_dir = data_dir / "summaries"
    summaries_dir.mkdir(parents=True, exist_ok=True)
    (summaries_dir / f"{key}.md").write_text(summary_text, encoding="utf-8")

    print(f"  Summary saved for {key} → L3")
    conn.close()


def main():
    parser = argparse.ArgumentParser(description="Generate LLM summaries (L3)")
    parser.add_argument("--key", type=str, help="Single paper key to summarize")
    parser.add_argument("--batch", action="store_true", help="Batch summarize all L2 papers")
    parser.add_argument("--save", type=str, help="Save summary text for a paper key")
    parser.add_argument("--summary-text", type=str, help="Summary text (with --save)")
    args = parser.parse_args()

    config = load_config()

    if args.save:
        summary = args.summary_text
        if not summary:
            # Read from stdin
            summary = sys.stdin.read()
        save_summary(args.save, summary, config)
        return

    if args.key:
        summarize_paper(args.key, config)
    elif args.batch:
        data_dir = get_data_dir(config)
        set_db_path(data_dir)
        conn = get_db()
        init_db(conn)
        items = get_all_items(conn, limit=50)  # safe batch limit
        l2_keys = [i["key"] for i in items if i["processing_level"] >= 2 and not i.get("local_summary")]
        print(f"Batch summarizing {len(l2_keys)} papers...")
        for k in l2_keys:
            summarize_paper(k, config)
            print()
        conn.close()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
