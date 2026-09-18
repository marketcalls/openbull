#!/usr/bin/env python3
"""Train the SMC signal win/loss classifier.

Reads historical OHLCV from Databricks Delta tables, generates SMC signals via
the shared engine (backend.services.smc_features), labels each by forward-walk
(TP-before-SL), trains a HistGradientBoosting classifier LOCALLY, saves the model
for the backend to serve, and logs the run to Databricks MLflow (best-effort).

Usage (repo root, venv):
  python smc_train.py                       # options 5m, intraday, LONG-ONLY (default)
  python smc_train.py --sources indices:5m  # custom source:interval list
  python smc_train.py --allow-short         # include short signals too
  python smc_train.py --start 2024-01-01 --rr 2 --horizon 48
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
from backend.services.smc_features import (  # noqa: E402
    FEATURE_KEYS, generate_smc_signals, label_signal, feature_row,
)

MODEL_DIR = ROOT / "models"
MODEL_PATH = MODEL_DIR / "smc_winrate.joblib"
META_PATH = MODEL_DIR / "smc_winrate.meta.json"


def _group_by_symbol(rows: list[dict]) -> dict[str, list[dict]]:
    g: dict[str, list[dict]] = {}
    for r in rows:
        g.setdefault(r["symbol"], []).append(r)
    for sym in g:
        g[sym].sort(key=lambda c: c["timestamp"])
    return g


def _liquid(candles: list[dict], min_bars: int, min_price: float) -> bool:
    """Drop thin/degenerate option series: too few bars, near-zero price, or a
    dead (near-flat) range. These generate pure-noise SMC signals that destroy
    the model's discrimination (observed AUC 0.71 -> 0.51 when included)."""
    if len(candles) < min_bars:
        return False
    closes = sorted(c["close"] for c in candles)
    med = closes[len(closes) // 2]
    if med < min_price:
        return False
    rng = closes[-1] - closes[0]
    if med > 0 and rng / med < 0.5:  # < 50% lifetime range = barely traded
        return False
    return True


def build_dataset(sources: list[tuple[str, str]], start: str | None, end: str | None,
                  rr: float, horizon: int, same_day: bool,
                  min_bars: int = 300, min_price: float = 20.0, long_only: bool = False):
    import databricks_export as dbx
    X, y, ts, syms = [], [], [], []
    for source, interval in sources:
        print(f"  reading {source}/{interval} …", flush=True)
        rows = dbx.db_read_all(source, interval, start, end)
        bysym = _group_by_symbol(rows)
        n_sig = 0
        n_skip = 0
        for sym, candles in bysym.items():
            if source == "options" and not _liquid(candles, min_bars, min_price):
                n_skip += 1
                continue
            sigs = generate_smc_signals(candles, rr=rr)
            for s in sigs:
                if long_only and s["direction"] != "long":
                    continue
                lab = label_signal(candles, s, horizon=horizon, same_day=same_day)
                if lab is None:
                    continue
                X.append(feature_row(s)); y.append(lab); ts.append(s["timestamp"]); syms.append(f"{source}:{sym}")
                n_sig += 1
        kept = len(bysym) - n_skip
        print(f"    {source}/{interval}: {kept}/{len(bysym)} liquid symbols ({n_skip} thin skipped), {n_sig} labeled signals", flush=True)
    return (np.array(X, dtype=float), np.array(y, dtype=int),
            np.array(ts, dtype=np.int64), np.array(syms, dtype=object))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", default="futures:5m,options:5m",
                    help="comma list of source:interval (intraday futures + options CE/PE)")
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--rr", type=float, default=2.0)
    ap.add_argument("--horizon", type=int, default=48)
    ap.add_argument("--test-frac", type=float, default=0.2)
    ap.add_argument("--overnight", action="store_true",
                    help="allow signals to resolve across sessions (default: intraday only)")
    ap.add_argument("--min-bars", type=int, default=300,
                    help="drop option contracts with fewer 5m bars (thin/illiquid)")
    ap.add_argument("--min-price", type=float, default=20.0,
                    help="drop option contracts with median price below this")
    ap.add_argument("--allow-short", action="store_true",
                    help="include short (sell) signals too (default: long-only, for a long-only options strategy)")
    args = ap.parse_args()
    args.long_only = not args.allow_short

    same_day = not args.overnight
    args.intraday = same_day
    sources = [(s.split(":")[0], s.split(":")[1]) for s in args.sources.split(",")]
    print(f"Building dataset from {sources} (rr={args.rr}, horizon={args.horizon}, intraday={same_day}) …")
    X, y, ts, syms = build_dataset(sources, args.start, args.end, args.rr, args.horizon, same_day,
                                   min_bars=args.min_bars, min_price=args.min_price,
                                   long_only=args.long_only)
    if len(y) < 200:
        print(f"Only {len(y)} labeled signals — not enough to train. Widen range/sources."); return
    base_rate = float(y.mean())
    print(f"Total labeled signals: {len(y)} | base win-rate: {base_rate:.3f}")

    # PER-SYMBOL time split (no leakage): for each contract, oldest (1-test_frac)
    # goes to train, newest test_frac to test. This keeps every contract present
    # in both sets, so newly-listed far-month expiries (which otherwise land
    # entirely in a global newest-time test slice) don't wreck generalization.
    by: dict[str, list[int]] = {}
    for idx in range(len(y)):
        by.setdefault(syms[idx], []).append(idx)
    tr_idx, te_idx = [], []
    for _sym, idxs in by.items():
        idxs.sort(key=lambda k: ts[k])
        cut_s = int(len(idxs) * (1 - args.test_frac))
        tr_idx.extend(idxs[:cut_s]); te_idx.extend(idxs[cut_s:])
    tr_idx = np.array(sorted(tr_idx, key=lambda k: ts[k]), dtype=int)
    te_idx = np.array(sorted(te_idx, key=lambda k: ts[k]), dtype=int)
    Xtr, Xte, ytr, yte = X[tr_idx], X[te_idx], y[tr_idx], y[te_idx]

    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import roc_auc_score, accuracy_score, brier_score_loss
    clf = HistGradientBoostingClassifier(
        max_depth=4, learning_rate=0.06, max_iter=350, l2_regularization=1.0,
        min_samples_leaf=40, validation_fraction=0.15, early_stopping=True, random_state=42,
    )
    clf.fit(Xtr, ytr)

    proba = clf.predict_proba(Xte)[:, 1]
    pred = (proba >= 0.5).astype(int)
    metrics = {
        "n_total": int(len(y)), "n_train": int(len(ytr)), "n_test": int(len(yte)),
        "base_win_rate": round(base_rate, 4),
        "test_base_rate": round(float(yte.mean()), 4),
        "auc": round(float(roc_auc_score(yte, proba)), 4) if len(set(yte)) > 1 else None,
        "accuracy": round(float(accuracy_score(yte, pred)), 4),
        "brier": round(float(brier_score_loss(yte, proba)), 4),
    }
    print("Test metrics:", json.dumps(metrics, indent=1))
    # decile calibration
    print("Calibration (pred bucket -> actual win-rate):")
    for lo in (0.0, 0.2, 0.4, 0.6, 0.8):
        m = (proba >= lo) & (proba < lo + 0.2)
        if m.sum():
            print(f"  {lo:.1f}-{lo+0.2:.1f}: n={int(m.sum())} actual={yte[m].mean():.3f}")

    MODEL_DIR.mkdir(exist_ok=True)
    import joblib
    joblib.dump(clf, MODEL_PATH)
    meta = {
        "feature_keys": FEATURE_KEYS, "metrics": metrics, "rr": args.rr,
        "horizon": args.horizon, "sources": args.sources, "intraday": same_day,
        "min_bars": args.min_bars, "min_price": args.min_price, "long_only": args.long_only,
        "trained_at": datetime.now(timezone.utc).isoformat(), "sklearn_model": "HistGradientBoostingClassifier",
    }
    META_PATH.write_text(json.dumps(meta, indent=2))
    print(f"Saved model -> {MODEL_PATH}")

    _log_mlflow(clf, metrics, args, base_rate)


def _log_mlflow(clf, metrics, args, base_rate):
    """Best-effort MLflow logging to Databricks; never fails the run."""
    try:
        import databricks_export as dbx
        dbx._load_dotenv()
        host = os.environ.get("DATABRICKS_HOST", "")
        token = os.environ.get("DATABRICKS_TOKEN", "")
        if not (host and token):
            print("MLflow: no Databricks creds — skipped."); return
        os.environ["DATABRICKS_HOST"] = host if host.startswith("http") else f"https://{host}"
        os.environ["DATABRICKS_TOKEN"] = token
        import mlflow
        mlflow.set_tracking_uri("databricks")
        try:
            mlflow.set_experiment("/Shared/openbull_smc")
        except Exception:
            mlflow.set_tracking_uri(f"file:{ROOT / 'mlruns'}")
            mlflow.set_experiment("openbull_smc")
        with mlflow.start_run(run_name=f"smc_winrate_{datetime.now():%Y%m%d_%H%M}"):
            mlflow.log_params({"rr": args.rr, "horizon": args.horizon, "sources": args.sources,
                               "model": "HistGradientBoosting"})
            mlflow.log_metrics({k: v for k, v in metrics.items() if isinstance(v, (int, float))})
            try:
                mlflow.sklearn.log_model(clf, name="model")
            except Exception:
                mlflow.sklearn.log_model(clf, artifact_path="model")
        print("MLflow: logged run to Databricks (/Shared/openbull_smc).")
    except Exception as e:  # noqa: BLE001
        print(f"MLflow logging skipped: {type(e).__name__}: {str(e)[:160]}")


if __name__ == "__main__":
    main()
