"""8-K monitoring entrypoint.

For each ticker in the comp set, list 8-K filings filed since last run,
ask the LLM to extract acquisition details, and append new rows to the
configured output (CSV by default, Google Sheets if OUTPUT_MODE=sheets).
Idempotent via state.json — re-runs won't double-write.

Tickers are processed in parallel (ThreadPoolExecutor, default 8 workers).
8-Ks whose item codes signal non-M&A events (e.g. only 2.02 earnings,
5.02 officer changes) are skipped before the LLM call for cost + speed.

Run with: python -m src.monitor_8k
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import config, sec_client, llm_parser, state, writer as sheets_client


# 8-K items that commonly carry acquisition disclosures
_ACQ_ITEMS = {"1.01", "2.01", "7.01", "8.01"}
# Items that are almost never acquisition-related on their own
_NON_ACQ_ITEMS = {"2.02", "5.02", "5.03", "5.07"}


def _items_suggest_acquisition(items: list[str]) -> bool:
    """True if 8-K item codes suggest M&A. Returns True for unknown/mixed
    items to be safe (we'd rather pay an LLM call than miss a deal)."""
    if not items:
        return True
    item_set = set(items)
    if item_set & _ACQ_ITEMS:
        return True
    if item_set <= _NON_ACQ_ITEMS:
        return False
    return True


def _process_ticker(ticker: str, label: str, since_iso: str, last_acc: str | None):
    """Run one ticker. Returns dict with results for the main thread to apply.

    Avoids touching the writer here — collection-only — so writes are
    serialized in the main thread to avoid races.
    """
    out = {
        "ticker": ticker,
        "label": label,
        "logs": [],
        "rows": [],          # list of (row_dict, accession_no, filed_at, target, structure)
        "newest_acc": None,
        "analyzed": 0,
        "skipped": 0,
        "errors": 0,
    }
    try:
        filings = sec_client.list_recent_8ks(ticker, since_iso=since_iso, limit=25)
    except Exception as e:
        out["logs"].append(f"[{ticker}] list_recent_8ks error: {e}")
        out["errors"] += 1
        return out

    new_filings = []
    for f in filings:
        if last_acc and f.accession_no == last_acc:
            break
        new_filings.append(f)

    if not new_filings:
        out["logs"].append(f"[{ticker}] no new 8-Ks since last run (state: {last_acc or 'none'})")
        return out

    out["newest_acc"] = new_filings[0].accession_no
    out["logs"].append(f"[{ticker}] {len(new_filings)} new 8-K(s) to analyze")

    for f in new_filings:
        if not _items_suggest_acquisition(f.items):
            out["logs"].append(
                f"[{ticker}]   {f.accession_no} ({f.filed_at}): items {f.items} not M&A-relevant, skipped (no LLM call)"
            )
            continue
        try:
            text = sec_client.fetch_filing_text(f.primary_doc_url)
            if not text:
                out["logs"].append(f"[{ticker}]   {f.accession_no} ({f.filed_at}): empty text, skipped")
                continue
            out["analyzed"] += 1
            ann = llm_parser.extract_8k_acquisition_announcement(text)
            if not ann:
                out["logs"].append(f"[{ticker}]   {f.accession_no} ({f.filed_at}): not an acquisition")
                out["skipped"] += 1
                continue
            target = ann.get("target", "")
            if not target:
                out["logs"].append(f"[{ticker}]   {f.accession_no} ({f.filed_at}): acquisition flagged but no target name, skipped")
                continue
            date_iso = ann.get("announced_date") or f.filed_at
            if not config.is_recent_enough(date_iso):
                out["logs"].append(
                    f"[{ticker}]   {f.accession_no} ({f.filed_at}): {target} (announced {date_iso}) older than {config.MAX_ACQUISITION_AGE_DAYS}d cutoff, skipped"
                )
                continue
            structure = ann.get("structure", "unknown")
            summary = ann.get("summary", "")
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
            out["rows"].append((row, f.accession_no, f.filed_at, target, structure))
        except Exception as e:
            out["logs"].append(f"[{ticker}]   {f.accession_no}: error: {e}")
            out["logs"].append(traceback.format_exc())
            out["errors"] += 1

    return out


def run() -> int:
    s = state.load()
    # Lookback window tied to MAX_ACQUISITION_AGE_DAYS so sec-api filters
    # at the source — we never even fetch filings older than what would
    # be kept anyway. Pad by 14 days so a deal announced just before the
    # cutoff but with closed_date inside the window still gets caught.
    lookback_days = (config.MAX_ACQUISITION_AGE_DAYS or 90) + 14
    default_since = (dt.date.today() - dt.timedelta(days=lookback_days)).isoformat()

    ws = sheets_client.open_sheet()
    sheets_client.ensure_header(ws)

    max_workers = max(1, int(os.getenv("MAX_WORKERS", "8")))

    new_count = 0
    total = {"analyzed": 0, "skipped": 0, "errors": 0}

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {
            ex.submit(
                _process_ticker,
                ticker,
                label,
                default_since,
                state.last_seen(s, ticker, "8-K"),
            ): (ticker, label)
            for ticker, label in config.COMP_SET
        }
        for future in as_completed(futures):
            result = future.result()
            for log in result["logs"]:
                print(log)
            for k in ("analyzed", "skipped", "errors"):
                total[k] += result[k]
            # Serialize writes in the main thread to avoid races on the CSV/Sheet
            for row, acc_no, filed_at, target, structure in result["rows"]:
                existing = sheets_client.find_row_index(ws, row["Acquirer"], target)
                if existing:
                    print(f"[{result['ticker']}]   {acc_no} ({filed_at}): {target} already tracked (row {existing})")
                else:
                    sheets_client.append_acquisition(ws, row)
                    new_count += 1
                    val = row["$ to cap table"]
                    val_s = f"${val:,.0f}" if isinstance(val, (int, float)) else (val or "n/a")
                    print(f"[{result['ticker']}]   {acc_no} ({filed_at}): ACQUISITION → {target} ({val_s}, {structure})")
            if result["newest_acc"]:
                state.mark_seen(s, result["ticker"], "8-K", result["newest_acc"])

    state.save(s)
    print(
        f"\nDone: {new_count} new acquisitions written, "
        f"{total['analyzed']} filings analyzed by LLM ({total['skipped']} judged non-M&A), "
        f"{total['errors']} errors. Used {max_workers} parallel workers."
    )
    return 0 if total["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(run())
