"""Diagnostic tool: show what's happening at each stage of the 10-Q pipeline.

Run with: .venv/bin/python -m scripts.inspect_10q <TICKER>

Reports:
- The 10-Q filing URL that was selected
- Total length of the fetched filing text
- All locations of "Business Combinations" and "Acquisition" with surrounding
  context (so you can eyeball whether the locator is finding the right section)
- The section the locator extracts (first 2K chars printed, full section saved
  to scripts/inspect_output.txt)
- The LLM raw output and parsed result
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import sec_client, llm_parser


def main(ticker: str) -> int:
    print(f"=== inspecting most recent 10-Q for {ticker} ===\n")

    filings = sec_client.list_recent_10qs(ticker, since_iso=None, limit=2)
    if not filings:
        print(f"no 10-Q/10-K filings returned for {ticker}", file=sys.stderr)
        return 1
    f = filings[0]
    print(f"filing:        {f.accession_no} ({f.form_type})")
    print(f"filed at:      {f.filed_at}")
    print(f"period:        {f.period_of_report}")
    print(f"primary doc:   {f.primary_doc_url}")
    print(f"filing index:  {f.filing_url}\n")

    text = sec_client.fetch_filing_text(f.primary_doc_url)
    if not text:
        print("FAIL: filing text came back empty", file=sys.stderr)
        return 1
    print(f"filing text:   {len(text):,} chars\n")

    # Find all "Business Combinations" matches and their offsets
    bc_hits = [m.start() for m in re.finditer(r"Business\s+Combinations", text, flags=re.IGNORECASE)]
    acq_hits = [m.start() for m in re.finditer(r"\bAcquisitions?\b", text, flags=re.IGNORECASE)]
    print(f"matches:       'Business Combinations' x {len(bc_hits)}, 'Acquisition(s)' x {len(acq_hits)}")
    if bc_hits:
        print("\nfirst 5 'Business Combinations' matches with context:")
        for i, off in enumerate(bc_hits[:5]):
            context = text[max(0, off-120):off+200].replace("\n", " ")
            print(f"  [{i}] @ {off:,}: ...{context}...")
    print()

    section = sec_client.locate_business_combinations_section(text)
    out_path = Path(__file__).resolve().parent / "inspect_output.txt"
    out_path.write_text(section, encoding="utf-8")
    print(f"located section: {len(section):,} chars (full text saved to {out_path})")
    preview = section[:2000].replace("  ", " ")
    print(f"\n--- section preview (first 2K chars) ---\n{preview}\n--- end preview ---\n")

    print("calling LLM on located section...")
    data = llm_parser.extract_10q_business_combination(section)
    print("\nLLM result:")
    print(json.dumps(data, indent=2, default=str))
    if data and data.get("acquisitions"):
        print(f"\n=> {len(data['acquisitions'])} acquisition(s) extracted")
    else:
        print("\n=> NO acquisitions extracted (this is the bug we're hunting)")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python -m scripts.inspect_10q <TICKER>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1].upper()))
