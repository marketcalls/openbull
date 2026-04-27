"""
Symbol metadata lookups for the sandbox.

The sandbox needs three things from the symbol master that the rest of the
app already keeps populated (``symtoken`` table):

* **lot_size** — F&O orders must be in multiples of this.
* **tick_size** — LIMIT / SL price must be a multiple of this.
* **instrument_type** — disambiguates futures / call / put / equity so we can
  pick the right leverage and apply the right validation rules.

Read-only — never writes to ``symtoken``. Treated as an authoritative source:
if the symbol isn't here, the order is rejected (same as openalgo).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select

from backend.models.symbol import SymToken
from backend.sandbox._db import session_scope

logger = logging.getLogger(__name__)


# F&O exchanges. ``CRYPTO`` not currently in openbull's symbol master, but
# kept in the set so the validation gate matches openalgo's exchange list.
FNO_EXCHANGES = {"NFO", "BFO", "MCX", "CDS", "BCD", "CRYPTO"}
EQUITY_EXCHANGES = {"NSE", "BSE"}

# Product compatibility per exchange. CNC is delivery (equity only), NRML is
# overnight derivatives, MIS is intraday on either. Same matrix openalgo
# enforces in its order_manager.
PRODUCT_EXCHANGE_OK: dict[str, set[str]] = {
    "CNC": EQUITY_EXCHANGES,
    "NRML": FNO_EXCHANGES,
    "MIS": EQUITY_EXCHANGES | FNO_EXCHANGES,
}


@dataclass(frozen=True)
class SymbolInfo:
    symbol: str
    exchange: str
    lot_size: int
    tick_size: float
    instrument_type: str  # "EQ", "FUT", "CE", "PE", or "" if unknown

    @property
    def is_option(self) -> bool:
        return self.instrument_type in ("CE", "PE")

    @property
    def is_future(self) -> bool:
        return self.instrument_type == "FUT" or self.instrument_type.endswith("FUT")

    @property
    def is_equity(self) -> bool:
        return self.exchange in EQUITY_EXCHANGES and not self.is_option and not self.is_future


def get_symbol_info(symbol: str, exchange: str) -> SymbolInfo | None:
    """Look up the symbol master. Returns ``None`` if the symbol isn't there.

    The caller (order placement) treats ``None`` as a hard rejection — same as
    openalgo, which refuses to accept orders for unknown symbols."""
    if not symbol or not exchange:
        return None
    with session_scope() as db:
        row = db.execute(
            select(SymToken).where(
                SymToken.symbol == symbol,
                SymToken.exchange == exchange,
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return SymbolInfo(
            symbol=row.symbol,
            exchange=row.exchange,
            lot_size=int(row.lotsize or 1),
            tick_size=float(row.tick_size or 0.05),
            instrument_type=(row.instrumenttype or "").upper(),
        )


def classify_from_symbol(symbol: str, exchange: str) -> str:
    """Best-effort instrument classification when ``symtoken`` doesn't have a
    type column populated. Tail-pattern check on the trading symbol — same
    convention used by NSE / BSE F&O contracts (``...CE`` call, ``...PE`` put,
    ``...FUT`` future)."""
    if not symbol:
        return ""
    s = symbol.upper()
    if s.endswith("CE"):
        return "CE"
    if s.endswith("PE"):
        return "PE"
    if s.endswith("FUT"):
        return "FUT"
    if exchange in EQUITY_EXCHANGES:
        return "EQ"
    return ""


def is_product_exchange_compatible(product: str, exchange: str) -> bool:
    """``True`` if (product, exchange) is a legal combination. Matches openalgo's
    rejection rules: CNC only on NSE/BSE; NRML only on derivatives exchanges;
    MIS on either."""
    allowed = PRODUCT_EXCHANGE_OK.get(product.upper())
    if allowed is None:
        return False
    return exchange.upper() in allowed
