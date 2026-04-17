"""
Abstract base class for broker streaming adapters.
Each adapter connects to a broker's WebSocket, normalizes ticks,
and publishes them on a ZeroMQ PUB socket.
"""

import json
import logging
import socket as _socket
import threading
from abc import ABC, abstractmethod

import zmq

from backend.config import get_settings

logger = logging.getLogger(__name__)

# Modes matching OpenAlgo convention
MODE_LTP = 1
MODE_QUOTE = 2
MODE_DEPTH = 3

MODE_NAME = {MODE_LTP: "LTP", MODE_QUOTE: "QUOTE", MODE_DEPTH: "DEPTH"}


def _check_port_available(port: int) -> bool:
    """Return True if the TCP port is free to bind on 127.0.0.1."""
    try:
        with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
            s.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
            s.settimeout(1.0)
            s.bind(("127.0.0.1", port))
            return True
    except OSError:
        return False


class BaseBrokerAdapter(ABC):
    """Base class for broker WebSocket streaming adapters."""

    _bound_ports: set[int] = set()
    _port_lock = threading.Lock()

    def __init__(self, auth_token: str, broker_config: dict):
        self.auth_token = auth_token
        self.broker_config = broker_config
        self._zmq_context: zmq.Context | None = None
        self._zmq_socket: zmq.Socket | None = None
        self._zmq_port: int | None = None
        self._running = False

    @property
    def zmq_port(self) -> int | None:
        return self._zmq_port

    def setup_zmq(self) -> int:
        """Create a ZMQ PUB socket, bind to the configured ZMQ_PORT, return the port number."""
        settings = get_settings()
        port = settings.zmq_port

        with self._port_lock:
            if not _check_port_available(port):
                raise RuntimeError(
                    f"Port {port} is busy. Free it before starting the WebSocket proxy "
                    f"(set ZMQ_PORT in .env to use a different port)."
                )

            self._zmq_context = zmq.Context()
            self._zmq_socket = self._zmq_context.socket(zmq.PUB)
            self._zmq_socket.bind(f"tcp://127.0.0.1:{port}")
            self._bound_ports.add(port)

        self._zmq_port = port
        logger.info("ZMQ PUB bound on tcp://127.0.0.1:%d", port)
        return port

    def publish(self, topic: str, data: dict) -> None:
        """Publish a JSON message on the ZMQ PUB socket.

        Topic format: ``{EXCHANGE}_{SYMBOL}_{MODE}``
        e.g. ``NSE_RELIANCE_LTP``, ``NSE_INDEX_NIFTY_QUOTE``
        """
        if self._zmq_socket is None:
            return
        payload = json.dumps(data, separators=(",", ":"))
        self._zmq_socket.send_multipart([topic.encode(), payload.encode()])

    def cleanup_zmq(self) -> None:
        """Close ZMQ socket and context."""
        if self._zmq_socket:
            try:
                self._zmq_socket.close(linger=0)
            except Exception:
                pass
        if self._zmq_context:
            try:
                self._zmq_context.term()
            except Exception:
                pass
        with self._port_lock:
            if self._zmq_port:
                self._bound_ports.discard(self._zmq_port)
        self._zmq_port = None
        logger.info("ZMQ resources cleaned up")

    # -- abstract interface --

    @abstractmethod
    def connect(self) -> None:
        """Connect to the broker WebSocket (called from a background thread)."""

    @abstractmethod
    def subscribe(self, symbols: list[dict], mode: int) -> None:
        """Subscribe to symbols. Each item: {symbol, exchange}."""

    @abstractmethod
    def unsubscribe(self, symbols: list[dict], mode: int) -> None:
        """Unsubscribe from symbols."""

    @abstractmethod
    def disconnect(self) -> None:
        """Disconnect from broker WS and clean up."""
