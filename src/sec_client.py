"""Thin wrapper around sec-api for filing discovery + retrieval.

Filings are discovered via the Query API (Lucene-style filters). Filing
text is fetched directly from EDGAR's HTML and stripped to plain text for
LLM parsing.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass

import warnings

import httpx
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from sec_api import QueryApi

from . import config

# SEC filings are sometimes served as XHTML; lxml's HTML parser handles them
# fine but BeautifulSoup emits a warning. Suppress — we don't need the noise.
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)


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
        primary_doc_url=normalize_filing_url(primary),
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

_WHITESPACE_RE = re.compile(r"[ \t]+")
_BLANK_LINE_RE = re.compile(r"\n\s*\n\s*\n+")
_UA = "ma_comp_tracker/0.1 (contact: adityanaidu16344@gmail.com)"

# Tags whose content is non-narrative and should be discarded entirely
_NOISE_TAGS = {
    "script", "style", "noscript", "meta", "link",
    "head", "header", "footer", "nav", "svg", "form",
    "button", "input", "select", "iframe",
}


def normalize_filing_url(url: str) -> str:
    """SEC's inline XBRL viewer (https://www.sec.gov/ix?doc=/Archives/...)
    returns a JS wrapper, not the actual filing. Strip /ix?doc= so we hit
    the underlying HTML at /Archives/... directly.
    """
    if not url:
        return url
    if "/ix?doc=" in url:
        m = re.match(r"^(https?://[^/]+)/ix\?doc=(.*)$", url)
        if m:
            host, rest = m.group(1), m.group(2)
            if not rest.startswith("/"):
                rest = "/" + rest
            url = host + rest
    return url


def fetch_filing_text(url: str, timeout: float = 30.0) -> str:
    """GET the filing HTML and parse to plain text suitable for LLM input.

    Uses BeautifulSoup with lxml to strip script/style/nav noise, decode HTML
    entities, and emit paragraph-separated text (one element per line). This
    is much cleaner than regex tag-stripping which leaves JS and CSS content
    inline.
    """
    url = normalize_filing_url(url)
    if not url:
        return ""
    html = ""
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
    if not html:
        return ""
    return _html_to_text(html)


def _html_to_text(html: str) -> str:
    """Convert SEC filing HTML to clean text. Strips scripts/styles, collapses
    whitespace, preserves paragraph breaks for LLM readability.
    """
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(_NOISE_TAGS):
        tag.decompose()
    # Tables in 10-Q footnotes carry purchase-price data — keep their text but
    # add line breaks so the LLM sees one cell per line, not one giant blob.
    for tr in soup.find_all("tr"):
        tr.append("\n")
    for cell in soup.find_all(["td", "th"]):
        cell.insert_after(" | ")
    # Block-level elements get newline separation
    for block in soup.find_all(["p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5"]):
        block.insert_after("\n")
    text = soup.get_text(separator=" ", strip=False)
    # Collapse horizontal whitespace, then squeeze 3+ blank lines down to 2
    lines = [_WHITESPACE_RE.sub(" ", ln).strip() for ln in text.split("\n")]
    lines = [ln for ln in lines if ln]
    return _BLANK_LINE_RE.sub("\n\n", "\n".join(lines)).strip()


def locate_business_combinations_section(filing_text: str) -> str:
    """Slice the filing text to the Business Combinations / Acquisitions footnote.

    Strategy: find ALL acquisition-related heading candidates, score each by
    (a) how strong the heading pattern is (numbered "Note N. Business
    Acquisitions" > inline "Business Combinations"), (b) position (footnotes
    in back half), and (c) presence of acquisition-narrative phrasing nearby
    ("we acquired", "all outstanding equity", "purchase consideration"). Pick
    the highest-scoring match and return a 40K-char window from there.
    """
    if not filing_text:
        return ""

    # Candidate heading patterns. Higher base score = more specific.
    patterns = [
        # Numbered notes with explicit "Business Acquisitions/Combinations"
        (r"\bNote\s+\d{1,2}[\.:\)\s\-—]+Business\s+Acquisitions?\b", 16),
        (r"\b\d{1,2}\s*[\.\)]\s+Business\s+Acquisitions?\b",         15),
        (r"\bNote\s+\d{1,2}[\.:\)\s\-—]+Business\s+Combinations\b",  14),
        (r"\b\d{1,2}\s*[\.\)]\s+Business\s+Combinations\b",          13),
        # Numbered notes with just "Acquisitions"
        (r"\bNote\s+\d{1,2}[\.:\)\s\-—]+Acquisitions?\b",             9),
        (r"\b\d{1,2}\s*[\.\)]\s+Acquisitions?\b",                     9),
        # Named alternatives
        (r"\bAcquisitions?\s+and\s+Divestitures?\b",                  8),
        (r"\bCompleted\s+Acquisitions?\b",                            7),
        (r"\bRecent\s+Acquisitions?\b",                               6),
        # Inline / standalone phrase (weakest — usually appears in many places)
        (r"\bBusiness\s+Acquisitions?\b",                             4),
        (r"\bBusiness\s+Combinations\b",                              3),
    ]

    candidates: list[tuple[int, int]] = []
    for pat, base_score in patterns:
        for m in re.finditer(pat, filing_text, flags=re.IGNORECASE):
            candidates.append((m.start(), base_score))

    if not candidates:
        mid = int(len(filing_text) * 0.55)
        return filing_text[max(0, mid - 20_000):mid + 20_000]

    n = len(filing_text)
    # Strong narrative signals: text only appears in actual acquisition
    # footnotes, not in accounting policies or intangibles tables.
    NARRATIVE = (
        "we acquired", "the company acquired", "the registrant acquired",
        "all outstanding equity", "all outstanding shares",
        "purchase consideration", "total purchase consideration",
        "initial cash consideration", "acquisition-date fair value",
        "consideration transferred",
    )
    # Weaker signals: present in many financial sections (intangibles tables,
    # goodwill rollforwards). Worth +0.5 each, not +1.
    WEAK = (
        "goodwill", "intangible assets", "fair value",
        "contingent consideration", "deferred consideration",
    )

    scored: list[tuple[int, int]] = []
    for off, base in candidates:
        position_score = 0
        if off > n * 0.40:
            position_score += 3
        if off > n * 0.60:
            position_score += 3
        # Window for keyword scoring — look forward from the heading
        window = filing_text[off:off + 6_000].lower()
        narrative_score = sum(2 for kw in NARRATIVE if kw in window)
        weak_score      = sum(1 for kw in WEAK if kw in window) // 2  # int division → halves
        total = base + position_score + narrative_score + weak_score
        scored.append((off, total))

    # Highest score wins; tie-break on later document position
    scored.sort(key=lambda x: (x[1], x[0]), reverse=True)
    best_off = scored[0][0]
    return filing_text[max(0, best_off - 500):best_off + 40_000]
