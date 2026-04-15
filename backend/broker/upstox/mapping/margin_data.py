"""
Upstox margin data mapping - transforms OpenBull position format to Upstox margin format.
Adapted from OpenAlgo's upstox margin_data.py.
"""

import logging

from backend.broker.upstox.mapping.order_data import get_token_from_cache
from backend.broker.upstox.mapping.transform_data import map_product_type

logger = logging.getLogger(__name__)


def transform_margin_positions(positions: list[dict]) -> list[dict]:
    """Transform OpenBull margin positions to Upstox margin format."""
    transformed_positions: list[dict] = []
    skipped_positions: list[str] = []

    for position in positions:
        try:
            symbol = position["symbol"]
            exchange = position["exchange"]

            instrument_key = get_token_from_cache(symbol, exchange)

            if not instrument_key or str(instrument_key).lower() == "none":
                logger.warning("Instrument key not found for %s on %s", symbol, exchange)
                skipped_positions.append(f"{symbol} ({exchange})")
                continue

            instrument_key_str = str(instrument_key).strip()
            if "|" not in instrument_key_str:
                logger.warning(
                    "Invalid instrument key format for %s (%s): %s",
                    symbol, exchange, instrument_key_str,
                )
                skipped_positions.append(f"{symbol} ({exchange})")
                continue

            transformed_position = {
                "instrument_key": instrument_key_str,
                "quantity": int(position["quantity"]),
                "transaction_type": position["action"].upper(),
                "product": map_product_type(position["product"]),
            }

            if position.get("price") and float(position["price"]) > 0:
                transformed_position["price"] = float(position["price"])

            transformed_positions.append(transformed_position)

        except Exception as e:
            logger.error("Error transforming margin position %s: %s", position, e)
            skipped_positions.append(f"{position.get('symbol', 'unknown')}")

    if skipped_positions:
        logger.warning("Skipped %d margin position(s): %s", len(skipped_positions), skipped_positions)

    return transformed_positions


def parse_margin_response(response_data: dict) -> dict:
    """Parse Upstox margin response to OpenBull standard format."""
    try:
        if not response_data or not isinstance(response_data, dict):
            return {"status": "error", "message": "Invalid response from broker"}

        if response_data.get("status") != "success":
            error_message = response_data.get("message", "Failed to calculate margin")
            errors = response_data.get("errors")
            if isinstance(errors, list) and errors:
                error_message = errors[0].get("message", error_message)
            return {"status": "error", "message": error_message}

        data = response_data.get("data", {})
        required_margin = data.get("required_margin", 0)
        final_margin = data.get("final_margin", 0)
        margin_benefit = required_margin - final_margin

        margins = data.get("margins", [])
        total_span = sum(m.get("span_margin", 0) for m in margins)
        total_exposure = sum(m.get("exposure_margin", 0) for m in margins)

        return {
            "status": "success",
            "data": {
                "total_margin_required": required_margin,
                "span_margin": total_span,
                "exposure_margin": total_exposure,
                "margin_benefit": margin_benefit,
            },
        }

    except Exception as e:
        logger.error("Error parsing Upstox margin response: %s", e)
        return {"status": "error", "message": f"Failed to parse margin response: {e}"}
