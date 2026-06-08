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
                target = ann.get("target", "")
                if not target:
                    print(f"[{ticker}]   {f.accession_no} ({f.filed_at}): acquisition flagged but no target name extracted, skipped")
                    continue
                date_iso = ann.get("announced_date") or f.filed_at
                structure = ann.get("structure", "unknown")
                summary  = ann.get("summary", "")
                notes = f"{summary} (Structure: {structure})" if summary else f"Structure: {structure}"
                row = {
                    "Company":        target,
                    "Acquirer":       label,
                    "Date":           config.format_month_year(date_iso),
                    "Motivation":     "",
                    "$ to cap table": config.pick_cap_table_value(ann),
                    "Revenue ($)":    "",
                    "Engineers":      "",
                    "$ / Engineer":   "",
                    "Rev. Multiple":  "",
                    "Notes":          notes,
                    "Source":         f.filing_url,
                }
                # Don't double-write if target already exists for this acquirer
                existing = sheets_client.find_row_index(ws, label, target)
                if existing:
                    print(f"[{ticker}]   {f.accession_no} ({f.filed_at}): {target} already tracked (row {existing})")
                else:
                    sheets_client.append_acquisition(ws, row)
                    new_count += 1
                    val = row["$ to cap table"]
                    val_s = f"${val:,.0f}" if isinstance(val, (int, float)) else (val or "n/a")
                    print(f"[{ticker}]   {f.accession_no} ({f.filed_at}): ACQUISITION → {target} ({val_s}, {structure})")
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
