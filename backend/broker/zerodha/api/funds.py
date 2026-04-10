"""
Zerodha funds API - fetch margin and funds data.
Adapted from OpenAlgo's zerodha funds.py. Key change: accepts config dict.
"""

import logging

from backend.utils.httpx_client import get_httpx_client

logger = logging.getLogger(__name__)


def get_margin_data(auth_token: str, config: dict | None = None) -> dict:
    """Fetch margin data from Zerodha's API.

    Args:
        auth_token: Zerodha Kite auth token (format: "api_key:access_token")
        config: Broker config dict with api_key, api_secret (optional)
    """
    client = get_httpx_client()
    headers = {"X-Kite-Version": "3", "Authorization": f"token {auth_token}"}

    try:
        response = client.get("https://api.kite.trade/user/margins", headers=headers)
        response.raise_for_status()
        margin_data = response.json()
    except Exception as e:
        error_message = str(e)
        try:
            if hasattr(e, "response") and e.response is not None:
                error_detail = e.response.json()
                error_message = error_detail.get("message", str(e))
        except Exception:
            pass
        logger.error("Error fetching margin data: %s", error_message)
        return {}

    if margin_data.get("status") == "error":
        logger.error("Error fetching margin data: %s", margin_data.get("errors"))
        return {}

    try:
        total_available_margin = sum([
            margin_data["data"]["commodity"]["net"],
            margin_data["data"]["equity"]["net"],
        ])
        total_used_margin = sum([
            margin_data["data"]["commodity"]["utilised"]["debits"],
            margin_data["data"]["equity"]["utilised"]["debits"],
        ])
        total_collateral = sum([
            margin_data["data"]["commodity"]["available"]["collateral"],
            margin_data["data"]["equity"]["available"]["collateral"],
        ])

        # Fetch PnL from position book
        total_realised = 0.0
        total_unrealised = 0.0
        try:
            pos_response = client.get(
                "https://api.kite.trade/portfolio/positions", headers=headers
            )
            pos_response.raise_for_status()
            position_book = pos_response.json()

            if position_book.get("status") == "success" and position_book.get("data"):
                net_positions = position_book["data"].get("net", [])
                open_positions = []

                for p in net_positions:
                    qty = p.get("quantity", 0)
                    if qty == 0:
                        total_realised += p.get("sell_value", 0) - p.get("buy_value", 0)
                    else:
                        open_positions.append(p)

                # Fetch live LTP for open positions
                if open_positions:
                    instruments = [f"{p['exchange']}:{p['tradingsymbol']}" for p in open_positions]
                    query = "&".join(f"i={inst}" for inst in instruments)
                    quote_response = client.get(
                        f"https://api.kite.trade/quote/ltp?{query}", headers=headers
                    )
                    quote_response.raise_for_status()
                    quote_data = quote_response.json()
                    ltp_map = {}
                    if quote_data.get("status") == "success" and quote_data.get("data"):
                        for key, val in quote_data["data"].items():
                            ltp_map[key] = val.get("last_price", 0)

                    for p in open_positions:
                        qty = p.get("quantity", 0)
                        avg_price = p.get("average_price", 0)
                        inst_key = f"{p['exchange']}:{p['tradingsymbol']}"
                        live_ltp = ltp_map.get(inst_key, p.get("last_price", 0))
                        total_unrealised += (live_ltp - avg_price) * qty

        except Exception as e:
            logger.error("Error fetching positions for PnL: %s", e)

        return {
            "availablecash": round(total_available_margin, 2),
            "collateral": round(total_collateral, 2),
            "m2munrealized": round(total_unrealised, 2),
            "m2mrealized": round(total_realised, 2),
            "utiliseddebits": round(total_used_margin, 2),
        }
    except KeyError:
        return {}
