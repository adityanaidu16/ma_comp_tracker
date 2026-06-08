"""10-Q / 10-K monitoring entrypoint.

For each ticker in the comp set, list 10-Q and 10-K filings filed since last
run, locate the Business Combinations footnote, ask the LLM to extract the
purchase-price breakdown, and update existing rows in the Google Sheet with
the reconciled numbers (cash consideration, stock, contingent, true cash to
cap table).

Run with: python -m src.monitor_10q
"""
from __future__ import annotations

import datetime as dt
import sys
import traceback

from . import config, sec_client, llm_parser, state, writer as sheets_client


def run() -> int:
    s = state.load()
    today = dt.date.today().isoformat()
    # On first run, look back 180 days (one or two reporting periods).
    default_since = (dt.date.today() - dt.timedelta(days=180)).isoformat()

    ws = sheets_client.open_sheet()
    sheets_client.ensure_header(ws)

    updated = 0
    added   = 0
    analyzed_count = 0
    err_count = 0

    for ticker, label in config.COMP_SET:
        last_acc = state.last_seen(s, ticker, "10-Q")
        try:
            filings = sec_client.list_recent_10qs(ticker, since_iso=default_since, limit=6)
        except Exception as e:
            print(f"[{ticker}] list_recent_10qs error: {e}", file=sys.stderr)
            traceback.print_exc()
            err_count += 1
            continue

        new_filings = []
        for f in filings:
            if last_acc and f.accession_no == last_acc:
                break
            new_filings.append(f)

        if not new_filings:
            print(f"[{ticker}] no new 10-Qs/10-Ks since last run (state: {last_acc or 'none'})")
            continue

        print(f"[{ticker}] {len(new_filings)} new 10-Q/10-K(s) to analyze")
        for f in new_filings:
            try:
                full = sec_client.fetch_filing_text(f.primary_doc_url)
                if not full:
                    print(f"[{ticker}]   {f.accession_no} ({f.filed_at}): empty filing text, skipped")
                    continue
                section = sec_client.locate_business_combinations_section(full)
                if not section:
                    print(f"[{ticker}]   {f.accession_no} ({f.filed_at}): no Business Combinations footnote located")
                    continue
                analyzed_count += 1
                data = llm_parser.extract_10q_business_combination(section)
                if not data or not data.get("acquisitions"):
                    print(f"[{ticker}]   {f.accession_no} ({f.filed_at}): footnote present but no acquisitions described")
                    continue
                for acq in data["acquisitions"]:
                    target = acq.get("target") or ""
                    if not target:
                        continue
                    date_iso = acq.get("closed_date") or f.filed_at
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
                    idx = sheets_client.find_row_index(ws, label, target)
                    if idx:
                        sheets_client.update_acquisition(ws, row, idx)
                        updated += 1
                        print(f"[{ticker}]   {f.accession_no} ({f.filed_at}): updated row {idx} for {target}")
                    else:
                        # No prior 8-K row; append as a fresh closed-reconciled row.
                        sheets_client.append_acquisition(ws, row)
                        added += 1
                        print(f"[{ticker}]   {f.accession_no} ({f.filed_at}): added (no prior 8-K row) for {target}")
            except Exception as e:
                print(f"[{ticker}] error processing {f.accession_no}: {e}", file=sys.stderr)
                traceback.print_exc()
                err_count += 1
                continue

        if filings:
            state.mark_seen(s, ticker, "10-Q", filings[0].accession_no)

    state.save(s)
    print(
        f"\nDone: {updated} rows updated, {added} new rows, "
        f"{analyzed_count} footnotes analyzed by LLM, {err_count} errors."
    )
    return 0 if err_count == 0 else 1


if __name__ == "__main__":
    sys.exit(run())
