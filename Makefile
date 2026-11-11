COMPOSE ?= docker compose
EXEC_API = $(COMPOSE) exec -T api

.PHONY: up down logs ps seed test demo reset shell build llm

up: ## Build and start everything, wait until healthy
	@test -f .env || cp .env.example .env
	$(COMPOSE) up --build -d --wait
	@$(COMPOSE) ps

build:
	$(COMPOSE) build

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=100

ps:
	$(COMPOSE) ps

seed: ## Generate synthetic data, build KB, ingest, load KG
	$(EXEC_API) python -m scripts.seed

test: ## pytest inside the api container
	$(EXEC_API) env LLM_PROVIDER=none python -m pytest -q tests

demo: ## Run S1-S7 in modes A and B, write docs/ab_results.md
	$(EXEC_API) python -m scripts.run_demo

reset: ## Destroy containers AND volumes (db, kg, caches), then restart
	$(COMPOSE) down -v
	$(MAKE) up

shell:
	$(COMPOSE) exec api bash

llm: ## Start with the optional Ollama service (profile llm)
	$(COMPOSE) --profile llm up --build -d --wait

screenshots: ## Capture UI screenshots into docs/screenshots (needs playwright, see ui/requirements-screenshots.txt)
	python3 ui/screenshots.py --base http://localhost:$(or $(UI_PORT),8501) --out docs/screenshots
