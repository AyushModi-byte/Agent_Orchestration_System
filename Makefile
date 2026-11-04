.PHONY: up down test live demo
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
