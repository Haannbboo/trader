# Trader (Agentic Trading Platform)

Trader is a highly modular, decoupled, and extensible agentic trading system built for real-time market data ingestion, dynamic feature extraction, risk guardrails, and LLM-driven trading loops.

## Repository Structure

```
├── pyproject.toml              # Project start (single package) and extras configurations
├── justfile · uv.lock · .env.example
├── docs/adr/                   # Architecture Decision Records (frozen, ingestion, protocols, registry)
├── config/{live,backtest}.yaml # Dynamic config for live/paper vs backtest runtimes
│
├── packages/                   # Core business logic packages
│   ├── contracts/           # Protocols/Ports and immutable event schemas (Pydantic)
│   ├── plugins/             # Central registry facilitating dynamic package registration
│   ├── transport/           # WS reconnection, rate-limiting, and schedulers
│   ├── bus/                 # InProcess and Redis Stream message buses
│   ├── config/              # Dynamic YAML configs loader
│   ├── observability/       # Telemetry, logging formats, and Prometheus gauges
│   ├── persistence/         # JSON line writer for transaction logs
│   │
│   ├── adapters/               # Broker and Feed adaptors (Market, News, Account)
│   │   ├── _base/              # Base adapter class
│   │   ├── market/             # ibkr, polygon, alpaca, databento
│   │   ├── news/               # benzinga, newsapi, rss
│   │   └── account/            # ibkr, alpaca
│   │
│   ├── market/              # Service aggregating market data and handling reuse
│   ├── news/                # Service aggregating news alerts
│   ├── account/             # Service managing client portfolio execution
│   ├── feature/             # Feature execution runtime and dependency DAG scheduler
│   │
│   ├── features/               # Technical and sentiment indicators (ML dependencies isolated here)
│   │   ├── technical/          # rsi, rolling_vol, returns
│   │   ├── crosssectional/     # rank, zscore
│   │   └── sentiment/          # ML-based text sentiment classification
│   │
│   ├── guardrail/           # Pre-flight position sizing and emergency kill switch checks
│   ├── tools/               # Agent tool bindings wrapping domain services
│   ├── agent/               # ReAct reasoning loop & agent execution harness
│   └── strategies/          # System prompts and strategy parameters
│
├── apps/                       # Lightweight application wiring runners
│   ├── live/main.py            # Main entry point for live/paper trading
│   ├── backtest/main.py        # Backtest simulator feeding historical data
│   ├── smoke/main.py           # Thin vertical slice verification
│   └── cli/main.py             # CLI monitoring and inspection tools
│
└── tests/                      # Conformance, integration, and End-to-End suites
```

## Setup & Running

### Requirements
- Python >= 3.11
- `uv` package manager

### 1. Setup Virtual Environment
Run the following command to create a virtual environment and install all packages in editable mode:
```bash
uv venv
uv pip install -e ".[dev,ibkr,polygon,alpaca,databento]"
```

### 2. Run Verification Smoke Test
To test the entire pipeline (mock ingestion -> market service -> features -> agent loop -> order execution) in a thin vertical slice:
```bash
uv run python apps/smoke/main.py
```

### 3. Run Unit and Integration Tests
```bash
uv run pytest
```

### 4. Run Backtester
```bash
uv run python apps/backtest/main.py
```

### 5. Run Live/Paper Trading
Configure variables in `config/live.yaml` and `.env`, then run:
```bash
uv run python apps/live/main.py
```

### 6. Run CLI Tool
```bash
uv run python apps/cli/main.py --help
uv run python apps/cli/main.py feature --name rsi
```
