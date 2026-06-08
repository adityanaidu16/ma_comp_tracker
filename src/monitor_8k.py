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

from . import config, sec_client, llm_parser, state, writer as sheets_client


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
    analyzed_count = 0
    skipped_count = 0

    for ticker, label in config.COMP_SET:
        last_acc = state.last_seen(s, ticker, "8-K")
        try:
            filings = sec_client.list_recent_8ks(ticker, since_iso=default_since, limit=25)
        except Exception as e:
            print(f"[{ticker}] list_recent_8ks error: {e}", file=sys.stderr)
            traceback.print_exc()
            err_count += 1
            continue

        # How many filings are actually new (newer than last_acc)
        new_filings = []
        for f in filings:
            if last_acc and f.accession_no == last_acc:
                break
            new_filings.append(f)

        if not new_filings:
            print(f"[{ticker}] no new 8-Ks since last run (state: {last_acc or 'none'})")
            continue

        print(f"[{ticker}] {len(new_filings)} new 8-K(s) to analyze")
        for f in new_filings:
            try:
                text = sec_client.fetch_filing_text(f.primary_doc_url)
                if not text:
                    print(f"[{ticker}]   {f.accession_no} ({f.filed_at}): empty filing text, skipped")
                    continue
                analyzed_count += 1
                ann = llm_parser.extract_8k_acquisition_announcement(text)
                if not ann:
                    print(f"[{ticker}]   {f.accession_no} ({f.filed_at}): not an acquisition")
                    skipped_count += 1
                    continue
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
                    print(f"[{ticker}]   {f.accession_no} ({f.filed_at}): {row['target']} already tracked (row {existing})")
                else:
                    sheets_client.append_acquisition(ws, row)
                    new_count += 1
                    val = row["headline_value_usd"]
                    val_s = f"${val:,.0f}" if isinstance(val, (int, float)) else (val or "n/a")
                    print(f"[{ticker}]   {f.accession_no} ({f.filed_at}): ACQUISITION → {row['target']} ({val_s}, {row['structure']})")
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
    print(
        f"\nDone: {new_count} new acquisitions written, "
        f"{analyzed_count} filings analyzed by LLM ({skipped_count} judged non-M&A), "
        f"{err_count} errors."
    )
    return 0 if err_count == 0 else 1


if __name__ == "__main__":
    sys.exit(run())
