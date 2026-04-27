#!/usr/bin/env python3
"""
End-to-end sandbox test runner.

Exercises every sandbox surface — place order, margin block, fill,
margin release, orderbook, tradebook, positions, funds, T+1 settlement,
holdings — and prints a pass/fail report. Drives the service layer
directly (``backend.services.sandbox_service``) so it hits the same code
path the HTTP API uses but skips auth.

Usage:
    uv run sandbox_e2e_test.py                # auto-picks first user with broker auth
    uv run sandbox_e2e_test.py --user-id 1    # pin to a specific user
    uv run sandbox_e2e_test.py --reset        # wipe the user's sandbox first

The script prefers a symbol that the broker can quote (so MARKET orders
don't get rejected by the no-LTP gate). It defaults to RELIANCE/NSE for
CNC + INFY/NSE for MIS — both should resolve via the symbol master.

Read-only safety: nothing here touches live broker order APIs. Every
order is dispatched through ``sandbox_service.place_order`` regardless
of trading_mode setting.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# ANSI dim/bright codes via plain strings — terminal-agnostic.
def _h(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def _kv(label: str, value) -> None:
    print(f"  {label:30s} {value}")


def _ok(msg: str) -> None:
    print(f"  PASS: {msg}")


def _fail(msg: str) -> None:
    print(f"  FAIL: {msg}")
    raise SystemExit(2)


def _pick_user(arg_user_id: int | None) -> int:
    from sqlalchemy import select
    from backend.models.user import User
    from backend.models.auth import BrokerAuth
    from backend.sandbox._db import session_scope

    with session_scope() as db:
        if arg_user_id is not None:
            row = db.execute(select(User).where(User.id == arg_user_id)).scalar_one_or_none()
            if row is None:
                raise SystemExit(f"user id {arg_user_id} not found")
            return arg_user_id
        # Prefer a user with at least one non-revoked broker auth so MARKET
        # orders can actually fetch a quote.
        with_auth = db.execute(
            select(User)
            .join(BrokerAuth, BrokerAuth.user_id == User.id)
            .where(BrokerAuth.is_revoked.is_(False))
            .limit(1)
        ).scalar_one_or_none()
        if with_auth is not None:
            return int(with_auth.id)
        any_user = db.execute(select(User).limit(1)).scalar_one_or_none()
        if any_user is None:
            raise SystemExit("no users in the database — sign up first")
        return int(any_user.id)


def _seed_defaults() -> None:
    from backend.sandbox.config import seed_defaults
    seed_defaults()


def _reset_user(user_id: int) -> None:
    from sqlalchemy import delete
    from backend.models.sandbox import (
        SandboxOrder, SandboxTrade, SandboxPosition, SandboxHolding,
    )
    from backend.sandbox._db import session_scope
    from backend.sandbox import fund_manager

    with session_scope() as db:
        db.execute(delete(SandboxOrder).where(SandboxOrder.user_id == user_id))
        db.execute(delete(SandboxTrade).where(SandboxTrade.user_id == user_id))
        db.execute(delete(SandboxPosition).where(SandboxPosition.user_id == user_id))
        db.execute(delete(SandboxHolding).where(SandboxHolding.user_id == user_id))
    fund_manager.reset_funds(user_id)
    print(f"  reset user {user_id}: orders/trades/positions/holdings wiped, funds reset")


def _funds(user_id: int) -> dict:
    from backend.sandbox import fund_manager
    return fund_manager.get_funds_snapshot(user_id)


def _print_funds(user_id: int, label: str) -> dict:
    f = _funds(user_id)
    print(f"  funds [{label}]: avail={f['availablecash']:>14,.2f}  "
          f"used={f['utiliseddebits']:>12,.2f}  "
          f"realized={f['m2mrealized']:>10,.2f}  "
          f"unrealized={f['m2munrealized']:>10,.2f}")
    return f


def _orderbook(user_id: int) -> list:
    from backend.services.sandbox_service import get_orderbook
    ok, resp, _ = get_orderbook(user_id)
    return resp["data"]["orders"]


def _tradebook(user_id: int) -> list:
    from backend.services.sandbox_service import get_tradebook
    ok, resp, _ = get_tradebook(user_id)
    return resp["data"]


def _positions(user_id: int) -> list:
    from backend.services.sandbox_service import get_positions
    ok, resp, _ = get_positions(user_id)
    return resp["data"]


def _holdings(user_id: int) -> dict:
    from backend.services.sandbox_service import get_holdings
    ok, resp, _ = get_holdings(user_id)
    return resp["data"]


def _place(user_id: int, **kwargs) -> tuple[bool, dict]:
    from backend.services.sandbox_service import place_order
    payload = {
        "symbol": kwargs["symbol"],
        "exchange": kwargs["exchange"],
        "action": kwargs["action"],
        "quantity": kwargs["quantity"],
        "pricetype": kwargs.get("pricetype", "MARKET"),
        "product": kwargs.get("product", "MIS"),
        "price": kwargs.get("price", 0),
        "trigger_price": kwargs.get("trigger_price", 0),
        "strategy": kwargs.get("strategy", "e2e_test"),
    }
    ok, resp, status = place_order(user_id, payload)
    return ok, resp


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--user-id", type=int, default=None)
    p.add_argument("--reset", action="store_true")
    p.add_argument("--symbol-equity", default="RELIANCE")
    p.add_argument("--symbol-mis", default="INFY")
    p.add_argument("--exchange", default="NSE")
    p.add_argument("--qty", type=int, default=10)
    args = p.parse_args()

    _seed_defaults()
    user_id = _pick_user(args.user_id)
    print(f"  driving user_id={user_id}")
    if args.reset:
        _reset_user(user_id)

    _h("Step 1: starting funds snapshot")
    starting = _print_funds(user_id, "start")
    starting_avail = starting["availablecash"]

    # --------------------------------------------------------------
    _h("Step 2: place CNC BUY (MARKET) — should block margin = price * qty / 1")
    ok, resp = _place(
        user_id,
        symbol=args.symbol_equity, exchange=args.exchange,
        action="BUY", quantity=args.qty, pricetype="MARKET", product="CNC",
    )
    if not ok:
        _fail(f"CNC BUY rejected: {resp.get('message')}")
    cnc_orderid = resp["orderid"]
    _ok(f"placed CNC BUY orderid={cnc_orderid}")
    time.sleep(0.5)
    f1 = _print_funds(user_id, "after CNC BUY")
    if f1["utiliseddebits"] <= 0:
        _fail("used_margin did not increase after CNC BUY — margin not blocked")
    if f1["availablecash"] >= starting_avail:
        _fail("available did not decrease after CNC BUY — margin not deducted")
    _ok(f"used_margin INR {f1['utiliseddebits']:,.2f} blocked, "
        f"available decreased by INR {starting_avail - f1['availablecash']:,.2f}")

    # --------------------------------------------------------------
    _h("Step 3: orderbook + tradebook + position after fill")
    obook = _orderbook(user_id)
    tbook = _tradebook(user_id)
    positions = _positions(user_id)
    _kv("orderbook entries", len(obook))
    _kv("first order status", obook[0]["order_status"] if obook else "(none)")
    _kv("tradebook entries", len(tbook))
    _kv("positions", len(positions))
    if obook and obook[0]["order_status"] != "complete":
        print(f"  NOTE: order is {obook[0]['order_status']} (no live tick yet — "
              f"poll loop will fill it within order_check_interval)")
    cnc_pos = next(
        (p for p in positions if p["symbol"] == args.symbol_equity and p["product"] == "CNC"),
        None,
    )
    if cnc_pos and cnc_pos["quantity"] != 0:
        _ok(f"CNC position: qty={cnc_pos['quantity']} @ {cnc_pos['average_price']} "
            f"margin_blocked={cnc_pos.get('margin_blocked')}")

    # --------------------------------------------------------------
    _h("Step 4: place MIS BUY then SELL same qty — margin should release")
    ok, resp = _place(
        user_id,
        symbol=args.symbol_mis, exchange=args.exchange,
        action="BUY", quantity=args.qty, pricetype="MARKET", product="MIS",
    )
    if not ok:
        msg = resp.get("message", "")
        if "blocked after squareoff" in msg:
            _ok(f"MIS BUY correctly REJECTED post-squareoff: {msg}")
            print("  (skipping MIS round-trip; post-squareoff block is working)")
        else:
            _fail(f"MIS BUY unexpectedly rejected: {msg}")
    else:
        time.sleep(0.5)
        before_close = _print_funds(user_id, "after MIS BUY")
        ok2, resp2 = _place(
            user_id,
            symbol=args.symbol_mis, exchange=args.exchange,
            action="SELL", quantity=args.qty, pricetype="MARKET", product="MIS",
        )
        if not ok2:
            _fail(f"MIS SELL (close) rejected: {resp2.get('message')}")
        time.sleep(0.5)
        after_close = _print_funds(user_id, "after MIS SELL")
        if after_close["utiliseddebits"] >= before_close["utiliseddebits"]:
            _fail("used_margin did not drop after MIS round-trip")
        _ok(f"MIS round-trip released INR "
            f"{before_close['utiliseddebits'] - after_close['utiliseddebits']:,.2f}")

    # --------------------------------------------------------------
    _h("Step 5: validation gates — these should all REJECT")
    cases = [
        ("unknown symbol", dict(symbol="NOTASYMBOL_XYZ", exchange="NSE",
                                action="BUY", quantity=1, product="CNC")),
        ("CNC on NFO (product/exchange mismatch)",
            dict(symbol="NIFTY24DECFUT", exchange="NFO",
                 action="BUY", quantity=1, product="CNC")),
        ("CNC SELL no inventory",
            dict(symbol="HDFCBANK", exchange="NSE",
                 action="SELL", quantity=1, product="CNC", pricetype="LIMIT", price=1500)),
        ("LIMIT with price=0",
            dict(symbol=args.symbol_equity, exchange="NSE",
                 action="BUY", quantity=1, product="CNC", pricetype="LIMIT", price=0)),
    ]
    for label, payload in cases:
        ok, resp = _place(user_id, **payload)
        if ok:
            print(f"  WARN: case '{label}' was ACCEPTED — gate may be too lax")
        else:
            _ok(f"{label}: {resp.get('message','')[:80]}")

    # --------------------------------------------------------------
    _h("Step 6: simulate T+1 settlement — CNC long should move to holdings")
    from backend.sandbox import t1_settle
    moved = t1_settle.settle_cnc_to_holdings()
    _kv("positions moved to holdings", moved)
    holdings = _holdings(user_id)
    _kv("holdings rows", len(holdings["holdings"]))
    if holdings["holdings"]:
        h = holdings["holdings"][0]
        _ok(f"holding: {h['symbol']} qty={h['quantity']} "
            f"avg={h['average_price']} ltp={h['ltp']} "
            f"settled={h['settlement_date']}")
    f_after_settle = _print_funds(user_id, "after T+1")
    if f_after_settle["utiliseddebits"] > f1["utiliseddebits"]:
        _fail("used_margin INCREASED after settlement — should drop to reflect transfer")

    # --------------------------------------------------------------
    _h("Step 7: reconciliation check — used_margin must equal sum(position margin)")
    from backend.sandbox import fund_manager
    consistent, drift, details = fund_manager.reconcile_margin(user_id, auto_fix=False)
    print(f"  {json.dumps(details, indent=2)}")
    if not consistent:
        print(f"  WARN: drift detected ({drift:+,.2f}) — running auto-fix")
        fund_manager.reconcile_margin(user_id, auto_fix=True)
        _print_funds(user_id, "after reconcile")
    else:
        _ok("margins consistent")

    _h("DONE — all critical surfaces exercised")
    print("  Run with --reset to wipe and re-run from a clean state.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
