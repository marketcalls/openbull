export interface OptionLeg {
  symbol: string;
  label: string;
  ltp: number;
  open: number;
  high: number;
  low: number;
  prev_close: number;
  volume: number;
  oi: number;
  bid: number;
  ask: number;
  bid_qty: number;
  ask_qty: number;
  lotsize: number;
  tick_size: number;
}

export interface OptionStrike {
  strike: number;
  ce: OptionLeg | null;
  pe: OptionLeg | null;
}

export interface OptionChainResponse {
  status: "success" | "error";
  underlying: string;
  underlying_ltp: number;
  underlying_prev_close: number;
  expiry_date: string;
  atm_strike: number;
  chain: OptionStrike[];
  message?: string;
}

export interface ExpiryResponse {
  status: "success" | "error";
  data: string[];
  message?: string;
}

export interface UnderlyingOption {
  symbol: string;
  name: string;
}

export interface UnderlyingsResponse {
  status: "success" | "error";
  data: UnderlyingOption[];
  message?: string;
}

export interface PlaceOrderResponse {
  status: "success" | "error";
  orderid?: string;
  message?: string;
}

export interface PlaceOrderRequest {
  apikey: string;
  strategy?: string;
  symbol: string;
  exchange: string;
  action: "BUY" | "SELL";
  quantity: number | string;
  pricetype: "MARKET" | "LIMIT" | "SL" | "SL-M";
  product: "MIS" | "NRML" | "CNC";
  price?: number | string;
  trigger_price?: number | string;
  disclosed_quantity?: number | string;
}

export const FNO_EXCHANGES: ReadonlyArray<{ value: "NFO" | "BFO"; label: string }> = [
  { value: "NFO", label: "NFO" },
  { value: "BFO", label: "BFO" },
];

// Fallback list shown before the underlyings API responds — replaced once the
// /web/symbols/underlyings call returns the distinct option-ticker prefixes.
export const FALLBACK_UNDERLYINGS: Record<"NFO" | "BFO", UnderlyingOption[]> = {
  NFO: [
    { symbol: "NIFTY", name: "NIFTY 50" },
    { symbol: "BANKNIFTY", name: "NIFTY BANK" },
    { symbol: "FINNIFTY", name: "NIFTY FIN SERVICE" },
    { symbol: "MIDCPNIFTY", name: "NIFTY MID SELECT" },
  ],
  BFO: [
    { symbol: "SENSEX", name: "BSE SENSEX" },
    { symbol: "BANKEX", name: "BSE BANKEX" },
  ],
};

export const STRIKE_COUNTS: ReadonlyArray<{ value: number; label: string }> = [
  { value: 5, label: "5 strikes" },
  { value: 10, label: "10 strikes" },
  { value: 15, label: "15 strikes" },
  { value: 20, label: "20 strikes" },
  { value: 25, label: "25 strikes" },
];

// Index symbols whose spot quote lives on NSE_INDEX / BSE_INDEX in the symtoken
// table — used by the WebSocket subscription to fetch the underlying spot.
export const NSE_INDEX_SYMBOLS = new Set([
  "NIFTY",
  "BANKNIFTY",
  "FINNIFTY",
  "MIDCPNIFTY",
  "NIFTYNXT50",
  "NIFTYIT",
  "NIFTYPHARMA",
  "NIFTYBANK",
  "INDIAVIX",
]);
export const BSE_INDEX_SYMBOLS = new Set(["SENSEX", "BANKEX", "SENSEX50"]);

export function getUnderlyingExchange(symbol: string, optionExchange: string): string {
  if (NSE_INDEX_SYMBOLS.has(symbol)) return "NSE_INDEX";
  if (BSE_INDEX_SYMBOLS.has(symbol)) return "BSE_INDEX";
  if (optionExchange === "BFO") return "BSE";
  return "NSE";
}

// Column model — used by the table renderer + column-toggle dropdown.
export type ColumnKey =
  | "oi"
  | "volume"
  | "bid_qty"
  | "bid"
  | "ltp"
  | "ask"
  | "ask_qty"
  | "spread";

export interface ColumnDef {
  key: ColumnKey;
  label: string;
  defaultVisible: boolean;
}

export const COLUMNS: ColumnDef[] = [
  { key: "oi", label: "OI", defaultVisible: true },
  { key: "volume", label: "Volume", defaultVisible: true },
  { key: "bid_qty", label: "Bid Qty", defaultVisible: true },
  { key: "bid", label: "Bid", defaultVisible: true },
  { key: "ltp", label: "LTP", defaultVisible: true },
  { key: "ask", label: "Ask", defaultVisible: true },
  { key: "ask_qty", label: "Ask Qty", defaultVisible: true },
  { key: "spread", label: "Spread", defaultVisible: false },
];

export const VISIBLE_COLUMNS_STORAGE_KEY = "openbull_optionchain_visible_columns";
