# RawFish — top-level Makefile.
#
# One-command spin-up for the full local stack (native Ollama on the
# host + dockerised Neo4j + dockerised RawFish backend/frontend).
#
# Quick start:
#   make help        # show this list
#   make up          # bring the local stack up (first run pulls models)
#   make down        # stop containers, keep data
#   make purge       # stop + drop Neo4j data volume
#   make status      # what's running where
#   make logs        # tail container logs
#   make pull-models # only pull/refresh Ollama models
#
# First-run setup:
#   make init        # copies .env.local.template -> .env.local
#   $EDITOR .env.local    # set NEO4J_PASSWORD + SECRET_KEY
#   make up
#
# All targets are .PHONY because they don't produce file outputs the
# user is meant to consume directly.

REPO_ROOT  := $(shell pwd)
SCRIPT     := bash process/local_stack.sh
ENV_LOCAL  := $(REPO_ROOT)/.env.local
ENV_TPL    := $(REPO_ROOT)/.env.local.template
ifneq ($(wildcard /opt/homebrew/bin/node),)
export PATH := /opt/homebrew/bin:$(PATH)
endif

.DEFAULT_GOAL := help

.PHONY: help
help:  ## Show this help.
	@printf "RawFish — local-stack Makefile targets\n\n"
	@awk 'BEGIN {FS = ":.*##"} \
	     /^[a-zA-Z0-9_-]+:.*##/ { \
	       printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2 \
	     }' $(MAKEFILE_LIST) | sort
	@printf "\n"
	@printf "Active env file: %s\n" "$(ENV_LOCAL)"
	@if [ ! -f "$(ENV_LOCAL)" ]; then \
	  printf "  \033[33m(not present yet -- run 'make init')\033[0m\n"; \
	fi

.PHONY: init
init:  ## Copy .env.local.template -> .env.local for first-run setup.
	@if [ -f "$(ENV_LOCAL)" ]; then \
	  echo "$(ENV_LOCAL) already exists; refusing to overwrite."; \
	  exit 1; \
	fi
	@cp "$(ENV_TPL)" "$(ENV_LOCAL)"
	@echo "Wrote $(ENV_LOCAL)."
	@echo "Edit it: set NEO4J_PASSWORD and SECRET_KEY (32+ chars)."
	@echo "Generate a SECRET_KEY with:"
	@echo "  python3 -c \"import secrets; print(secrets.token_hex(32))\""

.PHONY: up
up:  ## Bring the full local stack up (Ollama + Neo4j + RawFish).
	@$(SCRIPT) up

.PHONY: down
down:  ## Stop containers (data volume preserved).
	@$(SCRIPT) down

.PHONY: purge
purge:  ## Stop and drop the Neo4j data volume (starts from a clean graph next time).
	@$(SCRIPT) purge

.PHONY: restart
restart:  ## Down + up.
	@$(SCRIPT) restart

.PHONY: status
status:  ## Show docker compose ps + Ollama liveness.
	@$(SCRIPT) status

.PHONY: logs
logs:  ## Tail container logs (Ctrl-C to exit).
	@$(SCRIPT) logs

.PHONY: pull-models
pull-models:  ## Pull / refresh Ollama models declared in .env.local.
	@$(SCRIPT) pull

.PHONY: smoke
smoke:  ## Run the mock-mode wire-surface smoke walk (no Ollama / Neo4j needed).
	@cd backend && .venv/bin/python ../process/demo_smoke.py

.PHONY: smoke-local-report
smoke-local-report:  ## Run create->prepare->start->report smoke against local Ollama (skips if absent).
	@set -a; [ -f .env.local ] && . ./.env.local; set +a; \
	cd backend && .venv/bin/python ../process/local_report_smoke.py

.PHONY: smoke-local-report-required
smoke-local-report-required:  ## Require create->prepare->start->report against local Ollama.
	@set -a; [ -f .env.local ] && . ./.env.local; set +a; \
	RAWFISH_REQUIRE_LOCAL_REPORT_SMOKE=true; export RAWFISH_REQUIRE_LOCAL_REPORT_SMOKE; \
	cd backend && .venv/bin/python ../process/local_report_smoke.py

.PHONY: probe-llm
probe-llm:  ## Real JSON-mode round-trip probe (4 gates; mimics RawFish's exact call pattern).
	@set -a; [ -f .env.local ] && . ./.env.local; set +a; \
	rc=0; python3 process/probe_llm_json_mode.py || rc=$$?; \
	case "$$rc" in \
	  0) exit 0 ;; \
	  2) echo "JSON-mode probe warning only: hard checks passed; language guard remains active."; exit 0 ;; \
	  *) exit "$$rc" ;; \
	esac

.PHONY: smoke-live
smoke-live:  ## Hit the running stack's /health and generic creator endpoints.
	@printf "Backend /health: "
	@curl -fsS --max-time 3 http://localhost:5001/health || \
	  (echo "DOWN" && exit 1)
	@echo ""
	@printf "Creator personas count: "
	@curl -fsS --max-time 3 http://localhost:5001/api/sim/use-cases/creator/personas | \
	  python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d['data']['personas']))"
	@printf "Frontend / : "
	@curl -fsS --max-time 3 -o /dev/null -w "%{http_code}\n" http://localhost:3000/

.PHONY: demo-check
demo-check: smoke-live probe-llm smoke-local-report-required  ## Demo rehearsal against the running stack + required local report/chat smoke.

.PHONY: hygiene-gates
hygiene-gates:  ## Run lightweight hygiene gates + git diff --check.
	@git diff --check
	@bash process/run_hygiene_gates.sh

.PHONY: closure-gates
closure-gates:  ## Run closure-contract drift checks; any drift fails.
	@python3 -B process/check_rawfish_closure_contract.py

.PHONY: gates
gates: hygiene-gates closure-gates  ## Run hygiene + closure-contract gates.

.PHONY: test-backend
test-backend:  ## Backend pytest (full suite, all skips disclosed).
	@cd backend && .venv/bin/python -m pytest tests/ -q -ra

.PHONY: test-frontend
test-frontend:  ## Frontend vitest (requires Node 20.19+).
	@cd frontend && npm run test:run

.PHONY: build-frontend
build-frontend:  ## Production-style frontend build.
	@cd frontend && npm run build

.PHONY: test-browser
test-browser:  ## Playwright cockpit smoke across desktop and mobile.
	@cd frontend && npm run test:e2e

.PHONY: ci
ci: gates test-backend test-frontend build-frontend test-browser smoke probe-llm smoke-local-report-required  ## Gates, tests, build, browser smoke, provider probe, required report smoke.
	@echo ""
	@echo "✓ All CI gates green."

.PHONY: ollama-list
ollama-list:  ## Show models already pulled on the host Ollama.
	@ollama list

.PHONY: ollama-stop
ollama-stop:  ## Stop the native Ollama serve process.
	@pkill -f "ollama serve" && echo "Ollama serve stopped." || echo "No ollama serve to stop."

.PHONY: neo4j-shell
neo4j-shell:  ## Open cypher-shell against the running Neo4j container.
	@docker exec -it rawfish-local-neo4j \
	  cypher-shell -u neo4j -p "$$NEO4J_PASSWORD"

.PHONY: env-check
env-check:  ## Verify .env.local + required tooling presence.
	@[ -f "$(ENV_LOCAL)" ] || (echo "✗ $(ENV_LOCAL) missing -- run 'make init'" && exit 1)
	@grep -q "^NEO4J_PASSWORD=CHANGE-ME" "$(ENV_LOCAL)" && \
	  (echo "✗ NEO4J_PASSWORD still the template placeholder in .env.local" && exit 1) || true
	@grep -q "^SECRET_KEY=CHANGE-ME" "$(ENV_LOCAL)" && \
	  (echo "✗ SECRET_KEY still the template placeholder in .env.local" && exit 1) || true
	@command -v docker >/dev/null 2>&1 || (echo "✗ docker missing" && exit 1)
	@command -v ollama >/dev/null 2>&1 || (echo "✗ ollama missing (install from https://ollama.com/download)" && exit 1)
	@echo "✓ env-check passed."
