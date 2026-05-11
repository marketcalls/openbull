"""Strategy-level risk evaluation — pure functions, no I/O.

Three rule layers, evaluated in order on every tick after per-leg eval:

  1. Trail-SL-to-entry (cross-cutting hook fired from the leg loop) —
     when any leg's SL fires AND ``strategy.trail_sl_to_entry=true``, the
     remaining open legs' ``effective_sl`` jumps to their ``entry_avg``,
     ``state.trail_to_entry_active`` flips ``true``, and the Overall SL
     branch is disabled for the rest of the run.

  2. Lock-profit (Lock mode):
        arm at if_profit_reaches → static floor at lock_profit
        trigger when mtm_total ≤ floor

  3. Lock-profit (Lock + Trail mode):
        same arm rule, then floor = max(lock_profit, peak − trail_step)
        trigger when mtm_total ≤ floor

  4. Overall SL / Target (skipped when trail_to_entry_active):
        target hit:    mtm_total ≥ overall_target_mtm
        sl hit:        mtm_total ≤ −|overall_sl_mtm|

Semantics match ``docs/plan/strategy-module.md`` Section 9.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


# Top-level "what should happen now" outcome
@dataclass
class StrategyOutcome:
    """One per tick, after per-leg eval."""

    # State updates (always applied)
    lock_armed: bool = False
    lock_floor: Optional[float] = None
    pnl_peak: float = 0.0
    pnl_trough: float = 0.0

    # Events to publish this tick (may be multiple)
    events: list[dict[str, Any]] = field(default_factory=list)

    # If set, engine.stop_run is invoked with this stop_reason after the
    # delta is broadcast. None = run continues.
    stop_reason: Optional[str] = None


def _abs_or_none(v: Optional[float]) -> Optional[float]:
    if v is None:
        return None
    return abs(float(v))


def apply_trail_to_entry(legs: dict, triggering_leg_id: int) -> int:
    """Move every still-open leg's effective_sl to its entry_avg.

    Called *only* when a leg's SL fires AND ``strategy.trail_sl_to_entry``
    is true. The triggering leg is excluded (it's about to be exited
    anyway). Returns the number of legs whose SL was actually moved so
    the caller can decide whether to publish the event.
    """
    moved = 0
    for lid_str, leg in legs.items():
        if str(triggering_leg_id) == lid_str:
            continue
        if leg.get("status") != "open":
            continue
        entry = leg.get("entry_avg")
        if entry is None:
            continue
        new_sl = float(entry)
        prev = leg.get("effective_sl")
        # Only move if it's a strict improvement vs the prior SL — never
        # widen a tightened SL.
        position = leg.get("position")
        if prev is not None:
            if position == "B" and new_sl <= prev:
                continue
            if position == "S" and new_sl >= prev:
                continue
        leg["effective_sl"] = new_sl
        moved += 1
    return moved


def evaluate_strategy(
    *,
    pnl_realized: float,
    pnl_unrealized: float,
    prior_pnl_peak: float,
    prior_pnl_trough: float,
    lock_armed: bool,
    lock_floor: Optional[float],
    trail_to_entry_active: bool,
    overall_sl_mtm: Optional[float],
    overall_target_mtm: Optional[float],
    lock_profit_cfg: Optional[dict[str, Any]],
) -> StrategyOutcome:
    """Evaluate the strategy-level rule layer on this tick.

    Inputs are pure values; outputs describe the state transitions and
    events that the tick processor should then apply. Order: lock-profit
    first (it can move the floor) then overall SL/target (skipped if
    trail-to-entry is active).
    """
    mtm_total = pnl_realized + pnl_unrealized
    pnl_peak = max(prior_pnl_peak, mtm_total)
    pnl_trough = min(prior_pnl_trough, mtm_total)

    out = StrategyOutcome(
        lock_armed=lock_armed,
        lock_floor=lock_floor,
        pnl_peak=pnl_peak,
        pnl_trough=pnl_trough,
    )

    # ---- Lock-profit (both modes share the arm check) ----
    if lock_profit_cfg:
        mode = lock_profit_cfg.get("mode")
        arm_at = lock_profit_cfg.get("if_profit_reaches")
        floor_initial = lock_profit_cfg.get("lock_profit")
        trail_step = lock_profit_cfg.get("trail_step")

        if not out.lock_armed and arm_at is not None and mtm_total >= float(arm_at):
            out.lock_armed = True
            out.lock_floor = float(floor_initial or 0.0)
            out.events.append({
                "kind": "lock_profit_armed",
                "severity": "info",
                "message": (
                    f"Lock-profit armed (mode={mode}) at MTM ₹{mtm_total:.2f}, "
                    f"floor set to ₹{out.lock_floor:.2f}"
                ),
                "payload": {
                    "mode": mode,
                    "mtm_total_at_arm": mtm_total,
                    "lock_floor": out.lock_floor,
                },
            })

        if out.lock_armed:
            # Trail mode ratchets the floor upward as the peak rises.
            if mode == "lock_and_trail" and trail_step:
                new_floor = max(
                    float(floor_initial or 0.0),
                    out.pnl_peak - float(trail_step),
                )
                if out.lock_floor is None or new_floor > out.lock_floor:
                    prev = out.lock_floor
                    out.lock_floor = new_floor
                    if prev is not None and new_floor > prev:
                        out.events.append({
                            "kind": "lock_profit_floor_advanced",
                            "severity": "info",
                            "message": (
                                f"Lock-profit floor advanced to ₹{new_floor:.2f} "
                                f"(peak ₹{out.pnl_peak:.2f}, step ₹{trail_step})"
                            ),
                            "payload": {
                                "lock_floor": new_floor,
                                "pnl_peak": out.pnl_peak,
                                "trail_step": float(trail_step),
                            },
                        })

            # Trigger if MTM dives below the floor.
            if out.lock_floor is not None and mtm_total <= out.lock_floor:
                out.events.append({
                    "kind": "lock_profit_triggered",
                    "severity": "warn",
                    "message": (
                        f"Lock-profit triggered — MTM ₹{mtm_total:.2f} hit floor "
                        f"₹{out.lock_floor:.2f}; closing all legs"
                    ),
                    "payload": {
                        "mtm_total_at_trigger": mtm_total,
                        "lock_floor": out.lock_floor,
                    },
                })
                out.stop_reason = "lock_profit"
                return out

    # ---- Overall SL / Target (bypassed when trail-to-entry is live) ----
    if not trail_to_entry_active:
        target = overall_target_mtm
        sl = _abs_or_none(overall_sl_mtm)
        if target is not None and mtm_total >= float(target):
            out.events.append({
                "kind": "overall_target_hit",
                "severity": "info",
                "message": (
                    f"Overall target hit — MTM ₹{mtm_total:.2f} ≥ "
                    f"₹{float(target):.2f}; closing all legs"
                ),
                "payload": {
                    "mtm_total_at_trigger": mtm_total,
                    "overall_target_mtm": float(target),
                },
            })
            out.stop_reason = "overall_target"
            return out
        if sl is not None and mtm_total <= -sl:
            out.events.append({
                "kind": "overall_sl_hit",
                "severity": "warn",
                "message": (
                    f"Overall SL hit — MTM ₹{mtm_total:.2f} ≤ "
                    f"−₹{sl:.2f}; closing all legs"
                ),
                "payload": {
                    "mtm_total_at_trigger": mtm_total,
                    "overall_sl_mtm": sl,
                },
            })
            out.stop_reason = "overall_sl"
            return out

    return out
