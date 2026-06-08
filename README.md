# ma_comp_tracker

Monitors SEC 8-K and 10-Q filings from a configurable comp set of public
acquirers, extracts acquisition details with a cheap LLM (DeepSeek V4-Flash
via OpenRouter by default), and writes results to a CSV.

## Team quickstart

```bash
git clone https://github.com/adityanaidu16/ma_comp_tracker.git
cd ma_comp_tracker
make setup            # creates venv, installs deps, copies .env template
# Edit .env: fill in SEC_API_KEY, OPENROUTER_API_KEY, SEC_USER_AGENT
make run              # 8-K + 10-Q monitors + summary, all in one command
make summary          # show current tracker contents anytime
```

Then open `data/acquisitions.csv` (or paste it into your shared Sheet).

All common operations are wired up in the Makefile. Run `make help` for
the full list. The most used ones:

| Command | What it does |
|---|---|
| `make run` | Full daily run: 8-K monitor, 10-Q monitor, prints summary |
| `make summary` | Read-only view of the current CSV, most recent deals first |
| `make inspect TICKER=CSCO TERM=Splunk` | Diagnose why a specific deal is missing |
| `make reset` | Wipe `state.json` + CSV, preserve `.env` |

## Two-stage workflow

- **Stage 1 — 8-K monitoring (daily):** detects acquisitions when they're
  announced. Captures headline value, structure (cash vs stock), target name,
  and dates.
- **Stage 2 — 10-Q / 10-K reconciliation (weekly):** parses the Business
  Combinations / Acquisitions footnote and Subsequent Events section in
  subsequent quarterly/annual filings. Updates existing rows with the real
  purchase-price allocation: cash consideration, stock fair value, contingent
  consideration (earn-outs), and the cap-table total.

Re-running is idempotent — `data/state.json` tracks the last processed
filing per ticker per form type. The CSV is overwritten each run with the
full current dataset (not just new rows), so a fresh `make summary` always
reflects the latest state.

## Setup

### 1. Install

```bash
make setup
```

Or manually:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

### 2. API keys

Fill in `.env`:

- `SEC_API_KEY`: get at https://sec-api.io/profile
- `OPENROUTER_API_KEY`: get at https://openrouter.ai/keys
- `SEC_USER_AGENT`: your contact email. Required by SEC; each user should
  set their own (SEC throttles by UA).
- `OPENROUTER_MODEL`: leave as `deepseek/deepseek-chat` (cheapest), or
  override with e.g. `anthropic/claude-sonnet-4.5` for higher accuracy.
- `MAX_ACQUISITION_AGE_DAYS`: how recent an acquisition must be to land
  in the CSV. Default 180 (~ two quarters).
- `MAX_WORKERS`: number of parallel ticker workers. Default 8.

### 3. Configure the comp set

Edit `src/config.py`, modify `COMP_SET`. Each entry is
`("TICKER", "Human-readable name")`. The ticker must match the company's
SEC EDGAR ticker.

## Running

### Manual

```bash
make run               # both monitors + summary
make run-8k            # 8-K only
make run-10q           # 10-Q / 10-K only
```

First run looks back `MAX_ACQUISITION_AGE_DAYS + 14` days for 8-Ks and
`MAX_ACQUISITION_AGE_DAYS + 90` days for 10-Q/10-K. Subsequent runs only
process filings newer than what's in `data/state.json`.

### Scheduled

Add to crontab (`crontab -e`):

```cron
# Daily at 7:30am ET (12:30 UTC) — catches overnight 8-K filings
30 12 * * * cd /path/to/ma_comp_tracker && make run-8k >> data/8k.log 2>&1

# Weekly Monday at 8am ET — reconcile from new 10-Qs
0 13 * * 1 cd /path/to/ma_comp_tracker && make run-10q >> data/10q.log 2>&1
```

## CSV columns

| Column | Filled by | Notes |
|---|---|---|
| `Company` | both stages | LLM-extracted target name |
| `Acquirer` | both stages | Human name from `COMP_SET` |
| `Date` | both stages | "Mon YYYY" (e.g. "Feb 2026") |
| `Motivation` | blank | Manual fill |
| `$ to cap table` | both | Best estimate of total value to cap-table holders |
| `Revenue ($)` | blank | Not in SEC filings |
| `Engineers` | blank | Not in SEC filings |
| `$ / Engineer` | blank | Sheet-side formula |
| `Rev. Multiple` | blank | Sheet-side formula |
| `Notes` | both | LLM summary + structure / component breakdown |
| `Source` | both | Filing index URL (8-K initially, replaced by 10-Q on reconciliation) |

## Limitations

- **LLM extraction is not a substitute for an analyst's eye.** Spot-check
  rows before using them in IC discussions. The `Notes` column captures
  details the structured fields miss.
- **Some 8-Ks announce acquisitions vaguely** (real deal terms in an
  Exhibit 99.1 press release). Values for those may stay blank until the
  10-Q reconciliation pass updates them.
- **The Business Combinations footnote isn't a standardized SEC section,**
  so the locator heuristic occasionally misses or picks a related section
  (intangibles, goodwill). Use `make inspect TICKER=XXX TERM=YYY` to
  diagnose.
- **Stock-consideration fair values are recorded at closing date,** not the
  announcement-date stock price. A deal announced at $5B might land in the
  10-Q at $4.2B if the acquirer's stock dropped between sign and close.

## Adding new comp set tickers

Edit `COMP_SET` in `src/config.py`. On the next run, `state.json` will
have no entry for that ticker, so the monitor backfills automatically.

## Troubleshooting

- **Empty filing text from sec-api:** EDGAR occasionally rate-limits. The
  client retries 3 times with backoff. Persistent empty responses usually
  mean a URL format change — inspect manually.
- **LLM returns garbage JSON:** if the model under `OPENROUTER_MODEL`
  doesn't reliably emit JSON, try `anthropic/claude-sonnet-4.5` or
  `openai/gpt-5-mini` as a higher-accuracy fallback.
- **Missing an acquisition you know happened:** use the diagnostic script:
  ```bash
  make inspect TICKER=SNOW TERM=Natoma   # show all "Natoma" mentions in SNOW's latest 10-Q
  make inspect-8k TICKER=SNOW            # walk recent 8-Ks and the LLM's verdict on each
  ```
