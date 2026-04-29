"""
Fyers margin data mapping - transforms OpenBull position format to Fyers margin format.
Adapted from OpenAlgo's fyers margin_data.py.
"""

import logging

from backend.broker.upstox.mapping.order_data import get_brsymbol_from_cache
from backend.broker.fyers.mapping.transform_data import (
    map_action,
    map_order_type,
    map_product_type,
)

logger = logging.getLogger(__name__)


def transform_margin_positions(positions: list[dict]) -> list[dict]:
    """Transform OpenBull margin positions to Fyers ``multiorder/margin`` request format."""
    transformed_positions: list[dict] = []
    skipped_positions: list[str] = []

    for position in positions:
        try:
            symbol = position["symbol"]
            exchange = position["exchange"]

            br_symbol = get_brsymbol_from_cache(symbol, exchange)

            if not br_symbol or str(br_symbol).lower() == "none":
                logger.warning("Symbol not found for: %s on exchange: %s", symbol, exchange)
                skipped_positions.append(f"{symbol} ({exchange})")
                continue

            br_symbol_str = str(br_symbol).strip()
            if not br_symbol_str:
                logger.warning(
                    "Invalid symbol format for %s (%s): '%s'",
                    symbol, exchange, br_symbol_str,
                )
                skipped_positions.append(f"{symbol} ({exchange})")
                continue

            transformed_positions.append({
                "symbol": br_symbol_str,
                "qty": int(position["quantity"]),
                "side": map_action(position["action"].upper()),
                "type": map_order_type(position["pricetype"]),
                "productType": map_product_type(position["product"]),
                "limitPrice": float(position.get("price", 0.0) or 0.0),
                "stopLoss": 0.0,
                "stopPrice": float(position.get("trigger_price", 0.0) or 0.0),
                "takeProfit": 0.0,
            })

        except Exception as e:
            logger.error("Error transforming margin position %s: %s", position, e)
            skipped_positions.append(f"{position.get('symbol', 'unknown')}")

    if skipped_positions:
        logger.warning(
            "Skipped %d margin position(s): %s", len(skipped_positions), skipped_positions,
        )

    return transformed_positions


def parse_margin_response(response_data: dict) -> dict:
    """Parse Fyers margin response into the OpenBull standard envelope.

    Fyers does not provide SPAN / Exposure breakdown — we map ``margin_new_order``
    to ``total_margin_required`` and report span/exposure as 0.
    """
    try:
        if not response_data or not isinstance(response_data, dict):
            return {"status": "error", "message": "Invalid response from broker"}

        if response_data.get("s") != "ok":
            error_message = response_data.get("message", "Failed to calculate margin")
            error_code = response_data.get("code", "Unknown")
            return {
                "status": "error",
                "message": f"Fyers API Error (Code {error_code}): {error_message}",
            }

        data = response_data.get("data", {})
        margin_new_order = data.get("margin_new_order", 0)

        return {
            "status": "success",
            "data": {
                "total_margin_required": margin_new_order,
                "span_margin": 0,
                "exposure_margin": 0,
            },
        }

    except Exception as e:
        logger.error("Error parsing Fyers margin response: %s", e)
        return {"status": "error", "message": f"Failed to parse margin response: {e}"}
