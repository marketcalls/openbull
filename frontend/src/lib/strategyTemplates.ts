/**
 * Strategy templates — click-to-fill presets for the Strategy Builder.
 *
 * Phase 1 of the openalgo UI port (April 2026): schema migrated from
 * "ATM/ITMn/OTMn" string offsets + 5 categories to signed-integer
 * `strikeOffset` + 3-direction enum + a `payoffPath` SVG path that drives
 * the mini icon rendered in the TemplateGrid card. Source library is
 * openalgo's `frontend/src/lib/strategyTemplates.ts`; the openalgo
 * field names (`side`, `optionType`) have been renamed to openbull's
 * conventions (`action`, `option_type`) so downstream code that consumes
 * leg fields doesn't fork.
 *
 * ---------------------------------------------------------------------------
 * MATH INVARIANTS — read this before editing a template
 * ---------------------------------------------------------------------------
 *
 * 1. `strikeOffset` is a SIGNED integer in units of strike-steps. The sign is
 *    spatial, not directional — positive means "above ATM" regardless of CE
 *    vs PE. Examples (NIFTY at ATM=24000 with 50-point strike step):
 *
 *      strikeOffset:  0  → 24000  (ATM)
 *      strikeOffset: +2  → 24100  (2 strikes up)
 *      strikeOffset: -2  → 23900  (2 strikes down)
 *
 *    This is unambiguous: Bull Call Spread "buy ATM call, sell call 2 strikes
 *    higher" is { action:'BUY', option_type:'CE', strikeOffset:0 } +
 *    { action:'SELL', option_type:'CE', strikeOffset:+2 }. Bull Put Spread
 *    "sell ATM put, buy put 2 strikes lower" is
 *    { action:'SELL', option_type:'PE', strikeOffset:0 } +
 *    { action:'BUY', option_type:'PE', strikeOffset:-2 }.
 *
 * 2. `expiryOffset` is integer index into the user's expiry list:
 *      0 = the expiry the user picked in the page header (default)
 *      1 = next expiry after that (one step further out)
 *      2 = the expiry after that
 *
 *    Used only for calendar / diagonal templates. The Strategy Builder
 *    resolves it against the broker's expiry list for the underlying.
 *
 * 3. Lot multipliers (`lots`) are absolute, not relative — a 1×2 ratio
 *    spread has `lots:1` on the long leg and `lots:2` on the short leg.
 *    Lot size (contract size) is NOT in the template; the builder reads
 *    that off the chain context (or the underlying-default fallback).
 *
 * 4. Templates DO NOT carry any pricing or Greeks. The builder fills
 *    `entry_price` from the chain LTP at the resolved strike, and the
 *    server-side snapshot endpoint computes Greeks / P&L / margin from
 *    the resulting legs. Schema migration cannot break math correctness
 *    — the math pipeline only sees legs (strike, action, lots, lot_size,
 *    expiry_date, option_type, entry_price), all of which are produced
 *    downstream of the template.
 *
 * 5. SVG payoff icon convention:
 *      viewBox is 0 0 100 40
 *      x = 0..100 represents the underlying range, low → high
 *      y = 0 (top, max profit) → 40 (bottom, max loss)
 *      the zero line sits at y = 20
 *      paths use absolute coords ("M0,30 L55,30 L100,2"), no curves
 */

import type { Action, OptionType } from "@/types/strategy";

export type Direction = "BULLISH" | "BEARISH" | "NON_DIRECTIONAL";

export interface TemplateLeg {
  action: Action;
  option_type: OptionType;
  /** Signed integer offset in strike-steps from ATM. 0 = ATM, +2 = 2 strikes up. */
  strikeOffset: number;
  /** Lot multiplier (1, 2, …). 1 by default; 2 for ratio spreads. */
  lots: number;
  /** Expiry index relative to the page's primary expiry. 0 = same expiry. */
  expiryOffset?: number;
}

export interface StrategyTemplate {
  id: string;
  name: string;
  direction: Direction;
  description: string;
  legs: TemplateLeg[];
  /** SVG path d-string drawn in viewBox 0,0 → 100,40 for the mini icon. */
  payoffPath: string;
}

/**
 * The curated set — 30 strategies, ordered for grid display.
 *
 *   BULLISH (9)         · directional, profits as spot rises
 *   BEARISH (9)         · directional, profits as spot falls
 *   NON_DIRECTIONAL (12) · vol/calendar/range plays — no spot-direction view
 *
 * Order within each block was chosen for the openalgo TemplateGrid card grid
 * (most-common first → exotic last) so muscle memory carries across the two
 * apps.
 */
export const STRATEGY_TEMPLATES: StrategyTemplate[] = [
  // ── BULLISH ─────────────────────────────────────────────────────────
  {
    id: "long_call",
    name: "Long Call",
    direction: "BULLISH",
    description: "Unlimited upside, limited downside. Best for a strong bullish view.",
    legs: [{ action: "BUY", option_type: "CE", strikeOffset: 0, lots: 1 }],
    payoffPath: "M0,30 L55,30 L100,2",
  },
  {
    id: "short_put",
    name: "Short Put",
    direction: "BULLISH",
    description: "Collect premium; profit if price stays above strike.",
    legs: [{ action: "SELL", option_type: "PE", strikeOffset: 0, lots: 1 }],
    payoffPath: "M0,38 L50,10 L100,10",
  },
  {
    id: "bull_call_spread",
    name: "Bull Call Spread",
    direction: "BULLISH",
    description: "Buy ATM call, sell OTM call. Capped profit & loss.",
    legs: [
      { action: "BUY", option_type: "CE", strikeOffset: 0, lots: 1 },
      { action: "SELL", option_type: "CE", strikeOffset: 2, lots: 1 },
    ],
    payoffPath: "M0,28 L50,28 L75,6 L100,6",
  },
  {
    id: "bull_put_spread",
    name: "Bull Put Spread",
    direction: "BULLISH",
    description: "Sell ATM put, buy OTM put. Net credit trade.",
    legs: [
      { action: "SELL", option_type: "PE", strikeOffset: 0, lots: 1 },
      { action: "BUY", option_type: "PE", strikeOffset: -2, lots: 1 },
    ],
    payoffPath: "M0,34 L25,34 L50,10 L100,10",
  },
  {
    id: "call_ratio_back_spread",
    name: "Call Ratio Back Spread",
    direction: "BULLISH",
    description:
      "Sell 1 ATM call, buy 2 OTM calls. Small credit; unlimited upside if market rallies hard.",
    legs: [
      { action: "SELL", option_type: "CE", strikeOffset: 0, lots: 1 },
      { action: "BUY", option_type: "CE", strikeOffset: 2, lots: 2 },
    ],
    payoffPath: "M0,18 L40,18 L60,28 L75,22 L100,2",
  },
  {
    id: "long_synthetic",
    name: "Long Synthetic",
    direction: "BULLISH",
    description:
      "Buy ATM call + sell ATM put (same strike). Synthetic long futures — unlimited up & down.",
    legs: [
      { action: "BUY", option_type: "CE", strikeOffset: 0, lots: 1 },
      { action: "SELL", option_type: "PE", strikeOffset: 0, lots: 1 },
    ],
    payoffPath: "M0,38 L100,2",
  },
  {
    id: "range_forward",
    name: "Range Forward",
    direction: "BULLISH",
    description:
      "Sell OTM put + buy OTM call. Bullish collar — limited downside, unlimited upside.",
    legs: [
      { action: "SELL", option_type: "PE", strikeOffset: -2, lots: 1 },
      { action: "BUY", option_type: "CE", strikeOffset: 2, lots: 1 },
    ],
    payoffPath: "M0,38 L30,22 L65,22 L100,2",
  },
  {
    id: "bullish_butterfly",
    name: "Bullish Butterfly",
    direction: "BULLISH",
    description:
      "Call butterfly centred above spot. Max profit if spot rallies to the body strike.",
    legs: [
      { action: "BUY", option_type: "CE", strikeOffset: 0, lots: 1 },
      { action: "SELL", option_type: "CE", strikeOffset: 2, lots: 2 },
      { action: "BUY", option_type: "CE", strikeOffset: 4, lots: 1 },
    ],
    payoffPath: "M0,26 L55,26 L70,4 L85,26 L100,26",
  },
  {
    id: "bullish_condor",
    name: "Bullish Condor",
    direction: "BULLISH",
    description:
      "Call condor above spot — profit zone over a range of higher strikes. Defined risk both ends.",
    legs: [
      { action: "BUY", option_type: "CE", strikeOffset: 0, lots: 1 },
      { action: "SELL", option_type: "CE", strikeOffset: 1, lots: 1 },
      { action: "SELL", option_type: "CE", strikeOffset: 3, lots: 1 },
      { action: "BUY", option_type: "CE", strikeOffset: 4, lots: 1 },
    ],
    payoffPath: "M0,26 L45,26 L60,6 L80,6 L92,26 L100,26",
  },

  // ── BEARISH ─────────────────────────────────────────────────────────
  {
    id: "short_call",
    name: "Short Call",
    direction: "BEARISH",
    description: "Collect premium; profit if price stays below strike.",
    legs: [{ action: "SELL", option_type: "CE", strikeOffset: 0, lots: 1 }],
    payoffPath: "M0,10 L50,10 L100,38",
  },
  {
    id: "long_put",
    name: "Long Put",
    direction: "BEARISH",
    description: "Unlimited downside profit, limited loss. Best for a strong bearish view.",
    legs: [{ action: "BUY", option_type: "PE", strikeOffset: 0, lots: 1 }],
    payoffPath: "M0,2 L45,30 L100,30",
  },
  {
    id: "bear_call_spread",
    name: "Bear Call Spread",
    direction: "BEARISH",
    description: "Sell ATM call, buy OTM call. Net credit trade.",
    legs: [
      { action: "SELL", option_type: "CE", strikeOffset: 0, lots: 1 },
      { action: "BUY", option_type: "CE", strikeOffset: 2, lots: 1 },
    ],
    payoffPath: "M0,10 L50,10 L75,34 L100,34",
  },
  {
    id: "bear_put_spread",
    name: "Bear Put Spread",
    direction: "BEARISH",
    description: "Buy ATM put, sell OTM put. Capped profit & loss.",
    legs: [
      { action: "BUY", option_type: "PE", strikeOffset: 0, lots: 1 },
      { action: "SELL", option_type: "PE", strikeOffset: -2, lots: 1 },
    ],
    payoffPath: "M0,6 L25,6 L50,28 L100,28",
  },
  {
    id: "put_ratio_back_spread",
    name: "Put Ratio Back Spread",
    direction: "BEARISH",
    description:
      "Sell 1 ATM put, buy 2 OTM puts. Small credit; unlimited downside if market falls hard.",
    legs: [
      { action: "SELL", option_type: "PE", strikeOffset: 0, lots: 1 },
      { action: "BUY", option_type: "PE", strikeOffset: -2, lots: 2 },
    ],
    payoffPath: "M0,2 L25,22 L40,28 L60,18 L100,18",
  },
  {
    id: "short_synthetic",
    name: "Short Synthetic",
    direction: "BEARISH",
    description:
      "Sell ATM call + buy ATM put. Synthetic short futures — unlimited up & down.",
    legs: [
      { action: "SELL", option_type: "CE", strikeOffset: 0, lots: 1 },
      { action: "BUY", option_type: "PE", strikeOffset: 0, lots: 1 },
    ],
    payoffPath: "M0,2 L100,38",
  },
  {
    id: "risk_reversal",
    name: "Risk Reversal",
    direction: "BEARISH",
    description:
      "Buy OTM put + sell OTM call. Bearish collar — profits on downside, unlimited upside loss.",
    legs: [
      { action: "BUY", option_type: "PE", strikeOffset: -2, lots: 1 },
      { action: "SELL", option_type: "CE", strikeOffset: 2, lots: 1 },
    ],
    payoffPath: "M0,2 L35,22 L70,22 L100,38",
  },
  {
    id: "bearish_butterfly",
    name: "Bearish Butterfly",
    direction: "BEARISH",
    description:
      "Put butterfly centred below spot. Max profit if spot falls to the body strike.",
    legs: [
      { action: "BUY", option_type: "PE", strikeOffset: 0, lots: 1 },
      { action: "SELL", option_type: "PE", strikeOffset: -2, lots: 2 },
      { action: "BUY", option_type: "PE", strikeOffset: -4, lots: 1 },
    ],
    payoffPath: "M0,26 L15,26 L30,4 L45,26 L100,26",
  },
  {
    id: "bearish_condor",
    name: "Bearish Condor",
    direction: "BEARISH",
    description:
      "Put condor below spot — profit zone over a range of lower strikes. Defined risk both ends.",
    legs: [
      { action: "BUY", option_type: "PE", strikeOffset: 0, lots: 1 },
      { action: "SELL", option_type: "PE", strikeOffset: -1, lots: 1 },
      { action: "SELL", option_type: "PE", strikeOffset: -3, lots: 1 },
      { action: "BUY", option_type: "PE", strikeOffset: -4, lots: 1 },
    ],
    payoffPath: "M0,26 L8,26 L20,6 L40,6 L55,26 L100,26",
  },

  // ── NON_DIRECTIONAL ─────────────────────────────────────────────────
  {
    id: "long_straddle",
    name: "Long Straddle",
    direction: "NON_DIRECTIONAL",
    description: "Buy ATM call + put. Profits from a large move either way.",
    legs: [
      { action: "BUY", option_type: "CE", strikeOffset: 0, lots: 1 },
      { action: "BUY", option_type: "PE", strikeOffset: 0, lots: 1 },
    ],
    payoffPath: "M0,4 L50,30 L100,4",
  },
  {
    id: "short_straddle",
    name: "Short Straddle",
    direction: "NON_DIRECTIONAL",
    description: "Sell ATM call + put. Profits if price stays pinned near strike.",
    legs: [
      { action: "SELL", option_type: "CE", strikeOffset: 0, lots: 1 },
      { action: "SELL", option_type: "PE", strikeOffset: 0, lots: 1 },
    ],
    payoffPath: "M0,36 L50,10 L100,36",
  },
  {
    id: "long_strangle",
    name: "Long Strangle",
    direction: "NON_DIRECTIONAL",
    description: "Buy OTM call + OTM put. Cheaper than straddle; needs bigger move.",
    legs: [
      { action: "BUY", option_type: "PE", strikeOffset: -2, lots: 1 },
      { action: "BUY", option_type: "CE", strikeOffset: 2, lots: 1 },
    ],
    payoffPath: "M0,6 L30,26 L70,26 L100,6",
  },
  {
    id: "short_strangle",
    name: "Short Strangle",
    direction: "NON_DIRECTIONAL",
    description: "Sell OTM call + OTM put. Wider profit zone than short straddle.",
    legs: [
      { action: "SELL", option_type: "PE", strikeOffset: -2, lots: 1 },
      { action: "SELL", option_type: "CE", strikeOffset: 2, lots: 1 },
    ],
    payoffPath: "M0,34 L30,14 L70,14 L100,34",
  },
  {
    id: "long_iron_condor",
    name: "Long Iron Condor",
    direction: "NON_DIRECTIONAL",
    description: "Bull put spread + bear call spread. Defined-risk range play.",
    legs: [
      { action: "BUY", option_type: "PE", strikeOffset: -4, lots: 1 },
      { action: "SELL", option_type: "PE", strikeOffset: -2, lots: 1 },
      { action: "SELL", option_type: "CE", strikeOffset: 2, lots: 1 },
      { action: "BUY", option_type: "CE", strikeOffset: 4, lots: 1 },
    ],
    payoffPath: "M0,30 L20,30 L35,14 L65,14 L80,30 L100,30",
  },
  {
    id: "short_iron_condor",
    name: "Short Iron Condor",
    direction: "NON_DIRECTIONAL",
    description:
      "Reverse of long iron condor — long wings pay off on a big move; short body caps the middle.",
    legs: [
      { action: "SELL", option_type: "PE", strikeOffset: -4, lots: 1 },
      { action: "BUY", option_type: "PE", strikeOffset: -2, lots: 1 },
      { action: "BUY", option_type: "CE", strikeOffset: 2, lots: 1 },
      { action: "SELL", option_type: "CE", strikeOffset: 4, lots: 1 },
    ],
    payoffPath: "M0,10 L20,10 L35,26 L65,26 L80,10 L100,10",
  },
  {
    id: "long_iron_fly",
    name: "Long Iron Fly",
    direction: "NON_DIRECTIONAL",
    description: "Short ATM straddle + long OTM wings. Max profit pinned at ATM.",
    legs: [
      { action: "BUY", option_type: "PE", strikeOffset: -2, lots: 1 },
      { action: "SELL", option_type: "PE", strikeOffset: 0, lots: 1 },
      { action: "SELL", option_type: "CE", strikeOffset: 0, lots: 1 },
      { action: "BUY", option_type: "CE", strikeOffset: 2, lots: 1 },
    ],
    payoffPath: "M0,30 L25,30 L50,6 L75,30 L100,30",
  },
  {
    id: "short_iron_fly",
    name: "Short Iron Fly",
    direction: "NON_DIRECTIONAL",
    description:
      "Long ATM straddle + short OTM wings. Max profit on a big move; max loss pinned at ATM.",
    legs: [
      { action: "SELL", option_type: "PE", strikeOffset: -2, lots: 1 },
      { action: "BUY", option_type: "PE", strikeOffset: 0, lots: 1 },
      { action: "BUY", option_type: "CE", strikeOffset: 0, lots: 1 },
      { action: "SELL", option_type: "CE", strikeOffset: 2, lots: 1 },
    ],
    payoffPath: "M0,10 L25,10 L50,34 L75,10 L100,10",
  },
  {
    id: "call_butterfly",
    name: "Call Butterfly",
    direction: "NON_DIRECTIONAL",
    description: "Long call butterfly centred at ATM. Max profit if spot pins at the body strike.",
    legs: [
      { action: "BUY", option_type: "CE", strikeOffset: -2, lots: 1 },
      { action: "SELL", option_type: "CE", strikeOffset: 0, lots: 2 },
      { action: "BUY", option_type: "CE", strikeOffset: 2, lots: 1 },
    ],
    payoffPath: "M0,30 L35,30 L50,6 L65,30 L100,30",
  },
  {
    id: "put_butterfly",
    name: "Put Butterfly",
    direction: "NON_DIRECTIONAL",
    description: "Long put butterfly centred at ATM. Put-side mirror of the call butterfly.",
    legs: [
      { action: "BUY", option_type: "PE", strikeOffset: 2, lots: 1 },
      { action: "SELL", option_type: "PE", strikeOffset: 0, lots: 2 },
      { action: "BUY", option_type: "PE", strikeOffset: -2, lots: 1 },
    ],
    payoffPath: "M0,30 L35,30 L50,6 L65,30 L100,30",
  },
  {
    id: "jade_lizard",
    name: "Jade Lizard",
    direction: "NON_DIRECTIONAL",
    description:
      "Sell OTM put + short OTM call spread. No upside risk if credit > call-spread width.",
    legs: [
      { action: "SELL", option_type: "PE", strikeOffset: -2, lots: 1 },
      { action: "SELL", option_type: "CE", strikeOffset: 2, lots: 1 },
      { action: "BUY", option_type: "CE", strikeOffset: 4, lots: 1 },
    ],
    payoffPath: "M0,34 L20,34 L35,14 L75,14 L90,20 L100,20",
  },
  {
    id: "call_calendar",
    name: "Call Calendar",
    direction: "NON_DIRECTIONAL",
    description:
      "Sell near-expiry ATM CE, buy far-expiry ATM CE. Profits from near-leg theta.",
    legs: [
      { action: "SELL", option_type: "CE", strikeOffset: 0, lots: 1, expiryOffset: 0 },
      { action: "BUY", option_type: "CE", strikeOffset: 0, lots: 1, expiryOffset: 1 },
    ],
    payoffPath: "M0,32 L25,28 L42,6 L65,18 L100,28",
  },
  {
    id: "put_calendar",
    name: "Put Calendar",
    direction: "NON_DIRECTIONAL",
    description:
      "Sell near-expiry ATM PE, buy far-expiry ATM PE. Put-side equivalent of the call calendar.",
    legs: [
      { action: "SELL", option_type: "PE", strikeOffset: 0, lots: 1, expiryOffset: 0 },
      { action: "BUY", option_type: "PE", strikeOffset: 0, lots: 1, expiryOffset: 1 },
    ],
    payoffPath: "M0,28 L35,18 L58,6 L75,28 L100,32",
  },
];

/**
 * Resolve a signed strikeOffset to an absolute strike via the available
 * strike grid and the current ATM. Returns null when the offset would walk
 * off either end of the grid (e.g. asking for +6 on a thin near-expiry chain).
 *
 * Math invariant: this is a pure index lookup —
 *
 *   strike = sortedStrikes[indexOf(atm) + strikeOffset]
 *
 * The option type is irrelevant here because strikeOffset is already signed
 * (positive = above ATM, negative = below ATM). The caller is responsible
 * for picking the right LTP map (CE vs PE) once the strike is resolved.
 */
export function resolveStrikeOffset(
  strikeOffset: number,
  atm: number,
  strikes: number[],
): number | null {
  if (strikes.length === 0) return null;
  const sorted = [...strikes].sort((a, b) => a - b);
  const atmIdx = sorted.indexOf(atm);
  if (atmIdx < 0) return null;
  const targetIdx = atmIdx + strikeOffset;
  if (targetIdx < 0 || targetIdx >= sorted.length) return null;
  return sorted[targetIdx];
}

/** Group templates by direction for the TemplateGrid's filter tabs. */
export function templatesByDirection(): Record<Direction, StrategyTemplate[]> {
  const out: Record<Direction, StrategyTemplate[]> = {
    BULLISH: [],
    BEARISH: [],
    NON_DIRECTIONAL: [],
  };
  for (const tpl of STRATEGY_TEMPLATES) {
    out[tpl.direction].push(tpl);
  }
  return out;
}

/** Direction → live count, used for the badge on each filter tab. */
export function directionCounts(): Record<Direction, number> {
  const c: Record<Direction, number> = { BULLISH: 0, BEARISH: 0, NON_DIRECTIONAL: 0 };
  for (const t of STRATEGY_TEMPLATES) c[t.direction]++;
  return c;
}
