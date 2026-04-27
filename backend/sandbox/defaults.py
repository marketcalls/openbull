"""
Defaults seeded into ``sandbox_config`` on first use. Values mirror openalgo's
defaults so strategies ported from there feel identical.
"""

DEFAULT_CONFIGS: list[tuple[str, str, str]] = [
    # (key, value, description)
    ("starting_capital", "10000000", "Default paper-trading capital (1 Cr INR)"),
    ("reset_day", "Sunday", "Day of week to auto-reset sandbox (Never=off)"),
    ("reset_time", "00:00", "IST time to run the weekly reset"),

    # Per-instrument leverage. ``leverage_mis / nrml / cnc`` are kept for
    # backwards compatibility (older sandboxes only had product-level
    # leverage); the engine prefers the instrument-specific keys when the
    # symbol can be resolved to an instrument type via the symbol DB.
    ("leverage_mis", "5", "[legacy] product-only MIS leverage"),
    ("leverage_nrml", "1", "[legacy] product-only NRML leverage"),
    ("leverage_cnc", "1", "[legacy] product-only CNC leverage"),
    ("equity_mis_leverage", "5", "Equity MIS intraday leverage (NSE / BSE)"),
    ("equity_cnc_leverage", "1", "Equity CNC delivery leverage (NSE / BSE)"),
    ("futures_leverage", "10", "Futures leverage (NFO / BFO / MCX / CDS / BCD / NCDEX)"),
    ("option_buy_leverage", "1", "Option BUY leverage (premium paid in full)"),
    ("option_sell_leverage", "1", "Option SELL leverage (raise to require less margin on shorts)"),

    # Square-off times per exchange group (IST, HH:MM).
    ("squareoff_nse_nfo_bse_bfo", "15:15", "MIS squareoff time for equity / F&O"),
    ("squareoff_cds", "16:45", "MIS squareoff time for currency"),
    ("squareoff_mcx", "23:30", "MIS squareoff time for commodities"),

    # Execution engine + MTM cadence (separated so users can poll quickly
    # for fills but recompute MTM less aggressively).
    ("order_check_interval_seconds", "5", "Polling fallback interval for pending orders"),
    ("mtm_update_interval_seconds", "5", "Interval at which positions' MTM is recomputed"),
]


# Per-product margin multiplier (legacy fallback). The engine resolves the
# *real* leverage via :func:`backend.sandbox.config.get_leverage_for` which
# combines (exchange, product, instrument_type, action) into one of the
# ``equity_mis_leverage`` / ``futures_leverage`` / ``option_*_leverage`` keys.
LEVERAGE_KEYS = {
    "MIS": "leverage_mis",
    "NRML": "leverage_nrml",
    "CNC": "leverage_cnc",
}
