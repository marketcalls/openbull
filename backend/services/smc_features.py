"""Shared SMC signal + feature engine (pure, OHLCV-only).

Single source of truth used by BOTH the training pipeline (smc_train.py, which
labels historical Databricks candles) and the serving endpoint (smc_model_service),
so the features a signal is scored on at inference exactly match those it was
trained on.

Pipeline:
  generate_smc_signals(candles) -> [signal{...features}]   # structure → BOS/CHoCH setups
  label_signal(candles, sig)    -> 1 win | 0 loss | None    # forward-walk TP-before-SL
  feature_row(sig)              -> [float,...]  (FEATURE_KEYS order)

A "signal" fires when price breaks the last confirmed swing (BOS in-trend or
CHoCH counter-trend). entry = break close, SL = last opposing swing, TP = RR×risk.
Labels come from walking subsequent candles: TP hit first = win, SL first = loss
(ambiguous same-bar = loss, conservative), neither within horizon = undecided.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))

SWING_K = 2          # fractal half-width (5-bar swing)
DEFAULT_RR = 2.0     # take-profit at RR × risk
LABEL_HORIZON = 48   # bars to resolve TP/SL (e.g. 48×5m ≈ 4h)

# Ordered feature vector — training and serving MUST use this exact order.
FEATURE_KEYS = [
    "is_choch", "dir_long", "break_strength_atr", "risk_atr", "rr",
    "trend_run", "ema_slope", "prem_disc", "swing_range_pct", "atr_pct",
    "rsi14", "ret3", "ret6", "ret12", "body_ratio", "upper_wick", "lower_wick",
    "rvol", "tod", "dow",
]


def _ema(vals: list[float], p: int) -> list[float]:
    out = [float("nan")] * len(vals)
    if not vals:
        return out
    k = 2 / (p + 1)
    prev = vals[0]
    out[0] = prev
    for i in range(1, len(vals)):
        prev = vals[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def _rsi(closes: list[float], p: int = 14) -> list[float]:
    out = [50.0] * len(closes)
    if len(closes) <= p:
        return out
    gains = losses = 0.0
    for i in range(1, p + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0); losses += max(-d, 0)
    ag, al = gains / p, losses / p
    for i in range(p + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (p - 1) + max(d, 0)) / p
        al = (al * (p - 1) + max(-d, 0)) / p
        rs = ag / al if al > 1e-12 else 999.0
        out[i] = 100 - 100 / (1 + rs)
    return out


def _atr(cs: list[dict], p: int = 14) -> list[float]:
    out = [0.0] * len(cs)
    trs = [0.0] * len(cs)
    for i in range(len(cs)):
        if i == 0:
            trs[i] = cs[i]["high"] - cs[i]["low"]
        else:
            pc = cs[i - 1]["close"]
            trs[i] = max(cs[i]["high"] - cs[i]["low"], abs(cs[i]["high"] - pc), abs(cs[i]["low"] - pc))
    run = 0.0
    for i in range(len(cs)):
        if i < p:
            run += trs[i]
            out[i] = run / (i + 1)
        elif i == p:
            out[i] = sum(trs[:p]) / p
        else:
            out[i] = (out[i - 1] * (p - 1) + trs[i]) / p
    return out


def _swings(cs: list[dict], i: int, k: int = SWING_K):
    """Return (is_swing_high, is_swing_low) for bar i using a k-bar fractal.
    Confirmed only when i has k bars on each side (no lookahead at signal time
    beyond the fractal itself, which is inherent to swing detection)."""
    if i < k or i + k >= len(cs):
        return False, False
    h, l = cs[i]["high"], cs[i]["low"]
    sh = all(h >= cs[i + j]["high"] for j in range(-k, k + 1) if j != 0)
    sl = all(l <= cs[i + j]["low"] for j in range(-k, k + 1) if j != 0)
    return sh, sl


def generate_smc_signals(candles: list[dict], rr: float = DEFAULT_RR) -> list[dict]:
    """Detect BOS/CHoCH setups over the candle series and attach features."""
    cs = candles
    n = len(cs)
    if n < 30:
        return []
    closes = [c["close"] for c in cs]
    ema20 = _ema(closes, 20)
    ema50 = _ema(closes, 50)
    rsi = _rsi(closes, 14)
    atr = _atr(cs, 14)
    vols = [c.get("volume", 0) or 0 for c in cs]

    signals: list[dict] = []
    last_sh = last_sl = None            # price of last confirmed swing high/low
    last_sh_i = last_sl_i = None
    trend = 0                           # +1 up, -1 down (from last BOS)
    trend_run = 0                       # consecutive same-dir breaks

    for i in range(SWING_K, n - SWING_K):
        sh, sl = _swings(cs, i, SWING_K)
        if sh:
            last_sh, last_sh_i = cs[i]["high"], i
        if sl:
            last_sl, last_sl_i = cs[i]["low"], i

        c = cs[i]["close"]
        atri = atr[i] if atr[i] > 1e-9 else max(1e-9, c * 0.001)

        # Bullish break of last swing high
        if last_sh is not None and c > last_sh and last_sl is not None:
            is_choch = 1.0 if trend < 0 else 0.0
            entry = c
            sl_px = last_sl
            risk = entry - sl_px
            if risk > atri * 0.2:  # ignore micro/zero-risk setups
                sig = _build_signal(cs, i, "long", entry, sl_px, entry + rr * risk, rr,
                                    is_choch, last_sh, last_sl, ema20, ema50, rsi, atr, vols, trend_run)
                signals.append(sig)
                trend_run = trend_run + 1 if trend >= 0 else 1
                trend = 1
                last_sh = None  # consume the level

        # Bearish break of last swing low
        elif last_sl is not None and c < last_sl and last_sh is not None:
            is_choch = 1.0 if trend > 0 else 0.0
            entry = c
            sl_px = last_sh
            risk = sl_px - entry
            if risk > atri * 0.2:
                sig = _build_signal(cs, i, "short", entry, sl_px, entry - rr * risk, rr,
                                    is_choch, last_sh, last_sl, ema20, ema50, rsi, atr, vols, trend_run)
                signals.append(sig)
                trend_run = trend_run + 1 if trend <= 0 else 1
                trend = -1
                last_sl = None
    return signals


def _build_signal(cs, i, direction, entry, sl_px, tp_px, rr, is_choch,
                  last_sh, last_sl, ema20, ema50, rsi, atr, vols, trend_run) -> dict:
    c = cs[i]
    atri = atr[i] if atr[i] > 1e-9 else max(1e-9, c["close"] * 0.001)
    rng = c["high"] - c["low"] or 1e-9
    body = abs(c["close"] - c["open"])
    upper = c["high"] - max(c["close"], c["open"])
    lower = min(c["close"], c["open"]) - c["low"]
    swing_hi = last_sh if last_sh is not None else c["high"]
    swing_lo = last_sl if last_sl is not None else c["low"]
    mid = (swing_hi + swing_lo) / 2
    half = (swing_hi - swing_lo) / 2 or 1e-9
    broken = swing_hi if direction == "long" else swing_lo

    def _ret(k):
        j = i - k
        return (c["close"] / cs[j]["close"] - 1) if j >= 0 and cs[j]["close"] else 0.0

    # relative volume vs trailing 20
    lo = max(0, i - 20)
    vwin = vols[lo:i]
    avgv = (sum(vwin) / len(vwin)) if vwin else 0.0
    rvol = (vols[i] / avgv) if avgv > 0 else 0.0

    dt = datetime.fromtimestamp(c["timestamp"], IST)
    feats = {
        "is_choch": is_choch,
        "dir_long": 1.0 if direction == "long" else 0.0,
        "break_strength_atr": abs(c["close"] - broken) / atri,
        "risk_atr": abs(entry - sl_px) / atri,
        "rr": rr,
        "trend_run": float(min(trend_run, 10)),
        "ema_slope": (ema20[i] - ema50[i]) / c["close"] if c["close"] else 0.0,
        "prem_disc": _clamp((c["close"] - mid) / half),
        "swing_range_pct": (swing_hi - swing_lo) / c["close"] * 100 if c["close"] else 0.0,
        "atr_pct": atri / c["close"] * 100 if c["close"] else 0.0,
        "rsi14": rsi[i],
        "ret3": _ret(3) * 100, "ret6": _ret(6) * 100, "ret12": _ret(12) * 100,
        "body_ratio": body / rng, "upper_wick": upper / rng, "lower_wick": lower / rng,
        "rvol": min(rvol, 10.0),
        "tod": dt.hour * 60 + dt.minute,
        "dow": float(dt.weekday()),
    }
    feats = {k: (0.0 if (v is None or (isinstance(v, float) and math.isnan(v))) else float(v)) for k, v in feats.items()}
    return {"i": i, "timestamp": c["timestamp"], "direction": direction,
            "entry": round(entry, 2), "sl": round(sl_px, 2), "tp": round(tp_px, 2),
            "rr": rr, "is_choch": bool(is_choch), "features": feats}


def _clamp(x: float, lo: float = -1.5, hi: float = 1.5) -> float:
    return max(lo, min(hi, x))


def label_signal(candles: list[dict], sig: dict, horizon: int = LABEL_HORIZON, same_day: bool = False):
    """Forward-walk from the signal bar: 1 if TP hit before SL, 0 if SL first
    (or both same bar), None if unresolved within `horizon` bars.

    ``same_day=True`` makes it INTRADAY: the walk stops at the session boundary
    (next IST date), so a signal never resolves on an overnight hold — if TP/SL
    isn't hit within the same trading day it's dropped (None)."""
    cs = candles
    i = sig["i"]
    long = sig["direction"] == "long"
    tp, sl = sig["tp"], sig["sl"]
    sig_day = datetime.fromtimestamp(cs[i]["timestamp"], IST).date() if same_day else None
    for j in range(i + 1, min(len(cs), i + 1 + horizon)):
        if same_day and datetime.fromtimestamp(cs[j]["timestamp"], IST).date() != sig_day:
            return None  # crossed into the next session → intraday-undecided
        hi, lo = cs[j]["high"], cs[j]["low"]
        if long:
            hit_sl = lo <= sl
            hit_tp = hi >= tp
        else:
            hit_sl = hi >= sl
            hit_tp = lo <= tp
        if hit_sl:      # conservative: SL wins ties within the same bar
            return 0
        if hit_tp:
            return 1
    return None


def feature_row(sig: dict) -> list[float]:
    f = sig["features"]
    return [float(f.get(k, 0.0)) for k in FEATURE_KEYS]
