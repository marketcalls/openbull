#!/usr/bin/env python3
"""
Comprehensive OpenBull → Databricks export for ML / trade-optimisation analysis.

Pulls the full market dataset OpenBull can reach through Upstox and lands it in a
tidy Unity Catalog **volume** plus queryable **Delta tables**:

  indices   OHLCV for the 7 indices                (1m / 5m / 15m / daily)
  futures   OHLCV for each index's near future     (5m / daily)
  options   ATM±12 CE/PE for the nearest expiries  (5m / daily) — with strike/type/expiry/moneyness
  scans     the server-side premarket scan log     (one row per slot × ranked option + positioning)

Volume layout (user-friendly, one stacked CSV per kind+interval):

  /Volumes/<catalog>/<schema>/analysis/
    README.txt                       what each folder holds
    _manifest.csv                    catalogue: kind, index, interval, rows, path, updated
    indices/   indices_1m.csv  indices_5m.csv  indices_15m.csv  indices_daily.csv
    futures/   futures_5m.csv  futures_daily.csv
    options/   <INDEX>/<INDEX>_options_5m.csv   <INDEX>_options_daily.csv
    scans/     premarket_scans.csv

Delta tables (catalog.schema): analysis_indices, analysis_futures, analysis_options, premarket_scans.

Usage (repo root, venv python — reuses .env via databricks_export._load_dotenv):
  python analysis_export.py                       # everything, default windows
  python analysis_export.py --no-options          # skip the heavy option pull
  python analysis_export.py --option-expiries 2   # nearest 2 expiries instead of 1
  python analysis_export.py --indices NIFTY,BANKNIFTY
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import os
from datetime import date as date_cls, datetime, timedelta, timezone
from pathlib import Path

# reuse the proven bits from the daily exporter
from databricks_export import _load_dotenv, push_to_databricks

_load_dotenv()

IST = timezone(timedelta(hours=5, minutes=30))
EXPORT_DIR = Path(__file__).resolve().parent / "exports" / "analysis"
VOLUME = "analysis"

ALL_INDICES = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50", "SENSEX", "BANKEX"]

# How far back to pull per interval.
# Futures/options live only for their contract's ~3-month window, so a modest
# window avoids firing empty chunked calls per expired strike on the daily run.
WINDOW_DAYS = {"1m": 30, "5m": 180, "15m": 365, "D": 1500}
# Indices are continuous symbols — Upstox serves deep history. Pull ~10 years of
# 5m/15m/daily; 1m stays short (Upstox only serves recent 1m anyway).
INDEX_WINDOW_DAYS = {"1m": 60, "5m": 3653, "15m": 3653, "D": 1650}  # daily capped to the 5m/15m floor (~Jan 2022)
INDEX_INTERVALS = ["1m", "5m", "15m", "D"]
FUT_INTERVALS = ["5m", "D"]
OPT_INTERVALS = ["5m", "D"]
SPAN = 12  # ATM ± 12 strikes

OHLCV_COLS = ["name", "symbol", "exchange", "interval", "ts", "datetime_ist", "open", "high", "low", "close", "volume", "oi"]
OPT_COLS = ["name", "symbol", "exchange", "interval", "expiry", "strike", "type", "moneyness", "ts", "datetime_ist", "open", "high", "low", "close", "volume", "oi"]


# ── Upstox access (mirrors premarket_service conventions) ────────────────────
def _bk():
    from backend.services.premarket_service import (
        INDEX_OPT_EXCH, INDEX_SPOT_EXCH, INDEX_STEP, _MONTHS, _parse_expiry,
    )
    from backend.services.history_service import get_history_with_auth
    from backend.services.market_data_service import get_expiry_dates
    return dict(
        INDEX_OPT_EXCH=INDEX_OPT_EXCH, INDEX_SPOT_EXCH=INDEX_SPOT_EXCH, INDEX_STEP=INDEX_STEP,
        MONTHS=_MONTHS, parse_expiry=_parse_expiry, hist=get_history_with_auth, expiries=get_expiry_dates,
    )


async def _get_auth() -> tuple[str, str] | None:
    from sqlalchemy import select
    from backend.broker.upstox.mapping.order_data import _load_symbol_cache
    from backend.database import async_session
    from backend.models.auth import BrokerAuth
    from backend.security import decrypt_value

    await _load_symbol_cache()
    async with async_session() as db:
        ba = (await db.execute(select(BrokerAuth).where(BrokerAuth.is_revoked == False))).scalars().first()  # noqa: E712
        if not ba:
            return None
        return decrypt_value(ba.access_token), ba.broker_name


def _candles(bk, symbol, exchange, interval, start, end, token, broker) -> list[dict]:
    ok, body, _ = bk["hist"](symbol, exchange, interval, start, end, token, broker, None)
    return (body.get("data") or []) if ok else []


def _row(name, symbol, exchange, interval, c) -> dict:
    ts = int(c["timestamp"])
    return {
        "name": name, "symbol": symbol, "exchange": exchange, "interval": interval, "ts": ts,
        "datetime_ist": datetime.fromtimestamp(ts, IST).strftime("%Y-%m-%d %H:%M"),
        "open": c["open"], "high": c["high"], "low": c["low"], "close": c["close"],
        "volume": c.get("volume", 0), "oi": c.get("oi", 0),
    }


def _safe_purge(rows: list[dict]) -> str | None:
    """Idempotent-but-non-destructive purge predicate for accumulate pushes.

    Deletes only the batch symbols' rows AT OR AFTER the earliest bar we are
    reloading (min ts in this batch), so anything OLDER than the current pull
    window (e.g. deep history beyond WINDOW_DAYS) is preserved untouched, while
    the re-pulled window is refreshed without duplicates. Call PER INTERVAL so
    the 5m (180d) window's lower bound never deletes deep daily history and
    vice-versa."""
    syms = sorted({r["symbol"] for r in rows if r.get("symbol")})
    tss = [int(r["ts"]) for r in rows if r.get("ts") is not None]
    if not syms or not tss:
        return None
    in_list = ", ".join("'" + s.replace("'", "''") + "'" for s in syms)
    return f"symbol IN ({in_list}) AND CAST(ts AS BIGINT) >= {min(tss)}"


# ── Pulls ────────────────────────────────────────────────────────────────────
def pull_indices(bk, indices, token, broker, intervals) -> dict[str, list[dict]]:
    """Returns {interval: rows} stacked across all indices."""
    end = datetime.now(IST).date()
    out: dict[str, list[dict]] = {iv: [] for iv in intervals}
    for name in indices:
        exch = bk["INDEX_SPOT_EXCH"].get(name, "NSE_INDEX")
        for iv in intervals:
            start = end - timedelta(days=INDEX_WINDOW_DAYS[iv])
            cs = _candles(bk, name, exch, iv, start.isoformat(), end.isoformat(), token, broker)
            for c in cs:
                out[iv].append(_row(name, name, exch, iv, c))
            print(f"  index {name:11} {iv:4} {len(cs)} bars")
    return out


def pull_futures(bk, indices, token, broker, intervals) -> dict[str, list[dict]]:
    end = datetime.now(IST).date()
    out: dict[str, list[dict]] = {iv: [] for iv in intervals}
    for name in indices:
        opt_exch = bk["INDEX_OPT_EXCH"].get(name, "NFO")
        ok, body, _ = bk["expiries"](name, opt_exch, "futures")
        exps = (body.get("data") or []) if ok else []
        cand = sorted(((bk["parse_expiry"](e), e) for e in exps), key=lambda x: (x[0] or date_cls.max))
        fut_exps = [e for d, e in cand if d and d >= end][:2]  # near + next month
        for e in fut_exps:
            sym = f"{name}{e.replace('-', '')}FUT"
            for iv in intervals:
                start = end - timedelta(days=WINDOW_DAYS[iv])
                cs = _candles(bk, sym, opt_exch, iv, start.isoformat(), end.isoformat(), token, broker)
                for c in cs:
                    out[iv].append(_row(name, sym, opt_exch, iv, c))
                if cs:
                    print(f"  fut   {sym:18} {iv:4} {len(cs)} bars")
    return out


def pull_options(bk, indices, token, broker, intervals, n_expiries) -> dict[str, dict[str, list[dict]]]:
    """Returns {index: {interval: rows}} for ATM±12 CE/PE over the nearest expiries."""
    end = datetime.now(IST).date()
    out: dict[str, dict[str, list[dict]]] = {}
    for name in indices:
        step = bk["INDEX_STEP"].get(name, 50)
        spot_exch = bk["INDEX_SPOT_EXCH"].get(name, "NSE_INDEX")
        opt_exch = bk["INDEX_OPT_EXCH"].get(name, "NFO")
        per_iv: dict[str, list[dict]] = {iv: [] for iv in intervals}

        # ATM from the latest daily close
        daily = sorted(_candles(bk, name, spot_exch, "D", (end - timedelta(days=15)).isoformat(), end.isoformat(), token, broker), key=lambda c: c["timestamp"])
        if not daily:
            print(f"  opt   {name}: no spot — skip")
            out[name] = per_iv
            continue
        atm = round(daily[-1]["close"] / step) * step

        ok, body, _ = bk["expiries"](name, opt_exch, "options")
        exps = (body.get("data") or []) if ok else []
        cand = sorted(((bk["parse_expiry"](e), e) for e in exps), key=lambda x: (x[0] or date_cls.max))
        upcoming = [(d, e) for d, e in cand if d and d >= end][:n_expiries]

        for exp_d, exp in upcoming:
            ddmmmyy = exp.replace("-", "")
            for k in range(-SPAN, SPAN + 1):
                strike = atm + k * step
                if strike <= 0:
                    continue
                off = k
                money = "ATM" if off == 0 else (f"ATM+{off}" if off > 0 else f"ATM{off}")
                for typ in ("CE", "PE"):
                    sym = f"{name}{ddmmmyy}{strike}{typ}"
                    for iv in intervals:
                        # option life is short; pull the contract's full window
                        start = end - timedelta(days=WINDOW_DAYS[iv])
                        cs = _candles(bk, sym, opt_exch, iv, start.isoformat(), end.isoformat(), token, broker)
                        for c in cs:
                            r = _row(name, sym, opt_exch, iv, c)
                            r.update({"expiry": exp, "strike": strike, "type": typ, "moneyness": money})
                            per_iv[iv].append(r)
            total = sum(len(v) for v in per_iv.values())
            print(f"  opt   {name} {exp} ATM±{SPAN}: {total} bars so far")
        out[name] = per_iv
    return out


async def pull_scans() -> list[dict]:
    from databricks_export import export_scans
    return await export_scans(None, None)


# ── Volume upload (organised, nested paths) ──────────────────────────────────
def _csv_bytes(rows: list[dict], cols: list[str]) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue().encode("utf-8")


def _put_volume(rel_path: str, data: bytes) -> bool:
    """PUT bytes to /Volumes/<cat>/<schema>/analysis/<rel_path> via the Files API."""
    import urllib.parse
    import urllib.request

    host = os.environ.get("DATABRICKS_HOST", "").replace("https://", "").rstrip("/")
    token = os.environ.get("DATABRICKS_TOKEN")
    catalog = os.environ.get("DATABRICKS_CATALOG", "main")
    schema = os.environ.get("DATABRICKS_SCHEMA", "openbull")
    if not (host and token):
        return False
    dest = f"/Volumes/{catalog}/{schema}/{VOLUME}/{rel_path}"
    url = f"https://{host}/api/2.0/fs/files{urllib.parse.quote(dest)}?overwrite=true"
    req = urllib.request.Request(url, data=data, method="PUT",
                                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/octet-stream"})
    resp = urllib.request.urlopen(req)
    return resp.status in (200, 204)


def _ensure_volume() -> None:
    host = os.environ.get("DATABRICKS_HOST", "").replace("https://", "").rstrip("/")
    http_path = os.environ.get("DATABRICKS_HTTP_PATH")
    token = os.environ.get("DATABRICKS_TOKEN")
    catalog = os.environ.get("DATABRICKS_CATALOG", "main")
    schema = os.environ.get("DATABRICKS_SCHEMA", "openbull")
    if not (host and http_path and token):
        return
    try:
        from databricks import sql
        with sql.connect(server_hostname=host, http_path=http_path, access_token=token) as conn:
            with conn.cursor() as cur:
                cur.execute(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")
                cur.execute(f"CREATE VOLUME IF NOT EXISTS {catalog}.{schema}.{VOLUME}")
    except Exception as e:  # noqa: BLE001
        print(f"  (volume pre-create skipped: {e})")


README = """OpenBull market dataset for analysis & ML
==========================================

Refreshed by analysis_export.py (daily after close). Folders:

  indices/   OHLCV for the 7 indices, one stacked CSV per interval
             (indices_1m, indices_5m, indices_15m, indices_daily).
  futures/   OHLCV for each index's near & next-month future.
  options/   <INDEX>/<INDEX>_options_<interval>.csv — ATM±12 CE/PE for the
             nearest expiries, with strike / type / expiry / moneyness columns.
  scans/     premarket_scans.csv — the live scan log (slot × ranked option +
             positioning bias / PCR / walls / max-pain).

Columns: name, symbol, exchange, interval, ts (epoch s), datetime_ist,
open, high, low, close, volume, oi (+ strike/type/expiry/moneyness for options).

The same data is also in Delta tables: analysis_indices, analysis_futures,
analysis_options, premarket_scans. Use the openbull_ml notebook to build
features and train the models.
"""


# ── Main ─────────────────────────────────────────────────────────────────────
def _write_local(rel_path: str, data: bytes) -> Path:
    p = EXPORT_DIR / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


async def main() -> None:
    ap = argparse.ArgumentParser(description="Comprehensive OpenBull → Databricks analysis export")
    ap.add_argument("--indices", default=",".join(ALL_INDICES), help="comma list of indices")
    ap.add_argument("--no-indices", action="store_true")
    ap.add_argument("--no-futures", action="store_true")
    ap.add_argument("--no-options", action="store_true")
    ap.add_argument("--no-scans", action="store_true")
    ap.add_argument("--no-depth", action="store_true")
    ap.add_argument("--option-expiries", type=int, default=1, help="nearest N expiries for options")
    ap.add_argument("--no-push", action="store_true", help="skip Delta push (volume only)")
    ap.add_argument("--no-volume", action="store_true", help="skip volume upload (Delta only)")
    args = ap.parse_args()

    indices = [s.strip().upper() for s in args.indices.split(",") if s.strip()]
    bk = _bk()
    auth = await _get_auth()
    if not auth:
        print("No active Upstox session — re-authenticate first.")
        return
    token, broker = auth

    do_push = not args.no_push
    do_vol = not args.no_volume
    if do_vol:
        _ensure_volume()
    manifest: list[dict] = []

    def emit(kind: str, name: str, interval: str, rows: list[dict], cols: list[str], rel_path: str, table: str | None) -> None:
        data = _csv_bytes(rows, cols)
        _write_local(rel_path, data)
        ok_v = _put_volume(rel_path, data) if do_vol else False
        if do_push and table and rows:
            push_to_databricks(table, cols, rows)
        manifest.append({"kind": kind, "index": name, "interval": interval, "rows": len(rows),
                         "path": rel_path, "updated": datetime.now(IST).strftime("%Y-%m-%d %H:%M")})
        print(f"  -> {rel_path}: {len(rows)} rows{' [vol]' if ok_v else ''}")

    if not args.no_indices:
        print("INDICES:")
        idx = pull_indices(bk, indices, token, broker, INDEX_INTERVALS)
        for iv in INDEX_INTERVALS:
            label = "daily" if iv == "D" else iv
            emit("indices", "ALL", iv, idx[iv], OHLCV_COLS, f"indices/indices_{label}.csv", None)
        if do_push:
            push_to_databricks("analysis_indices", OHLCV_COLS, [r for iv in INDEX_INTERVALS for r in idx[iv]])

    if not args.no_futures:
        print("FUTURES:")
        fut = pull_futures(bk, indices, token, broker, FUT_INTERVALS)
        for iv in FUT_INTERVALS:
            label = "daily" if iv == "D" else iv
            emit("futures", "ALL", iv, fut[iv], OHLCV_COLS, f"futures/futures_{label}.csv", None)
        if do_push:
            # accumulate futures across contract rollovers too (see options note below);
            # push per interval so each interval's purge lower bound is its own window
            for iv in FUT_INTERVALS:
                if fut[iv]:
                    push_to_databricks("analysis_futures", OHLCV_COLS, fut[iv],
                                       replace=False, purge_where=_safe_purge(fut[iv]))

    if not args.no_options:
        print("OPTIONS:")
        opt = pull_options(bk, indices, token, broker, OPT_INTERVALS, args.option_expiries)
        for name in indices:
            for iv in OPT_INTERVALS:
                label = "daily" if iv == "D" else iv
                emit("options", name, iv, opt[name][iv], OPT_COLS, f"options/{name}/{name}_options_{label}.csv", None)
        if do_push:
            # ACCUMULATE across runs: append this run's contracts and keep every
            # previously-captured expiry AND all deep history. Push PER INTERVAL and
            # purge only each batch's re-pulled window (symbol + ts >= earliest bar),
            # so re-runs stay idempotent without wiping other expiries or history
            # older than the pull window. (replace=True would CREATE OR REPLACE the
            # whole table every run — why only the nearest expiry ever survived.)
            for iv in OPT_INTERVALS:
                rows_iv = [r for name in indices for r in opt[name][iv]]
                if rows_iv:
                    push_to_databricks("analysis_options", OPT_COLS, rows_iv,
                                       replace=False, purge_where=_safe_purge(rows_iv))

    if not args.no_scans:
        print("SCANS:")
        scans = await pull_scans()
        from databricks_export import SCAN_COLUMNS
        emit("scans", "ALL", "slot", scans, SCAN_COLUMNS, "scans/premarket_scans.csv", "premarket_scans")

    if not args.no_depth:
        print("DEPTH:")
        from databricks_export import export_depth, DEPTH_COLUMNS
        depth = await export_depth(None, None)
        emit("depth", "ALL", "snap", depth, DEPTH_COLUMNS, "depth/depth_snapshots.csv", "depth_snapshots")

    if do_vol:
        _put_volume("README.txt", README.encode("utf-8"))
        _put_volume("_manifest.csv", _csv_bytes(manifest, ["kind", "index", "interval", "rows", "path", "updated"]))
        print("  -> README.txt, _manifest.csv [vol]")
    print("done.")


if __name__ == "__main__":
    asyncio.run(main())
