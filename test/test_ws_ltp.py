"""
Test WebSocket LTP streaming.
Usage: uv run python test/test_ws_ltp.py
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

        # Subscribe LTP
        await ws.send(json.dumps({
            "action": "subscribe",
            "symbols": INSTRUMENTS,
            "mode": "LTP",
        }))
        sub = json.loads(await ws.recv())
        print(f"Subscribed: {len(sub.get('subscriptions', []))} symbols in LTP mode\n")

        # Stream
        print(f"{'Symbol':30s} {'LTP':>10s} {'Change':>10s}")
        print("-" * 55)

        try:
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
                if msg.get("type") == "market_data":
                    d = msg["data"]
                    print(f"{msg['symbol']:30s} {d.get('ltp', 0):>10.2f} {d.get('change', 0):>10.2f}")
        except (asyncio.TimeoutError, KeyboardInterrupt):
            print("\nStopped.")


if __name__ == "__main__":
    asyncio.run(main())
