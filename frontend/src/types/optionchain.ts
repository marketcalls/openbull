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

export const FNO_EXCHANGES: ReadonlyArray<{ value: string; label: string }> = [
  { value: "NFO", label: "NFO" },
  { value: "BFO", label: "BFO" },
  { value: "MCX", label: "MCX" },
  { value: "CDS", label: "CDS" },
];

export const DEFAULT_UNDERLYINGS: Record<string, string[]> = {
  NFO: ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"],
  BFO: ["SENSEX", "BANKEX"],
  MCX: ["CRUDEOIL", "GOLD", "SILVER", "NATURALGAS"],
  CDS: ["USDINR", "EURINR", "GBPINR", "JPYINR"],
};

export const STRIKE_COUNTS: ReadonlyArray<{ value: number; label: string }> = [
  { value: 5, label: "5 strikes" },
  { value: 10, label: "10 strikes" },
  { value: 15, label: "15 strikes" },
  { value: 20, label: "20 strikes" },
  { value: 25, label: "25 strikes" },
];
