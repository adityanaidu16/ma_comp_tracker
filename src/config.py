"""Config: comp set, env vars, sheet layout.

Edit COMP_SET to add or remove tickers. Each entry is (ticker, label) where
label is the human-friendly name written to the Sheet. Tickers must match
SEC EDGAR (which usually means the listed ticker on the primary exchange).
"""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# --- comp set ---------------------------------------------------------------
# Edit this list to control which acquirers are monitored. Order doesn't matter.
COMP_SET: list[tuple[str, str]] = [
    ("CSCO", "Cisco"),
    ("PANW", "Palo Alto Networks"),
    ("SNOW", "Snowflake"),
    ("CRM",  "Salesforce"),
    ("DDOG", "Datadog"),
    ("NOW",  "ServiceNow"),
    ("CRWD", "CrowdStrike"),
    ("ZS",   "Zscaler"),
    ("MDB",  "MongoDB"),
    ("NET",  "Cloudflare"),
    ("FTNT", "Fortinet"),
    ("ORCL", "Oracle"),
    ("ADBE", "Adobe"),
    ("WDAY", "Workday"),
    ("MSFT", "Microsoft"),
]

# --- env ---------------------------------------------------------------------
SEC_API_KEY            = os.getenv("SEC_API_KEY", "").strip()
OPENROUTER_API_KEY     = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL       = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-chat").strip()

# Drop acquisitions older than this many days from the output. Default 180
# (~ last two quarters). Increase to widen the window, set to 0 to disable.
MAX_ACQUISITION_AGE_DAYS = int(os.getenv("MAX_ACQUISITION_AGE_DAYS", "180"))

# --- CSV column layout -----------------------------------------------------
# Columns written to data/acquisitions.csv, in order. Match the comp tracker
# schema. Most are intentionally left blank when not in SEC filings
# (Motivation, Revenue, Engineer headcount, derived ratios) and can be
# filled in manually or by formulas after import to a Sheet.
SHEET_COLUMNS = [
    "Company",          # target company (from filing)
    "Acquirer",         # human-friendly acquirer name from COMP_SET
    "Date",             # "Mon YYYY" (e.g. "Feb 2026")
    "Motivation",       # blank — analyst fills in
    "$ to cap table",   # best estimate of net cash to target shareholders
    "Revenue ($)",      # blank — typically not in 10-Q
    "Engineers",        # blank — not in SEC filings
    "$ / Engineer",     # blank — derived in Sheet
    "Rev. Multiple",    # blank — derived in Sheet
    "Notes",            # LLM summary + reconciliation notes
    "Source",           # SEC filing URL (8-K first, replaced by 10-Q on reconciliation)
]

def require(name: str, value: str) -> str:
    if not value:
        raise RuntimeError(
            f"{name} is empty. Set it in .env (see .env.example) before running."
        )
    return value


def format_month_year(iso_date: str) -> str:
    """Convert 'YYYY-MM-DD' to 'Mon YYYY' (e.g. '2026-02-05' -> 'Feb 2026').

    Returns the input unchanged if it can't be parsed; returns '' for empty.
    """
    if not iso_date:
        return ""
    try:
        d = dt.date.fromisoformat(str(iso_date)[:10])
        return d.strftime("%b %Y")
    except (ValueError, TypeError):
        return str(iso_date)


def parse_loose_date(date_str) -> dt.date | None:
    """Parse a date string in any of the common formats the LLM might emit.

    Tries ISO (YYYY-MM-DD, YYYY-MM, YYYY), then human ("Feb 2026",
    "February 2026"). Returns None when nothing parses.
    """
    if not date_str:
        return None
    s = str(date_str).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y", "%b %Y", "%B %Y", "%b. %Y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def is_recent_enough(date_str, max_days: int | None = None,
                     today: dt.date | None = None) -> bool:
    """True iff the date string is within max_days of `today`. Unparseable
    dates return True (don't silently drop rows we can't categorize).
    """
    if max_days is None:
        max_days = MAX_ACQUISITION_AGE_DAYS
    if max_days <= 0:
        return True
    d = parse_loose_date(date_str)
    if d is None:
        return True
    today = today or dt.date.today()
    return (today - d).days <= max_days


def pick_cap_table_value(data: dict) -> float | str:
    """Pick the best 'total value to cap table' value from LLM extraction.

    M&A convention: "$ to cap table" = total deal value transferred to target
    shareholders (cash + stock at fair value + contingent), not cash alone.
    For Snowflake/Observe: $596.2M (= $286.2M cash + $285.3M stock + escrow),
    not the $286.2M cash component.

    Priority:
      1. total_consideration_usd (10-Q, disclosed total)
      2. headline_value_usd (8-K, announced total)
      3. Sum of (cash + stock + contingent) components if any are present
      4. cash_consideration alone (worst case — better than nothing)
      5. true_cash_to_capital (cash net of escrow/WC/debt — cash-only fallback)

    Returns '' if nothing usable.
    """
    # 1-2: prefer disclosed totals
    for key in ("total_consideration_usd", "headline_value_usd"):
        v = data.get(key)
        if isinstance(v, (int, float)) and v > 0:
            return v
    # 3: sum of components
    summed = 0.0
    for key in ("cash_consideration_usd", "stock_consideration_usd",
                "contingent_consideration_usd"):
        v = data.get(key)
        if isinstance(v, (int, float)) and v > 0:
            summed += float(v)
    if summed > 0:
        return summed
    # 4-5: cash-only fallback
    for key in ("cash_component_usd", "cash_consideration_usd", "true_cash_to_capital_usd"):
        v = data.get(key)
        if isinstance(v, (int, float)) and v > 0:
            return v
    return ""
