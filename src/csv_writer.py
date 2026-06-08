"""CSV output mode. No Google API required.

Mirrors the sheets_client.py API so monitor_8k / monitor_10q can switch
backends transparently via OUTPUT_MODE env var.

The CSV lives at data/acquisitions.csv by default. Each mutation rewrites
the whole file (small-data assumption — fine up to a few thousand rows).
"""
from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

from . import config

CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "acquisitions.csv"


class _CsvBuffer:
    """In-memory CSV that mimics a gspread worksheet enough for our callers."""

    def __init__(self, path: Path):
        self.path = path
        self.rows: list[dict] = []
        self.headers: list[str] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        with open(self.path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            self.headers = list(reader.fieldnames or [])
            self.rows = [dict(r) for r in reader]

    def _save(self) -> None:
        if not self.headers:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=self.headers)
            writer.writeheader()
            writer.writerows(self.rows)


def open_sheet() -> _CsvBuffer:
    return _CsvBuffer(CSV_PATH)


def ensure_header(ws: _CsvBuffer) -> None:
    if not ws.headers:
        ws.headers = list(config.SHEET_COLUMNS)
        ws._save()


def append_acquisition(ws: _CsvBuffer, row: dict) -> None:
    row.setdefault("last_updated", dt.date.today().isoformat())
    if not ws.headers:
        ws.headers = list(config.SHEET_COLUMNS)
    out = {col: row.get(col, "") for col in ws.headers}
    ws.rows.append(out)
    ws._save()


def find_row_index(ws: _CsvBuffer, acquirer_ticker: str, target: str) -> int | None:
    """Return 1-indexed row (row 1 = header) matching this acquirer + target."""
    if not target:
        return None
    target_lc = target.lower()
    for i, r in enumerate(ws.rows, start=2):
        if str(r.get("acquirer_ticker", "")).strip().upper() == acquirer_ticker.upper():
            row_target = str(r.get("target", "")).lower()
            if row_target and (target_lc in row_target or row_target in target_lc):
                return i
    return None


def update_acquisition(ws: _CsvBuffer, row: dict, row_index: int) -> None:
    idx = row_index - 2  # convert to 0-indexed data row (row 1 is header)
    if idx < 0 or idx >= len(ws.rows):
        return
    row.setdefault("last_updated", dt.date.today().isoformat())
    for col, val in row.items():
        if val is None or val == "":
            continue
        if col not in ws.headers:
            continue
        ws.rows[idx][col] = val
    ws._save()
