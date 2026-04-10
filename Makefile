.PHONY: dev lint format test e2e audit docs

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

docs:
	mkdir -p docs
	cd docs && uv run python -m pydoc -w \
	    skyops \
	    skyops.api \
	    skyops.config \
	    skyops.lock \
	    skyops.main \
	    skyops.ssh_config \
	    skyops.ui \
	    skyops.userdata \
	    skyops.version_check

audit:
	uv run pip-audit
