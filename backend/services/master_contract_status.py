"""
In-memory master contract download status tracker.
"""

import time
import logging
from threading import Lock

logger = logging.getLogger(__name__)

_lock = Lock()
_status: dict = {
    "status": "idle",
    "message": "",
    "broker": None,
    "total_symbols": 0,
    "started_at": None,
    "completed_at": None,
    "duration_seconds": None,
}


def get_download_status() -> dict:
    with _lock:
        return dict(_status)


def set_downloading(broker: str):
    with _lock:
        _status.update({
            "status": "downloading",
            "message": f"Downloading master contracts for {broker}...",
            "broker": broker,
            "total_symbols": 0,
            "started_at": time.time(),
            "completed_at": None,
            "duration_seconds": None,
        })
    logger.info("Master contract status: downloading for %s", broker)


def set_success(broker: str, total_symbols: int):
    with _lock:
        started = _status.get("started_at")
        now = time.time()
        _status.update({
            "status": "success",
            "message": f"Downloaded {total_symbols} symbols for {broker}",
            "broker": broker,
            "total_symbols": total_symbols,
            "completed_at": now,
            "duration_seconds": round(now - started, 1) if started else None,
        })
    logger.info("Master contract status: success - %d symbols for %s", total_symbols, broker)


def set_error(broker: str, error_message: str):
    with _lock:
        started = _status.get("started_at")
        now = time.time()
        _status.update({
            "status": "error",
            "message": error_message,
            "broker": broker,
            "completed_at": now,
            "duration_seconds": round(now - started, 1) if started else None,
        })
    logger.info("Master contract status: error for %s - %s", broker, error_message)
