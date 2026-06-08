"""CSV writer for acquisitions data.

Writes to data/acquisitions.csv. Each mutation rewrites the whole file
(small-data assumption — fine up to a few thousand rows). Header order
is controlled by config.SHEET_COLUMNS.
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


def find_row_index(ws: _CsvBuffer, acquirer_name: str, target: str) -> int | None:
    """Return 1-indexed row (row 1 = header) matching this Acquirer + Company.

    Matches case-insensitive on the human-friendly Acquirer name (e.g. "Cisco")
    and case-insensitive substring on Company (target) to tolerate slight
    variations between 8-K and 10-Q phrasing.
    """
    if not target:
        return None
    target_lc = target.strip().lower()
    acq_lc = acquirer_name.strip().lower()
    for i, r in enumerate(ws.rows, start=2):
        if str(r.get("Acquirer", "")).strip().lower() == acq_lc:
            row_target = str(r.get("Company", "")).strip().lower()
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
