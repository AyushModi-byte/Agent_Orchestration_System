# `uv run` when uv is installed, otherwise the active Python (a venv you created yourself).
RUN := $(shell command -v uv >/dev/null 2>&1 && echo "uv run")
PY := $(if $(RUN),$(RUN) python,python)
ARGS ?= --fake

.PHONY: up down test live demo demo-ui seed docs
up:
	docker compose up -d --build --wait
down:
	docker compose down
test:
	$(RUN) ruff format --check .
	$(RUN) ruff check .
	$(RUN) mypy
	$(RUN) pytest -x -q -m "not live"
	cd web && npm run build && npm test
live:
	LIVE_LLM=1 $(RUN) pytest -m live
# make demo                      scripted, zero quota
# LIVE_LLM=1 make demo ARGS=--live   one real recording (spends free-tier quota)
demo:
	$(PY) -m demo.run_demo $(ARGS)
seed:
	$(PY) -m demo.seed_target_repo --repo $(REPO)
docs:
	$(PY) scripts/gen_api_docs.py
demo-ui:
	CONDUCTOR_FAKE_LLM=1 $(RUN) uvicorn conductor.api.main:app --port 8000 & cd web && npm run dev
