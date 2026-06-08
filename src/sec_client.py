"""Thin wrapper around sec-api for filing discovery + retrieval.

Filings are discovered via the Query API (Lucene-style filters). Filing
text is fetched directly from EDGAR's HTML and stripped to plain text for
LLM parsing.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass

import httpx
from sec_api import QueryApi

from . import config


@dataclass
class Filing:
    ticker: str
    accession_no: str
    form_type: str
    filed_at: str          # ISO date
    period_of_report: str  # ISO date
    items: list[str]       # 8-K item codes (e.g. ["2.01"]); empty for 10-Q/10-K
    primary_doc_url: str   # URL of the main document HTML
    filing_url: str        # EDGAR filing index URL
    company_name: str


def _client() -> QueryApi:
    api_key = config.require("SEC_API_KEY", config.SEC_API_KEY)
    return QueryApi(api_key=api_key)


def _build_filing(ticker: str, hit: dict) -> Filing:
    # `items` may come as a list (newer sec-api responses) or a delimited
    # string (older). Handle both. Also tolerate non-string values for
    # other fields and treat None as empty.
    items_raw = hit.get("items")
    if isinstance(items_raw, list):
        items = [str(s).strip() for s in items_raw if s is not None and str(s).strip()]
    elif isinstance(items_raw, str):
        items = [s.strip() for s in re.split(r"[,;\s]+", items_raw) if s.strip()]
    else:
        items = []

    docs = hit.get("documentFormatFiles") or []
    primary = ""
    for d in docs:
        if not isinstance(d, dict):
            continue
        if str(d.get("type", "")).upper() in {"8-K", "10-Q", "10-K"}:
            primary = d.get("documentUrl") or ""
            break
    if not primary and docs:
        first = docs[0]
        if isinstance(first, dict):
            primary = first.get("documentUrl") or ""

    def _str(val) -> str:
        return "" if val is None else str(val)

    filed_at = _str(hit.get("filedAt"))[:10]
    period   = _str(hit.get("periodOfReport"))[:10]

    return Filing(
        ticker=ticker,
        accession_no=_str(hit.get("accessionNo")),
        form_type=_str(hit.get("formType")),
        filed_at=filed_at,
        period_of_report=period,
        items=items,
        primary_doc_url=primary,
        filing_url=_str(hit.get("linkToFilingDetails")) or _str(hit.get("linkToHtml")),
        company_name=_str(hit.get("companyName")),
    )


def list_recent_8ks(ticker: str, since_iso: str | None = None, limit: int = 20) -> list[Filing]:
    """Return recent 8-K filings for `ticker`, optionally only those filed on/after `since_iso`.

    We deliberately do NOT pre-filter by item code because 8-Ks announcing
    acquisitions can be filed under Item 1.01 (entry into material agreement),
    Item 2.01 (completion of acquisition), or Item 7.01 (Reg FD). The LLM
    filter in monitor_8k.py handles relevance.
    """
    q = f"ticker:{ticker} AND formType:\"8-K\""
    if since_iso:
        q += f" AND filedAt:[{since_iso} TO *]"
    payload = {
        "query": q,
        "from": "0", "size": str(limit),
        "sort": [{"filedAt": {"order": "desc"}}],
    }
    resp = _client().get_filings(payload)
    return [_build_filing(ticker, h) for h in resp.get("filings", [])]


def list_recent_10qs(ticker: str, since_iso: str | None = None, limit: int = 6) -> list[Filing]:
    q = f"ticker:{ticker} AND (formType:\"10-Q\" OR formType:\"10-K\")"
    if since_iso:
        q += f" AND filedAt:[{since_iso} TO *]"
    payload = {
        "query": q,
        "from": "0", "size": str(limit),
        "sort": [{"filedAt": {"order": "desc"}}],
    }
    resp = _client().get_filings(payload)
    return [_build_filing(ticker, h) for h in resp.get("filings", [])]


# --- filing text retrieval --------------------------------------------------

_HTML_TAG_RE   = re.compile(r"<[^>]+>")
_ENTITY_RE     = re.compile(r"&[a-z#0-9]+;")
_WHITESPACE_RE = re.compile(r"\s+")
_UA = "ma_comp_tracker/0.1 (contact: adityanaidu16344@gmail.com)"


def fetch_filing_text(url: str, timeout: float = 30.0) -> str:
    """GET the filing HTML and strip to plain text suitable for LLM parsing."""
    if not url:
        return ""
    for attempt in range(3):
        try:
            with httpx.Client(timeout=timeout, headers={"User-Agent": _UA}, follow_redirects=True) as c:
                r = c.get(url)
                r.raise_for_status()
                html = r.text
            break
        except (httpx.HTTPError, httpx.TimeoutException):
            if attempt == 2:
                return ""
            time.sleep(1.5 * (attempt + 1))
    text = _HTML_TAG_RE.sub(" ", html)
    text = _ENTITY_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def locate_business_combinations_section(filing_text: str) -> str:
    """Slice the filing text to the Business Combinations / Acquisitions footnote.

    10-Q and 10-K footnotes don't have standardized section codes, so we use
    heading-text patterns. If we can't find a clear header, return a 30K-char
    window around the first 'Business Combinations' mention (LLM will sort out).
    """
    if not filing_text:
        return ""
    # Look for footnote headers, ordered by specificity
    candidates = [
        r"(?:^|\.)\s*\d{1,2}\.\s*Business\s+Combinations",
        r"(?:^|\.)\s*Note\s+\d{1,2}[\.:\)]?\s*Business\s+Combinations",
        r"(?:^|\.)\s*\d{1,2}\.\s*Acquisitions",
        r"(?:^|\.)\s*Note\s+\d{1,2}[\.:\)]?\s*Acquisitions",
    ]
    for pat in candidates:
        m = re.search(pat, filing_text, flags=re.IGNORECASE)
        if m:
            start = m.start()
            # Take 25K chars from the heading; usually covers the whole footnote
            return filing_text[start:start + 25_000]
    # Fallback: search for first inline mention and grab a window
    m = re.search(r"Business\s+Combinations", filing_text, flags=re.IGNORECASE)
    if m:
        start = max(0, m.start() - 1_000)
        return filing_text[start:start + 30_000]
    # Last resort: return the financial-statements area heuristically (middle 40%)
    mid = len(filing_text) // 2
    return filing_text[max(0, mid - 15_000):mid + 15_000]
