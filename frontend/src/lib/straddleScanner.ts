/**
 * Pure helpers for the Straddles & Strangles Chain scanner.
 *
 * Two responsibilities:
 *   1. From a fetched option chain (POST /api/v1/optionchain), derive a
 *      single scanner row for one of: Short Straddle, Long Straddle,
 *      Short Strangle, Long Strangle.
 *   2. From the user's open positions, group CE/PE pairs on the same
 *      underlying+expiry+product+direction into "Active Straddle / Strangle"
 *      cards.
 *
 * Black-76 / POP / breakeven math is delegated to lib/black76 +
 * lib/probabilityOfProfit. We hold r=q=0 — the rest of OpenBull's options
 * math uses the same convention for INR index options.
 */

import {
  findBreakevens,
  greeks,
  impliedVol,
  parseOptionSymbol,
  payoffCurve,
  timeToExpiryYears,
  type PayoffLeg,
} from "@/lib/black76";
import { probabilityOfProfit } from "@/lib/probabilityOfProfit";
import type { Action, OptionType } from "@/types/strategy";
import type { OptionStrike } from "@/types/optionchain";
import type { PositionItem } from "@/types/order";

export type StrategyKey =
  | "short_straddle"
  | "long_straddle"
  | "short_strangle"
  | "long_strangle";

export interface StrategyConfig {
  key: StrategyKey;
  label: string;
  isStrangle: boolean;
  callAction: Action;
  putAction: Action;
}

export const STRATEGIES: Record<StrategyKey, StrategyConfig> = {
  short_straddle: {
    key: "short_straddle",
    label: "Short Straddle",
    isStrangle: false,
    callAction: "SELL",
    putAction: "SELL",
  },
  long_straddle: {
    key: "long_straddle",
    label: "Long Straddle",
    isStrangle: false,
    callAction: "BUY",
    putAction: "BUY",
  },
  short_strangle: {
    key: "short_strangle",
    label: "Short Strangle",
    isStrangle: true,
    callAction: "SELL",
    putAction: "SELL",
  },
  long_strangle: {
    key: "long_strangle",
    label: "Long Strangle",
    isStrangle: true,
    callAction: "BUY",
    putAction: "BUY",
  },
};

export const STRATEGY_KEYS: StrategyKey[] = [
  "short_straddle",
  "long_straddle",
  "short_strangle",
  "long_strangle",
];

// ─── Moneyness model ────────────────────────────────────────────────────

/** Strike position relative to ATM. ITMn = call's strike below spot by n
 *  steps (call is in-the-money); OTMn = call's strike above spot by n
 *  steps. For strangles, the put leg mirrors the call leg's offset
 *  (OTM strangle = both OTM, ITM strangle = both ITM / "guts"). */
export type MoneynessLabel =
  | "ITM4" | "ITM3" | "ITM2" | "ITM1"
  | "ATM"
  | "OTM1" | "OTM2" | "OTM3" | "OTM4";

export const MONEYNESS_LABELS: MoneynessLabel[] = [
  "ITM4", "ITM3", "ITM2", "ITM1",
  "ATM",
  "OTM1", "OTM2", "OTM3", "OTM4",
];

/** Returns [callOffset, putOffset] in strike-grid steps from ATM.
 *  Straddle = same strike both legs; Strangle = symmetric outward
 *  (OTM) or inward (ITM, "guts") split. */
export function moneynessToOffsets(
  label: MoneynessLabel,
  isStrangle: boolean,
): [number, number] {
  if (label === "ATM") return [0, 0];
  const n = parseInt(label.slice(3), 10) || 0;
  const isOtm = label.startsWith("OTM");
  if (isStrangle) {
    return isOtm ? [+n, -n] : [-n, +n];
  }
  // Straddle: both legs share the same strike — same signed offset.
  return isOtm ? [+n, +n] : [-n, -n];
}

// ─── Underlyings with weekly expiries (the only ones SEBI still allows) ─

export const WEEKLY_UNDERLYINGS = new Set<string>(["NIFTY", "SENSEX"]);

// ─── Expiry bucketing ───────────────────────────────────────────────────

export type ExpiryBucket =
  | "current_week"
  | "next_week"
  | "current_month"
  | "next_month";

export const EXPIRY_BUCKETS: Array<{ value: ExpiryBucket; label: string }> = [
  { value: "current_week", label: "Current Week" },
  { value: "next_week", label: "Next Week" },
  { value: "current_month", label: "Current Month" },
  { value: "next_month", label: "Next Month" },
];

export interface ResolvedExpiries {
  /** First weekly expiry. Null for monthly-only underlyings. */
  currentWeek: string | null;
  /** Second weekly expiry. */
  nextWeek: string | null;
  /** First monthly expiry (last expiry in its calendar month). */
  currentMonth: string | null;
  /** Second monthly expiry. */
  nextMonth: string | null;
}

/**
 * Bucket the broker's expiry list into week/month rolls.
 *
 * Definition of "monthly": the latest expiry in each calendar month
 * (works whether the underlying has weeklies or only monthlies).
 *
 * For monthly-only underlyings we leave currentWeek / nextWeek null —
 * the page treats null as "skip this underlying for the week buckets",
 * which is the correct UX given only NIFTY and SENSEX expire weekly.
 */
export function resolveExpiries(
  expiries: string[],
  exchange: string,
  hasWeekly: boolean,
): ResolvedExpiries {
  const parsed: Array<{ display: string; date: Date }> = [];
  for (const e of expiries) {
    const d = parseExpiryDisplay(e, exchange);
    if (d) parsed.push({ display: e, date: d });
  }
  parsed.sort((a, b) => a.date.getTime() - b.date.getTime());

  // Monthlies: last expiry in each (year, month).
  const byMonth = new Map<string, { display: string; date: Date }>();
  for (const p of parsed) {
    const key = `${p.date.getFullYear()}-${p.date.getMonth()}`;
    const cur = byMonth.get(key);
    if (!cur || p.date.getTime() > cur.date.getTime()) byMonth.set(key, p);
  }
  const monthlies = [...byMonth.values()].sort(
    (a, b) => a.date.getTime() - b.date.getTime(),
  );

  return {
    currentWeek: hasWeekly ? (parsed[0]?.display ?? null) : null,
    nextWeek: hasWeekly ? (parsed[1]?.display ?? null) : null,
    currentMonth: monthlies[0]?.display ?? null,
    nextMonth: monthlies[1]?.display ?? null,
  };
}

export function pickBucketExpiry(
  resolved: ResolvedExpiries,
  bucket: ExpiryBucket,
): string | null {
  switch (bucket) {
    case "current_week":
      return resolved.currentWeek;
    case "next_week":
      return resolved.nextWeek;
    case "current_month":
      return resolved.currentMonth;
    case "next_month":
      return resolved.nextMonth;
  }
}

// ─── Expiry parsing ─────────────────────────────────────────────────────

const MONTH_MAP: Record<string, number> = {
  JAN: 0, FEB: 1, MAR: 2, APR: 3, MAY: 4, JUN: 5,
  JUL: 6, AUG: 7, SEP: 8, OCT: 9, NOV: 10, DEC: 11,
};

const EXPIRY_HHMM: Record<string, [number, number]> = {
  NFO: [15, 30],
  BFO: [15, 30],
  CDS: [12, 30],
  MCX: [23, 30],
};

/** Parse the API expiry display ("12-MAY-2026") to a Date at the local
 *  exchange close time. Returns null on bad input. */
export function parseExpiryDisplay(
  s: string,
  exchange = "NFO",
): Date | null {
  if (!s) return null;
  const parts = s.split("-");
  if (parts.length !== 3) return null;
  const dd = parseInt(parts[0], 10);
  const mm = MONTH_MAP[parts[1].toUpperCase()];
  let yyyy = parseInt(parts[2], 10);
  if (yyyy < 100) yyyy += 2000;
  if (!Number.isFinite(dd) || mm === undefined || !Number.isFinite(yyyy)) {
    return null;
  }
  const [hh, mn] = EXPIRY_HHMM[exchange.toUpperCase()] ?? [15, 30];
  return new Date(yyyy, mm, dd, hh, mn, 0, 0);
}

// ─── Scanner row ────────────────────────────────────────────────────────

export interface ScannerRow {
  /** Stable key for React. */
  id: string;
  underlying: string;
  /** Options exchange (NFO / BFO / MCX). */
  exchange: string;
  /** Display "DD-MMM-YYYY". */
  expiry: string;
  /** Strike position label (ITM4..ATM..OTM4) — drives the "Strike" column. */
  moneyness: MoneynessLabel;
  spot: number;
  /** ATM strike from the chain — surfaced for the Active Strikes column even
   *  when the chosen strategy uses OTM strikes (strangle). */
  atmStrike: number;
  callStrike: number;
  callPrem: number;
  callDelta: number;
  callIv: number;
  callSymbol: string;
  callTickSize: number;
  putStrike: number;
  putPrem: number;
  putDelta: number;
  putIv: number;
  putSymbol: string;
  putTickSize: number;
  avgIv: number;
  /** Per-share max profit. `null` = unlimited (long straddle/strangle). */
  maxProfit: number | null;
  /** Lower / upper breakeven spots. May be null when the curve doesn't
   *  cross zero in the sampled range. */
  beMinus: number | null;
  bePlus: number | null;
  /** [0, 1] probability the strategy is profitable at expiry. */
  pop: number | null;
  /** Lot size (from the broker's chain). 1 if missing. */
  lotSize: number;
}

interface BuildRowParams {
  underlying: string;
  exchange: string;
  expiry: string;
  spot: number;
  atmStrike: number;
  chain: OptionStrike[];
  strategy: StrategyKey;
  /** Strike position relative to ATM for the call/put legs. */
  moneyness: MoneynessLabel;
}

export function buildScannerRow(p: BuildRowParams): ScannerRow | null {
  const cfg = STRATEGIES[p.strategy];
  if (!Number.isFinite(p.spot) || p.spot <= 0) return null;
  if (!Number.isFinite(p.atmStrike) || p.atmStrike <= 0) return null;
  if (!Array.isArray(p.chain) || p.chain.length === 0) return null;

  const sorted = [...p.chain].sort((a, b) => a.strike - b.strike);
  const atmIdx = sorted.findIndex((s) => s.strike === p.atmStrike);
  if (atmIdx < 0) return null;

  // Moneyness → signed offsets for call/put. Clamp at chain edges so
  // narrow chains still emit something rather than null.
  const [callDelta, putDelta] = moneynessToOffsets(p.moneyness, cfg.isStrangle);
  const callIdx = Math.max(0, Math.min(sorted.length - 1, atmIdx + callDelta));
  const putIdx = Math.max(0, Math.min(sorted.length - 1, atmIdx + putDelta));

  const callRow = sorted[callIdx];
  const putRow = sorted[putIdx];
  const callLeg = callRow?.ce;
  const putLeg = putRow?.pe;
  if (!callLeg || !putLeg) return null;

  // Prefer LTP; fall back to bid/ask mid when LTP is zero (common after
  // hours and for newly-listed strikes that haven't traded yet). Don't
  // bail on zero — a row with strikes but no IV is still useful for
  // chain inspection.
  const midOrZero = (bid: number, ask: number): number => {
    const b = Number(bid) || 0;
    const a = Number(ask) || 0;
    if (b > 0 && a > 0) return (b + a) / 2;
    return Math.max(b, a, 0);
  };
  const callPrem =
    Number(callLeg.ltp) > 0
      ? Number(callLeg.ltp)
      : midOrZero(callLeg.bid, callLeg.ask);
  const putPrem =
    Number(putLeg.ltp) > 0
      ? Number(putLeg.ltp)
      : midOrZero(putLeg.bid, putLeg.ask);

  const expiryDate = parseExpiryDisplay(p.expiry, p.exchange);
  if (!expiryDate) return null;
  const T = timeToExpiryYears(expiryDate);

  // IV via Black-76 inversion — only attempt when we have positive prices.
  // r=0 (INR index convention). Uses spot as forward; for INR index options
  // with negligible carry that's the same assumption the backend snapshot
  // makes.
  const callIv =
    callPrem > 0 ? impliedVol(callPrem, p.spot, callRow.strike, T, 0, "c") : null;
  const putIv =
    putPrem > 0 ? impliedVol(putPrem, p.spot, putRow.strike, T, 0, "p") : null;
  // When a leg's IV can't be solved — most commonly a stale sub-intrinsic
  // price (e.g. a deep-ITM put quoted below K−S on weekend/after-hours data)
  // — fall back to the *partner* leg's solved IV instead of a near-zero
  // floor. For a straddle both legs share a strike, so put-call parity makes
  // their IV identical; for a strangle the partner's IV is still a far better
  // proxy than 0.0001. The old 0.0001 floor pinned the leg's delta to ±1 and
  // halved the blended sigma feeding POP, distorting both for those rows.
  const IV_FLOOR = 0.0001;
  const sigCall = callIv ?? putIv ?? IV_FLOOR;
  const sigPut = putIv ?? callIv ?? IV_FLOOR;

  const callG = greeks(p.spot, callRow.strike, T, 0, sigCall, "c", callPrem);
  const putG = greeks(p.spot, putRow.strike, T, 0, sigPut, "p", putPrem);

  // Per-share payoff legs (lots=lotSize=1 so ScannerRow.maxProfit /
  // breakevens are reported per share — Per-Lot toggle on the page
  // multiplies by lotSize.)
  const legs: PayoffLeg[] = [
    {
      action: cfg.callAction,
      optionType: "CE",
      strike: callRow.strike,
      lots: 1,
      lotSize: 1,
      entryPrice: callPrem,
    },
    {
      action: cfg.putAction,
      optionType: "PE",
      strike: putRow.strike,
      lots: 1,
      lotSize: 1,
      entryPrice: putPrem,
    },
  ];

  const totalPrem = callPrem + putPrem;
  const maxProfit: number | null =
    cfg.callAction === "SELL" ? totalPrem : null;

  // Sample the payoff curve wide enough to capture both wings of a
  // straddle even when IV is high. ±40% of spot is generous; falling
  // back to the inner strike range if spot is degenerate.
  const range = Math.max(p.spot * 0.4, totalPrem * 4, 100);
  const lo = Math.max(p.spot - range, 1);
  const hi = p.spot + range;
  const curve = payoffCurve(legs, lo, hi, 401);
  const breakevens = findBreakevens(curve);
  const beMinus = breakevens.find((s) => s < p.spot) ?? null;
  const bePlus = [...breakevens].reverse().find((s) => s > p.spot) ?? null;

  const popResult = probabilityOfProfit({
    legs,
    spot: p.spot,
    legIvDecimals: [sigCall, sigPut],
    legDteYears: [T, T],
  });

  const lotSize =
    Number(callLeg.lotsize) > 0
      ? Number(callLeg.lotsize)
      : Number(putLeg.lotsize) > 0
        ? Number(putLeg.lotsize)
        : 1;

  return {
    id: `${p.underlying}|${p.exchange}|${p.expiry}|${p.strategy}|${p.moneyness}`,
    underlying: p.underlying,
    exchange: p.exchange,
    expiry: p.expiry,
    moneyness: p.moneyness,
    spot: p.spot,
    atmStrike: p.atmStrike,
    callStrike: callRow.strike,
    callPrem,
    callDelta: callG.delta,
    callIv: sigCall,
    callSymbol: callLeg.symbol,
    callTickSize: Number(callLeg.tick_size) || 0.05,
    putStrike: putRow.strike,
    putPrem,
    putDelta: putG.delta,
    putIv: sigPut,
    putSymbol: putLeg.symbol,
    putTickSize: Number(putLeg.tick_size) || 0.05,
    avgIv: (sigCall + sigPut) / 2,
    maxProfit,
    beMinus,
    bePlus,
    pop: popResult?.probability ?? null,
    lotSize,
  };
}

// ─── Active straddle/strangle grouping (from open positions) ─────────────

export interface ActiveLeg {
  /** The original PositionItem from /web/positions. */
  position: PositionItem;
  optionType: OptionType;
  strike: number;
  expiry: Date;
  /** Unsigned share quantity. */
  qty: number;
  /** Sign — "L" if quantity > 0 (long), "S" if quantity < 0 (short). */
  sign: "L" | "S";
}

export type ActiveGroupType =
  | "Long Straddle"
  | "Short Straddle"
  | "Long Strangle"
  | "Short Strangle"
  | "Long CE Only"
  | "Short CE Only"
  | "Long PE Only"
  | "Short PE Only";

export interface ActiveGroup {
  /** Stable id — underlying|exchange|expiryISO|product|sign. */
  id: string;
  type: ActiveGroupType;
  underlying: string;
  exchange: string;
  expiry: Date;
  /** Display string ("DD-MMM-YYYY"). */
  expiryDisplay: string;
  product: string;
  sign: "L" | "S";
  ce: ActiveLeg | null;
  pe: ActiveLeg | null;
  /** Total live LTP-based MTM for the pair. */
  mtm: number;
  /** Net entry premium (positive = debit paid; negative = credit received). */
  netEntryPremium: number;
  /** Net current premium (mark-to-market basis: long sums LTPs, short sums LTPs). */
  netCurrentPremium: number;
}

function isPair(t: ActiveGroupType): boolean {
  return t === "Long Straddle" || t === "Short Straddle" ||
         t === "Long Strangle" || t === "Short Strangle";
}

function expiryISO(d: Date): string {
  return `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`;
}

function expiryDisplay(d: Date): string {
  const months = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"];
  const dd = d.getDate().toString().padStart(2, "0");
  return `${dd}-${months[d.getMonth()]}-${d.getFullYear()}`;
}

/**
 * Group positions into straddle/strangle pairs.
 *
 * Pairing rule: same (underlying, exchange, product) + same direction
 * sign + matching CE & PE legs → straddle (same strike) or strangle
 * (different strikes). Lopsided positions (CE without PE or vice versa)
 * surface as "*-Only" entries so the user can spot half-closed states.
 *
 * Filters out qty=0 rows up front.
 */
export function groupPositionsToStraddles(
  positions: PositionItem[],
): ActiveGroup[] {
  const live = positions.filter((p) => Number(p.quantity) !== 0);

  type Bucket = {
    underlying: string;
    exchange: string;
    expiry: Date;
    product: string;
    sign: "L" | "S";
    ce: ActiveLeg | null;
    pe: ActiveLeg | null;
  };
  const buckets = new Map<string, Bucket>();

  for (const pos of live) {
    const ps = parseOptionSymbol(pos.symbol, pos.exchange);
    if (!ps) continue;
    const sign: "L" | "S" = Number(pos.quantity) > 0 ? "L" : "S";
    const key = `${ps.underlying}|${pos.exchange}|${expiryISO(ps.expiry)}|${pos.product}|${sign}`;
    let b = buckets.get(key);
    if (!b) {
      b = {
        underlying: ps.underlying,
        exchange: pos.exchange,
        expiry: ps.expiry,
        product: pos.product,
        sign,
        ce: null,
        pe: null,
      };
      buckets.set(key, b);
    }
    const leg: ActiveLeg = {
      position: pos,
      optionType: ps.optionType,
      strike: ps.strike,
      expiry: ps.expiry,
      qty: Math.abs(Number(pos.quantity)),
      sign,
    };
    if (ps.optionType === "CE") {
      // Multiple CE legs in the same bucket would mean different strikes
      // on the same expiry+sign — that's ratio-spread territory, not a
      // straddle/strangle. Pick whichever has more qty so we surface
      // *something*; the unmatched ones won't show but won't be wrong.
      if (!b.ce || leg.qty > b.ce.qty) b.ce = leg;
    } else {
      if (!b.pe || leg.qty > b.pe.qty) b.pe = leg;
    }
  }

  const out: ActiveGroup[] = [];
  for (const [key, b] of buckets) {
    let type: ActiveGroupType;
    if (b.ce && b.pe) {
      const isStraddle = b.ce.strike === b.pe.strike;
      type =
        b.sign === "L"
          ? isStraddle ? "Long Straddle" : "Long Strangle"
          : isStraddle ? "Short Straddle" : "Short Strangle";
    } else if (b.ce) {
      type = b.sign === "L" ? "Long CE Only" : "Short CE Only";
    } else if (b.pe) {
      type = b.sign === "L" ? "Long PE Only" : "Short PE Only";
    } else {
      continue;
    }

    // Sum MTM and premiums across the (up to) two legs.
    let mtm = 0;
    let entryPrem = 0;
    let currentPrem = 0;
    for (const leg of [b.ce, b.pe]) {
      if (!leg) continue;
      mtm += Number(leg.position.pnl) || 0;
      // Sign-aware: long pays debit (entry > 0); short receives credit (treat as -entry).
      const sgn = leg.sign === "L" ? 1 : -1;
      entryPrem += sgn * (Number(leg.position.average_price) || 0);
      currentPrem += sgn * (Number(leg.position.ltp) || 0);
    }

    out.push({
      id: key,
      type,
      underlying: b.underlying,
      exchange: b.exchange,
      expiry: b.expiry,
      expiryDisplay: expiryDisplay(b.expiry),
      product: b.product,
      sign: b.sign,
      ce: b.ce,
      pe: b.pe,
      mtm,
      netEntryPremium: entryPrem,
      netCurrentPremium: currentPrem,
    });
  }

  // Pairs first, orphans last. Within pairs, sort by underlying then
  // nearest expiry — so a NIFTY weekly shows above a BANKNIFTY monthly.
  out.sort((a, b) => {
    const aPair = isPair(a.type) ? 0 : 1;
    const bPair = isPair(b.type) ? 0 : 1;
    if (aPair !== bPair) return aPair - bPair;
    if (a.underlying !== b.underlying) {
      return a.underlying.localeCompare(b.underlying);
    }
    return a.expiry.getTime() - b.expiry.getTime();
  });

  return out;
}
