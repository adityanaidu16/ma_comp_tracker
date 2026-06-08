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

# Output mode: "csv" (default, writes data/acquisitions.csv, no Google setup
# required) or "sheets" (writes directly to a Google Sheet via service account).
OUTPUT_MODE            = os.getenv("OUTPUT_MODE", "csv").strip()

# Sheets mode only — ignored when OUTPUT_MODE=csv
GOOGLE_SHEET_ID        = os.getenv("GOOGLE_SHEET_ID", "").strip()
GOOGLE_SHEET_TAB       = os.getenv("GOOGLE_SHEET_TAB", "M&A Comps").strip()
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv(
    "GOOGLE_SERVICE_ACCOUNT_JSON",
    str(ROOT / "service_account.json"),
).strip()

OPENROUTER_MODEL       = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-chat").strip()

# --- sheet column layout ---------------------------------------------------
# Columns written to the CSV / Sheet, in order. Match the user's existing
# M&A comp tracker schema. Most are intentionally left blank when not in
# SEC filings (Motivation, Revenue, Engineer headcount, derived ratios)
# and can be filled in manually.
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


def pick_cap_table_value(data: dict) -> float | str:
    """Pick the best 'cash that went to the cap table' value from LLM extraction.

    Priority: 10-Q true_cash_to_capital > 10-Q cash_consideration > 10-Q total >
    8-K cash_component > 8-K headline. Returns '' if nothing usable.
    """
    for key in (
        "true_cash_to_capital_usd",
        "cash_consideration_usd",
        "total_consideration_usd",
        "cash_component_usd",
        "headline_value_usd",
    ):
        v = data.get(key)
        if isinstance(v, (int, float)) and v > 0:
            return v
    return ""
