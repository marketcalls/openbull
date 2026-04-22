"""
WebSocket proxy server.

Accepts client WS connections on WEBSOCKET_PORT, authenticates via API key,
manages symbol subscriptions, and routes ZeroMQ market data to subscribers.

Client protocol (JSON over WebSocket):
  -> {"action":"authenticate","api_key":"..."}
  <- {"type":"auth","status":"success","broker":"upstox"}

  -> {"action":"subscribe","symbols":[{"symbol":"NIFTY","exchange":"NSE_INDEX"}],"mode":"Quote"}
  <- {"type":"subscribe","status":"success","subscriptions":[...]}

  <- {"type":"market_data","symbol":"NIFTY","exchange":"NSE_INDEX","mode":"quote","data":{...}}

  -> {"action":"unsubscribe","symbols":[...],"mode":"Quote"}
  <- {"type":"unsubscribe","status":"success"}
"""

import asyncio
import json
import logging
import time
import uuid
from typing import Any

import websockets
import zmq
import zmq.asyncio

from backend.websocket_proxy.auth import verify_api_key_standalone
from backend.websocket_proxy.base_adapter import (
    BaseBrokerAdapter,
    MODE_DEPTH,
    MODE_LTP,
    MODE_NAME,
    MODE_QUOTE,
)

logger = logging.getLogger("ws_proxy")

MODE_MAP = {"LTP": MODE_LTP, "QUOTE": MODE_QUOTE, "DEPTH": MODE_DEPTH, "FULL": MODE_DEPTH}

# -- module-level state (single-user) --
_adapter: BaseBrokerAdapter | None = None
_adapter_lock = asyncio.Lock()
_clients: dict[str, websockets.WebSocketServerProtocol] = {}
_authenticated_clients: set[str] = set()  # client_ids that have authenticated
_subscription_index: dict[tuple[str, str, int], set[str]] = {}  # (symbol, exchange, mode) -> client_ids
_last_send_time: dict[tuple[str, str], float] = {}  # (symbol, exchange) -> timestamp
_server: websockets.WebSocketServer | None = None
_zmq_task: asyncio.Task | None = None

LTP_THROTTLE_SEC = 0.05  # 50ms
MAX_WS_CONNECTIONS = 10
MAX_MESSAGE_SIZE = 65536  # 64KB
MAX_SYMBOLS_PER_SUBSCRIBE = 1000


def _create_adapter(broker_name: str, auth_token: str, config: dict) -> BaseBrokerAdapter:
    if broker_name == "upstox":
        from backend.broker.upstox.streaming.upstox_adapter import UpstoxAdapter
        return UpstoxAdapter(auth_token, config)
    elif broker_name == "zerodha":
        from backend.broker.zerodha.streaming.zerodha_adapter import ZerodhaAdapter
        return ZerodhaAdapter(auth_token, config)
    raise ValueError(f"No streaming adapter for broker: {broker_name}")


async def _send_json(ws, data: dict) -> None:
    try:
        await ws.send(json.dumps(data))
    except websockets.ConnectionClosed:
        pass


async def _broadcast(client_ids: set[str], message: dict) -> None:
    """Send message to multiple clients in parallel."""
    tasks = []
    for cid in client_ids:
        ws = _clients.get(cid)
        if ws:
            tasks.append(_send_json(ws, message))
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


# ---- ZeroMQ listener ----

async def _zmq_listener(zmq_port: int) -> None:
    """Subscribe to the adapter's ZMQ PUB and route messages to WS clients."""
    ctx = zmq.asyncio.Context()
    sub = ctx.socket(zmq.SUB)
    sub.connect(f"tcp://127.0.0.1:{zmq_port}")
    sub.setsockopt(zmq.SUBSCRIBE, b"")
    logger.info("ZMQ SUB connected to tcp://127.0.0.1:%d", zmq_port)

    try:
        while True:
            try:
                parts = await asyncio.wait_for(sub.recv_multipart(), timeout=0.3)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            if len(parts) != 2:
                continue

            topic_str = parts[0].decode()
            data_str = parts[1].decode()

            # Parse topic: EXCHANGE_SYMBOL_MODE
            # Handle compound exchanges like NSE_INDEX
            segments = topic_str.split("_")
            if len(segments) < 3:
                continue

            mode_str = segments[-1]  # LTP / QUOTE / DEPTH
            mode = MODE_MAP.get(mode_str)
            if mode is None:
                continue

            # Exchange can be 1 or 2 segments (NSE vs NSE_INDEX)
            if len(segments) >= 4 and f"{segments[0]}_{segments[1]}" in (
                "NSE_INDEX", "BSE_INDEX", "MCX_INDEX",
            ):
                exchange = f"{segments[0]}_{segments[1]}"
                symbol = "_".join(segments[2:-1])
            else:
                exchange = segments[0]
                symbol = "_".join(segments[1:-1])

            # LTP throttle
            now = time.monotonic()
            if mode == MODE_LTP:
                key = (symbol, exchange)
                last = _last_send_time.get(key, 0)
                if now - last < LTP_THROTTLE_SEC:
                    continue
                _last_send_time[key] = now

            try:
                market_data = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            # Route to subscribers — mode hierarchy: DEPTH includes QUOTE includes LTP
            all_clients: dict[str, int] = {}
            for m in range(1, mode + 1):
                for cid in _subscription_index.get((symbol, exchange, m), set()):
                    all_clients[cid] = m

            if not all_clients:
                continue

            mode_name = market_data.get("mode", MODE_NAME.get(mode, "ltp").lower())
            message = {
                "type": "market_data",
                "symbol": symbol,
                "exchange": exchange,
                "mode": mode_name,
                "data": market_data,
            }
            await _broadcast(set(all_clients.keys()), message)

    finally:
        sub.close(linger=0)
        ctx.term()
        logger.info("ZMQ listener shut down")


# ---- Client handler ----

async def _handle_client(ws: websockets.WebSocketServerProtocol) -> None:
    global _adapter, _zmq_task

    # Enforce connection limit
    if len(_clients) >= MAX_WS_CONNECTIONS:
        await _send_json(ws, {"type": "error", "message": "Max connections reached"})
        await ws.close()
        return

    client_id = str(uuid.uuid4())
    _clients[client_id] = ws
    logger.info("Client %s connected (%d total)", client_id[:8], len(_clients))

    broker_name: str | None = None

    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await _send_json(ws, {"type": "error", "message": "Invalid JSON"})
                continue

            action = msg.get("action")

            # -- authenticate --
            if action == "authenticate":
                api_key = msg.get("api_key")
                if not api_key:
                    await _send_json(ws, {"type": "auth", "status": "error", "message": "api_key required"})
                    continue

                try:
                    user_id, auth_token, bname, config = await verify_api_key_standalone(api_key)
                except ValueError:
                    await _send_json(ws, {"type": "auth", "status": "error", "message": "Authentication failed"})
                    continue

                broker_name = bname
                _authenticated_clients.add(client_id)

                # Create adapter if needed (single-user: one adapter at a time)
                async with _adapter_lock:
                    if _adapter is None:
                        try:
                            _adapter = _create_adapter(broker_name, auth_token, config)
                            zmq_port = _adapter.setup_zmq()

                            # Connect adapter in a background thread (it blocks)
                            loop = asyncio.get_running_loop()
                            await loop.run_in_executor(None, _adapter.connect)

                            # Start ZMQ listener
                            _zmq_task = asyncio.create_task(_zmq_listener(zmq_port))
                            logger.info("Adapter %s created and connected (ZMQ port %d)", broker_name, zmq_port)
                        except Exception as e:
                            logger.exception("Failed to create adapter: %s", e)
                            # Release any resources the partial setup acquired
                            # (ZMQ PUB socket/port + broker WS), otherwise the
                            # next auth attempt fails with "Port is busy".
                            if _adapter is not None:
                                try:
                                    _adapter.disconnect()
                                except Exception:
                                    pass
                                try:
                                    _adapter.cleanup_zmq()
                                except Exception:
                                    pass
                            _adapter = None
                            await _send_json(ws, {"type": "auth", "status": "error", "message": "Broker connection failed"})
                            continue

                await _send_json(ws, {
                    "type": "auth",
                    "status": "success",
                    "broker": broker_name,
                })

            # -- subscribe --
            elif action == "subscribe":
                if _adapter is None or client_id not in _authenticated_clients:
                    await _send_json(ws, {"type": "subscribe", "status": "error", "message": "Not authenticated"})
                    continue

                symbols = msg.get("symbols", [])
                if not isinstance(symbols, list) or len(symbols) > MAX_SYMBOLS_PER_SUBSCRIBE:
                    await _send_json(ws, {"type": "subscribe", "status": "error", "message": f"symbols must be a list (max {MAX_SYMBOLS_PER_SUBSCRIBE})"})
                    continue
                mode = MODE_MAP.get(msg.get("mode", "LTP").upper(), MODE_LTP)

                for item in symbols:
                    sym, exch = item.get("symbol"), item.get("exchange")
                    if sym and exch:
                        _subscription_index.setdefault((sym, exch, mode), set()).add(client_id)

                # Forward to broker adapter (in thread — adapter methods may block)
                loop = asyncio.get_running_loop()
                try:
                    await loop.run_in_executor(None, _adapter.subscribe, symbols, mode)
                except Exception as e:
                    logger.error("Subscribe error: %s", e)

                await _send_json(ws, {
                    "type": "subscribe",
                    "status": "success",
                    "subscriptions": [
                        {"symbol": s["symbol"], "exchange": s["exchange"], "mode": MODE_NAME.get(mode)}
                        for s in symbols if s.get("symbol") and s.get("exchange")
                    ],
                })

            # -- unsubscribe --
            elif action == "unsubscribe":
                if _adapter is None or client_id not in _authenticated_clients:
                    await _send_json(ws, {"type": "unsubscribe", "status": "error", "message": "Not authenticated"})
                    continue

                symbols = msg.get("symbols", [])
                mode = MODE_MAP.get(msg.get("mode", "LTP").upper(), MODE_LTP)

                symbols_to_unsub = []
                for item in symbols:
                    sym, exch = item.get("symbol"), item.get("exchange")
                    if sym and exch:
                        subs = _subscription_index.get((sym, exch, mode))
                        if subs:
                            subs.discard(client_id)
                            if not subs:
                                del _subscription_index[(sym, exch, mode)]
                                symbols_to_unsub.append(item)

                if symbols_to_unsub:
                    loop = asyncio.get_running_loop()
                    try:
                        await loop.run_in_executor(None, _adapter.unsubscribe, symbols_to_unsub, mode)
                    except Exception as e:
                        logger.error("Unsubscribe error: %s", e)

                await _send_json(ws, {"type": "unsubscribe", "status": "success"})

            else:
                await _send_json(ws, {"type": "error", "message": f"Unknown action: {action}"})

    except websockets.ConnectionClosed:
        pass
    finally:
        # Cleanup client subscriptions and auth state
        _clients.pop(client_id, None)
        _authenticated_clients.discard(client_id)
        for key in list(_subscription_index.keys()):
            subs = _subscription_index.get(key)
            if subs:
                subs.discard(client_id)
                if not subs:
                    del _subscription_index[key]
        logger.info("Client %s disconnected (%d remaining)", client_id[:8], len(_clients))


# ---- Public API ----

async def start_ws_proxy(host: str, port: int) -> None:
    """Start the WebSocket proxy server (runs forever until cancelled)."""
    global _server
    _server = await websockets.serve(
        _handle_client, host, port,
        ping_interval=30,
        ping_timeout=10,
        max_size=MAX_MESSAGE_SIZE,
    )
    logger.info("WebSocket proxy listening on ws://%s:%d", host, port)
    await asyncio.Future()  # run forever


async def shutdown_ws_proxy() -> None:
    """Gracefully shut down the proxy, adapter, and ZMQ listener."""
    global _adapter, _zmq_task, _server

    if _zmq_task:
        _zmq_task.cancel()
        try:
            await _zmq_task
        except asyncio.CancelledError:
            pass
        _zmq_task = None

    if _adapter:
        try:
            _adapter.disconnect()
        except Exception:
            pass
        _adapter.cleanup_zmq()
        _adapter = None

    if _server:
        _server.close()
        await _server.wait_closed()
        _server = None

    _clients.clear()
    _authenticated_clients.clear()
    _subscription_index.clear()
    _last_send_time.clear()
    logger.info("WebSocket proxy shut down")
