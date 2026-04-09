"""Abstract base for broker plugins.
Each broker implements these functions at module level in its api/ subpackage.
This file documents the expected interface; broker modules are NOT required to
inherit from this class -- they just need to expose the same function signatures.
"""


def authenticate_broker(code_or_token: str, config: dict) -> tuple[str | None, str | None]:
    """Exchange OAuth code/request_token for access token.
    config: {"api_key", "api_secret", "redirect_url"}
    Returns: (access_token, error_message)
    """
    raise NotImplementedError


def get_margin_data(auth_token: str, config: dict) -> dict:
    """Fetch account funds/margin.
    Returns: {"availablecash": float, "collateral": float, "m2munrealized": float,
              "m2mrealized": float, "utiliseddebits": float}
    """
    raise NotImplementedError


def get_order_book(auth_token: str) -> list[dict]:
    raise NotImplementedError


def get_trade_book(auth_token: str) -> list[dict]:
    raise NotImplementedError


def get_positions(auth_token: str) -> list[dict]:
    raise NotImplementedError


def get_holdings(auth_token: str) -> list[dict]:
    raise NotImplementedError


def place_order(auth_token: str, order_data: dict, config: dict) -> dict:
    raise NotImplementedError


def modify_order(auth_token: str, order_data: dict, config: dict) -> dict:
    raise NotImplementedError


def cancel_order(auth_token: str, order_id: str, config: dict) -> dict:
    raise NotImplementedError
