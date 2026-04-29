/**
 * Strategy templates — the click-to-fill presets the user picks from
 * the StrategyBuilder's "Templates" dropdown.
 *
 * Each template's legs reference strikes by *relative* offset from the
 * current ATM (e.g. "ATM", "OTM2", "ITM3") rather than absolute prices,
 * so the same template applies to NIFTY at 24,000 and BANKNIFTY at
 * 51,000 without re-authoring. The builder resolves these to concrete
 * strikes by looking at the available strike grid for the chosen
 * expiry, the same way OpenAlgo's StrategyBuilder does.
 *
 * Sign convention for `action`:
 *   BUY  — long the leg (pays premium)
 *   SELL — short the leg (collects premium)
 *
 * `expiryOffset` is reserved for diagonal/calendar templates — it
 * indexes into the available expiries list relative to the user's
 * primary pick (0 = same expiry as the page's expiry picker, 1 = next,
 * 2 = month after, etc.). Most templates leave it at 0.
 */

import type { Action, OptionType } from "@/types/strategy";

/** Relative strike offset. n = absolute number of strikes. */
export type StrikeOffset =
  | "ATM"
  | `ITM${1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10}`
  | `OTM${1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10}`;

export interface TemplateLeg {
  action: Action;
  option_type: OptionType;
  /** Strike relative to ATM. */
  offset: StrikeOffset;
  /** Lots multiplier — most templates default to 1. Ratios like 1×3 use 3. */
  lots: number;
  /** 0 = primary expiry, 1 = next expiry, etc. Used for calendar/diagonal. */
  expiryOffset?: number;
}

export interface StrategyTemplate {
  id: string;
  name: string;
  category: "neutral" | "bullish" | "bearish" | "volatility" | "calendar";
  /** One-line description shown under the template name. */
  description: string;
  legs: TemplateLeg[];
}

/**
 * The curated set. Order in this list = order in the dropdown. Keep it
 * grouped by category so similar payoffs sit next to each other.
 *
 * Sources:
 *   - OpenAlgo's strategyTemplates.ts (12 core strategies)
 *   - OpenBull-specific tweaks (broader OTM defaults for INR markets)
 */
export const STRATEGY_TEMPLATES: StrategyTemplate[] = [
  // ── Volatility ──────────────────────────────────────────────────────
  {
    id: "long_straddle",
    name: "Long Straddle",
    category: "volatility",
    description: "Buy ATM CE + Buy ATM PE. Long volatility — profits on big moves either way.",
    legs: [
      { action: "BUY", option_type: "CE", offset: "ATM", lots: 1 },
      { action: "BUY", option_type: "PE", offset: "ATM", lots: 1 },
    ],
  },
  {
    id: "short_straddle",
    name: "Short Straddle",
    category: "volatility",
    description: "Sell ATM CE + Sell ATM PE. Short volatility — collects premium if price stays put.",
    legs: [
      { action: "SELL", option_type: "CE", offset: "ATM", lots: 1 },
      { action: "SELL", option_type: "PE", offset: "ATM", lots: 1 },
    ],
  },
  {
    id: "long_strangle",
    name: "Long Strangle",
    category: "volatility",
    description: "Buy OTM2 CE + Buy OTM2 PE. Cheaper than a straddle, needs a bigger move.",
    legs: [
      { action: "BUY", option_type: "CE", offset: "OTM2", lots: 1 },
      { action: "BUY", option_type: "PE", offset: "OTM2", lots: 1 },
    ],
  },
  {
    id: "short_strangle",
    name: "Short Strangle",
    category: "volatility",
    description: "Sell OTM2 CE + Sell OTM2 PE. Wider profit zone than short straddle.",
    legs: [
      { action: "SELL", option_type: "CE", offset: "OTM2", lots: 1 },
      { action: "SELL", option_type: "PE", offset: "OTM2", lots: 1 },
    ],
  },

  // ── Neutral (defined-risk volatility) ───────────────────────────────
  {
    id: "iron_condor",
    name: "Iron Condor",
    category: "neutral",
    description: "Sell OTM2 strangle, buy OTM4 wings. Defined-risk premium collection.",
    legs: [
      { action: "BUY", option_type: "CE", offset: "OTM4", lots: 1 },
      { action: "SELL", option_type: "CE", offset: "OTM2", lots: 1 },
      { action: "SELL", option_type: "PE", offset: "OTM2", lots: 1 },
      { action: "BUY", option_type: "PE", offset: "OTM4", lots: 1 },
    ],
  },
  {
    id: "iron_butterfly",
    name: "Iron Butterfly",
    category: "neutral",
    description: "Sell ATM straddle, buy OTM2 wings. Tighter than condor, fatter premium.",
    legs: [
      { action: "BUY", option_type: "CE", offset: "OTM2", lots: 1 },
      { action: "SELL", option_type: "CE", offset: "ATM", lots: 1 },
      { action: "SELL", option_type: "PE", offset: "ATM", lots: 1 },
      { action: "BUY", option_type: "PE", offset: "OTM2", lots: 1 },
    ],
  },

  // ── Bullish ─────────────────────────────────────────────────────────
  {
    id: "bull_call_spread",
    name: "Bull Call Spread",
    category: "bullish",
    description: "Buy ATM CE, sell OTM2 CE. Defined-risk bullish play.",
    legs: [
      { action: "BUY", option_type: "CE", offset: "ATM", lots: 1 },
      { action: "SELL", option_type: "CE", offset: "OTM2", lots: 1 },
    ],
  },
  {
    id: "bull_put_spread",
    name: "Bull Put Spread",
    category: "bullish",
    description: "Sell ATM PE, buy OTM2 PE. Net credit, profits if price holds or rises.",
    legs: [
      { action: "SELL", option_type: "PE", offset: "ATM", lots: 1 },
      { action: "BUY", option_type: "PE", offset: "OTM2", lots: 1 },
    ],
  },
  {
    id: "long_call",
    name: "Long Call",
    category: "bullish",
    description: "Buy ATM CE. Unlimited upside, capped to premium loss.",
    legs: [{ action: "BUY", option_type: "CE", offset: "ATM", lots: 1 }],
  },

  // ── Bearish ─────────────────────────────────────────────────────────
  {
    id: "bear_put_spread",
    name: "Bear Put Spread",
    category: "bearish",
    description: "Buy ATM PE, sell OTM2 PE. Defined-risk bearish play.",
    legs: [
      { action: "BUY", option_type: "PE", offset: "ATM", lots: 1 },
      { action: "SELL", option_type: "PE", offset: "OTM2", lots: 1 },
    ],
  },
  {
    id: "bear_call_spread",
    name: "Bear Call Spread",
    category: "bearish",
    description: "Sell ATM CE, buy OTM2 CE. Net credit, profits if price holds or falls.",
    legs: [
      { action: "SELL", option_type: "CE", offset: "ATM", lots: 1 },
      { action: "BUY", option_type: "CE", offset: "OTM2", lots: 1 },
    ],
  },
  {
    id: "long_put",
    name: "Long Put",
    category: "bearish",
    description: "Buy ATM PE. Profits as price falls.",
    legs: [{ action: "BUY", option_type: "PE", offset: "ATM", lots: 1 }],
  },

  // ── Calendar / Diagonal ─────────────────────────────────────────────
  {
    id: "calendar_call",
    name: "Calendar Call",
    category: "calendar",
    description: "Sell near ATM CE, buy next-expiry ATM CE. Profits from time decay differential.",
    legs: [
      { action: "SELL", option_type: "CE", offset: "ATM", lots: 1, expiryOffset: 0 },
      { action: "BUY", option_type: "CE", offset: "ATM", lots: 1, expiryOffset: 1 },
    ],
  },
  {
    id: "calendar_put",
    name: "Calendar Put",
    category: "calendar",
    description: "Sell near ATM PE, buy next-expiry ATM PE.",
    legs: [
      { action: "SELL", option_type: "PE", offset: "ATM", lots: 1, expiryOffset: 0 },
      { action: "BUY", option_type: "PE", offset: "ATM", lots: 1, expiryOffset: 1 },
    ],
  },
];

/**
 * Resolve a relative offset to an absolute strike given the available strike
 * grid and the current ATM. Returns null when the offset would walk off
 * either end of the grid (e.g. asking for OTM10 on a thin chain).
 *
 * For PUTs the meaning of ITM/OTM is mirrored: ITM PE = strike *above* ATM,
 * OTM PE = strike *below* ATM. The caller passes ``optionType`` to disambiguate.
 */
export function resolveOffset(
  offset: StrikeOffset,
  atm: number,
  strikes: number[],
  optionType: OptionType,
): number | null {
  if (strikes.length === 0) return null;
  const sorted = [...strikes].sort((a, b) => a - b);
  const atmIdx = sorted.indexOf(atm);
  if (atmIdx < 0) return null;

  if (offset === "ATM") return atm;

  const m = /^(ITM|OTM)(\d+)$/.exec(offset);
  if (!m) return null;
  const dir = m[1] as "ITM" | "OTM";
  const n = parseInt(m[2], 10);

  // For CE: ITM = below ATM, OTM = above ATM
  // For PE: ITM = above ATM, OTM = below ATM
  let stepDir: 1 | -1;
  if (optionType === "CE") {
    stepDir = dir === "ITM" ? -1 : 1;
  } else {
    stepDir = dir === "ITM" ? 1 : -1;
  }

  const targetIdx = atmIdx + stepDir * n;
  if (targetIdx < 0 || targetIdx >= sorted.length) return null;
  return sorted[targetIdx];
}

/** Group templates by category for a sectioned dropdown. */
export function templatesByCategory(): Record<
  StrategyTemplate["category"],
  StrategyTemplate[]
> {
  const out = {
    bullish: [] as StrategyTemplate[],
    bearish: [] as StrategyTemplate[],
    neutral: [] as StrategyTemplate[],
    volatility: [] as StrategyTemplate[],
    calendar: [] as StrategyTemplate[],
  };
  for (const tpl of STRATEGY_TEMPLATES) {
    out[tpl.category].push(tpl);
  }
  return out;
}
