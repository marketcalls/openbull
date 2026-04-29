"""
Dhan margin data mapping.
Adapted from OpenAlgo's dhan margin_data.py.
"""

import logging

from backend.broker.dhan.mapping.transform_data import map_exchange_type
from backend.broker.upstox.mapping.order_data import get_token_from_cache

logger = logging.getLogger(__name__)


def _map_product_type_for_margin(product: str) -> str:
    """OpenBull product (CNC/NRML/MIS) -> Dhan product (CNC/MARGIN/INTRADAY)."""
    return {"CNC": "CNC", "NRML": "MARGIN", "MIS": "INTRADAY"}.get(product, "INTRADAY")


def transform_margin_positions(positions: list[dict], client_id: str | None = None) -> list[dict]:
    """Transform OpenBull positions to Dhan single-leg margin payloads.

    Dhan's margin calculator only accepts ONE order at a time, so the public
    API surface is a list of payloads — caller iterates and sums results.
    Skips positions with unresolvable token / exchange.
    """
    transformed: list[dict] = []
    skipped: list[str] = []

    for position in positions:
        try:
            symbol = position.get("symbol", "")
            exchange = position.get("exchange", "")
            token = get_token_from_cache(symbol, exchange)
            if not token:
                logger.warning("Token not found for %s on %s", symbol, exchange)
                skipped.append(f"{symbol} ({exchange})")
                continue

            exchange_segment = map_exchange_type(exchange)
            if not exchange_segment:
                logger.warning("Invalid exchange: %s", exchange)
                skipped.append(f"{symbol} ({exchange})")
                continue

            payload = {
                "dhanClientId": client_id or "",
                "exchangeSegment": exchange_segment,
                "transactionType": position["action"].upper(),
                "quantity": int(position["quantity"]),
                "productType": _map_product_type_for_margin(position["product"]),
                "securityId": str(token),
                "price": float(position.get("price", 0) or 0),
            }

            trigger_price = position.get("trigger_price", 0)
            if trigger_price and float(trigger_price) > 0:
                payload["triggerPrice"] = float(trigger_price)

            transformed.append(payload)

        except Exception as e:
            logger.error("Error transforming margin position %s: %s", position, e)
            skipped.append(position.get("symbol", "unknown"))

    if skipped:
        logger.warning("Skipped %d margin position(s): %s", len(skipped), skipped)

    return transformed


def parse_margin_response(response_data: dict) -> dict:
    """Parse a single Dhan margin response into OpenBull standard format."""
    try:
        if not response_data or not isinstance(response_data, dict):
            return {"status": "error", "message": "Invalid response from broker"}

        if response_data.get("errorType") or response_data.get("status") == "failed":
            return {
                "status": "error",
                "message": response_data.get("errorMessage", "Failed to calculate margin"),
            }

        total_margin = float(response_data.get("totalMargin") or 0)
        span_margin = float(response_data.get("spanMargin") or 0)
        exposure_margin = float(response_data.get("exposureMargin") or 0)

        return {
            "status": "success",
            "data": {
                "total_margin_required": total_margin,
                "span_margin": span_margin,
                "exposure_margin": exposure_margin,
            },
        }
    except Exception as e:
        logger.error("Error parsing Dhan margin response: %s", e)
        return {"status": "error", "message": f"Failed to parse margin response: {e}"}


def parse_batch_margin_response(responses: list[dict]) -> dict:
    """Sum individual leg margins into a single aggregated response.

    Limitation (Dhan API): no spread/hedge benefit accounting — pure summation.
    """
    try:
        total_margin = 0.0
        total_span = 0.0
        total_exposure = 0.0
        successful = 0

        for response in responses:
            if response.get("status") == "success":
                data = response.get("data", {})
                total_margin += float(data.get("total_margin_required") or 0)
                total_span += float(data.get("span_margin") or 0)
                total_exposure += float(data.get("exposure_margin") or 0)
                successful += 1

        logger.info(
            "Aggregated %d Dhan margin legs: total=%.2f span=%.2f exposure=%.2f",
            successful, total_margin, total_span, total_exposure,
        )

        return {
            "status": "success",
            "data": {
                "total_margin_required": total_margin,
                "span_margin": total_span,
                "exposure_margin": total_exposure,
            },
        }
    except Exception as e:
        logger.error("Error parsing Dhan batch margin response: %s", e)
        return {"status": "error", "message": f"Failed to parse batch margin response: {e}"}
