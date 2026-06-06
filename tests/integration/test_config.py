import os
import tempfile

from config import AppConfig, EnvSecretProvider

YAML_CONTENT = """
mode: live
adapters:
  market:
    - source: polygon
      symbols: ["AAPL", "MSFT"]
      timeframe: "1m"
    - source: alpaca
      symbols: ["SPY"]
      timeframe: "5m"
      enabled: false
    - source: alpaca
      name: stock
      feed: sip
      instruments: ["QQQ"]
  account:
    - source: alpaca
      paper: true
features:
  technical:
    - source: rsi
      period: 14
"""

DOTENV_CONTENT = """
# Comments are ignored
ALPACA_API_KEY=vendor_api_key
ALPACA_SECRET_KEY=env_secret_key
"""


def test_config_loading_and_merging():
    # Use temporary files for yaml and .env
    with (
        tempfile.NamedTemporaryFile(
            suffix=".yaml", mode="w", delete=False
        ) as yaml_file,
        tempfile.NamedTemporaryFile(suffix=".env", mode="w", delete=False) as env_file,
    ):
        yaml_file.write(YAML_CONTENT)
        yaml_file.close()

        env_file.write(DOTENV_CONTENT)
        env_file.close()

        try:
            # 2. Test AppConfig parsing
            app_config = AppConfig.load(yaml_file.name, env_file=env_file.name)

            assert app_config.settings.mode == "live"
            assert len(app_config.settings.adapters.market) == 3

            # Verify market source parsing details
            polygon = next(
                x for x in app_config.settings.adapters.market if x.source == "polygon"
            )
            assert polygon.name is None
            assert polygon.enabled is True
            assert polygon.params["symbols"] == ["AAPL", "MSFT"]
            assert polygon.params["timeframe"] == "1m"

            # 3. Test EnvSecretProvider mapping
            secrets = EnvSecretProvider(env_file=env_file.name)
            # test general secret retrieving
            assert secrets.get("ALPACA_API_KEY") == "vendor_api_key"

            # test vendor-only lookup: a plain alpaca source uses ALPACA_*
            account_secrets = secrets.for_source("account", "alpaca")
            assert account_secrets["api_key"] == "vendor_api_key"
            assert account_secrets["secret_key"] == "env_secret_key"

            # source-only lookup: market/alpaca/stock falls back to the vendor-level
            # ALPACA_API_KEY because named-prefix env vars are no longer used
            stock_secrets = secrets.for_source("market", "alpaca", "stock")
            assert stock_secrets["api_key"] == "vendor_api_key"

            # 4. Test source params merging (YAML + Secrets)
            merged = app_config.source_params("account", "alpaca")
            assert merged["paper"] is True
            assert merged["api_key"] == "vendor_api_key"
            assert merged["secret_key"] == "env_secret_key"

            # 5. Test enabled sources (respects enabled flag)
            enabled_markets = app_config.enabled_sources("market")
            assert len(enabled_markets) == 2
            assert enabled_markets[0].source == "polygon"
            assert enabled_markets[0].name is None
            assert enabled_markets[0].params["symbols"] == ["AAPL", "MSFT"]
            # market/alpaca/stock falls back to the vendor-level api_key
            stock_cfg = next(
                c for c in enabled_markets if c.source == "alpaca" and c.name == "stock"
            )
            assert stock_cfg.params["api_key"] == "vendor_api_key"
            assert stock_cfg.params["feed"] == "sip"
            assert stock_cfg.params["instruments"] == ["QQQ"]

            # `feed` and `instruments` stay in the generic params dict until
            # market runtime config has a stronger shape.
            stock_settings = next(
                s
                for s in app_config.settings.adapters.market
                if s.source == "alpaca" and s.name == "stock"
            )
            assert stock_settings.params["feed"] == "sip"
            assert stock_settings.params["instruments"] == ["QQQ"]

            enabled_accounts = app_config.enabled_sources("account")
            assert len(enabled_accounts) == 1
            assert enabled_accounts[0].source == "alpaca"
            assert enabled_accounts[0].name is None
            assert enabled_accounts[0].params["api_key"] == "vendor_api_key"

            # 6. Test enabled features
            enabled_features = app_config.enabled_features()
            assert len(enabled_features) == 1
            assert enabled_features[0].source == "rsi"
            assert enabled_features[0].name is None
            assert enabled_features[0].params["period"] == 14

        finally:
            os.unlink(yaml_file.name)
            os.unlink(env_file.name)


def test_env_secret_provider_ignores_name_for_canonical_lookup() -> None:
    """Named-prefix env vars (<SOURCE>_<NAME>_*) must NOT override vendor-level (<SOURCE>_*).

    Both market/alpaca/stock and account/alpaca should resolve to the same
    ALPACA_API_KEY when only the vendor-level var is set.
    """
    dotenv_content = """
ALPACA_API_KEY=vendor_api_key
ALPACA_STOCK_API_KEY=old_named_prefix_api_key
"""
    with tempfile.NamedTemporaryFile(suffix=".env", mode="w", delete=False) as env_file:
        env_file.write(dotenv_content)
        env_file.close()

        try:
            secrets = EnvSecretProvider(env_file=env_file.name)
            stock_secrets = secrets.for_source("market", "alpaca", "stock")

            assert stock_secrets["api_key"] == "vendor_api_key"
        finally:
            os.unlink(env_file.name)


# ---------------------------------------------------------------------------
# infra.bus — settings that flow into RedisStreamBus (or whichever bus impl)
# ---------------------------------------------------------------------------
BUS_YAML = """
mode: live
infra:
  bus:
    url: redis://localhost:6379/0
    stream: trader:events
    maxlen: 50000
"""

BUS_YAML_DEFAULTS = """
mode: live
infra:
  bus: {}
"""


def _load(yaml_text: str) -> AppConfig:
    with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
        f.write(yaml_text)
        f.flush()
        path = f.name
    try:
        return AppConfig.load(path, env_file="/dev/null")
    finally:
        os.unlink(path)


def test_infra_bus_section_parses_explicit_fields() -> None:
    """An `infra.bus` block in the yaml is parsed into typed fields."""
    cfg = _load(BUS_YAML)
    bus = cfg.settings.infra.bus
    assert bus.url == "redis://localhost:6379/0"
    assert bus.stream == "trader:events"
    assert bus.maxlen == 50000


def test_infra_bus_section_uses_sensible_defaults() -> None:
    """An empty `infra.bus` block still produces a usable config (no url set)."""
    cfg = _load(BUS_YAML_DEFAULTS)
    bus = cfg.settings.infra.bus
    assert bus.url is None
    assert bus.stream == "trader:events"
    assert bus.maxlen == 100_000


def test_settings_load_when_infra_section_absent() -> None:
    """Configs that pre-date the bus section keep loading with default bus settings."""
    cfg = _load(YAML_CONTENT)
    assert cfg.settings.infra.bus.url is None
    assert cfg.settings.infra.bus.stream == "trader:events"
