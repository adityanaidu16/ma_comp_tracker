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
combination where the FILING COMPANY is the ACQUIRER, return JSON in this
exact schema (the bracketed UPPERCASE strings are TYPE HINTS — never copy
them into your output):

{
  "is_acquisition": true,
  "target": "<EXACT TARGET COMPANY NAME AS DISCLOSED IN THE FILING>",
  "announced_date": "<YYYY-MM-DD>",
  "closed_date": "<YYYY-MM-DD OR null>",
  "headline_value_usd": <NUMBER OR null>,
  "structure": "<one of: all-cash | stock-and-cash | all-stock | unknown>",
  "cash_component_usd": <NUMBER OR null>,
  "stock_component_usd": <NUMBER OR null>,
  "summary": "<one-sentence description>"
}

If the 8-K is NOT about the filing company acquiring another company (e.g.
it's earnings, leadership change, divestiture, financing, dividend, etc.),
return: {"is_acquisition": false}

CRITICAL RULES:
- Do NOT invent or hallucinate target names. If the filing does not clearly
  name the target company, return {"is_acquisition": false}. Never output
  placeholder names like "Target Company Inc.", "the Target", "Acquired Co",
  or any other generic string. Only output the literal name the filing uses.
- Only flag as acquisition when the FILING COMPANY IS THE BUYER. If the
  filing company is being acquired (target side, merger proxy, etc.), return
  {"is_acquisition": false}.

Numeric conventions:
- All dollar values in USD (convert if disclosed in another currency).
- If value is in millions ("$500 million"), return 500000000.
- If value is in billions ("$1.2 billion"), return 1200000000.
- Return null for any field you cannot determine from the text.
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

_10Q_SYSTEM = """You analyze the Business Combinations / Business Acquisitions
footnote in SEC 10-Q and 10-K filings to extract purchase-price details for
acquisitions the filing company has completed.

EXTRACT EVERY NAMED ACQUISITION the footnote mentions — do not stop at the
first one. Some 10-Qs describe multiple acquisitions in separate subsections
(one per deal); your output must include every distinct target named.
Include:
- Acquisitions closed in the current reporting period
- Acquisitions closed in prior periods that are still being discussed
  (e.g. for goodwill measurement, purchase price allocation adjustments,
  earn-out remeasurements, integration disclosures)
- Pending acquisitions described in subsequent-events language, if a target
  is named and a value is disclosed

Before returning, re-scan the text for any sentence of the form "On [date],
we acquired [Name]" or "[Name] was acquired" or "[Name] acquisition" and
make sure each unique target appears as its own row in the output array.

Return JSON in this exact schema (the bracketed UPPERCASE strings are TYPE
HINTS — never copy them into your output):

{
  "acquisitions": [
    {
      "target": "<EXACT TARGET COMPANY NAME AS DISCLOSED, OR aggregate-undisclosed>",
      "closed_date": "<YYYY-MM-DD OR null>",
      "total_consideration_usd": <NUMBER OR null>,
      "cash_consideration_usd": <NUMBER OR null>,
      "stock_consideration_usd": <NUMBER OR null>,
      "contingent_consideration_usd": <NUMBER OR null>,
      "escrow_or_holdback_usd": <NUMBER OR null>,
      "debt_assumed_usd": <NUMBER OR null>,
      "working_capital_adjustment_usd": <NUMBER OR null>,
      "true_cash_to_capital_usd": <NUMBER OR null>,
      "notes": "<short note with anything material the structured fields miss>"
    }
  ]
}

CRITICAL RULES ON TARGET NAMES:
- The "target" field must be the LITERAL company name as the filing writes it
  (e.g. "WorkFusion, Inc.", "Splunk Inc.", "Peak AI Limited"). Look for proper
  nouns, capitalized company-name strings, and explicit naming like
  "we acquired X" or "the acquisition of X".
- Do NOT invent or hallucinate target names. NEVER output placeholder strings
  like "Target Company Inc.", "the Target", "Acquired Co.", "Acquired Company",
  "Company X", or any other generic name. These are not real targets.
- If the footnote uses AGGREGATE language ("we completed several acquisitions
  during the period for aggregate consideration of $X" or "the goodwill from
  acquisitions completed during the first nine months") WITHOUT naming specific
  target companies, emit ONE row with target = "aggregate-undisclosed" and put
  the period description in notes (e.g. "Cisco aggregate disclosure for first
  nine months FY2026, no per-deal target names provided").

OTHER RULES:
- If the footnote describes NO acquisitions at all (only generic accounting-
  policy language about how the company *would* account for a hypothetical
  future acquisition), return {"acquisitions": []}.
- For ongoing PPA / goodwill references to a previously-named acquisition,
  include the named target with whatever updated values are available. Set
  notes to indicate it's an ongoing reference.

"true_cash_to_capital_usd" should be your best estimate of net cash that
reached the target's cap table holders, defined as:
  cash_consideration - escrow_or_holdback - working_capital_adjustment - debt_assumed

If those components aren't broken out, set true_cash_to_capital_usd to the
cash_consideration figure and explain the limitation in notes.

All numeric values in USD. Convert millions/billions to absolute USD. Use
null for any field you cannot determine.
"""


def extract_10q_business_combination(text: str) -> dict | None:
    if not text:
        return None
    # Section locator may return up to 150K chars of merged windows. Bump
    # the LLM input cap so we don't truncate before all acquisitions are seen.
    snippet = text[:160_000]
    raw = _chat([
        {"role": "system", "content": _10Q_SYSTEM},
        {"role": "user", "content": f"Business Combinations / Acquisitions text follows.\n\n{snippet}"},
    ])
    data = _parse_json(raw)
    if not data:
        return None
    return data
