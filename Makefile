.PHONY: test-unit test-db test test-final

test-unit:
	uv run pytest -m unit || test $$? -eq 5

test-db:
	uv run pytest -m "api or integration or mcp or migration"

test:
	uv run pytest

test-final:
	TESTCONTAINERS_REUSE_ENABLE=false uv run pytest
