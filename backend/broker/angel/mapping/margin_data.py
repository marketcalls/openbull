"""
Angel One margin data mapping.
Adapted from OpenAlgo's angel margin_data.py.
"""

import logging

from backend.broker.angel.mapping.transform_data import (
    map_order_type,
    map_product_type,
)
from backend.broker.upstox.mapping.order_data import get_token_from_cache

logger = logging.getLogger(__name__)


def transform_margin_positions(positions: list[dict]) -> list[dict]:
    """Transform OpenBull margin positions to Angel margin format."""
    transformed_positions: list[dict] = []
    skipped_positions: list[str] = []

    for position in positions:
        try:
            symbol = position["symbol"]
            exchange = position["exchange"]

            token = get_token_from_cache(symbol, exchange)

            if not token or str(token).lower() == "none":
                logger.warning("Token not found for %s on %s", symbol, exchange)
                skipped_positions.append(f"{symbol} ({exchange})")
                continue

            token_str = str(token).strip()
            # Angel expects a numeric token. Reject anything that isn't a
            # plain number (decimals/negatives are tolerated to match openalgo).
            if not token_str.replace(".", "").replace("-", "").isdigit():
                logger.warning(
                    "Invalid token format for %s (%s): '%s'",
                    symbol, exchange, token_str,
                )
                skipped_positions.append(
                    f"{symbol} ({exchange}) - invalid token: {token_str}"
                )
                continue

            transformed_positions.append({
                "exchange": exchange,
                "qty": int(position["quantity"]),
                "price": float(position.get("price", 0)),
                "productType": map_product_type(position["product"]),
                "token": token_str,
                "tradeType": position["action"].upper(),
                "orderType": map_order_type(position["pricetype"]),
            })
        except Exception as e:
            logger.error("Error transforming margin position %s: %s", position, e)
            skipped_positions.append(
                f"{position.get('symbol', 'unknown')} - Error: {e}"
            )

    if skipped_positions:
        logger.warning(
            "Skipped %d margin position(s): %s",
            len(skipped_positions),
            ", ".join(skipped_positions),
        )

    if transformed_positions:
        logger.info(
            "Transformed %d margin position(s) for Angel calculation",
            len(transformed_positions),
        )

    return transformed_positions


def parse_margin_response(response_data: dict) -> dict:
    """Parse Angel margin calculator response to OpenBull standard format."""
    try:
        if not response_data or not isinstance(response_data, dict):
            return {"status": "error", "message": "Invalid response from broker"}

        if response_data.get("status") is False:
            return {
                "status": "error",
                "message": response_data.get("message", "Failed to calculate margin"),
            }

        data = response_data.get("data", {}) or {}
        margin_components = data.get("marginComponents", {}) or {}

        total_margin_required = data.get("totalMarginRequired", 0)
        span_margin = margin_components.get("spanMargin", 0)
        # Angel does not break out exposure margin separately.
        exposure_margin = 0

        return {
            "status": "success",
            "data": {
                "total_margin_required": total_margin_required,
                "span_margin": span_margin,
                "exposure_margin": exposure_margin,
            },
        }

    except Exception as e:
        logger.error("Error parsing Angel margin response: %s", e)
        return {"status": "error", "message": f"Failed to parse margin response: {e}"}
