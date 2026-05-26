#!/usr/bin/env python
"""PDF acquisition + Markdown conversion pipeline (L2).

Acquisition chain (configurable priority):
  1. Unpaywall — legal OA via open access API
  2. Sci-Hub  — mirror rotation fallback
"""
import argparse
import io
import re
import sys
import time

# Fix Unicode output on Windows GBK console
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import requests
from pathlib import Path

# Suppress SSL warnings for Sci-Hub mirrors
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from db import get_db, init_db, get_item_by_key, update_full_text, set_db_path
from config import load_config, get_data_dir


# ---------------------------------------------------------------------------
# PDF acquisition sources
# ---------------------------------------------------------------------------

def acquire_via_unpaywall(doi: str, config: dict, timeout: int = 30) -> str | None:
    """Query Unpaywall for an OA PDF URL and return it, or None.

    Unpaywall is a free, legal service that indexes open-access versions
    of scholarly articles across repositories and publisher sites.
    """
    email = (config.get("pdf", {}).get("unpaywall", {}).get("email", "").strip()
             or "researcher@openaccess.org")
    api_url = f"https://api.unpaywall.org/v2/{doi}?email={email}"

    try:
        resp = requests.get(api_url, timeout=timeout, headers={
            "User-Agent": "nanobot-zotero-bridge/1.0"
        })
        if resp.status_code == 422:
            print(f"  [Unpaywall] Invalid/missing email — set pdf.unpaywall.email in config.yaml")
            return None
        if resp.status_code != 200:
            print(f"  [Unpaywall] HTTP {resp.status_code}")
            return None
        data = resp.json()
        # Check all OA locations, prefer repositories over publishers
        locations = data.get("oa_locations") or []
        pdf_urls = []
        for loc in locations:
            url = loc.get("url_for_pdf") or loc.get("url")
            if url:
                host_type = loc.get("host_type", "unknown")
                # Repositories (arXiv, PMC, institutional) usually work without auth
                if host_type == "repository":
                    pdf_urls.insert(0, url)  # Prefer repositories
                else:
                    pdf_urls.append(url)

        if not pdf_urls:
            # Fallback to best_oa_location
            best = data.get("best_oa_location") or {}
            url = best.get("url_for_pdf") or best.get("url")
            if url:
                pdf_urls.append(url)

        if pdf_urls:
            print(f"  [Unpaywall] Found {len(pdf_urls)} OA location(s)")
            # Return the first (preferred) URL
            return pdf_urls[0]
        else:
            print(f"  [Unpaywall] No OA PDF found for this DOI")
    except Exception as e:
        print(f"  [Unpaywall] Error: {e}")
    return None


def acquire_via_scihub(doi: str, config: dict, timeout: int = 60) -> str | None:
    """Try Sci-Hub mirrors in order, return first working PDF URL.

    Parses the Sci-Hub page for an embedded PDF URL, then fetches it.
    Returns the local path to the saved PDF on success, None on failure.
    """
    mirrors = (config.get("pdf", {}).get("scihub", {}).get("mirrors", [])
               or ["https://sci-hub.se"])

    for mirror in mirrors:
        scihub_url = f"{mirror.rstrip('/')}/{doi}"
        try:
            print(f"  [Sci-Hub] Trying {mirror} ...")
            resp = requests.get(scihub_url, timeout=timeout, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
            }, verify=False)  # Sci-Hub mirrors often have SSL issues
            if resp.status_code != 200:
                print(f"    HTTP {resp.status_code}")
                continue

            # Find PDF URL in the Sci-Hub page
            pdf_match = re.search(r'(?:src|href)\s*=\s*["\'](//[^"\']+?\.pdf[^"\']*)["\']',
                                  resp.text, re.IGNORECASE)
            if not pdf_match:
                # Fallback: search for any //...pdf pattern
                pdf_match = re.search(r'//[^"\')\s]+?\.pdf(?:\?[^"\')\s]*)?',
                                      resp.text)
            if not pdf_match:
                print("    No PDF URL found in page")
                continue

            pdf_url = pdf_match.group(1) if pdf_match.lastindex else pdf_match.group(0)
            if pdf_url.startswith("//"):
                pdf_url = "https:" + pdf_url
            elif not pdf_url.startswith("http"):
                pdf_url = "https://" + pdf_url.lstrip("/")

            print(f"    PDF URL: {pdf_url[:80]}...")
            return pdf_url

        except requests.exceptions.Timeout:
            print(f"    Timeout")
        except Exception as e:
            print(f"    Error: {e}")
    return None


def download_pdf(pdf_url: str, output_path: Path, referer: str = "",
                 timeout: int = 120) -> bool:
    """Download a PDF from a URL and save to output_path. Returns True on success."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }
        if referer:
            headers["Referer"] = referer

        resp = requests.get(pdf_url, timeout=timeout, headers=headers,
                            allow_redirects=True)
        if resp.status_code != 200:
            print(f"    Download HTTP {resp.status_code}")
            return False
        if len(resp.content) < 1024:
            print(f"    Response too small ({len(resp.content)} bytes)")
            return False
        if b"%PDF" not in resp.content[:1024]:
            print(f"    Not a valid PDF (missing %%PDF header)")
            return False

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(resp.content)
        return True
    except Exception as e:
        print(f"    Download error: {e}")
    return False


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------

def convert_pdf_to_markdown(pdf_path: Path) -> str:
    try:
        from markitdown import MarkItDown
        md = MarkItDown()
        result = md.convert(str(pdf_path))
        return result.text_content
    except ImportError:
        print("  markitdown not installed. Run: pip install markitdown")
        return ""
    except Exception as e:
        print(f"  Conversion error: {e}")
        return ""


# ---------------------------------------------------------------------------
# Single-paper ingest
# ---------------------------------------------------------------------------

def ingest_paper(key: str, config: dict) -> bool:
    """Acquire PDF + convert to Markdown for a single paper. Returns True on success."""
    data_dir = get_data_dir(config)
    set_db_path(data_dir)
    conn = get_db()
    init_db(conn)

    item = get_item_by_key(conn, key)
    if not item:
        print(f"Paper not found: {key}")
        conn.close()
        return False

    title = item.get("title", "Unknown")
    doi = item.get("doi")
    print(f"Processing: {title[:80]}")
    print(f"  Key: {key}  DOI: {doi or 'N/A'}")

    if not doi:
        print("  No DOI — cannot acquire PDF")
        conn.close()
        return False

    pdf_config = config.get("pdf", {})
    pdf_dir = data_dir / "pdfs"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = pdf_dir / f"{key}.pdf"
    timeout = pdf_config.get("download_timeout", 60)

    # Step 1: Try each source in configured priority order
    sources = pdf_config.get("sources", ["unpaywall", "scihub"])
    pdf_url = None
    for source in sources:
        if source == "unpaywall":
            pdf_url = acquire_via_unpaywall(doi, config, timeout=30)
        elif source == "scihub":
            pdf_url = acquire_via_scihub(doi, config, timeout=timeout)
        if pdf_url:
            # Attempt download — fall through to next source on failure
            print(f"  Downloading PDF...")
            if download_pdf(pdf_url, pdf_path, timeout=120):
                break  # Success
            else:
                print(f"  Download from {source} failed, trying next source...")
                pdf_url = None  # Reset to try next source

    if not pdf_path.exists():
        print(f"  ALL SOURCES EXHAUSTED — could not acquire PDF for DOI: {doi}")
        print(f"  Title: {title}")
        print(f"  Key: {key}")
        print(f"  → Agent: ask user to provide the PDF file manually.")
        print(f"     Save to: {pdf_path}")
        print(f"     Then re-run: python scripts/ingest.py --key {key}")
        conn.close()
        return False

    file_size_kb = pdf_path.stat().st_size // 1024
    print(f"  PDF saved: {pdf_path.name} ({file_size_kb} KB)")

    # Step 2: Mark in DB
    conn.execute("UPDATE items SET has_pdf = 1 WHERE key = ?", (key,))
    conn.commit()

    # Step 3: Convert to Markdown
    print("  Converting to Markdown...")
    markdown_text = convert_pdf_to_markdown(pdf_path)
    if not markdown_text:
        conn.close()
        return False

    fulltext_dir = data_dir / "fulltext"
    fulltext_dir.mkdir(parents=True, exist_ok=True)
    fulltext_path = fulltext_dir / f"{key}.md"
    with open(fulltext_path, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n")
        f.write(markdown_text)

    update_full_text(conn, key, markdown_text)
    print(f"  Full text: {len(markdown_text)} chars → L2")
    conn.close()
    return True


# ---------------------------------------------------------------------------
# Batch ingest
# ---------------------------------------------------------------------------

def batch_ingest(config: dict, limit: int = 0, keys: list[str] | None = None):
    """Process multiple papers through L2 pipeline.

    Args:
        config: Configuration dict
        limit: Max papers to process (0 = unlimited)
        keys: Specific item keys to process (None = all without PDF)
    """
    data_dir = get_data_dir(config)
    set_db_path(data_dir)
    conn = get_db()
    init_db(conn)

    if keys:
        candidates = keys
    else:
        # Get items with DOI but no PDF
        rows = conn.execute(
            "SELECT key FROM items WHERE doi IS NOT NULL AND doi != '' AND has_pdf = 0"
        ).fetchall()
        candidates = [r["key"] for r in rows]

    if limit and limit > 0:
        candidates = candidates[:limit]

    total = len(candidates)
    print(f"Batch ingest: {total} papers with DOI, no PDF")
    if total == 0:
        print("  Nothing to process.")
        conn.close()
        return

    success = 0
    for i, key in enumerate(candidates, 1):
        print(f"\n[{i}/{total}] Key: {key}")
        if ingest_paper(key, config):
            success += 1
        # Small delay between requests to be a good net citizen
        if i < total:
            time.sleep(1.5)

    print(f"\nBatch complete: {success}/{total} succeeded")
    conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Download PDF and convert to Markdown (L2)")
    parser.add_argument("--key", type=str, help="Single Zotero item key")
    parser.add_argument("--batch", action="store_true",
                        help="Process all papers without PDF (batch mode)")
    parser.add_argument("--limit", type=int, default=0,
                        help="Max papers in batch mode (0 = all)")
    parser.add_argument("--keys", type=str, nargs="*",
                        help="Specific keys for batch processing")
    args = parser.parse_args()

    config = load_config()

    if args.batch or args.keys:
        batch_ingest(config, limit=args.limit, keys=args.keys)
    elif args.key:
        ingest_paper(args.key, config)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
