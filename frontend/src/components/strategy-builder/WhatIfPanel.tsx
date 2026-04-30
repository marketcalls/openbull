/**
 * What-if simulator strip — sliders for spot %, IV pp, and days forward
 * with the resulting position-level P&L and Greeks computed locally
 * via the pure-TS Black-76 in lib/black76.ts.
 *
 * Why local instead of round-tripping the snapshot endpoint: the
 * sliders are continuous (mousemove fires hundreds of events) and
 * we'd flood the broker rate budget for no reason. The math agrees
 * with the snapshot at zero-shift because both use the same Black-76
 * implementation; for non-zero shifts the simulator is the right tool.
 *
 * Output via onSimulationChange:
 *   { active, simulatedSpot, simulatedPnl }
 * The page passes this to PayoffChart so it can draw a magenta marker
 * at the simulated point — instant visual feedback without redrawing
 * the entire payoff curve on every slider tick.
 */

import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  aggregatePosition,
  greeks as legGreeks,
  simulateLegPrice,
  type SimulatedLeg,
} from "@/lib/black76";
import { cn } from "@/lib/utils";
import type { Greeks, SnapshotLegOutput } from "@/types/strategy";

export interface SimulationOutput {
  active: boolean;
  simulatedSpot: number;
  simulatedPnl: number | null;
  spotShiftPct: number;
  ivShiftPct: number;
  daysForward: number;
}

interface Props {
  snapshotLegs: SnapshotLegOutput[];
  /** Symbol → user-entered entry price (falls back to leg.ltp). */
  entryPriceBySymbol: Record<string, number>;
  spot: number;
  onSimulationChange?: (out: SimulationOutput) => void;
}

const SPOT_RANGE = 10; // ±10%
const SPOT_STEP = 0.5; // 0.5% steps
const IV_RANGE = 10; // ±10pp
const IV_STEP = 0.5; // 0.5pp
const MAX_DAYS_FORWARD = 30;

function actionSign(action: "BUY" | "SELL"): 1 | -1 {
  return action === "BUY" ? 1 : -1;
}

/** Filter+enrich snapshot legs so we have the inputs the simulator wants. */
function buildLegs(
  snapshotLegs: SnapshotLegOutput[],
  entryPriceBySymbol: Record<string, number>,
): Array<SimulatedLeg & { entryPrice: number }> {
  const out: Array<SimulatedLeg & { entryPrice: number }> = [];
  for (const l of snapshotLegs) {
    if (
      !l.option_type ||
      !l.strike ||
      l.implied_volatility == null ||
      l.implied_volatility <= 0 ||
      l.days_to_expiry == null ||
      l.days_to_expiry <= 0 ||
      l.ltp == null
    ) {
      continue;
    }
    const entry = entryPriceBySymbol[l.symbol] ?? l.ltp;
    out.push({
      K: l.strike,
      flag: l.option_type === "CE" ? "c" : "p",
      sigmaEntry: l.implied_volatility / 100,
      spotEntry: 0, // not used by simulateLegPrice — newSpot drives F
      T0: Math.max(l.days_to_expiry / 365, 0.0001),
      action: l.action,
      lots: l.lots,
      lotSize: l.lot_size,
      entryPrice: entry > 0 ? entry : l.ltp,
    });
  }
  return out;
}

function fmtSigned(n: number, digits = 2): string {
  return `${n >= 0 ? "+" : ""}${n.toFixed(digits)}`;
}

function signTone(n: number): string {
  if (!Number.isFinite(n) || n === 0) return "";
  return n > 0
    ? "text-emerald-600 dark:text-emerald-400"
    : "text-red-600 dark:text-red-400";
}

export function WhatIfPanel({
  snapshotLegs,
  entryPriceBySymbol,
  spot,
  onSimulationChange,
}: Props) {
  const [spotShiftPct, setSpotShiftPct] = useState(0);
  const [ivShiftPct, setIvShiftPct] = useState(0);
  const [daysForward, setDaysForward] = useState(0);

  const legs = useMemo(
    () => buildLegs(snapshotLegs, entryPriceBySymbol),
    [snapshotLegs, entryPriceBySymbol],
  );

  // Cap the days slider at the shortest leg's DTE — there's no payoff math
  // beyond the nearest expiry without re-pricing the leg as a future spot.
  const maxDays = useMemo(() => {
    if (legs.length === 0) return MAX_DAYS_FORWARD;
    const minT = Math.min(...legs.map((l) => l.T0)) * 365;
    return Math.min(MAX_DAYS_FORWARD, Math.max(1, Math.floor(minT)));
  }, [legs]);

  // Clamp daysForward when maxDays shrinks (e.g. after a leg expiry change).
  useEffect(() => {
    if (daysForward > maxDays) setDaysForward(maxDays);
  }, [maxDays, daysForward]);

  const simulatedSpot = useMemo(
    () => spot * (1 + spotShiftPct / 100),
    [spot, spotShiftPct],
  );

  const sim = useMemo(() => {
    if (legs.length === 0 || !Number.isFinite(simulatedSpot) || simulatedSpot <= 0) {
      return null;
    }
    let pnl = 0;
    let valid = true;
    const aggLegs: Array<{
      action: "BUY" | "SELL";
      lots: number;
      lotSize: number;
      ltp: number;
      greeks: Greeks;
    }> = [];

    for (const leg of legs) {
      const newPrice = simulateLegPrice(
        leg,
        simulatedSpot,
        daysForward,
        ivShiftPct,
      );
      if (!Number.isFinite(newPrice)) {
        valid = false;
        break;
      }
      const sign = actionSign(leg.action);
      pnl += sign * (newPrice - leg.entryPrice) * leg.lots * leg.lotSize;

      const newT = Math.max(leg.T0 - daysForward / 365, 0.0001);
      const newSigma = Math.max(leg.sigmaEntry + ivShiftPct / 100, 1e-6);
      const g = legGreeks(
        simulatedSpot,
        leg.K,
        newT,
        0,
        newSigma,
        leg.flag,
        newPrice,
      );
      aggLegs.push({
        action: leg.action,
        lots: leg.lots,
        lotSize: leg.lotSize,
        ltp: newPrice,
        greeks: g,
      });
    }
    if (!valid) return null;
    const agg = aggregatePosition(aggLegs);
    return { pnl, agg };
  }, [legs, simulatedSpot, daysForward, ivShiftPct]);

  const active = spotShiftPct !== 0 || ivShiftPct !== 0 || daysForward !== 0;

  // Surface the simulation upstream so PayoffChart can draw a marker.
  useEffect(() => {
    if (!onSimulationChange) return;
    onSimulationChange({
      active,
      simulatedSpot,
      simulatedPnl: sim?.pnl ?? null,
      spotShiftPct,
      ivShiftPct,
      daysForward,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, simulatedSpot, sim?.pnl, spotShiftPct, ivShiftPct, daysForward]);

  const handleReset = () => {
    setSpotShiftPct(0);
    setIvShiftPct(0);
    setDaysForward(0);
  };

  if (legs.length === 0) {
    return (
      <div className="rounded-md border border-border bg-muted/20 p-3 text-xs text-muted-foreground">
        What-if sliders activate once the snapshot has solved IV for every
        leg. Add legs and pick strikes — they'll show up here.
      </div>
    );
  }

  return (
    <div className="space-y-3 rounded-md border border-border bg-muted/30 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-medium">What-if simulator</p>
        <div className="flex items-center gap-2">
          {active && (
            <span className="text-[10px] font-medium uppercase tracking-wide text-amber-600 dark:text-amber-400">
              Active
            </span>
          )}
          <Button variant="ghost" size="sm" onClick={handleReset} disabled={!active}>
            Reset
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <SliderRow
          label="Spot shift"
          unit="%"
          value={spotShiftPct}
          min={-SPOT_RANGE}
          max={SPOT_RANGE}
          step={SPOT_STEP}
          onChange={setSpotShiftPct}
          formatValue={(v) => fmtSigned(v, 1)}
          subtext={`Simulated spot ${simulatedSpot.toFixed(2)}`}
        />
        <SliderRow
          label="IV shift"
          unit="pp"
          value={ivShiftPct}
          min={-IV_RANGE}
          max={IV_RANGE}
          step={IV_STEP}
          onChange={setIvShiftPct}
          formatValue={(v) => fmtSigned(v, 1)}
          subtext={`Vol of every leg shifted by ${fmtSigned(ivShiftPct, 1)}pp`}
        />
        <SliderRow
          label="Days forward"
          unit="d"
          value={daysForward}
          min={0}
          max={maxDays}
          step={1}
          onChange={(v) => setDaysForward(Math.round(v))}
          formatValue={(v) => `${v}`}
          subtext={
            maxDays === 0
              ? "Already at expiry"
              : `Capped at min-DTE (${maxDays}d)`
          }
        />
      </div>

      {/* Simulated outputs */}
      {sim && (
        <div className="grid grid-cols-2 gap-2 border-t border-border pt-3 sm:grid-cols-4">
          <Stat
            label="Simulated P&L"
            value={fmtSigned(sim.pnl, 2)}
            tone={signTone(sim.pnl)}
            big
          />
          <Stat
            label="Simulated Delta"
            value={sim.agg.delta.toFixed(2)}
          />
          <Stat
            label="Simulated Gamma"
            value={sim.agg.gamma.toFixed(4)}
          />
          <Stat
            label="Simulated Theta /day"
            value={sim.agg.theta.toFixed(2)}
            tone={signTone(sim.agg.theta)}
          />
        </div>
      )}
    </div>
  );
}

function SliderRow({
  label,
  unit,
  value,
  min,
  max,
  step,
  onChange,
  formatValue,
  subtext,
}: {
  label: string;
  unit: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
  formatValue: (v: number) => string;
  subtext: string;
}) {
  return (
    <div className="space-y-1">
      <div className="flex items-baseline justify-between">
        <span className="text-xs text-muted-foreground">{label}</span>
        <span className="font-mono text-xs font-semibold tabular-nums">
          {formatValue(value)}
          {unit}
        </span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-primary"
      />
      <p className="text-[10px] text-muted-foreground">{subtext}</p>
    </div>
  );
}

function Stat({
  label,
  value,
  tone = "",
  big = false,
}: {
  label: string;
  value: string;
  tone?: string;
  big?: boolean;
}) {
  return (
    <div className="space-y-0.5">
      <p className="text-[10px] uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
      <p
        className={cn(
          "font-mono tabular-nums",
          big ? "text-base font-bold" : "text-sm font-semibold",
          tone,
        )}
      >
        {value}
      </p>
    </div>
  );
}
