/**
 * Probability of Profit (POP) under a lognormal terminal-spot model.
 *
 * Assumptions:
 *   - log-returns are normal: ln(S_T / S) ~ N(-½σ²T, σ²T)
 *     (martingale measure with drift = 0, i.e. r = q = 0 — the same
 *      assumption the rest of OpenBull's options math uses for INR
 *      index options)
 *   - σ is a single representative IV. We use the average of the
 *     legs' solved IVs as a pragmatic blend; using the min-DTE leg
 *     would make POP horizon-correct but we'd also have to pick one
 *     leg's strike to feed σ, which is arbitrary.
 *   - T is the *minimum* leg's time-to-expiry. POP measures whether
 *     the strategy is profitable at the nearest leg's expiry — a
 *     longer-dated leg can keep ticking, but it doesn't have a
 *     pin-the-strike payoff at the same horizon.
 *
 * Algorithm:
 *   1. Identify profit regions on the at-expiry curve (contiguous
 *      stretches where pnl > 0). Use a fine sample so we don't miss
 *      narrow inner profit pockets in iron-condor-like setups.
 *   2. For each region [a, b], probability = F(b) − F(a) where
 *      F(x) = N((ln(x/S) + ½σ²T) / (σ√T))
 *      (CDF of S_T under the martingale measure).
 *   3. Sum probabilities across regions.
 *
 * Returns a value in [0, 1]; null when σ or T can't be derived.
 */

import { normCdf, payoffAtExpiry, type PayoffLeg } from "@/lib/black76";

export interface PopInputs {
  legs: PayoffLeg[];
  /** Current underlying spot. */
  spot: number;
  /** Decimal IV per leg (0.18 = 18%). */
  legIvDecimals: number[];
  /** Years to expiry per leg, indexed in lockstep with legIvDecimals. */
  legDteYears: number[];
  /** Sample resolution. 800 is plenty for typical strategies; cheaper
   *  callers can pass 400 with negligible accuracy loss. */
  steps?: number;
  /** How wide to sample around spot. ±5σ covers >99.99997% of the
   *  lognormal mass — anything outside contributes < 1e-6. */
  sigmaWidth?: number;
}

export interface PopResult {
  /** Probability in [0, 1]. */
  probability: number;
  /** Profit regions used in the calculation, for chart annotation. */
  profitRegions: Array<{ from: number; to: number }>;
  /** σ used (decimal). */
  sigma: number;
  /** T used (years). */
  T: number;
}

export function probabilityOfProfit(inputs: PopInputs): PopResult | null {
  const {
    legs,
    spot,
    legIvDecimals,
    legDteYears,
    steps = 800,
    sigmaWidth = 5,
  } = inputs;

  if (legs.length === 0 || legIvDecimals.length === 0 || legDteYears.length === 0) {
    return null;
  }
  // Use the shortest-DTE leg's expiry — that's when the strategy's payoff
  // crystallises for at least one leg. The aggregate IV blend gives σ.
  const T = Math.min(...legDteYears);
  const validIvs = legIvDecimals.filter((v) => Number.isFinite(v) && v > 0);
  if (T <= 0 || validIvs.length === 0) return null;
  const sigma = validIvs.reduce((s, v) => s + v, 0) / validIvs.length;
  if (sigma <= 0 || !Number.isFinite(sigma)) return null;

  // Sample the at-expiry curve over a wide enough range that profit
  // regions outside the strikes (e.g. long-straddle wings) are captured.
  const sigmaPrice = spot * sigma * Math.sqrt(T);
  const lo = Math.max(spot - sigmaWidth * sigmaPrice, 1e-6);
  const hi = spot + sigmaWidth * sigmaPrice;
  const dx = (hi - lo) / Math.max(steps - 1, 1);

  // Walk the curve, collect profit regions.
  const regions: Array<{ from: number; to: number }> = [];
  let inProfit = false;
  let regionStart = 0;
  for (let i = 0; i < steps; i++) {
    const x = lo + i * dx;
    const pnl = payoffAtExpiry(legs, x);
    if (pnl > 0 && !inProfit) {
      inProfit = true;
      regionStart = x;
    } else if (pnl <= 0 && inProfit) {
      inProfit = false;
      regions.push({ from: regionStart, to: x });
    }
  }
  if (inProfit) {
    regions.push({ from: regionStart, to: hi });
  }

  // Lognormal CDF under r = q = 0 (martingale measure with negative
  // half-vol drift): P(S_T < x) = N(ln(x/S)/(σ√T) + ½σ√T).
  // Equivalent to N((ln(x/S) + ½σ²T)/(σ√T)).
  const sqrtT = Math.sqrt(T);
  const cdf = (x: number): number => {
    if (x <= 0) return 0;
    const z = (Math.log(x / spot) + 0.5 * sigma * sigma * T) / (sigma * sqrtT);
    return normCdf(z);
  };

  let probability = 0;
  for (const r of regions) {
    probability += cdf(r.to) - cdf(r.from);
  }

  // Clamp — float drift can push a "should be 0.999999" to 1.000003.
  if (probability < 0) probability = 0;
  if (probability > 1) probability = 1;

  return { probability, profitRegions: regions, sigma, T };
}
