"""Diagnostic tool: show recent 8-Ks for a ticker + LLM verdict on each.

Run with: .venv/bin/python -m scripts.inspect_8k <TICKER>

For each 8-K filed in the last 180 days, prints:
- accession_no, filed_at, item codes
- filing size in chars
- the LLM's verdict (is_acquisition: true/false)
- if acquisition: target, value, structure
- if not: a one-line excerpt from the start of the filing to help diagnose
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import sec_client, llm_parser


def main(ticker: str) -> int:
    since = (dt.date.today() - dt.timedelta(days=180)).isoformat()
    print(f"=== inspecting {ticker} 8-Ks filed since {since} ===\n")

    filings = sec_client.list_recent_8ks(ticker, since_iso=since, limit=40)
    if not filings:
        print(f"no 8-K filings returned for {ticker}", file=sys.stderr)
        return 1

    found_any = False
    for f in filings:
        print(f"--- {f.accession_no} ({f.filed_at}) items={f.items or 'none'} ---")
        print(f"    url: {f.primary_doc_url}")
        text = sec_client.fetch_filing_text(f.primary_doc_url)
        if not text:
            print("    EMPTY text, skipped\n")
            continue
        print(f"    {len(text):,} chars")
        ann = llm_parser.extract_8k_acquisition_announcement(text)
        if ann:
            found_any = True
            print("    LLM: ACQUISITION")
            print("    " + json.dumps(ann, indent=2, default=str).replace("\n", "\n    "))
        else:
            print("    LLM: not an acquisition")
            # Show a snippet so the user can sanity-check
            snippet = text[:600].replace("|", " ").strip()
            print(f"    excerpt: {snippet[:400]}...")
        print()

    if not found_any:
        print(f"\nNo acquisitions found across {len(filings)} 8-K(s) for {ticker} in last 180 days.")
        print("If you expected one, paste the suspect filing's accession number and I'll diagnose.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python -m scripts.inspect_8k <TICKER>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1].upper()))
