/**
 * Strategy Builder + Strategy Portfolio shared TypeScript types.
 *
 * Mirrors the backend Pydantic schemas in:
 *   - backend/schemas/strategies.py        (saved-strategy CRUD)
 *   - backend/routers/strategybuilder.py   (snapshot + chart)
 *   - backend/services/strategy_builder_service.py  (snapshot response shape)
 *   - backend/services/strategy_chart_service.py    (chart response shape)
 *
 * Keeping these strict means the IDE flags drift the moment a backend
 * field name changes — far cheaper than tracking it down at runtime.
 */

export type Action = "BUY" | "SELL";
export type OptionType = "CE" | "PE";
export type StrategyMode = "live" | "sandbox";
export type StrategyStatus = "active" | "closed" | "expired";
export type LegStatus = "open" | "closed" | "expired";

// ────────────────────────────────────────────────────────────────────────
// Saved Strategy (matches backend Strategy table + StrategyLeg JSONB)
// ────────────────────────────────────────────────────────────────────────

export interface StrategyLeg {
  id?: string | null;
  action: Action;
  option_type: OptionType;
  strike: number;
  lots: number;
  lot_size?: number | null;
  expiry_date?: string | null;
  symbol?: string | null;
  entry_price?: number;
  exit_price?: number | null;
  status?: LegStatus;
  entry_time?: string | null;
  exit_time?: string | null;
}

export interface Strategy {
  id: number;
  user_id: number;
  name: string;
  underlying: string;
  exchange: string;
  expiry_date: string | null;
  mode: StrategyMode;
  status: StrategyStatus;
  legs: StrategyLeg[];
  notes: string | null;
  created_at: string;
  updated_at: string;
  closed_at: string | null;
}

export interface StrategyCreate {
  name: string;
  underlying: string;
  exchange: string;
  expiry_date?: string | null;
  mode?: StrategyMode;
  legs: StrategyLeg[];
  notes?: string | null;
}

export interface StrategyUpdate {
  name?: string;
  underlying?: string;
  exchange?: string;
  expiry_date?: string | null;
  status?: StrategyStatus;
  legs?: StrategyLeg[];
  notes?: string | null;
}

// ────────────────────────────────────────────────────────────────────────
// Snapshot endpoint (POST /web/strategybuilder/snapshot)
// ────────────────────────────────────────────────────────────────────────

/** Per-leg payload accepted by the snapshot endpoint. */
export interface SnapshotLegInput {
  symbol: string;
  action: Action;
  lots: number;
  lot_size: number;
  /** Per-leg exchange override (rarely needed; default is the request's options_exchange). */
  exchange?: string;
  /** Supplying this turns on `unrealized_pnl` in the response. */
  entry_price?: number;
}

export interface SnapshotRequest {
  underlying: string;
  /** Spot/forward exchange. Auto-resolved if omitted. */
  exchange?: string;
  /** Default leg exchange (NFO/BFO/CDS/MCX). */
  options_exchange?: string;
  /** Annualized %; per-exchange default if omitted. */
  interest_rate?: number;
  /** "HH:MM" — overrides the per-exchange expiry time. */
  expiry_time?: string;
  legs: SnapshotLegInput[];
}

export interface Greeks {
  delta: number;
  gamma: number;
  /** Per calendar day (already divided by 365). */
  theta: number;
  /** Per 1% vol move (already divided by 100). */
  vega: number;
  /** Per 1% rate move (already divided by 100). */
  rho: number;
}

export interface SnapshotLegOutput {
  index: number;
  symbol: string;
  exchange: string;
  action: Action;
  lots: number;
  lot_size: number;
  underlying?: string;
  strike?: number;
  option_type?: OptionType;
  expiry_date?: string;
  days_to_expiry?: number;
  ltp?: number | null;
  /** Solved IV in % (e.g. 12.34 means 12.34% annualised). */
  implied_volatility?: number;
  greeks?: Greeks;
  /** sign * lots * lot_size * ltp (positive = paid, negative = received). */
  position_premium?: number;
  entry_price?: number;
  unrealized_pnl?: number;
  /** Set when the leg failed pricing/parsing — non-fatal; the rest still renders. */
  error?: string;
  /** Set when greeks fell back to deep-ITM theoreticals. */
  note?: string;
}

export interface SnapshotTotals {
  /** Net debit (>0) or credit (<0) at current LTPs. */
  premium_paid: number;
  delta: number;
  gamma: number;
  theta: number;
  vega: number;
  rho: number;
  /** Present only when every leg supplied an entry_price. */
  unrealized_pnl?: number;
}

export interface SnapshotResponse {
  status: "success";
  underlying: string;
  exchange: string;
  spot_price: number;
  /** ISO-8601 with +05:30 — server-stamped IST. */
  as_of: string;
  legs: SnapshotLegOutput[];
  totals: SnapshotTotals;
}

// ────────────────────────────────────────────────────────────────────────
// Strategy Chart endpoint (POST /web/strategybuilder/chart)
// ────────────────────────────────────────────────────────────────────────

export interface ChartLegInput {
  symbol: string;
  action: Action;
  lots: number;
  lot_size: number;
  exchange?: string;
  entry_price?: number;
}

export interface ChartRequest {
  underlying: string;
  exchange?: string;
  options_exchange?: string;
  interval: string;
  /** Trading-day window. 1-60. Default 5. */
  days?: number;
  include_underlying?: boolean;
  legs: ChartLegInput[];
}

export interface ChartCandle {
  /** Unix seconds. */
  time: number;
  close: number;
}

export interface ChartLegSeries {
  index: number;
  symbol: string;
  exchange: string;
  action: Action;
  lots: number;
  lot_size: number;
  series: ChartCandle[];
  entry_price?: number;
  error?: string;
}

export interface ChartCombinedPoint {
  time: number;
  /** Position premium at this candle (rupees, signed BUY=+1):
   *  sum_legs(sign * lots * lot_size * close). Positive = net debit. */
  value: number;
  /** Per-share net premium with openalgo's sign convention (SELL=+1):
   *  sum_legs(per_share_sign * close). Positive = credit, negative = debit. */
  net_premium: number;
  /** |net_premium| — openalgo's "combined_premium" axis label. */
  combined_premium: number;
  /** Present only when every leg has entry_price. value(t) - entry_premium. */
  pnl?: number;
}

export type StrategyTag = "credit" | "debit" | "flat";

export interface ChartResponseData {
  underlying: string;
  underlying_ltp: number;
  exchange: string;
  interval: string;
  days: number;
  /** False when the broker can't deliver intraday history for the underlying. */
  underlying_available: boolean;
  underlying_series: ChartCandle[];
  leg_series: ChartLegSeries[];
  combined_series: ChartCombinedPoint[];
  /** Rupee net debit at entry (positive = paid premium, negative = received). */
  entry_premium: number | null;
  /** Per-share net premium at entry (openalgo sign: positive = credit). */
  entry_net_premium: number;
  /** |entry_net_premium| — what openalgo shows in its info bar. */
  entry_abs_premium: number;
  /** openalgo's classification: 'credit' | 'debit' | 'flat'. */
  tag: StrategyTag;
}

export interface ChartResponse {
  status: "success";
  data: ChartResponseData;
}

// ────────────────────────────────────────────────────────────────────────
// Multi-strike OI endpoint (POST /web/strategybuilder/multi-strike-oi)
// ────────────────────────────────────────────────────────────────────────

export interface MultiStrikeOILegInput {
  symbol: string;
  action: Action;
  exchange?: string;
  strike?: number;
  option_type?: OptionType;
  expiry_date?: string;
}

export interface MultiStrikeOIRequest {
  underlying: string;
  exchange?: string;
  options_exchange?: string;
  interval: string;
  days?: number;
  include_underlying?: boolean;
  legs: MultiStrikeOILegInput[];
}

/** {time: unix-seconds, value: OI count or underlying close}. */
export interface MultiStrikeOIPoint {
  time: number;
  value: number;
}

export interface MultiStrikeOILeg {
  index: number;
  symbol: string;
  exchange: string;
  action: Action;
  strike?: number;
  option_type?: OptionType;
  expiry?: string;
  /** False when the broker returned an all-zero OI series. */
  has_oi: boolean;
  series: MultiStrikeOIPoint[];
  error?: string;
}

export interface MultiStrikeOIData {
  underlying: string;
  underlying_ltp: number;
  exchange: string;
  interval: string;
  days: number;
  underlying_available: boolean;
  underlying_series: MultiStrikeOIPoint[];
  legs: MultiStrikeOILeg[];
}

export interface MultiStrikeOIResponse {
  status: "success";
  data: MultiStrikeOIData;
}
