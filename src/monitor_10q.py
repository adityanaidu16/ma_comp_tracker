"""10-Q / 10-K monitoring entrypoint.

For each ticker in the comp set, list 10-Q and 10-K filings filed since last
run, locate the Business Combinations / Acquisitions / Subsequent Events
sections, ask the LLM to extract the purchase-price breakdown, and update
existing rows in the output with reconciled numbers.

Tickers are processed in parallel (ThreadPoolExecutor, default 8 workers).
Writes are serialized in the main thread to avoid races.

Run with: python -m src.monitor_10q
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import config, sec_client, llm_parser, state, writer as sheets_client


def _process_ticker(ticker: str, label: str, since_iso: str, last_acc: str | None):
    out = {
        "ticker": ticker,
        "label": label,
        "logs": [],
        "rows": [],          # list of (row_dict, accession_no, filed_at, target)
        "newest_acc": None,
        "analyzed": 0,
        "errors": 0,
    }
    try:
        filings = sec_client.list_recent_10qs(ticker, since_iso=since_iso, limit=6)
    except Exception as e:
        out["logs"].append(f"[{ticker}] list_recent_10qs error: {e}")
        out["errors"] += 1
        return out

    new_filings = []
    for f in filings:
        if last_acc and f.accession_no == last_acc:
            break
        new_filings.append(f)

    if not new_filings:
        out["logs"].append(f"[{ticker}] no new 10-Qs/10-Ks since last run (state: {last_acc or 'none'})")
        return out

    out["newest_acc"] = new_filings[0].accession_no
    out["logs"].append(f"[{ticker}] {len(new_filings)} new 10-Q/10-K(s) to analyze")

    for f in new_filings:
        try:
            full = sec_client.fetch_filing_text(f.primary_doc_url)
            if not full:
                out["logs"].append(f"[{ticker}]   {f.accession_no} ({f.filed_at}): empty filing text, skipped")
                continue
            section = sec_client.locate_business_combinations_section(full)
            if not section:
                out["logs"].append(f"[{ticker}]   {f.accession_no} ({f.filed_at}): no Business Combinations / Acquisitions section located")
                continue
            out["analyzed"] += 1
            data = llm_parser.extract_10q_business_combination(section)
            if not data or not data.get("acquisitions"):
                out["logs"].append(f"[{ticker}]   {f.accession_no} ({f.filed_at}): section present but no acquisitions described")
                continue
            for acq in data["acquisitions"]:
                target = acq.get("target") or ""
                if not target:
                    continue
                date_iso = acq.get("closed_date") or f.filed_at
                if not config.is_recent_enough(date_iso):
                    out["logs"].append(
                        f"[{ticker}]   {f.accession_no} ({f.filed_at}): {target} (closed {date_iso}) older than {config.MAX_ACQUISITION_AGE_DAYS}d cutoff, skipped"
                    )
                    continue
                notes_parts = []
                if acq.get("notes"):
                    notes_parts.append(str(acq["notes"]))
                bits = []
                for k, label_bit in [
                    ("cash_consideration_usd",       "cash"),
                    ("stock_consideration_usd",      "stock"),
                    ("contingent_consideration_usd", "contingent"),
                ]:
                    v = acq.get(k)
                    if isinstance(v, (int, float)) and v > 0:
                        bits.append(f"{label_bit} ${v:,.0f}")
                if bits:
                    notes_parts.append("Components: " + ", ".join(bits))
                notes = " | ".join(notes_parts)
                row = {
                    "Company":        target,
                    "Acquirer":       label,
                    "Date":           config.format_month_year(date_iso),
                    "$ to cap table": config.pick_cap_table_value(acq),
                    "Notes":          notes,
                    "Source":         f.filing_url,
                }
                out["rows"].append((row, f.accession_no, f.filed_at, target))
        except Exception as e:
            out["logs"].append(f"[{ticker}]   {f.accession_no}: error: {e}")
            out["logs"].append(traceback.format_exc())
            out["errors"] += 1

    return out


def run() -> int:
    s = state.load()
    default_since = (dt.date.today() - dt.timedelta(days=180)).isoformat()

    ws = sheets_client.open_sheet()
    sheets_client.ensure_header(ws)

    max_workers = max(1, int(os.getenv("MAX_WORKERS", "8")))

    updated = 0
    added   = 0
    total = {"analyzed": 0, "errors": 0}

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {
            ex.submit(
                _process_ticker,
                ticker,
                label,
                default_since,
                state.last_seen(s, ticker, "10-Q"),
            ): (ticker, label)
            for ticker, label in config.COMP_SET
        }
        for future in as_completed(futures):
            result = future.result()
            for log in result["logs"]:
                print(log)
            for k in ("analyzed", "errors"):
                total[k] += result[k]
            for row, acc_no, filed_at, target in result["rows"]:
                idx = sheets_client.find_row_index(ws, row["Acquirer"], target)
                if idx:
                    sheets_client.update_acquisition(ws, row, idx)
                    updated += 1
                    print(f"[{result['ticker']}]   {acc_no} ({filed_at}): updated row {idx} for {target}")
                else:
                    sheets_client.append_acquisition(ws, row)
                    added += 1
                    print(f"[{result['ticker']}]   {acc_no} ({filed_at}): added (no prior 8-K row) for {target}")
            if result["newest_acc"]:
                state.mark_seen(s, result["ticker"], "10-Q", result["newest_acc"])

    state.save(s)
    print(
        f"\nDone: {updated} rows updated, {added} new rows, "
        f"{total['analyzed']} sections analyzed by LLM, {total['errors']} errors. "
        f"Used {max_workers} parallel workers."
    )
    return 0 if total["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(run())
