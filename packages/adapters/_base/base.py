"""
adapters/_base/base.py — BaseAdapter: shared by EVERY source (market/news/account).

Holds ONLY what is independent of *what the data is*: lifecycle, rate limiting,
error wrapping. It does not know Quote / Position / NewsItem — that's the whole
reason all three domains can share it. Depends on contracts + transport.

Template-method split used throughout this file:
  - public methods (start/stop/health) own the COMMON flow;
  - `_`-prefixed hooks are the source-specific bits a concrete adapter fills in.

----------------------------------------------------------------------------
HOW SECRETS / PARAMS REACH AN ADAPTER  (read this before touching __init__)
----------------------------------------------------------------------------
An adapter NEVER reads os.environ. Credentials arrive as constructor kwargs,
already resolved. The full chain:

  root .env            ALPACA_API_KEY=...   ALPACA_API_SECRET=...  (gitignored)
  config/*.yaml        account.sources: [{name: alpaca, paper: true}]
        |
        v
  ta.config.EnvSecretProvider.for_source("account", "alpaca")
        |   convention: strip the "<SOURCENAME_UPPER>_" prefix, lowercase ->
        |   ALPACA_API_KEY -> "api_key",  ALPACA_API_SECRET -> "api_secret"
        v
  AppConfig.source_params(...)   merges yaml (non-secret) + secrets ->
        {"api_key": "...", "api_secret": "...", "paper": True}
        |
        v
  registry.build_sources("account", [SourceConfig(name="alpaca", params=THAT)])
        |   does:  AlpacaAccountAdapter(**params)   # the dict is SPLATTED to kwargs
        v
  AlpacaAccountAdapter.__init__(self, api_key, api_secret, paper=False, **params)

So `**params` here is "whatever keys config produced for this source". WHICH
keys a source needs is NOT decided here and NOT decided by config — it's decided
by the CONCRETE adapter's __init__ signature. That's deliberate: auth differs
per platform (key+secret / bare key / JWT / OAuth), so each adapter declares its
own required kwargs and validation happens for free at construction:
a missing/extra key raises TypeError at startup, not on the first API call.

BaseAdapter therefore stays AUTH-AGNOSTIC: it only catches the leftover `**params`
and does the platform-independent setup. It must never assume "there is an
api_key". The same way the `_normalize_*` hooks keep it agnostic about data
shape, `**params` keeps it agnostic about credential shape.
"""

from __future__ import annotations

from loguru import logger
from transport import RateLimiter
from contracts import SourceCapabilities, SourceMode, AssetClass
from contracts.errors import TraderError


class BaseAdapter:
    """Base class for shared lifecycle, error wrapping, and rate limiting."""

    name: str

    def __init__(self, name: str = "", rate_limit: int = 10, **params) -> None:
        """Platform-INDEPENDENT setup only.

        A concrete adapter overrides __init__ to declare the credentials ITS
        platform needs (see the secret-flow note in the module docstring), then
        calls super().__init__(**params) to pass the leftover non-credential
        params (endpoints, rate-limit overrides, ...) up here.

        This base impl does NOT inspect params for any specific credential key.
        It stashes them and builds the RateLimiter from common setup arguments.
        Do NOT add `api_key`/`token`/etc. to THIS signature — that would bake one
        platform's auth scheme into every source.
        """
        self.name = name
        self.connected = False
        self._started = False
        self.params = params
        self.rate_limiter = RateLimiter(rate_limit=rate_limit, period=1.0)
        self._capabilities = SourceCapabilities(
            mode=SourceMode.PUSH,
            supports_streaming=True,
            asset_classes=(AssetClass.EQUITY,),
        )

    @property
    def capabilities(self) -> SourceCapabilities:
        """MUST be provided by the concrete adapter — the service routes by this,
        never by name. Not domain-specific, so it lives at this level.
        """
        return self._capabilities

    @property
    def limiter(self) -> RateLimiter:
        """Shared token bucket; adapters await self.limiter.acquire() before each
        upstream call.
        """
        return self.rate_limiter

    # --- lifecycle: common flow here, source-specific connect in the hooks ---
    async def start(self) -> None:
        """Common: guard double-start, then call _connect(), mark healthy."""
        if self._started:
            logger.warning(f"[{self.name}] Already started.")
            return

        logger.info(f"[{self.name}] Starting adapter...")
        try:
            await self._connect()
            self._started = True
            self.connected = True
            logger.info(f"[{self.name}] Started and connected successfully.")
        except Exception as e:
            wrapped = self._wrap_error(e)
            logger.error(f"[{self.name}] Failed to start: {wrapped}")
            raise wrapped

    async def stop(self) -> None:
        """Common: call _disconnect(), release resources, mark stopped."""
        if not self._started:
            logger.warning(f"[{self.name}] Already stopped or not started.")
            return

        logger.info(f"[{self.name}] Stopping adapter...")
        try:
            await self._disconnect()
        except Exception as e:
            wrapped = self._wrap_error(e)
            logger.error(f"[{self.name}] Error during disconnect: {wrapped}")
        finally:
            self._started = False
            self.connected = False
            logger.info(f"[{self.name}] Stopped successfully.")

    async def health(self) -> bool:
        """Common: default liveness; a source may refine via _check_health()."""
        if not self._started:
            return False
        try:
            return await self._check_health()
        except Exception as e:
            logger.warning(f"[{self.name}] Health check failed: {e}")
            return False

    def _wrap_error(self, exc: Exception) -> Exception:
        """Common: translate a raw upstream exception into a typed
        contracts.errors.* so callers handle one error vocabulary.
        """
        if isinstance(exc, TraderError):
            return exc
        return TraderError(str(exc))

    # --- hooks every source fills (no default behavior) ---
    async def _connect(self) -> None:
        """Hook for concrete adapter to establish connection."""
        pass

    async def _disconnect(self) -> None:
        """Hook for concrete adapter to close connection/release resources."""
        pass

    async def _check_health(self) -> bool:
        """Hook for concrete adapter to refine liveness check."""
        return self.connected
