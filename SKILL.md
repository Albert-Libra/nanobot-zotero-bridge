---
name: zotero-bridge
description: Sync and search a Zotero library locally. Multi-level paper processing (metadata → abstract → full-text → LLM summary). Agent prioritizes Zotero literature in scientific discussions while supplementing with web search. Use when discussing scientific topics, looking up papers, managing references, or needing to consult your personal research library.
---

# Zotero Bridge

Sync your Zotero library into a local searchable knowledge base, with multi-level paper processing.

## Setup

1. Get your Zotero API key at https://www.zotero.org/settings/keys
2. Find your user ID at same page (Your userID for use in API calls)
3. Copy `references/config-template.yaml` to `config.yaml` and fill in credentials
4. Install deps: `pip install -r requirements.txt`

## Usage

### Sync Library

```bash
# Quick sync (metadata + abstracts)
python scripts/sync.py

# Deep sync (full-text + summaries)
python scripts/sync.py --depth 3

# Force full sync (ignore incremental state)
python scripts/sync.py --full
```

### Search & Retrieve

```bash
# Keyword search across all processed content
python scripts/search.py "quantum error correction"

# RAG mode — returns context for injection into agent prompt
python scripts/search.py "surface codes" --mode rag --top-k 5
```

### Process Papers

```bash
# Download PDF and convert to markdown (L2) — single paper
python scripts/ingest.py --key ABC123

# Batch: acquire PDFs for all papers without PDF
python scripts/ingest.py --batch
python scripts/ingest.py --batch --limit 10     # first 10 only

# Generate LLM summary (L3)
python scripts/summarize.py --key ABC123

# Batch summarize all L2 papers
python scripts/summarize.py --batch
```

**PDF acquisition chain** (configurable via `pdf.sources` in config.yaml):
1. **Unpaywall** — queries the Unpaywall API for legal open-access versions (no API key needed, email for rate limits)
2. **Sci-Hub** — tries mirrors in rotation (`sci-hub.se`, `.st`, `.ru`)

Configure `pdf.unpaywall.email` in config.yaml to avoid rate limiting.

### Deep Search (Web + Zotero)

```bash
# Research a topic — combines Zotero + web search
python scripts/deep_search.py "CRISPR off-target prediction"
```

## Processing Levels

| Level | Flag | Content | Storage |
|-------|------|---------|---------|
| L0 | always | Metadata (title, authors, DOI, etc.) | SQLite |
| L1 | default | Abstract text | SQLite + abstracts/ |
| L2 | --depth 2 | Full text PDF → Markdown | fulltext/ |
| L3 | --depth 3 | LLM summary + citation graph | summaries/ + SQLite |

## Agent Interaction Rules

1. **When user asks a scientific question**, first run `search.py <query>` against local Zotero DB
2. **If relevant papers found**: cite them as primary references, inject via RAG
3. **Always assess whether additional web search is needed** — Zotero is priority, not exclusive. Supplement with web results as the situation demands.
4. **Suggest adding interesting web-found papers** to user's Zotero library.
5. **When PDF acquisition fails** (ingest returns non-zero, or output shows "All sources exhausted"):
   - **Stop immediately** — do NOT silently fall back to abstract-only or web search
   - **Ask the user to provide the PDF file directly**, listing the paper title, first author, year, DOI, and Zotero key
   - Once the user provides the PDF, save it to `<data_dir>/pdfs/<key>.pdf` and re-run `ingest.py --key <key>` to complete L2 conversion

## Dependencies

- pyzotero (Zotero Web API)
- markitdown (PDF → Markdown)
- requests (PDF downloads)
- Python stdlib sqlite3 + yaml
