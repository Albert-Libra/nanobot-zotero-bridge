#!/usr/bin/env python
"""PDF download + Markdown conversion pipeline (L2)."""
import argparse
import re
import requests
from pathlib import Path
from db import get_db, init_db, get_item_by_key, update_full_text, set_db_path
from config import load_config, get_data_dir


def download_from_scihub(doi: str, output_path: Path, mirror: str, timeout: int = 60) -> bool:
    """Try to download PDF from Sci-Hub using a DOI."""
    url = f"{mirror.rstrip('/')}/{doi}"
    try:
        resp = requests.get(url, timeout=timeout, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        if resp.status_code != 200:
            return False
        # Look for PDF URL in Sci-Hub page
        pdf_match = re.search(r'//[^"\']+?\.pdf', resp.text)
        if pdf_match:
            pdf_url = pdf_match.group(0)
            if not pdf_url.startswith("http"):
                pdf_url = "https:" + pdf_url
            pdf_resp = requests.get(pdf_url, timeout=timeout, headers={
                "Referer": url, "User-Agent": "Mozilla/5.0"
            })
            if pdf_resp.status_code == 200 and b"%PDF" in pdf_resp.content[:1024]:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                with open(output_path, "wb") as f:
                    f.write(pdf_resp.content)
                return True
    except Exception as e:
        print(f"  Sci-Hub error: {e}")
    return False


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


def ingest_paper(key: str, config: dict):
    data_dir = get_data_dir(config)
    set_db_path(data_dir)
    conn = get_db()
    init_db(conn)

    item = get_item_by_key(conn, key)
    if not item:
        print(f"Paper not found: {key}")
        conn.close()
        return

    title = item.get("title", "Unknown")
    doi = item.get("doi")
    print(f"Processing: {title[:80]}")
    print(f"  Key: {key}  DOI: {doi or 'N/A'}")

    pdf_config = config.get("pdf", {})
    pdf_dir = data_dir / "pdfs"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = pdf_dir / f"{key}.pdf"

    success = False
    if doi:
        scihub = pdf_config.get("scihub_mirror", "https://sci-hub.se")
        timeout = pdf_config.get("download_timeout", 60)
        print(f"  Downloading from Sci-Hub...")
        success = download_from_scihub(doi, pdf_path, scihub, timeout)

    if not success:
        print(f"  Could not download PDF")
        conn.close()
        return

    print(f"  PDF saved: {pdf_path.name}")
    conn.execute("UPDATE items SET has_pdf = 1 WHERE key = ?", (key,))
    conn.commit()

    print("  Converting to Markdown...")
    markdown_text = convert_pdf_to_markdown(pdf_path)
    if not markdown_text:
        conn.close()
        return

    fulltext_dir = data_dir / "fulltext"
    fulltext_dir.mkdir(parents=True, exist_ok=True)
    fulltext_path = fulltext_dir / f"{key}.md"
    with open(fulltext_path, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n")
        f.write(markdown_text)

    update_full_text(conn, key, markdown_text)
    print(f"  Full text saved: {len(markdown_text)} chars → L2")
    conn.close()


def main():
    parser = argparse.ArgumentParser(description="Download PDF and convert to markdown (L2)")
    parser.add_argument("--key", type=str, required=True, help="Zotero item key")
    args = parser.parse_args()
    config = load_config()
    ingest_paper(args.key, config)


if __name__ == "__main__":
    main()
