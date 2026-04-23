"""
Defaults seeded into ``sandbox_config`` on first use. Values mirror openalgo's
defaults so strategies ported from there feel identical.
"""

DEFAULT_CONFIGS: list[tuple[str, str, str]] = [
    # (key, value, description)
    ("starting_capital", "10000000", "Default paper-trading capital (₹1 Cr)"),
    ("reset_day", "Sunday", "Day of week to auto-reset sandbox (Never=off)"),
    ("reset_time", "00:00", "IST time to run the weekly reset"),
    # Leverage per product type (openalgo defaults)
    ("leverage_mis", "5", "MIS intraday leverage multiplier"),
    ("leverage_nrml", "1", "NRML overnight leverage multiplier"),
    ("leverage_cnc", "1", "CNC delivery leverage multiplier"),
    # Square-off times per exchange group (IST, HH:MM)
    ("squareoff_nse_nfo_bse_bfo", "15:15", "MIS squareoff time for equity / F&O"),
    ("squareoff_cds", "16:45", "MIS squareoff time for currency"),
    ("squareoff_mcx", "23:30", "MIS squareoff time for commodities"),
    # Execution engine cadence
    ("order_check_interval_seconds", "5", "Polling fallback interval for pending orders"),
    ("mtm_update_interval_seconds", "15", "Interval at which positions' MTM is recomputed"),
]


# Per-product margin multiplier. higher leverage = less margin required.
LEVERAGE_KEYS = {
    "MIS": "leverage_mis",
    "NRML": "leverage_nrml",
    "CNC": "leverage_cnc",
}
