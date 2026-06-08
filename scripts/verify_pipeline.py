"""Verify the sec-api -> LLM pipeline against a known M&A 8-K.

Picks Cisco's 8-K announcing the Splunk acquisition (filed 2023-09-21) and
runs it through the same fetch + LLM extraction path the daily monitor uses.
If this returns an acquisition dict, the pipeline is working end-to-end.

Run with: .venv/bin/python -m scripts.verify_pipeline
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import sec_client, llm_parser

# Cisco's 8-K announcing Splunk acquisition, filed 2023-09-21.
# Item 1.01 (entry into material agreement) + 7.01 (Reg FD).
SPLUNK_8K_URL = (
    "https://www.sec.gov/Archives/edgar/data/858877/000085887723000044/"
    "csco-20230921.htm"
)


def main() -> int:
    print("Fetching filing text...")
    text = sec_client.fetch_filing_text(SPLUNK_8K_URL)
    if not text:
        print("FAIL: filing text came back empty", file=sys.stderr)
        return 1
    print(f"OK: fetched {len(text):,} chars")

    print("\nRunning LLM extraction (this calls OpenRouter)...")
    result = llm_parser.extract_8k_acquisition_announcement(text)
    if not result:
        print(
            "FAIL: LLM did not flag this as an acquisition. "
            "Check OPENROUTER_API_KEY and OPENROUTER_MODEL.",
            file=sys.stderr,
        )
        return 1

    print("\nLLM result:")
    print(json.dumps(result, indent=2))
    print("\nPipeline OK — sec-api fetch + LLM extraction both working.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
