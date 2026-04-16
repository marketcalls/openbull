"""
Test WebSocket Quote streaming (OHLCV + OI).
Usage: uv run python test/test_ws_quote.py
"""

import asyncio
import json
import websockets

API_KEY = "4368c7c1bba345b9d1f3e813ae86af2b111bc17efb49c5b28e935781f34adac6"
WS_URL = "ws://127.0.0.1:8765"

INSTRUMENTS = [
    {"symbol": "CRUDEOIL20APR26FUT", "exchange": "MCX"},
    {"symbol": "NIFTY", "exchange": "NSE_INDEX"},
    {"symbol": "INFY", "exchange": "NSE"},
]


async def main():
    async with websockets.connect(WS_URL) as ws:
        # Authenticate
        await ws.send(json.dumps({"action": "authenticate", "api_key": API_KEY}))
        auth = json.loads(await ws.recv())
        print(f"Auth: {auth['status']} | Broker: {auth.get('broker')}")

        # Subscribe Quote
        await ws.send(json.dumps({
            "action": "subscribe",
            "symbols": INSTRUMENTS,
            "mode": "Quote",
        }))
        sub = json.loads(await ws.recv())
        print(f"Subscribed: {len(sub.get('subscriptions', []))} symbols in Quote mode\n")

        # Stream
        header = f"{'Symbol':30s} {'LTP':>10s} {'Open':>10s} {'High':>10s} {'Low':>10s} {'Volume':>10s} {'OI':>10s}"
        print(header)
        print("-" * len(header))

        try:
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
                if msg.get("type") == "market_data" and msg["data"].get("mode") == "quote":
                    d = msg["data"]
                    print(
                        f"{msg['symbol']:30s} "
                        f"{d.get('ltp', 0):>10.2f} "
                        f"{d.get('open', 0):>10.2f} "
                        f"{d.get('high', 0):>10.2f} "
                        f"{d.get('low', 0):>10.2f} "
                        f"{d.get('volume', 0):>10} "
                        f"{d.get('oi', 0):>10}"
                    )
        except (asyncio.TimeoutError, KeyboardInterrupt):
            print("\nStopped.")


if __name__ == "__main__":
    asyncio.run(main())
