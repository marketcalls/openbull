"""Per-leg risk evaluation — pure functions, no I/O.

Given a leg's current state and a fresh LTP, returns the list of risk events
that should fire (SL hit, target hit, trail armed, trail advanced) plus the
new state values to write. The caller (tick_processor) handles persistence,
order placement, and event-bus publishing — this module just computes.

Pure-function design keeps the rules trivially testable and prevents the
hot path from accumulating side effects in subtle places.

Semantics match ``docs/plan/strategy-module.md`` Section 9.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# ``position`` values from the leg config schema
POSITION_BUY = "B"
POSITION_SELL = "S"


@dataclass
class RiskOutcome:
    """Result of one evaluate_leg call.

    Always returns ``new_state`` — even when nothing fires — because trail
    bookkeeping (favorable_peak, effective_sl) advances on every tick.
    """

    # Updates to merge into the leg state dict
    leg_mtm: float
    favorable_peak: float
    trail_active: bool
    effective_sl: Optional[float]
    effective_target: Optional[float]

    # Risk-event triggers — at most one fires per tick per leg
    triggered: Optional[str] = None  # 'sl' | 'target' | 'trail_advanced' | 'trail_armed' | None
    # Diagnostic / event payload values
    sl_at: Optional[float] = None
    target_at: Optional[float] = None


def _signed_qty(position: str, qty: int) -> int:
    """B is long (+qty), S is short (-qty)."""
    return qty if position == POSITION_BUY else -qty


def _is_favorable_move(position: str, entry_avg: float, ltp: float) -> float:
    """Magnitude of favorable price movement from entry, in pts. Never negative."""
    if position == POSITION_BUY:
        return max(0.0, ltp - entry_avg)
    return max(0.0, entry_avg - ltp)


def evaluate_leg(
    *,
    position: str,
    qty: int,
    entry_avg: float,
    ltp: float,
    sl_pts: Optional[float],
    target_pts: Optional[float],
    trail_x: float,
    trail_y: float,
    prior_favorable_peak: float = 0.0,
    prior_trail_active: bool = False,
    prior_effective_sl: Optional[float] = None,
    prior_effective_target: Optional[float] = None,
) -> RiskOutcome:
    """Evaluate one tick against one open leg's rules.

    Order of checks (only the first hit fires this tick):
      1. SL hit  (uses effective_sl which may already have trailed)
      2. Target hit
      3. Trail SL bookkeeping (arms / advances ``effective_sl``)
    """
    sign = 1 if position == POSITION_BUY else -1
    leg_mtm = (ltp - entry_avg) * sign * qty

    # Default the effective levels on first call (entry tick).
    if prior_effective_sl is None and sl_pts and sl_pts > 0:
        base_sl = entry_avg - sl_pts if position == POSITION_BUY else entry_avg + sl_pts
    else:
        base_sl = prior_effective_sl

    if prior_effective_target is None and target_pts and target_pts > 0:
        base_target = entry_avg + target_pts if position == POSITION_BUY else entry_avg - target_pts
    else:
        base_target = prior_effective_target

    # Trail bookkeeping happens FIRST (purely state-update) so SL check below
    # uses the latest effective_sl. Trail never moves SL against the position.
    favorable_now = _is_favorable_move(position, entry_avg, ltp)
    new_favorable_peak = max(prior_favorable_peak, favorable_now)
    trail_active = prior_trail_active
    new_sl = base_sl

    trail_fired: Optional[str] = None
    if trail_x and trail_x > 0:
        if trail_y and trail_y > 0:
            # ---- Stepped trail (X = trigger, Y = step size) ----
            # Arms only after favorable_peak >= X, then advances the SL
            # in Y-pt steps each time peak crosses an X + kY boundary.
            # Useful for locking in profit progressively.
            if new_favorable_peak >= trail_x:
                if not trail_active:
                    trail_active = True
                    trail_fired = "trail_armed"
                steps_past = int((new_favorable_peak - trail_x) // trail_y)
                advance_pts = trail_y + steps_past * trail_y
                trailed_sl = (
                    entry_avg + advance_pts if position == POSITION_BUY
                    else entry_avg - advance_pts
                )
                # Only move SL favorably (up for B, down for S).
                if base_sl is None or (
                    trailed_sl > base_sl if position == POSITION_BUY
                    else trailed_sl < base_sl
                ):
                    new_sl = trailed_sl
                    if trail_fired is None:
                        trail_fired = "trail_advanced"
        else:
            # ---- Fixed-distance trail (X = trail distance, no Y) ----
            # Armed immediately at entry ± X (initial hard stop). On each
            # favorable extreme, the SL slides 1:1 with the peak so the
            # gap stays constant at X points. SL never retreats. Compatible
            # with sl_pts: whichever stop is more favorable wins, because
            # the SL-move-favorably-only check below handles both.
            trailed_sl = (
                entry_avg + new_favorable_peak - trail_x
                if position == POSITION_BUY
                else entry_avg - new_favorable_peak + trail_x
            )
            if not trail_active:
                trail_active = True
                trail_fired = "trail_armed"
            if base_sl is None or (
                trailed_sl > base_sl if position == POSITION_BUY
                else trailed_sl < base_sl
            ):
                # NOTE: we deliberately don't fire trail_advanced on every
                # tick that moves the SL — fixed-distance trail advances
                # continuously, which would flood the event bus. The UI
                # still sees the new effective_sl via the WS delta.
                new_sl = trailed_sl

    # Now check SL / Target against the (possibly trailed) levels.
    sl_hit = False
    target_hit = False
    if new_sl is not None:
        if position == POSITION_BUY and ltp <= new_sl:
            sl_hit = True
        elif position == POSITION_SELL and ltp >= new_sl:
            sl_hit = True
    if base_target is not None:
        if position == POSITION_BUY and ltp >= base_target:
            target_hit = True
        elif position == POSITION_SELL and ltp <= base_target:
            target_hit = True

    triggered: Optional[str] = None
    if sl_hit:
        triggered = "sl"
    elif target_hit:
        triggered = "target"
    elif trail_fired is not None:
        triggered = trail_fired

    return RiskOutcome(
        leg_mtm=leg_mtm,
        favorable_peak=new_favorable_peak,
        trail_active=trail_active,
        effective_sl=new_sl,
        effective_target=base_target,
        triggered=triggered,
        sl_at=new_sl if sl_hit else None,
        target_at=base_target if target_hit else None,
    )


def compute_strategy_mtm(legs: dict) -> tuple[float, float, float]:
    """Return ``(realized, unrealized, total)`` summed across all legs.

    A leg is "realized" once its status is ``closed`` — its locked-in P&L
    sits in ``leg.realized_pnl``. Open legs contribute via the live mtm.
    Rejected / configured legs contribute 0.
    """
    realized = 0.0
    unrealized = 0.0
    for leg in legs.values():
        status = leg.get("status")
        if status == "closed":
            realized += float(leg.get("realized_pnl") or 0.0)
        elif status == "open":
            unrealized += float(leg.get("mtm") or 0.0)
    return realized, unrealized, realized + unrealized
