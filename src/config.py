"""Config: comp set, env vars, sheet layout.

Edit COMP_SET to add or remove tickers. Each entry is (ticker, label) where
label is the human-friendly name written to the Sheet. Tickers must match
SEC EDGAR (which usually means the listed ticker on the primary exchange).
"""
from __future__ import annotations

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
# These are the columns written to the Sheet (in order). The first run will
# create the header row if the Sheet is empty.
SHEET_COLUMNS = [
    "acquirer",                 # e.g. "Cisco"
    "acquirer_ticker",          # e.g. "CSCO"
    "target",                   # acquired company name
    "announced_date",           # ISO YYYY-MM-DD
    "closed_date",              # ISO YYYY-MM-DD (or empty until 10-Q)
    "headline_value_usd",       # announced total deal value
    "cash_consideration_usd",   # cash paid (from 10-Q)
    "stock_consideration_usd",  # stock fair value (from 10-Q)
    "contingent_usd",           # earnout fair value (from 10-Q)
    "true_cash_to_capital_usd", # cash - escrow - WC adj - debt assumed
    "structure",                # "all-cash", "stock-and-cash", "all-stock"
    "stage",                    # "announced" / "closed-reconciled"
    "source_8k_url",
    "source_10q_url",
    "notes",
    "last_updated",
]

def require(name: str, value: str) -> str:
    if not value:
        raise RuntimeError(
            f"{name} is empty. Set it in .env (see .env.example) before running."
        )
    return value
