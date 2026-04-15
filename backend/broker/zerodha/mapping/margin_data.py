"""
Zerodha margin data mapping - transforms OpenBull position format to Zerodha margin format.
Adapted from OpenAlgo's zerodha margin_data.py.
"""

import logging

from backend.broker.upstox.mapping.order_data import get_brsymbol_from_cache
from backend.broker.zerodha.mapping.transform_data import map_order_type, map_product_type

logger = logging.getLogger(__name__)


def transform_margin_positions(positions: list[dict]) -> list[dict]:
    """Transform OpenBull margin positions to Zerodha margin format."""
    transformed_positions: list[dict] = []
    skipped_positions: list[str] = []

    for position in positions:
        try:
            symbol = position["symbol"]
            exchange = position["exchange"]

            br_symbol = get_brsymbol_from_cache(symbol, exchange)
            if not br_symbol or str(br_symbol).lower() == "none":
                logger.warning("Symbol not found for %s on %s", symbol, exchange)
                skipped_positions.append(f"{symbol} ({exchange})")
                continue

            br_symbol_str = str(br_symbol).strip()
            if not br_symbol_str:
                skipped_positions.append(f"{symbol} ({exchange})")
                continue

            transformed_position = {
                "exchange": exchange,
                "tradingsymbol": br_symbol_str,
                "transaction_type": position["action"].upper(),
                "variety": "regular",
                "product": map_product_type(position["product"]),
                "order_type": map_order_type(position["pricetype"]),
                "quantity": int(position["quantity"]),
                "price": float(position.get("price", 0)),
                "trigger_price": float(position.get("trigger_price", 0)),
            }
            transformed_positions.append(transformed_position)

        except Exception as e:
            logger.error("Error transforming margin position %s: %s", position, e)
            skipped_positions.append(f"{position.get('symbol', 'unknown')}")

    if skipped_positions:
        logger.warning("Skipped %d margin position(s): %s", len(skipped_positions), skipped_positions)

    return transformed_positions


def parse_margin_response(response_data: dict) -> dict:
    """Parse Zerodha margin response to OpenBull standard format."""
    try:
        if not response_data or not isinstance(response_data, dict):
            return {"status": "error", "message": "Invalid response from broker"}

        if response_data.get("status") != "success":
            error_message = response_data.get("message", "Failed to calculate margin")
            if response_data.get("error_type"):
                error_message = f"{response_data['error_type']}: {error_message}"
            return {"status": "error", "message": error_message}

        data = response_data.get("data", {})

        total_margin_required = 0
        span_margin = 0
        exposure_margin = 0
        margin_benefit = 0

        if isinstance(data, dict) and "final" in data:
            initial = data.get("initial", {})
            final = data.get("final", {})

            total_margin_required = initial.get("total", 0)
            span_margin = final.get("span", 0)
            exposure_margin = final.get("exposure", 0)
            margin_benefit = initial.get("total", 0) - final.get("total", 0)

        elif isinstance(data, list):
            for order in data:
                span_margin += order.get("span", 0)
                exposure_margin += order.get("exposure", 0)
                total_margin_required += order.get("total", 0)

        return {
            "status": "success",
            "data": {
                "total_margin_required": total_margin_required,
                "span_margin": span_margin,
                "exposure_margin": exposure_margin,
                "margin_benefit": margin_benefit,
            },
        }

    except Exception as e:
        logger.error("Error parsing Zerodha margin response: %s", e)
        return {"status": "error", "message": f"Failed to parse margin response: {e}"}
