"""
Test WebSocket Depth streaming (5-level bid/ask + OHLCV).
Usage: uv run python test/test_ws_depth.py
Press Ctrl+C to stop.
"""

import asyncio
import json
import websockets

API_KEY = "4368c7c1bba345b9d1f3e813ae86af2b111bc17efb49c5b28e935781f34adac6"
WS_URL = "ws://127.0.0.1:8765"

INSTRUMENTS = [
    {"symbol": "CRUDEOIL20APR26FUT", "exchange": "MCX"},
]


def print_depth(symbol, data):
    depth = data.get("depth", {})
    bids = depth.get("buy", [])
    asks = depth.get("sell", [])

    print(f"\n{'=' * 70}")
    print(f"  {symbol}  LTP: {data.get('ltp')}  Vol: {data.get('volume')}  OI: {data.get('oi')}")
    print(f"{'=' * 70}")
    print(f"  {'BID':^30s}  |  {'ASK':^30s}")
    print(f"  {'Qty':>8s} {'Price':>10s} {'Orders':>8s}  |  {'Price':>10s} {'Qty':>8s} {'Orders':>8s}")
    print(f"  {'-' * 30}  |  {'-' * 30}")

    for i in range(5):
        bid = bids[i] if i < len(bids) else {}
        ask = asks[i] if i < len(asks) else {}
        print(
            f"  {bid.get('quantity', 0):>8} {bid.get('price', 0):>10.2f} {bid.get('orders', 0):>8}  |  "
            f"{ask.get('price', 0):>10.2f} {ask.get('quantity', 0):>8} {ask.get('orders', 0):>8}"
        )


async def main():
    try:
        async with websockets.connect(WS_URL) as ws:
            # Authenticate
            await ws.send(json.dumps({"action": "authenticate", "api_key": API_KEY}))
            auth = json.loads(await ws.recv())
            print(f"Auth: {auth['status']} | Broker: {auth.get('broker')}")

            # Subscribe Depth
            await ws.send(json.dumps({
                "action": "subscribe",
                "symbols": INSTRUMENTS,
                "mode": "Depth",
            }))
            sub = json.loads(await ws.recv())
            print(f"Subscribed: {len(sub.get('subscriptions', []))} symbols in Depth mode")

            # Stream
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
                if msg.get("type") == "market_data" and msg["data"].get("mode") == "full":
                    print_depth(msg["symbol"], msg["data"])

    except (asyncio.TimeoutError, asyncio.CancelledError):
        print("\nNo data received (timeout). Stopped.")
    except websockets.ConnectionClosed:
        print("\nConnection closed.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")
