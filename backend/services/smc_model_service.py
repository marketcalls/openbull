"""Serve the trained SMC win/loss classifier.

Loads models/smc_winrate.joblib (lazy, cached) and scores candles using the SAME
engine that trained it (smc_features), so features match exactly. Given a candle
series it returns the SMC signals with a calibrated P(win)."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[2]
_MODEL_PATH = _ROOT / "models" / "smc_winrate.joblib"
_META_PATH = _ROOT / "models" / "smc_winrate.meta.json"

_model = None
_meta: dict | None = None
_mtime: float | None = None
_lock = threading.Lock()


def _load():
    """Load the model, reloading automatically if the joblib file changed on
    disk (e.g. after a retrain) so a running backend serves the latest model
    without a restart."""
    global _model, _meta, _mtime
    if not _MODEL_PATH.exists():
        raise FileNotFoundError("SMC model not trained yet — run `python smc_train.py`.")
    disk_mtime = _MODEL_PATH.stat().st_mtime
    if _model is None or _mtime != disk_mtime:
        with _lock:
            disk_mtime = _MODEL_PATH.stat().st_mtime
            if _model is None or _mtime != disk_mtime:
                import joblib
                _model = joblib.load(_MODEL_PATH)
                _meta = json.loads(_META_PATH.read_text()) if _META_PATH.exists() else {}
                _mtime = disk_mtime
    return _model, _meta


def is_ready() -> bool:
    return _MODEL_PATH.exists()


def score_candles(candles: list[dict], rr: float | None = None, limit: int = 20) -> dict[str, Any]:
    """Run the SMC engine over `candles` and attach P(win) to each signal.
    Returns the most recent `limit` signals (newest first)."""
    from backend.services.smc_features import generate_smc_signals, feature_row

    model, meta = _load()
    use_rr = rr if rr is not None else float((meta or {}).get("rr", 2.0))
    sigs = generate_smc_signals(candles, rr=use_rr)
    if (meta or {}).get("long_only"):
        sigs = [s for s in sigs if s["direction"] == "long"]
    if not sigs:
        return {"status": "success", "signals": [], "meta": _public_meta(meta)}

    import numpy as np
    X = np.array([feature_row(s) for s in sigs], dtype=float)
    proba = model.predict_proba(X)[:, 1]
    out = []
    for s, p in zip(sigs, proba):
        out.append({
            "i": s["i"], "timestamp": s["timestamp"], "direction": s["direction"],
            "entry": s["entry"], "sl": s["sl"], "tp": s["tp"], "rr": s["rr"],
            "is_choch": s["is_choch"], "win_prob": round(float(p), 3),
        })
    out.sort(key=lambda x: -x["timestamp"])
    return {"status": "success", "signals": out[:limit], "count": len(sigs), "meta": _public_meta(meta)}


def _public_meta(meta: dict | None) -> dict:
    m = meta or {}
    mt = m.get("metrics", {})
    return {
        "trained_at": m.get("trained_at"),
        "base_win_rate": mt.get("base_win_rate"),
        "auc": mt.get("auc"),
        "n_total": mt.get("n_total"),
        "rr": m.get("rr"),
        "horizon": m.get("horizon"),
        "intraday": m.get("intraday"),
        "sources": m.get("sources"),
        "long_only": m.get("long_only"),
    }
