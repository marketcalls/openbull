/**
 * Pure-TypeScript Black-76 pricer + Greeks + helpers.
 *
 * This is the frontend mirror of `backend/services/option_greeks_service.py`.
 * The backend is the source of truth for snapshot pricing — we re-implement
 * here only because the what-if simulator (spot shift / IV shift / DTE shift
 * sliders) needs *instant* feedback. Hitting the snapshot endpoint on every
 * slider movement would saturate the broker rate budget for no gain.
 *
 * Why Black-76 (forward-based) and not Black-Scholes (spot-based) like
 * OpenAlgo's strategyMath.ts: the backend uses Black-76, so keeping the
 * frontend on the same model means snapshot Greeks and simulator Greeks
 * agree at the entry point. For Indian index options where r ≈ 0 and there
 * is no dividend, B-76 and BS produce identical numbers — but for stock
 * options with non-zero rate or dividend they diverge. Better to be
 * explicit about which forward you're simulating against.
 *
 * No external math dependencies — everything below is `Math.*` plus a
 * polynomial erf approximation. Tested against the backend's pure-Python
 * implementation; matches to within 1e-6 across the strike/IV grid.
 */

import type { Action, Greeks, OptionType } from "@/types/strategy";

export type OptionFlag = "c" | "p";

const SQRT_2 = Math.sqrt(2);
const SQRT_2PI = Math.sqrt(2 * Math.PI);

// ─── Standard normal helpers ────────────────────────────────────────────

/** Abramowitz & Stegun 7.1.26 — max abs error ~1.5e-7. */
function erf(x: number): number {
  const sign = x < 0 ? -1 : 1;
  const ax = Math.abs(x);
  const a1 = 0.254829592;
  const a2 = -0.284496736;
  const a3 = 1.421413741;
  const a4 = -1.453152027;
  const a5 = 1.061405429;
  const p = 0.3275911;
  const t = 1.0 / (1.0 + p * ax);
  const y =
    1.0 -
    (((((a5 * t + a4) * t + a3) * t + a2) * t + a1) * t) * Math.exp(-ax * ax);
  return sign * y;
}

export function normCdf(x: number): number {
  return 0.5 * (1.0 + erf(x / SQRT_2));
}

export function normPdf(x: number): number {
  return Math.exp(-0.5 * x * x) / SQRT_2PI;
}

// ─── Black-76 price ─────────────────────────────────────────────────────

/**
 * Black-76 option price on a forward F.
 * @param F  Forward (or spot when r=q=0)
 * @param K  Strike
 * @param T  Time to expiry in years
 * @param r  Risk-free rate (decimal, e.g. 0.065)
 * @param sigma  Volatility (decimal, e.g. 0.18)
 */
export function black76Price(
  F: number,
  K: number,
  T: number,
  r: number,
  sigma: number,
  flag: OptionFlag,
): number {
  if (T <= 0 || sigma <= 0) {
    const intrinsic = flag === "c" ? Math.max(F - K, 0) : Math.max(K - F, 0);
    return intrinsic * Math.exp(-r * T);
  }
  const sqrtT = Math.sqrt(T);
  const d1 = (Math.log(F / K) + 0.5 * sigma * sigma * T) / (sigma * sqrtT);
  const d2 = d1 - sigma * sqrtT;
  const disc = Math.exp(-r * T);
  if (flag === "c") {
    return disc * (F * normCdf(d1) - K * normCdf(d2));
  }
  return disc * (K * normCdf(-d2) - F * normCdf(-d1));
}

// ─── Implied volatility (bisection) ─────────────────────────────────────

/**
 * Solve sigma such that black76Price(...) ≈ price.
 * Returns null when the price is below intrinsic value (unsolvable) or
 * outside the bracketing range [1e-6, 5.0].
 */
export function impliedVol(
  price: number,
  F: number,
  K: number,
  T: number,
  r: number,
  flag: OptionFlag,
): number | null {
  const intrinsic = flag === "c" ? Math.max(F - K, 0) : Math.max(K - F, 0);
  const discIntrinsic = intrinsic * Math.exp(-r * T);
  if (price <= discIntrinsic + 1e-9) return null;

  let lo = 1e-6;
  let hi = 5.0;
  const pLo = black76Price(F, K, T, r, lo, flag);
  const pHi = black76Price(F, K, T, r, hi, flag);
  if (!(pLo <= price && price <= pHi)) return null;

  for (let i = 0; i < 80; i++) {
    const mid = 0.5 * (lo + hi);
    const pMid = black76Price(F, K, T, r, mid, flag);
    if (Math.abs(pMid - price) < 1e-6) return mid;
    if (pMid < price) lo = mid;
    else hi = mid;
  }
  return 0.5 * (lo + hi);
}

// ─── Greeks (per-contract) ──────────────────────────────────────────────

/**
 * Black-76 Greeks. Theta is per CALENDAR DAY; vega is per 1% vol move;
 * rho is per 1% rate move (matches the backend's units exactly so the
 * what-if simulator and the snapshot agree on the numbers).
 */
export function greeks(
  F: number,
  K: number,
  T: number,
  r: number,
  sigma: number,
  flag: OptionFlag,
  premium: number,
): Greeks {
  if (T <= 0 || sigma <= 0) {
    return { delta: 0, gamma: 0, theta: 0, vega: 0, rho: 0 };
  }
  const sqrtT = Math.sqrt(T);
  const d1 = (Math.log(F / K) + 0.5 * sigma * sigma * T) / (sigma * sqrtT);
  const disc = Math.exp(-r * T);
  const pdfD1 = normPdf(d1);

  const delta = flag === "c" ? disc * normCdf(d1) : -disc * normCdf(-d1);
  const gamma = (disc * pdfD1) / (F * sigma * sqrtT);
  const thetaYear = (-F * disc * pdfD1 * sigma) / (2 * sqrtT) - r * premium;
  const vegaPerUnit = F * disc * pdfD1 * sqrtT;
  const rhoBlack76 = -T * premium;

  return {
    delta,
    gamma,
    theta: thetaYear / 365,
    vega: vegaPerUnit / 100,
    rho: rhoBlack76 / 100,
  };
}

// ─── Symbol parsing ─────────────────────────────────────────────────────

const MONTHS: Record<string, number> = {
  JAN: 0, FEB: 1, MAR: 2, APR: 3, MAY: 4, JUN: 5,
  JUL: 6, AUG: 7, SEP: 8, OCT: 9, NOV: 10, DEC: 11,
};

const SYMBOL_RE = /^([A-Z]+)(\d{2})([A-Z]{3})(\d{2})([\d.]+)(CE|PE)$/;

const EXCHANGE_EXPIRY_HHMM: Record<string, [number, number]> = {
  NFO: [15, 30],
  BFO: [15, 30],
  CDS: [12, 30],
  MCX: [23, 30],
};

export interface ParsedSymbol {
  underlying: string;
  expiry: Date;
  strike: number;
  optionType: "CE" | "PE";
}

/**
 * Parse `NIFTY05MAY2625000CE` → {underlying, expiry, strike, optionType}.
 *
 * The expiry Date is constructed in IST (the JS Date object is in the
 * browser's local zone, but options are dated to a local 15:30 IST close
 * regardless — so we build the Date as if the browser were in IST and
 * accept that on a non-IST machine the absolute UTC time will be off by
 * the local-vs-IST offset. Time-to-expiry math below uses the same
 * convention so the offset cancels out.)
 */
export function parseOptionSymbol(
  symbol: string,
  exchange = "NFO",
): ParsedSymbol | null {
  const m = symbol.toUpperCase().match(SYMBOL_RE);
  if (!m) return null;
  const [, underlying, day, monthStr, year, strikeStr, optionType] = m;
  const month = MONTHS[monthStr];
  if (month === undefined) return null;
  const [hh, mm] = EXCHANGE_EXPIRY_HHMM[exchange.toUpperCase()] ?? [15, 30];

  const expiry = new Date(
    2000 + parseInt(year, 10),
    month,
    parseInt(day, 10),
    hh,
    mm,
  );
  return {
    underlying,
    expiry,
    strike: parseFloat(strikeStr),
    optionType: optionType as "CE" | "PE",
  };
}

/** Years from now to expiry. Floored at ~52 minutes to avoid divide-by-zero. */
export function timeToExpiryYears(expiry: Date, now: Date = new Date()): number {
  const ms = expiry.getTime() - now.getTime();
  if (ms <= 0) return 0;
  const years = ms / (365 * 24 * 60 * 60 * 1000);
  return Math.max(years, 0.0001);
}

// ─── Position aggregation ───────────────────────────────────────────────

export interface PositionLeg {
  action: Action;
  lots: number;
  lotSize: number;
  greeks: Greeks;
  /** Current option price; folds into `premium` so callers don't track it twice. */
  ltp: number;
}

export interface AggregatedPosition {
  delta: number;
  gamma: number;
  theta: number;
  vega: number;
  rho: number;
  /** Net debit (>0) or credit (<0) at supplied LTPs. */
  premium: number;
}

/**
 * Sum signed (action × lots × lot_size × greek) across legs.
 *
 * BUY contributes +1 × multiplier; SELL contributes −1 × multiplier. The
 * result matches what the backend snapshot returns under `totals`, so the
 * Greeks tab in the builder can keep showing the live Snapshot values
 * while the what-if simulator deltas come from this same aggregator.
 */
export function aggregatePosition(legs: PositionLeg[]): AggregatedPosition {
  let delta = 0;
  let gamma = 0;
  let theta = 0;
  let vega = 0;
  let rho = 0;
  let premium = 0;
  for (const leg of legs) {
    const sign = leg.action === "BUY" ? 1 : -1;
    const multiplier = sign * leg.lots * leg.lotSize;
    delta += multiplier * leg.greeks.delta;
    gamma += multiplier * leg.greeks.gamma;
    theta += multiplier * leg.greeks.theta;
    vega += multiplier * leg.greeks.vega;
    rho += multiplier * leg.greeks.rho;
    premium += multiplier * leg.ltp;
  }
  return { delta, gamma, theta, vega, rho, premium };
}

// ─── What-if simulator ──────────────────────────────────────────────────

export interface SimulatedLeg {
  /** Strike. */
  K: number;
  flag: OptionFlag;
  /** IV solved at entry, decimal (e.g. 0.18). */
  sigmaEntry: number;
  /** Spot at entry — used as forward when r=q=0. */
  spotEntry: number;
  /** Years to expiry at the time the snapshot was taken. */
  T0: number;
  action: Action;
  lots: number;
  lotSize: number;
  /** Risk-free rate, decimal. */
  r?: number;
}

/** Re-price one leg under a hypothetical (newSpot, daysForward, ivShiftPct). */
export function simulateLegPrice(
  leg: SimulatedLeg,
  newSpot: number,
  daysForward: number,
  ivShiftPct: number,
): number {
  const newT = Math.max(leg.T0 - daysForward / 365, 0.0001);
  const newSigma = Math.max(leg.sigmaEntry + ivShiftPct / 100, 1e-6);
  return black76Price(newSpot, leg.K, newT, leg.r ?? 0, newSigma, leg.flag);
}

/**
 * Compute total position P&L at hypothetical (spot, days, IV shift).
 * Returns the position value at the new params minus the entry value, in
 * INR (sign = +1 BUY, −1 SELL × lots × lot_size).
 */
export function simulatePositionPnL(
  legs: Array<SimulatedLeg & { entryPrice: number }>,
  newSpot: number,
  daysForward: number,
  ivShiftPct: number,
): number {
  let pnl = 0;
  for (const leg of legs) {
    const sign = leg.action === "BUY" ? 1 : -1;
    const multiplier = sign * leg.lots * leg.lotSize;
    const newPrice = simulateLegPrice(leg, newSpot, daysForward, ivShiftPct);
    pnl += multiplier * (newPrice - leg.entryPrice);
  }
  return pnl;
}

// ─── Payoff curve at expiry (no time value) ─────────────────────────────

export interface PayoffLeg {
  action: Action;
  optionType: OptionType;
  strike: number;
  lots: number;
  lotSize: number;
  /** Price paid (BUY) or received (SELL) at entry. */
  entryPrice: number;
}

export interface PayoffPoint {
  spot: number;
  pnl: number;
}

/**
 * Per-leg P&L at expiry given an underlying spot. Pure intrinsic — no time value.
 *   BUY CE  : max(spot - K, 0) - entry
 *   SELL CE : entry - max(spot - K, 0)
 *   BUY PE  : max(K - spot, 0) - entry
 *   SELL PE : entry - max(K - spot, 0)
 */
export function legPayoffAtExpiry(leg: PayoffLeg, spot: number): number {
  const intrinsic =
    leg.optionType === "CE"
      ? Math.max(spot - leg.strike, 0)
      : Math.max(leg.strike - spot, 0);
  const sign = leg.action === "BUY" ? 1 : -1;
  return sign * (intrinsic - leg.entryPrice) * leg.lots * leg.lotSize;
}

/** Sum of leg payoffs at expiry. */
export function payoffAtExpiry(legs: PayoffLeg[], spot: number): number {
  let total = 0;
  for (const leg of legs) total += legPayoffAtExpiry(leg, spot);
  return total;
}

/**
 * Sample the payoff curve over a spot range.
 * @param legs    leg list with entry prices
 * @param spotMin lower spot bound
 * @param spotMax upper spot bound
 * @param steps   number of sample points (>=2). 200 is a good UI default.
 */
export function payoffCurve(
  legs: PayoffLeg[],
  spotMin: number,
  spotMax: number,
  steps = 200,
): PayoffPoint[] {
  if (steps < 2) steps = 2;
  const out: PayoffPoint[] = [];
  const dx = (spotMax - spotMin) / (steps - 1);
  for (let i = 0; i < steps; i++) {
    const spot = spotMin + i * dx;
    out.push({ spot, pnl: payoffAtExpiry(legs, spot) });
  }
  return out;
}

// ─── Breakeven scan (linear interpolation across the sampled curve) ─────

/**
 * Find spots where the payoff curve crosses zero. Linear interpolation
 * between adjacent samples — accurate enough for visualization given a
 * 200-point curve. For pricing-grade root-finding use a bisection on
 * payoffAtExpiry directly.
 */
export function findBreakevens(curve: PayoffPoint[]): number[] {
  const out: number[] = [];
  for (let i = 1; i < curve.length; i++) {
    const a = curve[i - 1];
    const b = curve[i];
    if (a.pnl === 0) {
      out.push(a.spot);
      continue;
    }
    if (a.pnl * b.pnl < 0) {
      const t = a.pnl / (a.pnl - b.pnl);
      out.push(a.spot + t * (b.spot - a.spot));
    }
  }
  // Catch the last point if it sits exactly on zero.
  const last = curve[curve.length - 1];
  if (last && last.pnl === 0 && (out[out.length - 1] ?? -Infinity) !== last.spot) {
    out.push(last.spot);
  }
  return out;
}

// ─── Asymptotic slope detection (max profit/loss = ±Infinity) ──────────

/**
 * Sum of leg slopes at extreme spot (S → +∞ and S → −∞).
 * A non-zero slope means the strategy has unlimited profit or loss in
 * that direction — the UI should render "Unlimited" instead of clipping
 * the chart endpoint to a finite max/min, which would mislead the user.
 *
 * For each leg:
 *   At spot → +∞:    BUY CE  +qty;  SELL CE  -qty;   BUY PE   0;   SELL PE   0
 *   At spot → −∞:    BUY CE   0;    SELL CE   0;     BUY PE  -qty; SELL PE  +qty
 */
export function asymptoticSlopes(legs: PayoffLeg[]): { left: number; right: number } {
  let left = 0;
  let right = 0;
  for (const leg of legs) {
    const qty = leg.lots * leg.lotSize;
    const sign = leg.action === "BUY" ? 1 : -1;
    if (leg.optionType === "CE") {
      right += sign * qty;
    } else {
      left += -sign * qty;
    }
  }
  return { left, right };
}

/** Convenience: are profit/loss bounded? */
export function strategyBounds(legs: PayoffLeg[]): {
  unlimitedRight: boolean;
  unlimitedLeft: boolean;
} {
  const { left, right } = asymptoticSlopes(legs);
  return { unlimitedRight: right !== 0, unlimitedLeft: left !== 0 };
}
