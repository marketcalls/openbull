"""Order dispatch — single decision point that routes to sandbox vs live.

Engine code (engine.py) calls into here for every order it places. Both paths
return the same ``(success, response_dict, status_code)`` tuple shape so the
engine doesn't branch on mode for anything else.

Live mode is wired for completeness but is **only invokable from a strategy
run whose `mode='live'`**. Phase 10 (last build phase) is when live is
end-to-end exercised; until then sandbox is the only mode the UI can pick.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from backend.services import sandbox_service

logger = logging.getLogger(__name__)


def dispatch_order(
    *,
    mode: str,
    user_id: int,
    order_data: dict[str, Any],
    auth_token: Optional[str] = None,
    broker: Optional[str] = None,
    config: Optional[dict[str, Any]] = None,
) -> tuple[bool, dict[str, Any], int]:
    """Place an order through the right pipe for the run's mode.

    Args:
        mode: ``'live'`` or ``'sandbox'``.
        user_id: owner of the strategy run.
        order_data: standard OpenAlgo order dict (``symbol``, ``exchange``,
            ``action``, ``quantity``, ``pricetype``, ``product``, ``price``,
            ``trigger_price``, ``strategy``).
        auth_token / broker / config: only required for ``mode='live'``.

    Returns:
        ``(success, response_data, http_status_code)``.
    """
    if mode == "sandbox":
        return sandbox_service.place_order(user_id, order_data)

    if mode == "live":
        if not (auth_token and broker):
            return False, {
                "status": "error",
                "message": "Live mode requires broker auth context",
            }, 400
        from backend.services.order_service import place_order_with_auth

        return place_order_with_auth(
            order_data, auth_token, broker, config, user_id=user_id,
        )

    return False, {
        "status": "error",
        "message": f"Unknown run mode: {mode}",
    }, 400
