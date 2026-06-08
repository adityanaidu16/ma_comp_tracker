"""Print a readable summary of the current data/acquisitions.csv.

Useful as a quick standup-prep view: shows how many deals are tracked,
which acquirers, and the most recent ones at the top.

Run with: .venv/bin/python -m scripts.summary
or: make summary
"""
from __future__ import annotations

import csv
import datetime as dt
import sys
from pathlib import Path

CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "acquisitions.csv"


def _parse_date(s: str) -> dt.date | None:
    if not s:
        return None
    for fmt in ("%b %Y", "%B %Y", "%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            return dt.datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _fmt_dollar(v) -> str:
    try:
        n = float(v)
        if n >= 1e9:
            return f"${n/1e9:,.2f}B"
        if n >= 1e6:
            return f"${n/1e6:,.1f}M"
        return f"${n:,.0f}"
    except (TypeError, ValueError):
        return str(v) if v else "-"


def main() -> int:
    if not CSV_PATH.exists():
        print(f"No data yet at {CSV_PATH}.")
        print("Run `make run` to populate.")
        return 1

    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        print("CSV exists but has no acquisitions tracked yet.")
        return 0

    # Sort by parsed Date descending (most recent first)
    rows.sort(key=lambda r: (_parse_date(r.get("Date", "")) or dt.date.min), reverse=True)

    # Header
    print(f"\nm&a comp tracker — {len(rows)} acquisition(s) tracked\n")
    print(f"{'Date':<10}  {'Acquirer':<25}  {'Company':<40}  {'$ to cap table':>16}")
    print("-" * 99)

    # Body
    for r in rows:
        date = (r.get("Date") or "")[:10]
        acquirer = (r.get("Acquirer") or "")[:25]
        company = (r.get("Company") or "")[:40]
        value = _fmt_dollar(r.get("$ to cap table"))
        print(f"{date:<10}  {acquirer:<25}  {company:<40}  {value:>16}")

    # Footer: per-acquirer count
    by_acquirer: dict[str, int] = {}
    for r in rows:
        a = r.get("Acquirer", "(unknown)")
        by_acquirer[a] = by_acquirer.get(a, 0) + 1
    print(f"\nBy acquirer: " + ", ".join(f"{k} ({v})" for k, v in sorted(by_acquirer.items(), key=lambda x: -x[1])))
    print(f"Source CSV: {CSV_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
