"""Verify the sec-api -> LLM pipeline against a known recent acquisition.

Picks the most recent acquisition-flagged 8-K from Cisco (CSCO) within the
last 18 months that the LLM recognizes as M&A, then prints the extracted
fields. Doesn't hardcode a URL — uses the same sec-api lookup the monitor
uses, so URL format changes don't break it.

Run with: .venv/bin/python -m scripts.verify_pipeline
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import sec_client, llm_parser


def main() -> int:
    ticker = "CSCO"
    since = (dt.date.today() - dt.timedelta(days=18 * 30)).isoformat()
    print(f"Searching last 18 months of {ticker} 8-Ks for an acquisition...")

    filings = sec_client.list_recent_8ks(ticker, since_iso=since, limit=40)
    print(f"Got {len(filings)} 8-K filings\n")

    for f in filings:
        print(f"  trying {f.accession_no} ({f.filed_at})... ", end="", flush=True)
        text = sec_client.fetch_filing_text(f.primary_doc_url)
        if not text:
            print("empty text, skip")
            continue
        if len(text) < 3000:
            print(f"only {len(text):,} chars, likely XBRL viewer wrapper, skip")
            continue
        ann = llm_parser.extract_8k_acquisition_announcement(text)
        if not ann:
            print(f"({len(text):,} chars) not an acquisition")
            continue
        print(f"ACQUISITION!\n")
        print("Filing URL:", f.primary_doc_url)
        print("LLM result:")
        print(json.dumps(ann, indent=2))
        print("\nPipeline OK — sec-api fetch + LLM extraction working end-to-end.")
        return 0

    print("\nNo acquisition 8-Ks found in the last 18 months for", ticker)
    print("Pipeline mechanics may still be fine — try a different ticker or a longer window.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
