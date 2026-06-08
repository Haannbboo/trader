"""Alpaca market-data adapters (stock + option).

Alpaca splits its market-data REST and websocket surfaces by asset class, so
this package exposes TWO adapters registered under distinct names:

  - ``AlpacaStockMarketAdapter``  (``market`` / ``alpaca`` / ``stock``)
      REST:   ``StockHistoricalDataClient.get_stock_bars``
      Stream: ``StockDataStream``  -> ``subscribe_trades`` / ``_quotes`` / ``_bars``

  - ``AlpacaOptionMarketAdapter`` (``market`` / ``alpaca`` / ``option``)
      REST:   ``OptionHistoricalDataClient.get_option_bars``
      Stream: ``OptionDataStream`` -> same three ``subscribe_*`` channels

Both extend :class:`adapters._base.market.BaseMarketAdapter`, so they share the
market flow (rate-limit -> fetch/recv -> normalize -> wrap in Event). The
only source-specific bits filled in here are:

  * ``_channel_map``            — translate our channels to native tokens
  * ``_build_*_request``        — build the right alpaca-py request object
  * ``_normalize_*``            — alpaca-py payloads -> schema DTOs
  * ``_subscribe``              — websocket plumbing (handler -> asyncio queue)
  * ``_wrap``                   — attach source name + ts_event to the envelope

``get_quote`` is implemented in both adapters to fetch the latest (snapshot) quote
for a given instrument, routing it to the correct underlying asset-class REST API.

``alpaca-py`` is imported LAZILY (inside the build/connect/subscribe hooks) so
that registry discovery still works when the project is installed without the
``alpaca`` extra. Tests inject narrow fakes (``historical_client``,
``data_stream``) to exercise the read path without alpaca-py.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import (
    Any,
    AsyncIterator,
    Callable,
    Coroutine,
    Optional,
    Protocol,
    runtime_checkable,
)

from adapters._base import BaseMarketAdapter
from contracts import (
    AssetClass,
    Bar,
    Event,
    EventType,
    Instrument,
    MarketChannel,
    Quote,
    SourceCapabilities,
    SourceMode,
    Timeframe,
    instrument_to_occ,
)
from plugins import register


# ---------------------------------------------------------------------------
# Lazy-imported alpaca-py surfaces, typed as Protocols so tests can supply
# narrow fakes without depending on the SDK at test time.
# ---------------------------------------------------------------------------
@runtime_checkable
class _HistoricalClientLike(Protocol):
    def get_stock_bars(self, request: Any) -> Any: ...
    def get_option_bars(self, request: Any) -> Any: ...
    def get_stock_latest_quote(self, request: Any) -> Any: ...
    def get_option_latest_quote(self, request: Any) -> Any: ...


@runtime_checkable
class _DataStreamLike(Protocol):
    def subscribe_trades(
        self, handler: Callable[..., Coroutine[Any, Any, None]], *symbols: str
    ) -> Any: ...

    def subscribe_quotes(
        self, handler: Callable[..., Coroutine[Any, Any, None]], *symbols: str
    ) -> Any: ...

    def subscribe_bars(
        self, handler: Callable[..., Coroutine[Any, Any, None]], *symbols: str
    ) -> Any: ...

    def run(self) -> Any: ...
    def stop(self) -> None: ...


# Map our Timeframe -> alpaca-py TimeFrame(amount, unit). Encapsulated so the
# adapters don't import the SDK at module load. Filled lazily by the
# `_alpaca_timeframe` helper on first use.
_TIMEFRAME_MAP: dict[Timeframe, tuple[int, str]] = {
    Timeframe.S1: (1, "Second"),
    Timeframe.M1: (1, "Minute"),
    Timeframe.M5: (5, "Minute"),
    Timeframe.M15: (15, "Minute"),
    Timeframe.H1: (1, "Hour"),
    Timeframe.D1: (1, "Day"),
}


def _alpaca_timeframe(timeframe: Timeframe) -> Any:
    """Translate a contracts.Timeframe to an alpaca-py TimeFrame.

    Lazy import keeps the module importable without the ``alpaca`` extra."""
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    amount, unit = _TIMEFRAME_MAP[timeframe]
    return TimeFrame(amount=amount, unit=getattr(TimeFrameUnit, unit))


def _as_dict(raw: Any) -> dict:
    """Normalize alpaca-py payloads at the boundary.

    ``raw_data=True`` returns plain dicts; the default path returns Pydantic
    models with ``.model_dump()`` (or older ``.dict()``). We accept both.
    """
    if isinstance(raw, dict):
        return raw
    if hasattr(raw, "model_dump"):
        return raw.model_dump()
    if hasattr(raw, "dict"):
        return raw.dict()
    return vars(raw)


def _value(value: Any) -> Any:
    return getattr(value, "value", value)


def _first_present(raw: dict, *keys: str) -> Any:
    """Return the first alias value that is present, preserving falsy values.

    Alpaca raw-data dicts use compact keys like ``o``/``v``/``n`` while
    model-dumped SDK objects use names like ``open``/``volume``/``trade_count``.
    Avoid ``a or b`` here: zero prices, volumes, and trade counts are valid.
    """
    for key in keys:
        value = raw.get(key)
        if value is not None:
            return value
    return None


def _decimal(value: Any) -> Decimal:
    if value is None:
        raise ValueError("Expected decimal-compatible value, got None")
    return Decimal(str(_value(value)))


def _optional_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    return _decimal(value)


def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    raise ValueError(f"Cannot parse timestamp from {value!r}")


# ---------------------------------------------------------------------------
# Base: shared channel map + subscribe wiring for both stock & option.
# Concrete classes only differ in which historical client they build and how
# they map instruments to native symbols.
# ---------------------------------------------------------------------------
class _AlpacaMarketAdapterBase(BaseMarketAdapter):
    """Common stock/option plumbing. Concrete subclasses pick the right
    alpaca-py client and define ``_native_symbol(instrument)``."""

    asset_class: AssetClass  # set by subclasses

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_secret: str | None = None,
        historical_client: _HistoricalClientLike | None = None,
        data_stream: _DataStreamLike | None = None,
        rate_limit: int = 200,
        **params: Any,
    ) -> None:
        # `base_url` and `data_url` are intentionally not accepted here. The
        # market data REST/websocket clients have their own URL config that
        # is NOT the trading API URL, so we drop those kwargs defensively
        # rather than forwarding a value that's wrong for this adapter.
        params.pop("base_url", None)
        params.pop("data_url", None)
        super().__init__(
            name=self.__class__.__name__,
            rate_limit=rate_limit,
            **params,
        )
        self.api_key = api_key
        self.api_secret = api_secret
        self.historical_client = historical_client
        self.data_stream = data_stream
        # Capabilities are constant per asset class; cache on the instance.
        self._capabilities = SourceCapabilities(
            mode=SourceMode.PUSH,
            supports_streaming=True,
            asset_classes=(self.asset_class,),
            historical=True,
        )

    # --- SourcePort lifecycle ---
    async def _connect(self) -> None:
        if self.historical_client is None:
            self.historical_client = self._build_historical_client()
        if self.data_stream is None:
            self.data_stream = self._build_data_stream()

    async def _disconnect(self) -> None:
        if self.data_stream is not None:
            stream_loop = getattr(self.data_stream, "_loop", None)
            if stream_loop is not None and stream_loop.is_running():
                try:
                    self.data_stream.stop()
                except Exception:
                    pass

    # --- MarketSourcePort surface ---
    async def get_quote(
        self,
        instrument: Instrument,
        feed: str | None = None,
    ) -> Quote:
        """Fetch the latest quote for the given instrument.

        NOTE: This method only fetches the latest quote (snapshot) from Alpaca,
        and does not retrieve historical quote archives.
        """
        self._assert_supported(instrument)
        await self.limiter.acquire()
        client = self._require_historical_client()
        symbol = self._native_symbol(instrument)
        request = self._build_quote_request(symbol, feed)
        response = await asyncio.to_thread(self._fetch_quote, client, request)
        raw_quote = self._extract_quote_payload(response, symbol)
        return self._normalize_historical_quote_payload(raw_quote, instrument)

    async def get_bars(
        self,
        instrument: Instrument,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> list[Bar]:
        self._assert_supported(instrument)
        await self.limiter.acquire()
        client = self._require_historical_client()
        symbol = self._native_symbol(instrument)
        request = self._build_bars_request(symbol, timeframe, start, end)
        response = await asyncio.to_thread(self._fetch_bars, client, request)
        return [
            self._normalize_historical_bar_payload(raw_bar, instrument, timeframe)
            for raw_bar in self._extract_bar_payloads(response, symbol)
        ]

    def subscribe(
        self,
        instruments: list[Instrument],
        channels: list[MarketChannel],
    ) -> AsyncIterator[Event]:
        for instrument in instruments:
            self._assert_supported(instrument)
        return self._subscribe(instruments, channels)

    # --- hook builders for subclasses ---
    def _build_historical_client(self) -> _HistoricalClientLike:
        raise NotImplementedError

    def _build_data_stream(self) -> _DataStreamLike:
        raise NotImplementedError

    def _build_bars_request(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> Any:
        """Build a StockBarsRequest/OptionBarsRequest; subclasses pick the
        request class so the same helper serves both adapters."""
        raise NotImplementedError

    def _fetch_bars(self, client: _HistoricalClientLike, request: Any) -> Any:
        """Dispatch to the correct client method for this asset class.

        Subclasses implement this hook so ``get_bars`` does not need a
        broad ``except AttributeError`` fallback that masks internal SDK bugs.
        """
        raise NotImplementedError

    def _build_quote_request(self, symbol: str, feed: str | None = None) -> Any:
        """Build a StockLatestQuoteRequest/OptionLatestQuoteRequest; subclasses
        pick the request class and validate the feed setting."""
        raise NotImplementedError

    def _fetch_quote(self, client: _HistoricalClientLike, request: Any) -> Any:
        """Dispatch to the correct client method to fetch latest quote."""
        raise NotImplementedError

    def _extract_quote_payload(self, response: Any, symbol: str) -> Any:
        """Extract the latest quote payload for the symbol."""
        if isinstance(response, dict):
            val = response.get(symbol) or response.get(symbol.upper())
            if val is not None:
                return val
        data = getattr(response, "data", None)
        if isinstance(data, dict):
            val = data.get(symbol) or data.get(symbol.upper())
            if val is not None:
                return val
        raise ValueError(f"No latest quote returned for {symbol}")

    def _normalize_historical_quote_payload(
        self,
        raw_quote: Any,
        instrument: Instrument,
    ) -> Quote:
        payload = _as_dict(raw_quote)
        return self._normalize_quote({**payload, "_instrument": instrument})

    def _extract_bar_payloads(self, response: Any, symbol: str) -> list[Any]:
        """alpaca returns ``{symbol: [bar, bar, ...]}`` for raw_data=True and
        a ``BarSet`` whose ``.data`` is the same shape otherwise."""
        if isinstance(response, dict):
            values = response.get(symbol) or response.get(symbol.upper()) or []
            return list(values) if values is not None else []
        data = getattr(response, "data", None)
        if isinstance(data, dict):
            values = data.get(symbol) or data.get(symbol.upper()) or []
            return list(values) if values is not None else []
        return list(response) if response is not None else []

    def _normalize_historical_bar_payload(
        self,
        raw_bar: Any,
        instrument: Instrument,
        timeframe: Timeframe,
    ) -> Bar:
        if isinstance(raw_bar, Bar):
            return raw_bar
        payload = _as_dict(raw_bar)
        return self._normalize_bar(
            {**payload, "_instrument": instrument, "timeframe": timeframe}
        )

    def _assert_supported(self, instrument: Instrument) -> None:
        if instrument.asset_class is not self.asset_class:
            raise ValueError(
                f"{self.__class__.__name__} only supports "
                f"{self.asset_class.value} instruments, got "
                f"{instrument.asset_class.value}"
            )

    # --- _channel_map hook ---
    def _channel_map(self, channels: list[MarketChannel]) -> list[str]:
        # Our channel tokens map 1:1 to alpaca-py's subscribe_* methods. The
        # bridge in _subscribe iterates this list and dispatches.
        return [channel.value for channel in channels]

    # --- streaming plumbing (shared by stock + option) ---
    def _subscribe(
        self,
        instruments: list[Instrument],
        channels: list[MarketChannel],
    ) -> AsyncIterator[Event]:
        return _AlpacaStreamIterator(
            adapter=self,
            instruments=instruments,
            channels=channels,
        ).run()

    # --- normalization hooks filled in by concrete classes ---
    def _normalize_quote(self, raw: dict) -> Quote:
        return self._normalize_quote_impl(raw)

    def _normalize_bar(self, raw: dict) -> Bar:
        return self._normalize_bar_impl(raw)

    def _wrap(self, payload: Any, event_type: EventType) -> Event:
        ts_event = getattr(payload, "ts_event", None) or datetime.now(timezone.utc)
        return Event(
            type=event_type,
            source=self.name,
            payload=payload,
            ts_event=ts_event,
        )

    # --- shared normalization (instrument is threaded through ``_instrument``
    # to avoid re-deriving the native symbol or re-resolving the asset class) ---
    def _normalize_quote_impl(self, raw: dict) -> Quote:
        instrument: Instrument = raw["_instrument"]
        return Quote(
            instrument=instrument,
            ts_event=_parse_timestamp(_first_present(raw, "t", "timestamp")),
            bid=_optional_decimal(_first_present(raw, "bp", "bid_price")),
            ask=_optional_decimal(_first_present(raw, "ap", "ask_price")),
            bid_size=_optional_decimal(_first_present(raw, "bs", "bid_size")),
            ask_size=_optional_decimal(_first_present(raw, "as", "ask_size")),
            last=_optional_decimal(_first_present(raw, "p", "price")),
            last_size=_optional_decimal(_first_present(raw, "s", "size")),
        )

    def _normalize_bar_impl(self, raw: dict) -> Bar:
        instrument: Instrument = raw["_instrument"]
        timeframe: Timeframe = raw.get("timeframe") or Timeframe.M1
        trade_count = _first_present(raw, "n", "trade_count")
        return Bar(
            instrument=instrument,
            timeframe=timeframe,
            ts_open=_parse_timestamp(_first_present(raw, "t", "timestamp")),
            open=_decimal(_first_present(raw, "o", "open")),
            high=_decimal(_first_present(raw, "h", "high")),
            low=_decimal(_first_present(raw, "l", "low")),
            close=_decimal(_first_present(raw, "c", "close")),
            volume=_decimal(_first_present(raw, "v", "volume")),
            vwap=_optional_decimal(_first_present(raw, "vw", "vwap")),
            trades=int(_value(trade_count)) if trade_count is not None else None,
        )

    # --- internals ---
    def _require_historical_client(self) -> _HistoricalClientLike:
        if self.historical_client is None:
            self.historical_client = self._build_historical_client()
        return self.historical_client

    def _require_data_stream(self) -> _DataStreamLike:
        if self.data_stream is None:
            self.data_stream = self._build_data_stream()
        return self.data_stream

    def _native_symbol(self, instrument: Instrument) -> str:
        return instrument.symbol


# ---------------------------------------------------------------------------
# Streaming iterator — bridges the alpaca-py websocket thread to an asyncio
# generator that yields normalized Events. Mirrors the trade-update bridge in
# the Alpaca account adapter: subscribe -> spawn stream.run() in a worker
# thread -> handler hands dicts to an asyncio.Queue -> generator consumes.
# ---------------------------------------------------------------------------
class _AlpacaStreamIterator:
    def __init__(
        self,
        *,
        adapter: _AlpacaMarketAdapterBase,
        instruments: list[Instrument],
        channels: list[MarketChannel],
    ) -> None:
        self.adapter = adapter
        self.instruments = instruments
        self.channels = channels

    async def run(self) -> AsyncIterator[Event]:
        stream = self.adapter._require_data_stream()
        queue: asyncio.Queue[Optional[Event]] = asyncio.Queue()
        loop = asyncio.get_running_loop()
        channel_set = {channel.value for channel in self.channels}
        symbols_by_channel: dict[str, list[str]] = {c: [] for c in channel_set}

        async def _handle(channel: str) -> Callable[[Any], Coroutine[Any, Any, None]]:
            async def handler(data: Any) -> None:
                payload_dict = _as_dict(data)
                native = str(payload_dict.get("S") or payload_dict.get("symbol") or "")
                instrument = self._instrument_for(native)
                if instrument is None:
                    return
                if channel == "bars":
                    bar = self.adapter._normalize_bar(
                        {
                            **payload_dict,
                            "_instrument": instrument,
                            "timeframe": self._timeframe_for_native(native),
                        }
                    )
                    event: Event = self.adapter._wrap(bar, EventType.BAR)
                else:
                    quote = self.adapter._normalize_quote(
                        {**payload_dict, "_instrument": instrument}
                    )
                    # Trades populate last/last_size; quotes populate bid/ask.
                    # Same event type either way — consumers discriminate by
                    # which fields are present.
                    event = self.adapter._wrap(quote, EventType.QUOTE)
                # alpaca-py's stream callback runs on a worker thread. Hop back
                # to the adapter's event loop before touching the asyncio queue.
                asyncio.run_coroutine_threadsafe(queue.put(event), loop)

            return handler

        # Register one handler per requested channel.
        registered: list[tuple[str, Callable[[Any], Coroutine[Any, Any, None]]]] = []
        for channel in self.channels:
            symbol_list = [self.adapter._native_symbol(i) for i in self.instruments]
            symbols_by_channel[channel.value] = symbol_list
            if channel is MarketChannel.TRADES:
                handler = await _handle("trades")
                stream.subscribe_trades(handler, *symbol_list)
                registered.append(("trades", handler))
            elif channel is MarketChannel.QUOTES:
                handler = await _handle("quotes")
                stream.subscribe_quotes(handler, *symbol_list)
                registered.append(("quotes", handler))
            elif channel is MarketChannel.BARS:
                handler = await _handle("bars")
                stream.subscribe_bars(handler, *symbol_list)
                registered.append(("bars", handler))

        task = asyncio.create_task(asyncio.to_thread(stream.run))

        try:
            while True:
                if task.done() and queue.empty():
                    if task.cancelled():
                        break
                    exc = task.exception()
                    if exc is not None:
                        raise exc
                    break
                event = await queue.get()
                if event is None:
                    break
                yield event
        finally:
            stop = getattr(stream, "stop", None)
            if callable(stop):
                stop()
            task.cancel()

    def _instrument_for(self, native_symbol: str) -> Optional[Instrument]:
        for instrument in self.instruments:
            if self.adapter._native_symbol(instrument) == native_symbol:
                return instrument
        return None

    def _timeframe_for_native(self, native_symbol: str) -> Timeframe:
        # The stream's bars are tick-level (1m on Alpaca free tier; finer with
        # a paid plan). We don't know the bar's timeframe from the message
        # alone, so callers must subscribe to a single-timeframe stream per
        # subscribe() call OR accept the default. We default to M1 since that
        # is the common case; richer timeframe metadata would be a separate
        # follow-up.
        return Timeframe.M1


# ---------------------------------------------------------------------------
# Stock adapter
# ---------------------------------------------------------------------------
@register("market", "alpaca", "stock")
class AlpacaStockMarketAdapter(_AlpacaMarketAdapterBase):
    asset_class = AssetClass.EQUITY

    # Allowed values for the constructor's `feed` kwarg. alpaca-py supports
    # more (OTC, CRN), but for this adapter we only route through the two
    # equity feeds most callers care about. Add more here when needed.
    _ALLOWED_FEEDS = ("sip", "iex", "delayed_sip")

    def __init__(
        self,
        *args: Any,
        feed: str = "sip",
        **kwargs: Any,
    ) -> None:
        normalized = feed.lower()
        if normalized not in self._ALLOWED_FEEDS:
            raise ValueError(f"feed must be one of {self._ALLOWED_FEEDS}, got {feed!r}")
        self.feed = normalized
        super().__init__(*args, **kwargs)

    def _build_historical_client(self) -> _HistoricalClientLike:
        from alpaca.data.historical.stock import StockHistoricalDataClient

        return StockHistoricalDataClient(  # type: ignore[return-value]
            api_key=self.api_key,
            secret_key=self.api_secret,
        )

    def _build_data_stream(self) -> _DataStreamLike:
        from alpaca.data.live.stock import StockDataStream

        if self.api_key is None or self.api_secret is None:
            raise ValueError("api_key and api_secret are required for Alpaca streaming")
        return StockDataStream(  # type: ignore[return-value]
            api_key=self.api_key,
            secret_key=self.api_secret,
        )

    def _build_bars_request(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> Any:
        from alpaca.data.enums import DataFeed
        from alpaca.data.requests import StockBarsRequest

        return StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=_alpaca_timeframe(timeframe),
            start=start,
            end=end,
            feed=getattr(DataFeed, self.feed.upper()),
        )

    def _fetch_bars(self, client: _HistoricalClientLike, request: Any) -> Any:
        return client.get_stock_bars(request)

    def _build_quote_request(self, symbol: str, feed: str | None = None) -> Any:
        from alpaca.data.enums import DataFeed
        from alpaca.data.requests import StockLatestQuoteRequest

        feed_val = feed or self.feed
        normalized = feed_val.lower()
        if normalized not in self._ALLOWED_FEEDS:
            raise ValueError(
                f"feed must be one of {self._ALLOWED_FEEDS}, got {feed_val!r}"
            )

        return StockLatestQuoteRequest(
            symbol_or_symbols=symbol,
            feed=getattr(DataFeed, normalized.upper()),
        )

    def _fetch_quote(self, client: _HistoricalClientLike, request: Any) -> Any:
        return client.get_stock_latest_quote(request)


# ---------------------------------------------------------------------------
# Option adapter
# ---------------------------------------------------------------------------
@register("market", "alpaca", "option")
class AlpacaOptionMarketAdapter(_AlpacaMarketAdapterBase):
    asset_class = AssetClass.OPTION

    _ALLOWED_FEEDS = ("opra", "indicative")

    def __init__(
        self,
        *args: Any,
        feed: str = "opra",
        **kwargs: Any,
    ) -> None:
        normalized = feed.lower()
        if normalized not in self._ALLOWED_FEEDS:
            raise ValueError(f"feed must be one of {self._ALLOWED_FEEDS}, got {feed!r}")
        self.feed = normalized
        super().__init__(*args, **kwargs)

    def _build_historical_client(self) -> _HistoricalClientLike:
        from alpaca.data.historical.option import OptionHistoricalDataClient

        return OptionHistoricalDataClient(  # type: ignore[return-value]
            api_key=self.api_key,
            secret_key=self.api_secret,
        )

    def _build_data_stream(self) -> _DataStreamLike:
        from alpaca.data.enums import OptionsFeed
        from alpaca.data.live.option import OptionDataStream

        if self.api_key is None or self.api_secret is None:
            raise ValueError("api_key and api_secret are required for Alpaca streaming")
        return OptionDataStream(  # type: ignore[return-value]
            api_key=self.api_key,
            secret_key=self.api_secret,
            feed=getattr(OptionsFeed, self.feed.upper()),
        )

    def _build_bars_request(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> Any:
        from alpaca.data.requests import OptionBarsRequest

        # Option historical bars reject a `feed` query parameter. The feed
        # setting is still used for OptionDataStream subscriptions.
        return OptionBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=_alpaca_timeframe(timeframe),
            start=start,
            end=end,
        )

    def _fetch_bars(self, client: _HistoricalClientLike, request: Any) -> Any:
        return client.get_option_bars(request)

    def _build_quote_request(self, symbol: str, feed: str | None = None) -> Any:
        from alpaca.data.enums import OptionsFeed
        from alpaca.data.requests import OptionLatestQuoteRequest

        feed_val = feed or self.feed
        normalized = feed_val.lower()
        if normalized not in self._ALLOWED_FEEDS:
            raise ValueError(
                f"feed must be one of {self._ALLOWED_FEEDS}, got {feed_val!r}"
            )

        return OptionLatestQuoteRequest(
            symbol_or_symbols=symbol,
            feed=getattr(OptionsFeed, normalized.upper()),
        )

    def _fetch_quote(self, client: _HistoricalClientLike, request: Any) -> Any:
        return client.get_option_latest_quote(request)

    def _native_symbol(self, instrument: Instrument) -> str:
        if instrument.asset_class is not AssetClass.OPTION:
            raise ValueError(
                f"AlpacaOptionMarketAdapter requires OPTION instruments, got "
                f"{instrument.asset_class.value}"
            )
        return instrument_to_occ(instrument)


# ---------------------------------------------------------------------------
# Crypto adapter
# ---------------------------------------------------------------------------
@register("market", "alpaca", "crypto")
class AlpacaCryptoMarketAdapter(_AlpacaMarketAdapterBase):
    asset_class = AssetClass.CRYPTO

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

    # --- SDK factories (lazy imports so the module stays importable without
    # the ``alpaca`` extra) ---
    def _build_historical_client(self) -> _HistoricalClientLike:
        from alpaca.data.historical.crypto import CryptoHistoricalDataClient

        return CryptoHistoricalDataClient(  # type: ignore[return-value]
            api_key=self.api_key,
            secret_key=self.api_secret,
        )

    def _build_data_stream(self) -> _DataStreamLike:
        from alpaca.data.live.crypto import CryptoDataStream

        if self.api_key is None or self.api_secret is None:
            raise ValueError("api_key and api_secret are required for Alpaca streaming")
        return CryptoDataStream(  # type: ignore[return-value]
            api_key=self.api_key,
            secret_key=self.api_secret,
        )

    # --- Request builders ---
    def _build_bars_request(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> Any:
        from alpaca.data.requests import CryptoBarsRequest

        # CryptoBarsRequest has no `feed` kwarg. alpaca-py's get_crypto_bars
        # accepts a method-level `feed: CryptoFeed = CryptoFeed.US`; we rely
        # on that default via the base's _fetch_bars dispatch.
        return CryptoBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=_alpaca_timeframe(timeframe),
            start=start,
            end=end,
        )

    def _build_quote_request(self, symbol: str, feed: str | None = None) -> Any:
        from alpaca.data.requests import CryptoLatestQuoteRequest

        # `feed` is accepted to match the base's get_quote(instrument, feed=None)
        # signature, but alpaca-py exposes a single CryptoFeed.US — there is no
        # per-adapter allow-list to validate against. We deliberately swallow
        # the arg rather than raise. See
        # test_alpaca_crypto_get_quote_swallows_unsupported_feed_kwarg, which
        # pins this behaviour.
        return CryptoLatestQuoteRequest(symbol_or_symbols=symbol)

    # --- Client method dispatch ---
    def _fetch_bars(self, client: _HistoricalClientLike, request: Any) -> Any:
        return client.get_crypto_bars(request)

    def _fetch_quote(self, client: _HistoricalClientLike, request: Any) -> Any:
        return client.get_crypto_latest_quote(request)

    # --- Native symbol: pass through with a BASE/QUOTE shape check ---
    def _native_symbol(self, instrument: Instrument) -> str:
        # The asset-class check is redundant with the base's _assert_supported
        # for the pull path, but it matches the option adapter's pattern and
        # protects the streaming path's _instrument_for, which does not run
        # _assert_supported before consulting _native_symbol.
        if instrument.asset_class is not AssetClass.CRYPTO:
            raise ValueError(
                f"AlpacaCryptoMarketAdapter requires CRYPTO instruments, got "
                f"{instrument.asset_class.value}"
            )
        symbol = instrument.symbol
        if "/" not in symbol:
            raise ValueError(
                f"Crypto symbol must be in BASE/QUOTE form (e.g. 'BTC/USD'), "
                f"got {symbol!r}"
            )
        return symbol


__all__ = [
    "AlpacaOptionMarketAdapter",
    "AlpacaStockMarketAdapter",
]
