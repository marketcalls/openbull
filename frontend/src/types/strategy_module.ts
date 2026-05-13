/**
 * Strategy module types — mirrors backend/schemas/strategy_module.py.
 * Names kept identical to the Pydantic schemas so a future codegen pass
 * can replace this file with generated bindings.
 */

export type UniverseTab =
  | "weekly_monthly"
  | "monthly_only"
  | "stocks_fno"
  | "mcx";

export type StrategyType = "intraday" | "positional";
export type StrategyStatus = "stopped" | "running" | "paused" | "errored";
export type StrategyMode = "live" | "sandbox";
export type Product = "NRML" | "MIS" | "CNC";
export type PriceType = "MARKET" | "LIMIT";
export type Position = "B" | "S";
export type OptionType = "CE" | "PE";
export type Segment = "options" | "futures" | "cash";
export type ExpiryRank = "weekly" | "monthly" | "current" | "next";
export type StrikeMode = "atm" | "strike";
export type Weekday = "MON" | "TUE" | "WED" | "THU" | "FRI" | "SAT" | "SUN";

export interface TrailConfig {
  x: number;
  y: number;
}

export interface Leg {
  id: number;
  segment: Segment;
  expiry: ExpiryRank;
  lots: number;
  position: Position;
  option_type?: OptionType | null;
  strike_mode?: StrikeMode | null;
  atm_offset?: string | null;
  strike_value?: number | null;
  target_pts?: number | null;
  sl_pts?: number | null;
  trail: TrailConfig;
  momentum?: Record<string, unknown> | null;
}

export interface LockProfitConfig {
  mode: "lock" | "lock_and_trail";
  if_profit_reaches: number;
  lock_profit: number;
  trail_step?: number | null;
}

export interface SchedulerConfig {
  enabled: boolean;
  days: Weekday[];
  start_time: string; // HH:MM
  auto_stop_time?: string | null;
  default_mode: StrategyMode;
}

export interface WebhookIpAllowlistEntry {
  cidr: string;
  label?: string | null;
}

export interface StrategyCreate {
  name: string;
  universe_tab: UniverseTab;
  underlying: string;
  underlying_exchange: string;
  strategy_type: StrategyType;
  entry_time?: string | null;
  exit_time?: string | null;
  product: Product;
  pricetype: PriceType;
  legs: Leg[];
  overall_sl_mtm?: number | null;
  overall_target_mtm?: number | null;
  lock_profit?: LockProfitConfig | null;
  trail_sl_to_entry: boolean;
  scheduler?: SchedulerConfig | null;
  webhook_ip_allowlist?: WebhookIpAllowlistEntry[] | null;
  daily_loss_limit_inr?: number | null;
}

export type StrategyUpdate = Partial<StrategyCreate>;

export interface Strategy {
  id: number;
  name: string;
  universe_tab: UniverseTab;
  underlying: string;
  underlying_exchange: string;
  strategy_type: StrategyType;
  entry_time?: string | null;
  exit_time?: string | null;
  product: Product;
  pricetype: PriceType;
  legs: Leg[];
  overall_sl_mtm?: number | null;
  overall_target_mtm?: number | null;
  lock_profit?: LockProfitConfig | null;
  trail_sl_to_entry: boolean;
  scheduler?: SchedulerConfig | null;
  live_enabled: boolean;
  webhook_url: string;
  webhook_ip_allowlist?: WebhookIpAllowlistEntry[] | null;
  daily_loss_limit_inr?: number | null;
  status: StrategyStatus;
  current_run_id?: number | null;
  created_at: string;
  updated_at: string;
}

export interface StrategyListItem {
  id: number;
  name: string;
  universe_tab: UniverseTab;
  underlying: string;
  strategy_type: StrategyType;
  status: StrategyStatus;
  live_enabled: boolean;
  pnl_realized: number;
  pnl_unrealized: number;
  pnl_total: number;
  created_at: string;
  updated_at: string;
}

export interface StrategyCreateResponse {
  strategy: Strategy;
  webhook_token: string;
}

export const UNIVERSE_TAB_LABELS: Record<UniverseTab, string> = {
  weekly_monthly: "Weekly & Monthly Expiries",
  monthly_only: "Monthly Only Expiry",
  stocks_fno: "Stocks – Cash / F&O",
  mcx: "Commodities (MCX)",
};

export const UNIVERSE_TAB_HINT: Record<UniverseTab, string> = {
  weekly_monthly: "NIFTY, SENSEX",
  monthly_only: "MIDCPNIFTY, BANKNIFTY, FINNIFTY, BANKEX",
  stocks_fno: "All NIFTY 500 stocks",
  mcx: "CRUDEOIL, NATURALGAS, GOLD, SILVER, …",
};

/**
 * What expiry options are valid per tab. The backend leg validator is the
 * authority — this is just for the dropdown UX.
 */
export const TAB_EXPIRIES: Record<UniverseTab, ExpiryRank[]> = {
  weekly_monthly: ["weekly", "monthly"],
  monthly_only: ["monthly"],
  stocks_fno: ["monthly"],
  mcx: ["current", "next"],
};

/** What segments are allowed per tab. */
export const TAB_SEGMENTS: Record<UniverseTab, Segment[]> = {
  weekly_monthly: ["futures", "options"],
  monthly_only: ["futures", "options"],
  stocks_fno: ["cash", "futures", "options"],
  mcx: ["futures", "options"],
};

/**
 * Default underlyings per tab — used as the dropdown source until Phase 3
 * wires the dynamic /api/v1/strategy/underlyings endpoint. The MCX list is
 * Phase 3-dynamic; for Phase 2 we just take a small known-good seed.
 */
export const TAB_DEFAULT_UNDERLYINGS: Record<
  UniverseTab,
  Array<{ symbol: string; name: string; exchange: string }>
> = {
  weekly_monthly: [
    { symbol: "NIFTY", name: "Nifty 50", exchange: "NSE_INDEX" },
    { symbol: "SENSEX", name: "BSE SENSEX", exchange: "BSE_INDEX" },
  ],
  monthly_only: [
    { symbol: "BANKNIFTY", name: "Nifty Bank", exchange: "NSE_INDEX" },
    { symbol: "FINNIFTY", name: "Nifty Fin Service", exchange: "NSE_INDEX" },
    { symbol: "MIDCPNIFTY", name: "Nifty Midcap Select", exchange: "NSE_INDEX" },
    { symbol: "BANKEX", name: "BSE Bankex", exchange: "BSE_INDEX" },
  ],
  stocks_fno: [
    { symbol: "RELIANCE", name: "Reliance Industries", exchange: "NSE" },
    { symbol: "TCS", name: "Tata Consultancy Services", exchange: "NSE" },
    { symbol: "HDFCBANK", name: "HDFC Bank", exchange: "NSE" },
    { symbol: "INFY", name: "Infosys", exchange: "NSE" },
  ],
  mcx: [
    { symbol: "CRUDEOIL", name: "Crude Oil", exchange: "MCX" },
    { symbol: "NATURALGAS", name: "Natural Gas", exchange: "MCX" },
    { symbol: "GOLD", name: "Gold", exchange: "MCX" },
    { symbol: "SILVER", name: "Silver", exchange: "MCX" },
  ],
};

/** ATM offset choices shown in the strike-criteria dropdown. */
export const ATM_OFFSETS: string[] = [
  "ATM",
  "ITM1",
  "ITM2",
  "ITM3",
  "ITM4",
  "ITM5",
  "OTM1",
  "OTM2",
  "OTM3",
  "OTM4",
  "OTM5",
];
