.PHONY: up down test live demo demo-ui
up:
	docker compose up -d --wait
down:
	docker compose down
test:
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy
	uv run pytest -x -q -m "not live"
live:
	LIVE_LLM=1 uv run pytest -m live
demo:
	uv run python demo/run_demo.py

demo-ui:
	CONDUCTOR_FAKE_LLM=1 uv run uvicorn conductor.api.main:app --port 8000 & cd web && npm run dev
