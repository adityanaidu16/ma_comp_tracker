"""Track which filings have already been processed per ticker.

The state file is a JSON dict: {ticker: {form_type: last_processed_accession_no}}.
"""
from __future__ import annotations

import json
from pathlib import Path

STATE_PATH = Path(__file__).resolve().parent.parent / "data" / "state.json"


def load() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def save(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True))


def last_seen(state: dict, ticker: str, form_type: str) -> str | None:
    return state.get(ticker, {}).get(form_type)


def mark_seen(state: dict, ticker: str, form_type: str, accession_no: str) -> None:
    state.setdefault(ticker, {})[form_type] = accession_no
