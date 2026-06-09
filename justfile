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
    uv run ruff check --extend-select I --fix packages/ apps/ tests/

# Run the smoke app (vertical slice).
# Note: config/smoke.yaml sets infra.bus.url, so this requires a Redis to be
# reachable — `just up` first. Comment out the url in config/smoke.yaml if
# you want to run the smoke offline (it will fall back to InProcessBus).
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

# Drive a one-shot Pi Agent against the live gateway (apps/live must be running)
# Flags: -p "<prompt>" (headless), -c (continue last session), -r (resume picker)
agent *args:
    cd apps/agent && pnpm start -- {{args}}

# Start local infrastructure (Redis) in the background; waits until healthy
up:
    docker compose -f deploy/compose.yaml up -d --wait
    @echo "Redis is ready on redis://localhost:6379/0"

# Stop local infrastructure
down:
    docker compose -f deploy/compose.yaml down
