"""LLM extraction via OpenRouter using a cheap model (DeepSeek V4-Flash by default).

Two extractors:
  - extract_8k_acquisition_announcement(text) returns acquisition details from
    an 8-K (announced deal: target, headline value, structure, dates).
  - extract_10q_business_combination(text) returns the purchase-price breakdown
    from a 10-Q/10-K Business Combinations footnote (cash, stock, contingent,
    escrow, true cash to cap table).

Both return None when the LLM judges the text does not contain a real
acquisition. This is important: an 8-K can be filed under Item 1.01/2.01 for
many reasons unrelated to M&A.
"""
from __future__ import annotations

import json
import re

from openai import OpenAI

from . import config


def _client() -> OpenAI:
    return OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=config.require("OPENROUTER_API_KEY", config.OPENROUTER_API_KEY),
    )


def _chat(messages: list[dict]) -> str:
    resp = _client().chat.completions.create(
        model=config.OPENROUTER_MODEL,
        messages=messages,
        temperature=0,
        max_tokens=1200,
    )
    return resp.choices[0].message.content or ""


# JSON output sometimes comes wrapped in ```json fences. Strip them.
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _parse_json(raw: str) -> dict | None:
    if not raw:
        return None
    raw = raw.strip()
    m = _FENCE_RE.search(raw)
    if m:
        raw = m.group(1).strip()
    # Find the first { and last } in case the model added prose
    if "{" in raw and "}" in raw:
        raw = raw[raw.find("{"):raw.rfind("}") + 1]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


# --- 8-K extractor ---------------------------------------------------------

_8K_SYSTEM = """You analyze SEC 8-K filings to detect M&A acquisition announcements.

If the filing announces or describes an acquisition, merger, or business
combination where the FILING COMPANY is the ACQUIRER, return JSON:

{
  "is_acquisition": true,
  "target": "Target Company Inc.",
  "announced_date": "YYYY-MM-DD",
  "closed_date": "YYYY-MM-DD" or null,
  "headline_value_usd": number_or_null,
  "structure": "all-cash" | "stock-and-cash" | "all-stock" | "unknown",
  "cash_component_usd": number_or_null,
  "stock_component_usd": number_or_null,
  "summary": "one-sentence description"
}

If the 8-K is NOT about the filing company acquiring another company
(e.g. it's earnings, leadership change, divestiture, financing, etc.),
return: {"is_acquisition": false}

Rules:
- All dollar values in USD (convert if disclosed in another currency)
- If value is reported in millions ("$500 million"), return 500000000
- If value is reported in billions ("$1.2 billion"), return 1200000000
- Return null for fields you cannot determine from the text
- Only flag as acquisition when the FILING COMPANY IS THE BUYER. If the
  filing company is being acquired (target side), return is_acquisition: false.
"""


def extract_8k_acquisition_announcement(text: str) -> dict | None:
    if not text:
        return None
    snippet = text[:60_000]  # cap for token budget
    raw = _chat([
        {"role": "system", "content": _8K_SYSTEM},
        {"role": "user", "content": f"8-K filing text follows. Extract per the schema.\n\n{snippet}"},
    ])
    data = _parse_json(raw)
    if not data or not data.get("is_acquisition"):
        return None
    return data


# --- 10-Q / 10-K extractor -------------------------------------------------

_10Q_SYSTEM = """You analyze the Business Combinations footnote in SEC 10-Q
and 10-K filings to extract purchase-price-allocation details for acquisitions
the filing company completed.

For each acquisition the company describes in the footnote, return a JSON list:

{
  "acquisitions": [
    {
      "target": "Target Company Inc.",
      "closed_date": "YYYY-MM-DD" or null,
      "total_consideration_usd": number_or_null,
      "cash_consideration_usd": number_or_null,
      "stock_consideration_usd": number_or_null,
      "contingent_consideration_usd": number_or_null,
      "escrow_or_holdback_usd": number_or_null,
      "debt_assumed_usd": number_or_null,
      "working_capital_adjustment_usd": number_or_null,
      "true_cash_to_capital_usd": number_or_null,
      "notes": "one or two sentences with anything material the structured fields miss"
    }
  ]
}

If the footnote describes no acquisitions, return: {"acquisitions": []}

"true_cash_to_capital_usd" should be your best estimate of net cash that
actually reached the target's cap table holders, defined as:
  cash_consideration - escrow_or_holdback - working_capital_adjustment - debt_assumed

If those components aren't broken out, set true_cash_to_capital_usd to the
cash_consideration figure and explain the limitation in notes.

All numeric values in USD. Convert millions/billions to absolute USD.
Use null for any field you cannot determine.
"""


def extract_10q_business_combination(text: str) -> dict | None:
    if not text:
        return None
    snippet = text[:80_000]
    raw = _chat([
        {"role": "system", "content": _10Q_SYSTEM},
        {"role": "user", "content": f"Business Combinations footnote text follows.\n\n{snippet}"},
    ])
    data = _parse_json(raw)
    if not data:
        return None
    return data
