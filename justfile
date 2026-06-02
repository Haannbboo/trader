# Trader Justfile

# Display available commands
default:
    @just --list

# Setup virtual environment and install project with all extras
setup:
    uv venv
    uv pip install -e ".[dev,ibkr,polygon,alpaca,databento,sentiment]"

# Run tests
test:
    uv run pytest tests/

# Run the conformance tests specifically
test-conformance:
    uv run pytest tests/conformance/

# Run tests and show stdout
test-v:
    uv run pytest -s -vv tests/

# Format code using black or ruff
format:
    uv run black packages/ apps/ tests/
    uv run ruff check --fix packages/ apps/ tests/

# Run the smoke app (vertical slice)
smoke:
    uv run python apps/smoke/main.py

# Run live trading (dry/mock mode by default)
live:
    uv run python apps/live/main.py

# Run backtest
backtest:
    uv run python apps/backtest/main.py

# Run CLI
cli *args:
    uv run python apps/cli/main.py {{args}}

# Regenerate contracts/*.schema.json from Pydantic models in packages/contracts
gen-contracts:
    uv run python scripts/generate_contracts.py

# CI: fail if contracts/*.schema.json would change (catches Pydantic drift)
gen-contracts-check:
    uv run python scripts/generate_contracts.py --check
