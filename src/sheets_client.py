"""Google Sheets writer.

Uses a service account JSON file for auth. The service account email must
be granted Editor access on the target Sheet (Share → paste the email).

API:
  open_sheet()                     -> gspread.Worksheet ready to read/write
  ensure_header(ws)                -> writes config.SHEET_COLUMNS if row 1 is empty
  append_acquisition(ws, row)      -> adds a new row (8-K stage)
  update_acquisition(ws, row, key) -> updates the row matching `key` (10-Q stage)
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

from . import config

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]


def _client() -> gspread.Client:
    sa_path = Path(config.GOOGLE_SERVICE_ACCOUNT_JSON)
    if not sa_path.exists():
        raise RuntimeError(
            f"GOOGLE_SERVICE_ACCOUNT_JSON does not exist at {sa_path}. "
            "Create a GCP service account, download the JSON key, "
            "and point GOOGLE_SERVICE_ACCOUNT_JSON to it. See README."
        )
    creds = Credentials.from_service_account_file(str(sa_path), scopes=SCOPES)
    return gspread.authorize(creds)


def open_sheet():
    sheet_id = config.require("GOOGLE_SHEET_ID", config.GOOGLE_SHEET_ID)
    gc = _client()
    sh = gc.open_by_key(sheet_id)
    try:
        return sh.worksheet(config.GOOGLE_SHEET_TAB)
    except gspread.WorksheetNotFound:
        return sh.add_worksheet(title=config.GOOGLE_SHEET_TAB, rows=200, cols=len(config.SHEET_COLUMNS))


def ensure_header(ws) -> None:
    first = ws.row_values(1)
    if not first:
        ws.append_row(config.SHEET_COLUMNS, value_input_option="USER_ENTERED")


def _row_dict_to_list(row: dict) -> list:
    return [row.get(c, "") for c in config.SHEET_COLUMNS]


def append_acquisition(ws, row: dict) -> None:
    row.setdefault("last_updated", dt.date.today().isoformat())
    ws.append_row(_row_dict_to_list(row), value_input_option="USER_ENTERED")


def find_row_index(ws, acquirer_ticker: str, target: str) -> int | None:
    """Return the 1-indexed row in the Sheet matching (acquirer_ticker, target).

    Match is case-insensitive substring on target name to tolerate slight
    discrepancies between 8-K and 10-Q phrasing (e.g. "Splunk Inc." vs "Splunk").
    """
    if not target:
        return None
    rows = ws.get_all_records()
    target_lc = target.lower()
    for i, r in enumerate(rows, start=2):
        if str(r.get("acquirer_ticker", "")).strip().upper() == acquirer_ticker.upper():
            row_target = str(r.get("target", "")).lower()
            if row_target and (target_lc in row_target or row_target in target_lc):
                return i
    return None


def update_acquisition(ws, row: dict, row_index: int) -> None:
    row.setdefault("last_updated", dt.date.today().isoformat())
    headers = ws.row_values(1)
    existing = ws.row_values(row_index)
    # Pad existing to header length
    existing += [""] * (len(headers) - len(existing))
    for i, col in enumerate(headers):
        v = row.get(col)
        if v is None or v == "":
            continue  # don't overwrite existing values with empty
        existing[i] = v
    ws.update(
        f"A{row_index}:{gspread.utils.rowcol_to_a1(row_index, len(headers))}",
        [existing],
        value_input_option="USER_ENTERED",
    )
