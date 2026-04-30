/**
 * Payoff chart — At-Expiry + T+0 curves with breakevens, ±1σ/±2σ bands,
 * and a spot vertical line.
 *
 * Inputs are the snapshot result + entry prices from the builder. This
 * component does NO broker IO of its own — all numbers are derived from
 * what the snapshot already returned (per-leg IV, dte, ltp) plus the
 * user-entered entry prices for the at-expiry payoff. Re-renders are
 * cheap because the heavy curve generation is memoised on (legs, spot,
 * range, isDark).
 *
 * Sigma-band sizing uses the *minimum* leg DTE — the chart's relevance
 * shrinks to the nearest expiry, so showing a 30-day band on a 5-day
 * straddle would be misleading.
 *
 * Asymptotic-slope check: if the strategy has unlimited loss/profit on
 * either side, we render an "Unlimited" annotation at the chart edge.
 * This is the OpenAlgo trick that prevents the user from misreading a
 * clipped chart endpoint as a hard floor/ceiling.
 */

import { useMemo } from "react";

import Plot from "@/components/charts/Plot";
import { useTheme } from "@/contexts/ThemeContext";
import {
  asymptoticSlopes,
  black76Price,
  findBreakevens,
  payoffAtExpiry,
  type PayoffLeg,
} from "@/lib/black76";
import { probabilityOfProfit } from "@/lib/probabilityOfProfit";
import type { Greeks, SnapshotLegOutput } from "@/types/strategy";

/** Like PayoffLeg, plus IV + remaining time so we can re-price for T+0. */
interface EnrichedLeg extends PayoffLeg {
  /** Decimal — 0.18 for 18% IV. */
  ivDecimal: number;
  /** Years to expiry, floored at 0.0001 by the math lib. */
  dteYears: number;
  symbol: string;
  greeks: Greeks;
}

/** Marker the WhatIfPanel asks the chart to draw + simulation parameters
 *  the T+0 curve uses to re-price the legs.
 *
 *  - `spot` / `pnl` drive the magenta dot on the chart.
 *  - `ivShiftPct` / `daysForward` shift the IV and days-to-expiry the T+0
 *    curve uses for every sample point, so the dashed T+0 line responds
 *    to the IV / Days sliders (not just the Spot slider). When all three
 *    are zero the T+0 curve renders at "now". */
export interface SimulationMarker {
  spot: number;
  pnl: number | null;
  /** Mostly-cosmetic label so the user knows which sliders are non-zero. */
  label?: string;
  /** Vol shift in percentage points to add to every leg's IV. 0 = no shift. */
  ivShiftPct?: number;
  /** Calendar days to advance time. 0 = right now. */
  daysForward?: number;
}

interface Props {
  /** Snapshot legs — used as the source for IV / DTE / LTP / option metadata. */
  snapshotLegs: SnapshotLegOutput[];
  /** User-entered entry prices keyed by leg symbol — falls back to ltp when missing. */
  entryPriceBySymbol: Record<string, number>;
  spot: number;
  /** Optional override for the x-axis range. */
  spotRange?: [number, number];
  /** Number of sample points on each curve. 200 is a good default. */
  steps?: number;
  /** Optional what-if marker. When set, a magenta dot is plotted at
   *  the simulated point with a vertical guide line. Updates cheaply
   *  on every slider tick — no curve recomputation. */
  simulationMarker?: SimulationMarker | null;
}

/** Build enriched legs, dropping any that don't have enough info to price. */
function buildEnrichedLegs(
  snapshotLegs: SnapshotLegOutput[],
  entryPriceBySymbol: Record<string, number>,
): EnrichedLeg[] {
  const out: EnrichedLeg[] = [];
  for (const l of snapshotLegs) {
    if (
      !l.strike ||
      !l.option_type ||
      l.ltp == null ||
      l.days_to_expiry == null ||
      !l.greeks
    ) {
      continue;
    }
    const entry = entryPriceBySymbol[l.symbol] ?? l.ltp;
    out.push({
      action: l.action,
      optionType: l.option_type,
      strike: l.strike,
      lots: l.lots,
      lotSize: l.lot_size,
      entryPrice: entry > 0 ? entry : l.ltp,
      ivDecimal: (l.implied_volatility ?? 0) / 100,
      dteYears: Math.max((l.days_to_expiry ?? 0) / 365, 0.0001),
      symbol: l.symbol,
      greeks: l.greeks,
    });
  }
  return out;
}

/** P&L if the user closed at the given spot, with optional IV shift (in
 *  percentage points) and time forward (days). Both default to 0 → priced
 *  with the leg's currently-solved IV at "now". The shifts let the T+0
 *  curve respond to the What-If panel's IV and Days Forward sliders. */
function tPlusZeroPnl(
  legs: EnrichedLeg[],
  spot: number,
  ivShiftPct = 0,
  daysForward = 0,
): number {
  let total = 0;
  for (const leg of legs) {
    const sign = leg.action === "BUY" ? 1 : -1;
    const flag = leg.optionType === "CE" ? "c" : "p";
    // Shift IV by the slider's percentage points (e.g. ivShiftPct=+5 on a
    // leg with iv=18% gives 23%). Floor at 0.001 to keep Black-76 stable.
    const sigma = Math.max(leg.ivDecimal + ivShiftPct / 100, 0.001);
    // Advance time by daysForward; floor remaining time at the math lib's
    // tiny epsilon so legs at expiry still price (intrinsic only).
    const tYears = Math.max(leg.dteYears - daysForward / 365, 0.0001);
    // r = 0 — matches the backend's INR-options default.
    const theoretical =
      sigma > 0
        ? black76Price(spot, leg.strike, tYears, 0, sigma, flag)
        : Math.max(
            flag === "c" ? spot - leg.strike : leg.strike - spot,
            0,
          );
    total += sign * (theoretical - leg.entryPrice) * leg.lots * leg.lotSize;
  }
  return total;
}

function pickSpotRange(
  legs: EnrichedLeg[],
  spot: number,
  override?: [number, number],
): [number, number] {
  if (override) return override;
  if (legs.length === 0) {
    return [spot * 0.85, spot * 1.15];
  }
  const strikeMin = Math.min(...legs.map((l) => l.strike));
  const strikeMax = Math.max(...legs.map((l) => l.strike));
  // Pad to whichever is wider — strikes ±5% or spot ±15% — so the breakevens
  // and asymptotic edges are visible.
  const lo = Math.min(strikeMin * 0.95, spot * 0.85);
  const hi = Math.max(strikeMax * 1.05, spot * 1.15);
  return [lo, hi];
}

export function PayoffChart({
  snapshotLegs,
  entryPriceBySymbol,
  spot,
  spotRange,
  steps = 200,
  simulationMarker = null,
}: Props) {
  const { theme } = useTheme();
  const isDark = theme === "dark";

  const legs = useMemo(
    () => buildEnrichedLegs(snapshotLegs, entryPriceBySymbol),
    [snapshotLegs, entryPriceBySymbol],
  );

  const range = useMemo(
    () => pickSpotRange(legs, spot, spotRange),
    [legs, spot, spotRange],
  );

  // Probability of Profit — uses the same payoff legs and the snapshot's
  // solved IVs / DTEs. Cheap (one extra at-expiry sweep over an 800-pt
  // grid), so we recompute on every leg or spot change — same dep set
  // the curve already has, so React batches.
  const pop = useMemo(() => {
    if (legs.length === 0) return null;
    const payoffLegs: PayoffLeg[] = legs.map((l) => ({
      action: l.action,
      optionType: l.optionType,
      strike: l.strike,
      lots: l.lots,
      lotSize: l.lotSize,
      entryPrice: l.entryPrice,
    }));
    return probabilityOfProfit({
      legs: payoffLegs,
      spot,
      legIvDecimals: legs.map((l) => l.ivDecimal),
      legDteYears: legs.map((l) => l.dteYears),
    });
  }, [legs, spot]);

  // Pull simulation shifts off the marker so the T+0 curve responds to the
  // IV / Days sliders, not just the spot slider. Defaults to 0 when no
  // simulation is active so the curve renders "at now" as before.
  const simIvShiftPct = simulationMarker?.ivShiftPct ?? 0;
  const simDaysForward = simulationMarker?.daysForward ?? 0;

  const { xs, expiryY, t0Y, breakevens, bounds, sigmaXs } = useMemo(() => {
    const [lo, hi] = range;
    const xsLocal: number[] = new Array(steps);
    const expiryLocal: number[] = new Array(steps);
    const t0Local: number[] = new Array(steps);
    const dx = (hi - lo) / Math.max(steps - 1, 1);
    const payoffLegs: PayoffLeg[] = legs.map((l) => ({
      action: l.action,
      optionType: l.optionType,
      strike: l.strike,
      lots: l.lots,
      lotSize: l.lotSize,
      entryPrice: l.entryPrice,
    }));
    for (let i = 0; i < steps; i++) {
      const x = lo + i * dx;
      xsLocal[i] = x;
      expiryLocal[i] = payoffAtExpiry(payoffLegs, x);
      t0Local[i] =
        legs.length > 0
          ? tPlusZeroPnl(legs, x, simIvShiftPct, simDaysForward)
          : 0;
    }
    const beCurve = xsLocal.map((x, i) => ({ spot: x, pnl: expiryLocal[i] }));
    const breakevensLocal = findBreakevens(beCurve);
    const boundsLocal =
      payoffLegs.length > 0
        ? (() => {
            const slopes = asymptoticSlopes(payoffLegs);
            return {
              unlimitedRight: slopes.right !== 0,
              unlimitedLeft: slopes.left !== 0,
            };
          })()
        : { unlimitedRight: false, unlimitedLeft: false };

    // Sigma bands sized off the SHORTEST DTE leg so the band is meaningful
    // for the strategy's nearest-expiry exposure.
    const minDte = legs.length
      ? Math.min(...legs.map((l) => l.dteYears))
      : 0;
    const refIv = legs.length
      ? legs.reduce((s, l) => s + l.ivDecimal, 0) / legs.length
      : 0;
    const sigmaPrice = spot * refIv * Math.sqrt(minDte);
    const sigmaLocal = sigmaPrice > 0 ? [sigmaPrice, sigmaPrice * 2] : [];

    return {
      xs: xsLocal,
      expiryY: expiryLocal,
      t0Y: t0Local,
      breakevens: breakevensLocal,
      bounds: boundsLocal,
      sigmaXs: sigmaLocal,
    };
  }, [range, steps, legs, spot, simIvShiftPct, simDaysForward]);

  const colors = useMemo(
    () => ({
      text: isDark ? "#e0e0e0" : "#333333",
      grid: isDark ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.08)",
      zero: isDark ? "rgba(255,255,255,0.35)" : "rgba(0,0,0,0.35)",
      expiry: "#3b82f6", // blue-500
      t0: isDark ? "#a1a1aa" : "#71717a", // zinc
      profit: "rgba(34, 197, 94, 0.12)", // emerald-500/12
      loss: "rgba(239, 68, 68, 0.12)", // red-500/12
      spot: "#f59e0b", // amber-500
      breakeven: isDark ? "#fbbf24" : "#d97706",
      sigma: isDark ? "rgba(168, 85, 247, 0.25)" : "rgba(139, 92, 246, 0.25)",
      simMarker: "#ec4899", // pink-500 — distinct from spot/breakeven/zone fills
      simGuide: isDark ? "rgba(236, 72, 153, 0.6)" : "rgba(236, 72, 153, 0.6)",
      hoverBg: isDark ? "#1e293b" : "#ffffff",
      hoverText: isDark ? "#e0e0e0" : "#333333",
      hoverBorder: isDark ? "#475569" : "#e2e8f0",
    }),
    [isDark],
  );

  const traces = useMemo<unknown[]>(() => {
    if (legs.length === 0) return [];
    const out: unknown[] = [
      // At-expiry curve — primary
      {
        x: xs,
        y: expiryY,
        type: "scattergl",
        mode: "lines",
        name: "At Expiry",
        line: { color: colors.expiry, width: 2 },
        hovertemplate:
          "Spot %{x:.2f}<br>P&L %{y:.2f}<extra>At Expiry</extra>",
      },
      // T+0 curve — dashed
      {
        x: xs,
        y: t0Y,
        type: "scattergl",
        mode: "lines",
        name: "T+0",
        line: { color: colors.t0, width: 1.5, dash: "dash" },
        hovertemplate: "Spot %{x:.2f}<br>P&L %{y:.2f}<extra>T+0</extra>",
      },
    ];

    // What-if simulation marker — single dot at the simulated point.
    // Re-renders cheaply on every slider tick because it's just two
    // numbers; the curves above are memoised independently.
    if (
      simulationMarker &&
      simulationMarker.pnl !== null &&
      Number.isFinite(simulationMarker.spot) &&
      Number.isFinite(simulationMarker.pnl)
    ) {
      out.push({
        x: [simulationMarker.spot],
        y: [simulationMarker.pnl],
        type: "scatter",
        mode: "markers",
        name: simulationMarker.label ?? "What-if",
        marker: {
          size: 12,
          color: colors.simMarker,
          line: { color: colors.text, width: 1 },
          symbol: "diamond",
        },
        hovertemplate:
          "Spot %{x:.2f}<br>Simulated P&L %{y:.2f}<extra>What-if</extra>",
      });
    }
    return out;
  }, [legs, xs, expiryY, t0Y, colors, simulationMarker]);

  const layout = useMemo<Record<string, unknown>>(() => {
    const [lo, hi] = range;
    const yPad = (() => {
      if (expiryY.length === 0) return 1;
      const yMin = Math.min(...expiryY, ...t0Y);
      const yMax = Math.max(...expiryY, ...t0Y);
      return Math.max(Math.abs(yMin), Math.abs(yMax)) * 0.15 + 1;
    })();
    const yMin =
      expiryY.length === 0 ? -1 : Math.min(...expiryY, ...t0Y) - yPad;
    const yMax =
      expiryY.length === 0 ? 1 : Math.max(...expiryY, ...t0Y) + yPad;

    const shapes: unknown[] = [
      // Profit zone shading (above y=0)
      {
        type: "rect",
        xref: "paper",
        yref: "y",
        x0: 0,
        x1: 1,
        y0: 0,
        y1: yMax,
        fillcolor: colors.profit,
        line: { width: 0 },
        layer: "below",
      },
      // Loss zone shading (below y=0)
      {
        type: "rect",
        xref: "paper",
        yref: "y",
        x0: 0,
        x1: 1,
        y0: yMin,
        y1: 0,
        fillcolor: colors.loss,
        line: { width: 0 },
        layer: "below",
      },
      // Zero line
      {
        type: "line",
        xref: "paper",
        x0: 0,
        x1: 1,
        y0: 0,
        y1: 0,
        line: { color: colors.zero, width: 1 },
      },
      // Spot vertical
      {
        type: "line",
        x0: spot,
        x1: spot,
        yref: "paper",
        y0: 0,
        y1: 1,
        line: { color: colors.spot, width: 1.5, dash: "dot" },
      },
    ];

    // ±σ bands
    for (const s of sigmaXs) {
      shapes.push({
        type: "rect",
        x0: spot - s,
        x1: spot + s,
        yref: "paper",
        y0: 0,
        y1: 1,
        fillcolor: colors.sigma,
        line: { width: 0 },
        layer: "below",
        opacity: 0.3,
      });
    }

    // Breakeven verticals
    for (const be of breakevens) {
      shapes.push({
        type: "line",
        x0: be,
        x1: be,
        yref: "paper",
        y0: 0,
        y1: 1,
        line: { color: colors.breakeven, width: 1, dash: "dot" },
      });
    }

    // What-if simulated-spot guide
    if (
      simulationMarker &&
      Number.isFinite(simulationMarker.spot) &&
      simulationMarker.spot >= lo &&
      simulationMarker.spot <= hi
    ) {
      shapes.push({
        type: "line",
        x0: simulationMarker.spot,
        x1: simulationMarker.spot,
        yref: "paper",
        y0: 0,
        y1: 1,
        line: { color: colors.simGuide, width: 1, dash: "dashdot" },
      });
    }

    const annotations: unknown[] = [];
    annotations.push({
      x: spot,
      yref: "paper",
      y: 1,
      text: `Spot ${spot.toFixed(2)}`,
      showarrow: false,
      font: { color: colors.spot, size: 11 },
      xanchor: "left",
      yanchor: "top",
      bgcolor: "rgba(0,0,0,0)",
    });
    breakevens.forEach((be, i) => {
      annotations.push({
        x: be,
        yref: "paper",
        y: 0,
        text: `BE ${be.toFixed(0)}`,
        showarrow: false,
        font: { color: colors.breakeven, size: 10 },
        xanchor: i === 0 ? "right" : "left",
        yanchor: "top",
      });
    });
    if (bounds.unlimitedLeft) {
      annotations.push({
        x: lo,
        y: yMin,
        text: "← Unlimited loss",
        showarrow: false,
        font: { color: "#ef4444", size: 10 },
        xanchor: "left",
        yanchor: "bottom",
      });
    }
    if (bounds.unlimitedRight) {
      annotations.push({
        x: hi,
        y: yMax,
        text: "Unlimited profit →",
        showarrow: false,
        font: { color: "#22c55e", size: 10 },
        xanchor: "right",
        yanchor: "top",
      });
    }

    // POP corner badge
    if (pop && pop.probability >= 0) {
      annotations.push({
        xref: "paper",
        yref: "paper",
        x: 0.99,
        y: 0.97,
        xanchor: "right",
        yanchor: "top",
        text: `POP ${(pop.probability * 100).toFixed(1)}%`,
        showarrow: false,
        font: { color: colors.text, size: 11 },
        bgcolor: isDark ? "rgba(15,23,42,0.85)" : "rgba(255,255,255,0.85)",
        bordercolor: colors.grid,
        borderpad: 4,
      });
    }

    return {
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)",
      font: { color: colors.text, family: "system-ui, sans-serif" },
      hovermode: "x unified",
      hoverlabel: {
        bgcolor: colors.hoverBg,
        font: { color: colors.hoverText, size: 12 },
        bordercolor: colors.hoverBorder,
      },
      legend: {
        orientation: "h",
        x: 0.5,
        xanchor: "center",
        y: -0.15,
        font: { color: colors.text, size: 11 },
      },
      margin: { l: 70, r: 30, t: 30, b: 60 },
      xaxis: {
        title: { text: "Spot at expiry", font: { color: colors.text, size: 12 } },
        range: [lo, hi],
        gridcolor: colors.grid,
        tickfont: { color: colors.text, size: 10 },
        zeroline: false,
      },
      yaxis: {
        title: { text: "P&L (₹)", font: { color: colors.text, size: 12 } },
        range: [yMin, yMax],
        gridcolor: colors.grid,
        tickfont: { color: colors.text, size: 10 },
        zeroline: false,
      },
      shapes,
      annotations,
    };
  }, [
    range,
    expiryY,
    t0Y,
    sigmaXs,
    breakevens,
    bounds,
    spot,
    colors,
    simulationMarker,
    pop,
    isDark,
  ]);

  const config = useMemo(
    () => ({
      displayModeBar: true,
      displaylogo: false,
      modeBarButtonsToRemove: [
        "pan2d",
        "select2d",
        "lasso2d",
        "autoScale2d",
        "toggleSpikelines",
      ],
      responsive: true,
    }),
    [],
  );

  if (legs.length === 0) {
    return (
      <div className="flex h-[420px] flex-col items-center justify-center gap-1 text-center text-muted-foreground">
        <p className="text-sm">No payoff to draw yet.</p>
        <p className="text-xs">
          Add legs and pick strikes — the payoff updates with the snapshot.
        </p>
      </div>
    );
  }

  return (
    <Plot
      data={traces}
      layout={layout}
      config={config}
      useResizeHandler
      style={{ width: "100%", height: "480px" }}
    />
  );
}
