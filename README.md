# nanobot-zotero-bridge

Sync your [Zotero](https://www.zotero.org/) library into a local searchable knowledge base with multi-level paper processing — from metadata to AI summaries.

```
Zotero API → SQLite FTS5 → RAG-ready local search
```

## Processing Levels

| Level | Content | Source |
|-------|---------|--------|
| L0 | Metadata (title, authors, DOI, year) | Zotero API |
| L1 | Abstract | Zotero API |
| L2 | Full text (PDF → Markdown) | Sci-Hub + MarkItDown |
| L3 | AI Summary + Citation Graph | LLM + Semantic Scholar |

## Quick Start

```bash
# 1. Install
cd ~/.nanobot/skills/
git clone https://github.com/Albert-Libra/nanobot-zotero-bridge.git zotero-bridge
cd zotero-bridge
pip install -r requirements.txt

# 2. Configure
cp references/config-template.yaml config.yaml
# Edit config.yaml with your Zotero user ID and API key

# 3. Sync
python scripts/sync.py

# 4. Search
python scripts/search.py "your query here"

# 5. Deep search (Zotero + web guidance)
python scripts/deep_search.py "CRISPR off-target prediction"
```

## When Used as a nanobot Skill

The skill defines agent interaction rules — the AI agent will:

1. **Search Zotero first** when you ask scientific questions
2. **Cite your library papers** as primary references
3. **Supplement with web search** when Zotero coverage is insufficient
4. **Suggest adding** interesting web-found papers to your library

## Architecture

```
scripts/
├── sync.py          # Zotero API → local SQLite
├── search.py        # FTS5 search + RAG context builder
├── ingest.py        # PDF download + Markdown conversion (L2)
├── summarize.py     # LLM summary + citation graph (L3)
├── deep_search.py   # Combined Zotero + web search
├── db.py            # SQLite schema, CRUD, FTS5
└── config.py        # YAML config loader
```

Data stored in `zotero-data/` (gitignored):
- `zotero.db` — SQLite with FTS5 index
- `pdfs/` — Downloaded PDFs
- `fulltext/` — Markdown-converted full texts
- `summaries/` — AI-generated paper summaries

## Requirements

- Python 3.11+
- [Zotero API key](https://www.zotero.org/settings/keys)
- pyzotero, markitdown, requests, pyyaml
