"""
Dhan-specific mapping helpers used by the WebSocket adapter.
Adapted verbatim from OpenAlgo's dhan dhan_mapping.py.
"""


class DhanExchangeMapper:
    """Map between OpenBull exchange names and Dhan exchange codes/segments."""

    EXCHANGE_MAP = {
        "NSE": "NSE_EQ",
        "BSE": "BSE_EQ",
        "NFO": "NSE_FNO",
        "BFO": "BSE_FNO",
        "MCX": "MCX_COMM",
        "CDS": "NSE_CURRENCY",
        "BCD": "BSE_CURRENCY",
        "NSE_INDEX": "IDX_I",
        "BSE_INDEX": "IDX_I",
    }

    # Numeric segment code -> OpenBull exchange (segment 0 IDX_I covers
    # both NSE_INDEX and BSE_INDEX; defaults to NSE_INDEX).
    SEGMENT_TO_EXCHANGE = {
        0: "NSE_INDEX",
        1: "NSE",
        2: "NFO",
        3: "CDS",
        4: "BSE",
        5: "MCX",
        7: "BCD",
        8: "BFO",
    }

    DHAN_TO_OPENALGO = {v: k for k, v in EXCHANGE_MAP.items()}
    EXCHANGE_TO_SEGMENT = {v: k for k, v in SEGMENT_TO_EXCHANGE.items()}

    @classmethod
    def get_dhan_exchange(cls, exchange: str) -> str | None:
        return cls.EXCHANGE_MAP.get(exchange)

    @classmethod
    def get_openalgo_exchange(cls, dhan_exchange: str) -> str | None:
        return cls.DHAN_TO_OPENALGO.get(dhan_exchange)

    @classmethod
    def get_exchange_from_segment(cls, segment_code: int) -> str | None:
        return cls.SEGMENT_TO_EXCHANGE.get(segment_code)

    @classmethod
    def get_segment_from_exchange(cls, exchange: str) -> int | None:
        if exchange == "BSE_INDEX":
            return 0
        return cls.EXCHANGE_TO_SEGMENT.get(exchange)


class DhanCapabilityRegistry:
    """Capability registry: which exchanges support 5-/20-level depth."""

    DEPTH_SUPPORT = {
        "NSE": {5, 20},
        "NFO": {5, 20},
        "BSE": {5},
        "BFO": {5},
        "MCX": {5},
        "CDS": {5},
        "BCD": {5},
        "NSE_INDEX": {5},
        "BSE_INDEX": {5},
    }

    MAX_SUBSCRIPTIONS_5_DEPTH = 5000
    MAX_SUBSCRIPTIONS_20_DEPTH = 50
    MAX_INSTRUMENTS_PER_REQUEST = 100

    @classmethod
    def is_depth_level_supported(cls, exchange: str, depth_level: int) -> bool:
        return depth_level in cls.DEPTH_SUPPORT.get(exchange, set())

    @classmethod
    def get_supported_depth_levels(cls, exchange: str) -> set:
        return cls.DEPTH_SUPPORT.get(exchange, {5})

    @classmethod
    def get_fallback_depth_level(cls, exchange: str, requested_depth: int) -> int:
        supported = cls.get_supported_depth_levels(exchange)
        if requested_depth in supported:
            return requested_depth
        lower = [lvl for lvl in supported if lvl < requested_depth]
        if lower:
            return max(lower)
        return min(supported) if supported else 5

    @classmethod
    def get_max_subscriptions(cls, depth_level: int) -> int:
        if depth_level == 20:
            return cls.MAX_SUBSCRIPTIONS_20_DEPTH
        return cls.MAX_SUBSCRIPTIONS_5_DEPTH
