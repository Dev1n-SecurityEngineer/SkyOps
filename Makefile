.PHONY: dev lint format test e2e audit

dev:
	uv sync --all-groups

lint:
	uv run ruff format --check && uv run ruff check && uv run ty check src/

format:
	uv run ruff format .

test:
	uv run pytest

e2e:
	./tests/e2e/test_lifecycle.sh

audit:
	uv run pip-audit
