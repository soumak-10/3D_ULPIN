# =============================================================================
#  3D ULPIN Generation and Vertical Property Mapping System
#
#  `make help` lists every target.
# =============================================================================

COMPOSE := docker compose -f infra/docker-compose.yml --env-file .env
API     := apps/api
WEB     := apps/web

.DEFAULT_GOAL := help
.PHONY: help setup dev up down logs ps build clean \
        db-shell db-reset db-seed \
        api api-test api-lint api-fmt \
        web web-build web-lint check

help: ## List targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	 | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

# --- Containers --------------------------------------------------------------

setup: ## Copy the env examples into place (never overwrites)
	@[ -f .env ]                || cp .env.example .env               && echo "  .env"
	@[ -f $(API)/.env ]         || cp $(API)/.env.example $(API)/.env && echo "  $(API)/.env"
	@[ -f $(WEB)/.env.local ]   || cp $(WEB)/.env.example $(WEB)/.env.local && echo "  $(WEB)/.env.local"
	@echo "Set SECRET_KEY in .env before starting:"
	@echo '  python -c "import secrets; print(secrets.token_urlsafe(64))"'

dev: ## Build and start the whole stack
	$(COMPOSE) up --build

up: ## Start the stack in the background
	$(COMPOSE) up -d

down: ## Stop the stack (data survives)
	$(COMPOSE) down

logs: ## Follow logs — `make logs s=api` for one service
	$(COMPOSE) logs -f $(s)

ps: ## Show service status
	$(COMPOSE) ps

build: ## Rebuild images without starting
	$(COMPOSE) build

clean: ## Stop and DELETE the database volume
	$(COMPOSE) down -v

# --- Database ----------------------------------------------------------------

db-shell: ## psql as the superuser
	$(COMPOSE) exec postgres psql -U postgres -d ulpin_db

db-reset: ## Drop the volume and re-run the schema from scratch
	$(COMPOSE) down -v
	$(COMPOSE) up -d postgres
	@echo "Bootstrapping — watch it with: make logs s=postgres"

db-seed: ## Re-run the seeds against a running database
	$(COMPOSE) exec postgres psql -U postgres -d ulpin_db \
	  -v ON_ERROR_STOP=1 -f /sql/seeds/01_seed.sql

# --- Backend -----------------------------------------------------------------

api: ## Run FastAPI on the host (needs a running database)
	cd $(API) && uvicorn app.main:app --reload --port 8000

api-test: ## pytest
	cd $(API) && pytest -q

api-lint: ## ruff + mypy
	cd $(API) && ruff check . && mypy app

api-fmt: ## ruff format
	cd $(API) && ruff format . && ruff check --fix .

# --- Frontend ----------------------------------------------------------------

web: ## Run Next.js on the host
	cd $(WEB) && npm run dev

web-build: ## Production build — type-checks and lints
	cd $(WEB) && npm run build

web-lint: ## eslint
	cd $(WEB) && npm run lint

check: api-lint api-test web-lint web-build ## Everything CI would run
