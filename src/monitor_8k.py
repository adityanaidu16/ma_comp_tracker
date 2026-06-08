"""8-K monitoring entrypoint.

For each ticker in the comp set, list 8-K filings filed since last run,
ask the LLM to extract acquisition details, and append new rows to the
Google Sheet. Idempotent via state.json — re-runs won't double-write.

Run with: python -m src.monitor_8k
"""
from __future__ import annotations

import datetime as dt
import sys
import traceback

from . import config, sec_client, llm_parser, sheets_client, state


def run() -> int:
    s = state.load()
    today = dt.date.today().isoformat()
    # On first run, look back 90 days. On subsequent runs, sec_client filters
    # by accession_no via state.last_seen to avoid re-processing.
    default_since = (dt.date.today() - dt.timedelta(days=90)).isoformat()

    ws = sheets_client.open_sheet()
    sheets_client.ensure_header(ws)

    new_count = 0
    err_count = 0

    for ticker, label in config.COMP_SET:
        last_acc = state.last_seen(s, ticker, "8-K")
        try:
            filings = sec_client.list_recent_8ks(ticker, since_iso=default_since, limit=25)
        except Exception as e:
            print(f"[{ticker}] list_recent_8ks error: {e}", file=sys.stderr)
            err_count += 1
            continue

        # Newest first; only process filings strictly newer than last_acc
        for f in filings:
            if last_acc and f.accession_no == last_acc:
                break  # remainder are older, already seen
            try:
                text = sec_client.fetch_filing_text(f.primary_doc_url)
                if not text:
                    print(f"[{ticker}] empty text for {f.accession_no}", file=sys.stderr)
                    continue
                ann = llm_parser.extract_8k_acquisition_announcement(text)
                if not ann:
                    continue  # not an acquisition 8-K
                row = {
                    "acquirer": label,
                    "acquirer_ticker": ticker,
                    "target": ann.get("target", ""),
                    "announced_date": ann.get("announced_date", f.filed_at),
                    "closed_date": ann.get("closed_date", "") or "",
                    "headline_value_usd": ann.get("headline_value_usd", "") or "",
                    "cash_consideration_usd": ann.get("cash_component_usd", "") or "",
                    "stock_consideration_usd": ann.get("stock_component_usd", "") or "",
                    "contingent_usd": "",
                    "true_cash_to_capital_usd": "",
                    "structure": ann.get("structure", "unknown"),
                    "stage": "announced",
                    "source_8k_url": f.filing_url,
                    "source_10q_url": "",
                    "notes": ann.get("summary", ""),
                    "last_updated": today,
                }
                # Don't double-write if target already exists for this acquirer
                existing = sheets_client.find_row_index(ws, ticker, row["target"])
                if existing:
                    print(f"[{ticker}] already tracked: {row['target']} (row {existing}, skipping)")
                else:
                    sheets_client.append_acquisition(ws, row)
                    new_count += 1
                    print(f"[{ticker}] NEW acquisition: {row['target']} ({row['headline_value_usd']})")
            except Exception as e:
                print(f"[{ticker}] error processing {f.accession_no}: {e}", file=sys.stderr)
                traceback.print_exc()
                err_count += 1
                continue

        # Mark newest filing as seen even if it wasn't an acquisition, so we
        # don't re-process it next run.
        if filings:
            state.mark_seen(s, ticker, "8-K", filings[0].accession_no)

    state.save(s)
    print(f"\nDone: {new_count} new acquisitions, {err_count} errors.")
    return 0 if err_count == 0 else 1


if __name__ == "__main__":
    sys.exit(run())
