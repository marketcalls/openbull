"""
Centralized market-data cache and fan-out.

Every tick received by the WebSocket proxy is routed through
``process_market_data()``. The cache is the single source of live LTP / quote /
depth for any internal consumer:

- the WebSocket proxy itself (external clients)
- the RMS / risk engine (future)
- HTTP endpoints like /api/websocket/market-data

Design
------
- In-memory cache per symbol is authoritative (sub-millisecond reads).
- Redis mirrors the per-symbol snapshot with a short TTL so any worker can read
  the current LTP without talking to the broker. Writes are fire-and-forget
  async tasks — the tick hot path is never awaited on Redis.
- Priority subscribers (CRITICAL / HIGH / NORMAL / LOW) are in-process
  callbacks dispatched synchronously in priority order. Trade-management
  (CRITICAL) sees every tick before dashboards (LOW).
- A health monitor tracks feed freshness and auto-pauses trade-management
  callers when ticks stop flowing.

The singleton is cheap to construct and thread-safe for synchronous callers;
the async machinery lives alongside for the FastAPI app.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from backend.utils.redis_client import cache_set_json

logger = logging.getLogger(__name__)


class SubscriberPriority(IntEnum):
    """Priority levels for tick subscribers. Lower number fires first."""

    CRITICAL = 1  # RMS / trade management
    HIGH = 2     # Price alerts, monitoring
    NORMAL = 3   # Watchlists, general display
    LOW = 4      # Dashboards, analytics


class ConnectionStatus(IntEnum):
    DISCONNECTED = 0
    CONNECTING = 1
    CONNECTED = 2
    AUTHENTICATED = 3
    STALE = 4  # Connected but no data received recently


MAX_DATA_GAP_SECONDS = 30      # feed considered stale after this gap
MAX_DATA_AGE_SECONDS = 60      # per-tick timestamp staleness warning
MAX_PRICE_CHANGE_PERCENT = 20  # circuit-breaker sanity check
REDIS_CACHE_TTL = 60           # per-symbol snapshot TTL in Redis
HEALTH_CHECK_INTERVAL = 5


@dataclass
class HealthStatus:
    status: str = "unknown"
    connected: bool = False
    authenticated: bool = False
    last_data_timestamp: float = 0
    last_data_age_seconds: float = 0
    data_flow_healthy: bool = False
    cache_size: int = 0
    total_subscribers: int = 0
    critical_subscribers: int = 0
    total_updates_processed: int = 0
    validation_errors: int = 0
    stale_data_events: int = 0
    reconnect_count: int = 0
    uptime_seconds: float = 0
    message: str = ""


@dataclass
class ValidationResult:
    valid: bool
    error: str = ""
    warnings: list[str] = field(default_factory=list)


class _Validator:
    """Sanity-checks incoming ticks. Process-local history, no Redis dep."""

    def __init__(self):
        self._last_prices: dict[str, float] = {}
        self._lock = threading.Lock()

    def validate(self, data: dict[str, Any]) -> ValidationResult:
        warnings: list[str] = []
        symbol = data.get("symbol")
        exchange = data.get("exchange")
        if not symbol or not exchange:
            return ValidationResult(valid=False, error="Missing symbol or exchange")

        market_data = data.get("data", {}) or {}
        mode = data.get("mode")
        ltp = market_data.get("ltp")

        # Depth packets can legitimately arrive before LTP stream warms up.
        if mode == 3:
            depth_obj = market_data.get("depth") or {}
            has_levels = bool(
                (isinstance(depth_obj, dict) and (depth_obj.get("buy") or depth_obj.get("sell")))
                or market_data.get("bids")
                or market_data.get("asks")
            )
            if has_levels and (ltp is None or (isinstance(ltp, (int, float)) and ltp <= 0)):
                return ValidationResult(valid=True, warnings=["Depth update without positive LTP"])

        if ltp is None:
            return ValidationResult(valid=False, error="Missing LTP")
        if not isinstance(ltp, (int, float)):
            return ValidationResult(valid=False, error=f"Invalid LTP type: {type(ltp).__name__}")
        if ltp <= 0:
            return ValidationResult(valid=False, error=f"Non-positive LTP: {ltp}")

        ts = market_data.get("timestamp")
        if ts is not None:
            if isinstance(ts, str):
                try:
                    ts = float(ts)
                except ValueError:
                    ts = None
            if isinstance(ts, (int, float)):
                # Normalise ms to seconds
                ts_sec = ts / 1000 if ts > 1e12 else ts
                age = time.time() - ts_sec
                if age > MAX_DATA_AGE_SECONDS:
                    warnings.append(f"Tick timestamp is {age:.1f}s old")

        key = f"{exchange}:{symbol}"
        with self._lock:
            last = self._last_prices.get(key)
            if last and last > 0:
                change = abs((ltp - last) / last) * 100
                if change > MAX_PRICE_CHANGE_PERCENT:
                    warnings.append(f"Large price jump: {change:.2f}%")
            self._last_prices[key] = ltp

        return ValidationResult(valid=True, warnings=warnings)

    def forget(self, key: str | None = None) -> None:
        with self._lock:
            if key:
                self._last_prices.pop(key, None)
            else:
                self._last_prices.clear()


class MarketDataCache:
    """Process-wide singleton. Every tick flows through ``process_market_data``."""

    _instance: "MarketDataCache | None" = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> "MarketDataCache":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False  # type: ignore[attr-defined]
        return cls._instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return
        self._initialized = True

        self._lock = threading.Lock()
        self._cache: dict[str, dict[str, Any]] = {}

        # {priority: {id: {callback, filter, event_type, name}}}
        self._subscribers: dict[int, dict[int, dict[str, Any]]] = defaultdict(dict)
        self._sub_id = 0

        self._validator = _Validator()

        # Health state
        self._connection_status = ConnectionStatus.DISCONNECTED
        self._last_data_ts: float = 0
        self._reconnect_count = 0
        self._start_time = time.time()
        self._trade_paused = False
        self._pause_reason = ""

        # Metrics
        self._metrics = {
            "total_updates": 0,
            "validation_errors": 0,
            "stale_data_events": 0,
            "cache_hits": 0,
            "cache_misses": 0,
        }

        # Allow disabling Redis mirror for tests without a broker
        self._redis_enabled = True

        # Background health check (daemon). Flips status -> STALE when the feed
        # goes quiet.
        self._health_stop = threading.Event()
        self._health_thread = threading.Thread(
            target=self._health_loop, name="MDS-Health", daemon=True
        )
        self._health_thread.start()

        logger.debug("MarketDataCache initialized")

    # -- ingest ---------------------------------------------------------

    def process_market_data(self, data: dict[str, Any]) -> bool:
        """Validate, cache, and fan out a tick. Returns True on success."""
        try:
            result = self._validator.validate(data)
            if not result.valid:
                with self._lock:
                    self._metrics["validation_errors"] += 1
                logger.debug("Tick rejected: %s", result.error)
                return False

            symbol = data.get("symbol")
            exchange = data.get("exchange")
            mode = data.get("mode")
            market_data = data.get("data", {}) or {}
            if not symbol or not exchange:
                return False

            key = f"{exchange}:{symbol}"
            now = time.time()

            with self._lock:
                entry = self._cache.setdefault(
                    key, {"symbol": symbol, "exchange": exchange, "last_update": now}
                )

                if mode == 1:  # LTP
                    entry["ltp"] = {
                        "value": market_data.get("ltp"),
                        "timestamp": market_data.get("timestamp", now),
                        "volume": market_data.get("volume", 0),
                    }
                elif mode == 2:  # Quote
                    entry["quote"] = {
                        "open": market_data.get("open"),
                        "high": market_data.get("high"),
                        "low": market_data.get("low"),
                        "close": market_data.get("close"),
                        "ltp": market_data.get("ltp"),
                        "volume": market_data.get("volume", 0),
                        "change": market_data.get("change"),
                        "change_percent": market_data.get("change_percent"),
                        "timestamp": market_data.get("timestamp", now),
                    }
                    entry["ltp"] = {
                        "value": market_data.get("ltp"),
                        "timestamp": market_data.get("timestamp", now),
                        "volume": market_data.get("volume", 0),
                    }
                elif mode == 3:  # Depth
                    depth = market_data.get("depth") or {}
                    buy = (depth.get("buy") if isinstance(depth, dict) else None) or market_data.get("bids") or []
                    sell = (depth.get("sell") if isinstance(depth, dict) else None) or market_data.get("asks") or []
                    entry["depth"] = {
                        "buy": buy,
                        "sell": sell,
                        "ltp": market_data.get("ltp"),
                        "timestamp": market_data.get("timestamp", now),
                    }

                entry["last_update"] = now
                self._metrics["total_updates"] += 1
                self._last_data_ts = now

                if self._connection_status == ConnectionStatus.STALE:
                    self._connection_status = ConnectionStatus.AUTHENTICATED
                    self._trade_paused = False
                    self._pause_reason = ""

            # Mirror to Redis (fire-and-forget). Guarded in case no loop is running.
            if self._redis_enabled:
                self._mirror_to_redis(key, entry)

            self._broadcast(key, mode, data)
            return True

        except Exception as e:
            logger.exception("process_market_data failed: %s", e)
            return False

    # -- subscribers ----------------------------------------------------

    def subscribe(
        self,
        priority: SubscriberPriority,
        event_type: str,
        callback: Callable[[dict[str, Any]], None],
        filter_symbols: set[str] | None = None,
        name: str = "",
    ) -> int:
        """Subscribe a callback. Returns an id for unsubscribe()."""
        with self._lock:
            self._sub_id += 1
            sid = self._sub_id
            self._subscribers[priority][sid] = {
                "callback": callback,
                "filter": filter_symbols,
                "event_type": event_type,
                "name": name or f"sub_{sid}",
            }
        logger.debug("Subscriber %d added (%s, %s)", sid, priority.name, name)
        return sid

    def subscribe_critical(
        self,
        callback: Callable[[dict[str, Any]], None],
        filter_symbols: set[str] | None = None,
        name: str = "rms",
    ) -> int:
        """Shortcut for CRITICAL-priority LTP subscribers (RMS, stoploss, etc.)."""
        return self.subscribe(SubscriberPriority.CRITICAL, "ltp", callback, filter_symbols, name)

    def unsubscribe(self, subscriber_id: int) -> bool:
        with self._lock:
            for prio, subs in self._subscribers.items():
                if subscriber_id in subs:
                    del subs[subscriber_id]
                    return True
        return False

    # -- reads ----------------------------------------------------------

    def get_ltp(self, symbol: str, exchange: str) -> dict[str, Any] | None:
        with self._lock:
            entry = self._cache.get(f"{exchange}:{symbol}")
            if entry:
                self._metrics["cache_hits"] += 1
                return entry.get("ltp")
            self._metrics["cache_misses"] += 1
            return None

    def get_ltp_value(self, symbol: str, exchange: str) -> float | None:
        d = self.get_ltp(symbol, exchange)
        return d.get("value") if d else None

    def get_quote(self, symbol: str, exchange: str) -> dict[str, Any] | None:
        with self._lock:
            entry = self._cache.get(f"{exchange}:{symbol}")
            if entry:
                self._metrics["cache_hits"] += 1
                return entry.get("quote")
            self._metrics["cache_misses"] += 1
            return None

    def get_depth(self, symbol: str, exchange: str) -> dict[str, Any] | None:
        with self._lock:
            entry = self._cache.get(f"{exchange}:{symbol}")
            if entry:
                self._metrics["cache_hits"] += 1
                return entry.get("depth")
            self._metrics["cache_misses"] += 1
            return None

    def get_all(self, symbol: str, exchange: str) -> dict[str, Any]:
        with self._lock:
            entry = self._cache.get(f"{exchange}:{symbol}")
            return dict(entry) if entry else {}

    # -- health / safety -----------------------------------------------

    def set_connected(self, connected: bool, authenticated: bool = False) -> None:
        """Called by the WS proxy when the broker connection state changes."""
        with self._lock:
            prev = self._connection_status
            if connected:
                self._connection_status = (
                    ConnectionStatus.AUTHENTICATED if authenticated else ConnectionStatus.CONNECTED
                )
                if prev in (ConnectionStatus.DISCONNECTED, ConnectionStatus.STALE):
                    if prev == ConnectionStatus.STALE:
                        self._reconnect_count += 1
                    self._trade_paused = False
                    self._pause_reason = ""
            else:
                if prev in (ConnectionStatus.CONNECTED, ConnectionStatus.AUTHENTICATED):
                    self._reconnect_count += 1
                self._connection_status = ConnectionStatus.DISCONNECTED
                self._trade_paused = True
                self._pause_reason = "Broker connection lost"
                self._metrics["stale_data_events"] += 1

    def is_data_fresh(
        self, symbol: str | None = None, exchange: str | None = None, max_age_seconds: float = 30
    ) -> bool:
        with self._lock:
            if self._last_data_ts == 0:
                return False
            if (time.time() - self._last_data_ts) >= max_age_seconds:
                return False
            if symbol and exchange:
                entry = self._cache.get(f"{exchange}:{symbol}")
                if not entry:
                    return False
                return (time.time() - entry.get("last_update", 0)) < max_age_seconds
            return True

    def is_trade_management_safe(self) -> tuple[bool, str]:
        """RMS gate: returns (safe, reason) — call before acting on cached prices."""
        with self._lock:
            if self._trade_paused:
                return False, self._pause_reason
            if self._connection_status != ConnectionStatus.AUTHENTICATED:
                return False, f"Connection status: {self._connection_status.name}"
            age = time.time() - self._last_data_ts if self._last_data_ts else -1
            if age < 0 or age >= MAX_DATA_GAP_SECONDS:
                return False, f"No ticks for {age:.1f}s"
        return True, ""

    def get_health_status(self) -> HealthStatus:
        with self._lock:
            now = time.time()
            age = (now - self._last_data_ts) if self._last_data_ts else 0
            total_subs = sum(len(s) for s in self._subscribers.values())
            crit = len(self._subscribers.get(SubscriberPriority.CRITICAL, {}))
            authed = self._connection_status == ConnectionStatus.AUTHENTICATED
            flow_healthy = authed and self._last_data_ts > 0 and age < MAX_DATA_GAP_SECONDS
            return HealthStatus(
                status="healthy" if flow_healthy else "unhealthy",
                connected=self._connection_status
                in (ConnectionStatus.CONNECTED, ConnectionStatus.AUTHENTICATED),
                authenticated=authed,
                last_data_timestamp=self._last_data_ts,
                last_data_age_seconds=round(age, 2) if self._last_data_ts else 0,
                data_flow_healthy=flow_healthy,
                cache_size=len(self._cache),
                total_subscribers=total_subs,
                critical_subscribers=crit,
                total_updates_processed=self._metrics["total_updates"],
                validation_errors=self._metrics["validation_errors"],
                stale_data_events=self._metrics["stale_data_events"],
                reconnect_count=self._reconnect_count,
                uptime_seconds=round(now - self._start_time, 2),
                message=self._pause_reason,
            )

    def get_metrics(self) -> dict[str, Any]:
        with self._lock:
            total = self._metrics["cache_hits"] + self._metrics["cache_misses"]
            hit_rate = (self._metrics["cache_hits"] / total * 100) if total else 0
            return {
                "total_symbols": len(self._cache),
                "total_updates": self._metrics["total_updates"],
                "cache_hits": self._metrics["cache_hits"],
                "cache_misses": self._metrics["cache_misses"],
                "hit_rate": round(hit_rate, 2),
                "validation_errors": self._metrics["validation_errors"],
                "stale_data_events": self._metrics["stale_data_events"],
                "total_subscribers": sum(len(s) for s in self._subscribers.values()),
                "critical_subscribers": len(
                    self._subscribers.get(SubscriberPriority.CRITICAL, {})
                ),
            }

    # -- internals ------------------------------------------------------

    def _broadcast(self, symbol_key: str, mode: int | None, data: dict[str, Any]) -> None:
        mode_to_event = {1: "ltp", 2: "quote", 3: "depth"}
        event_type = mode_to_event.get(mode, "all") if isinstance(mode, int) else "all"
        with self._lock:
            # Snapshot per priority so we don't hold the lock across callbacks.
            by_prio = [
                (prio, list(subs.values()))
                for prio, subs in sorted(self._subscribers.items())
            ]
        for _, subs in by_prio:
            for sub in subs:
                sub_event = sub.get("event_type", "all")
                if sub_event != "all" and sub_event != event_type:
                    continue
                flt = sub.get("filter")
                if flt and symbol_key not in flt:
                    continue
                try:
                    sub["callback"](data)
                except Exception as e:
                    logger.exception(
                        "Subscriber %s raised: %s", sub.get("name", "?"), e
                    )

    def _mirror_to_redis(self, key: str, entry: dict[str, Any]) -> None:
        """Fire-and-forget Redis write. Safe from any thread."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no loop on this thread — skip mirror
        loop.call_soon_threadsafe(
            lambda: asyncio.create_task(
                cache_set_json(f"md:{key}", entry, REDIS_CACHE_TTL)
            )
        )

    def _health_loop(self) -> None:
        while not self._health_stop.wait(HEALTH_CHECK_INTERVAL):
            try:
                with self._lock:
                    if (
                        self._connection_status == ConnectionStatus.AUTHENTICATED
                        and self._last_data_ts > 0
                    ):
                        age = time.time() - self._last_data_ts
                        if age > MAX_DATA_GAP_SECONDS:
                            self._connection_status = ConnectionStatus.STALE
                            self._trade_paused = True
                            self._pause_reason = f"No ticks for {age:.1f}s"
                            self._metrics["stale_data_events"] += 1
                            logger.warning("Feed stale — %.1fs without data", age)
            except Exception as e:
                logger.exception("Health loop error: %s", e)

    def stop(self) -> None:
        """Stop the background health thread. Called on app shutdown."""
        self._health_stop.set()


# Global singleton helpers — mirror openalgo's public surface.

_cache_singleton: MarketDataCache | None = None
_singleton_lock = threading.Lock()


def get_market_data_cache() -> MarketDataCache:
    """Return the process-wide MarketDataCache."""
    global _cache_singleton
    if _cache_singleton is None:
        with _singleton_lock:
            if _cache_singleton is None:
                _cache_singleton = MarketDataCache()
    return _cache_singleton


def process_market_data(data: dict[str, Any]) -> bool:
    return get_market_data_cache().process_market_data(data)


def get_ltp(symbol: str, exchange: str) -> dict[str, Any] | None:
    return get_market_data_cache().get_ltp(symbol, exchange)


def get_ltp_value(symbol: str, exchange: str) -> float | None:
    return get_market_data_cache().get_ltp_value(symbol, exchange)


def get_quote(symbol: str, exchange: str) -> dict[str, Any] | None:
    return get_market_data_cache().get_quote(symbol, exchange)


def get_depth(symbol: str, exchange: str) -> dict[str, Any] | None:
    return get_market_data_cache().get_depth(symbol, exchange)


def subscribe_critical(
    callback: Callable[[dict[str, Any]], None],
    filter_symbols: set[str] | None = None,
    name: str = "rms",
) -> int:
    return get_market_data_cache().subscribe_critical(callback, filter_symbols, name)


def is_data_fresh(
    symbol: str | None = None, exchange: str | None = None, max_age_seconds: float = 30
) -> bool:
    return get_market_data_cache().is_data_fresh(symbol, exchange, max_age_seconds)


def is_trade_management_safe() -> tuple[bool, str]:
    return get_market_data_cache().is_trade_management_safe()


def get_health_status() -> HealthStatus:
    return get_market_data_cache().get_health_status()
