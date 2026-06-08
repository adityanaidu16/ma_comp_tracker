# M&A comp tracker — common operations for the team.
# Run `make` or `make help` to see the available commands.

PYTHON := .venv/bin/python

.PHONY: help setup run run-8k run-10q inspect inspect-8k verify summary reset clean

help: ## Show this help
	@echo "M&A comp tracker — make commands:"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*## ' Makefile | awk 'BEGIN{FS=":.*## "} {printf "  make %-12s  %s\n", $$1, $$2}'
	@echo ""
	@echo "First time setup: make setup, then edit .env to add your API keys."

setup: ## Create venv, install deps, copy .env template
	@python3 -m venv .venv
	@.venv/bin/pip install -q -r requirements.txt
	@test -f .env || cp .env.example .env
	@echo "Setup complete. Edit .env to add SEC_API_KEY, OPENROUTER_API_KEY,"
	@echo "and SEC_USER_AGENT (your contact email). Then run: make run"

run: run-8k run-10q summary ## Full run: 8-K, then 10-Q, then print summary

run-8k: ## Run the 8-K monitor (detects new acquisitions)
	$(PYTHON) -m src.monitor_8k

run-10q: ## Run the 10-Q monitor (reconciles purchase-price details)
	$(PYTHON) -m src.monitor_10q

inspect: ## Inspect a 10-Q. Usage: make inspect TICKER=CSCO [TERM=Natoma]
	@if [ -z "$(TICKER)" ]; then echo "Usage: make inspect TICKER=CSCO [TERM=SearchString]"; exit 2; fi
	$(PYTHON) -m scripts.inspect_10q $(TICKER) $(TERM)

inspect-8k: ## Inspect a ticker's recent 8-Ks. Usage: make inspect-8k TICKER=SNOW
	@if [ -z "$(TICKER)" ]; then echo "Usage: make inspect-8k TICKER=SNOW"; exit 2; fi
	$(PYTHON) -m scripts.inspect_8k $(TICKER)

verify: ## Verify the pipeline end-to-end (calls OpenRouter once)
	$(PYTHON) -m scripts.verify_pipeline

summary: ## Print a readable summary of the current CSV
	@$(PYTHON) -m scripts.summary

reset: ## Clear state.json and acquisitions.csv (preserves .env)
	@rm -f data/state.json data/acquisitions.csv
	@echo "State and CSV cleared. Next run will re-extract from scratch."

clean: ## Remove venv, data, all generated files (preserves .env)
	@rm -rf .venv data/state.json data/acquisitions.csv scripts/inspect_output.txt
	@echo "Cleaned. Run 'make setup' to start fresh."

.DEFAULT_GOAL := help
