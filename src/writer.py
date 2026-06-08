"""Output writer factory.

Picks between CSV (no Google API needed) and Google Sheets based on the
OUTPUT_MODE env var (default "csv"). Both backends expose the same API:
open_sheet(), ensure_header(), append_acquisition(), find_row_index(),
update_acquisition().
"""
from __future__ import annotations

from . import config

_mode = (config.OUTPUT_MODE or "csv").lower()

if _mode == "sheets":
    from . import sheets_client as _impl
else:
    from . import csv_writer as _impl

open_sheet         = _impl.open_sheet
ensure_header      = _impl.ensure_header
append_acquisition = _impl.append_acquisition
find_row_index     = _impl.find_row_index
update_acquisition = _impl.update_acquisition
