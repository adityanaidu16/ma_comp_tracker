# ma_comp_tracker

Monitors SEC 8-K and 10-Q filings from a configurable comp set of public
acquirers, extracts acquisition details with a cheap LLM (DeepSeek V4-Flash
via OpenRouter by default), and writes the results to a Google Sheet.

## Two-stage workflow

- **Stage 1 — 8-K monitoring (daily):** detects acquisitions when they're
  announced. Captures headline value, structure (cash vs stock), target name,
  dates. Appends one row per acquisition with `stage = "announced"`.
- **Stage 2 — 10-Q / 10-K reconciliation (weekly):** parses the Business
  Combinations footnote in subsequent quarterly/annual filings. Updates the
  existing row with the real purchase-price allocation: cash consideration,
  stock fair value, contingent consideration (earnouts), escrow, and a best-
  estimate "true cash to cap table" figure. Flips `stage` to
  `"closed-reconciled"`.

Re-running is idempotent — `data/state.json` tracks the last processed
filing per ticker per form type.

## Setup

### 1. Install

```bash
git clone <this repo>
cd ma_comp_tracker
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

### 2. API keys

Fill in `.env`:

- `SEC_API_KEY`: get at https://sec-api.io/profile
- `OPENROUTER_API_KEY`: get at https://openrouter.ai/keys
- `GOOGLE_SHEET_ID`: the long string from the Sheet URL (between `/d/` and
  `/edit`)
- `GOOGLE_SHEET_TAB`: the worksheet name (default `M&A Comps`)
- `OPENROUTER_MODEL`: leave as `deepseek/deepseek-chat` (cheapest), or
  override with e.g. `anthropic/claude-sonnet-4.5` for higher accuracy.

### 3. Google Sheets service account (5 minutes)

1. Go to https://console.cloud.google.com/iam-admin/serviceaccounts and pick
   or create a project.
2. Click **Create Service Account**, give it a name like
   `ma-comp-tracker-writer`, click **Create and Continue** through the
   permission steps (no roles needed for Sheets-only access).
3. After creating, click into the service account, go to the **Keys** tab,
   **Add Key** → **JSON**. A JSON file downloads.
4. Save the JSON file as `service_account.json` in the project root, or
   set `GOOGLE_SERVICE_ACCOUNT_JSON` in `.env` to wherever you save it.
5. **Enable the Sheets API** for the project at
   https://console.cloud.google.com/apis/library/sheets.googleapis.com
6. Open the JSON file, copy the `client_email` field (looks like
   `ma-comp-tracker-writer@your-project.iam.gserviceaccount.com`).
7. Open your Google Sheet → **Share** → paste the service account email
   → set to **Editor** → Send.

The service account can now read and write to the Sheet.

### 4. Configure the comp set

Edit `src/config.py`, modify the `COMP_SET` list. Each entry is
`("TICKER", "Human-readable name")`. The ticker must match the company's
SEC EDGAR ticker.

## Running

### Manual

```bash
.venv/bin/python -m src.monitor_8k    # daily, fast
.venv/bin/python -m src.monitor_10q   # weekly, slower (parses footnotes)
```

First run looks back 90 days (8-K) or 180 days (10-Q). Subsequent runs
only process filings newer than what's in `data/state.json`.

### Scheduled

Add to crontab (`crontab -e`):

```cron
# Daily at 7:30am ET (12:30 UTC) — catches overnight 8-K filings
30 12 * * * cd /path/to/ma_comp_tracker && .venv/bin/python -m src.monitor_8k >> data/8k.log 2>&1

# Weekly Monday at 8am ET — reconcile from new 10-Qs
0 13 * * 1 cd /path/to/ma_comp_tracker && .venv/bin/python -m src.monitor_10q >> data/10q.log 2>&1
```

Or run as a GitHub Action with the same schedule (see `tokentape/.github/workflows/daily.yml` for a reference workflow pattern; secrets become the env vars listed above).

## Sheet columns

The Sheet (or the configured tab) gets these columns written on first run:

| Column | Filled by | Notes |
|---|---|---|
| `acquirer` | both stages | Human name from `COMP_SET` |
| `acquirer_ticker` | both stages | |
| `target` | both stages | LLM-extracted target company |
| `announced_date` | 8-K | |
| `closed_date` | 10-Q | |
| `headline_value_usd` | 8-K, refined by 10-Q | Announced total deal size |
| `cash_consideration_usd` | 10-Q | |
| `stock_consideration_usd` | 10-Q | |
| `contingent_usd` | 10-Q | Earnout fair value |
| `true_cash_to_capital_usd` | 10-Q | LLM-estimated net cash to target shareholders |
| `structure` | 8-K | `all-cash` / `stock-and-cash` / `all-stock` |
| `stage` | both | `announced` or `closed-reconciled` |
| `source_8k_url` | 8-K | Click-through to EDGAR filing index |
| `source_10q_url` | 10-Q | |
| `notes` | both | LLM-extracted prose summary |
| `last_updated` | both | ISO date |

## Cost

Per run cost is roughly:
- 8-K monitoring: ~$0.001-0.005 per ticker per run (only filings with
  acquisition-like 8-K items hit the LLM; most don't)
- 10-Q monitoring: ~$0.01-0.03 per filing parsed (footnote text is larger)

With 15 tickers in the comp set, expect under $0.50/month total at default
DeepSeek pricing.

## Limitations

- **LLM extraction is not a substitute for an analyst's eye.** Spot-check
  the rows it produces before using them in IC discussions. The `notes`
  column captures details the structured fields can miss.
- **Some 8-Ks announce acquisitions vaguely** (e.g. press release attached
  as Exhibit 99.1 with the real details). The text we feed the LLM is the
  primary document, not exhibits, so headline values for those will be
  blank until the 10-Q updates them.
- **The Business Combinations footnote isn't a standardized SEC section,**
  so location heuristics in `sec_client.locate_business_combinations_section`
  occasionally miss. When that happens, the LLM gets a fallback window
  around the financial statements and usually still finds the data.
- **Stock-consideration fair values are recorded at closing date,** not the
  announcement-date stock price. A deal announced at $5B might land in the
  10-Q at $4.2B if the acquirer's stock dropped between sign and close.

## Adding new comp set tickers

Edit `src/config.py`, add the ticker. On the next run, `state.json` will
have no entry for that ticker, so the monitor backfills 90 days of 8-Ks
(or 180 days of 10-Qs) automatically.

## Troubleshooting

- **"service_account.json does not exist":** the file path in
  `GOOGLE_SERVICE_ACCOUNT_JSON` is wrong, or you haven't downloaded the
  service-account key yet. See setup step 3.
- **"This service account does not have access to spreadsheet …":** you
  didn't share the Sheet with the service account email. See setup step 7.
- **Empty filing text from sec-api:** EDGAR occasionally rate-limits. The
  client retries 3 times with backoff; persistent empty responses usually
  indicate the document URL has changed format. Inspect the URL manually.
- **LLM returns garbage JSON:** if the model under `OPENROUTER_MODEL`
  doesn't reliably emit JSON, try `anthropic/claude-sonnet-4.5` or
  `openai/gpt-5-mini` as a higher-quality fallback.
