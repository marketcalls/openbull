export interface SymbolSearchResult {
  symbol: string;
  brsymbol: string;
  name: string | null;
  exchange: string;
  brexchange: string | null;
  token: string | null;
  expiry: string | null;
  strike: number | null;
  lotsize: number | null;
  instrumenttype: string | null;
  tick_size: number | null;
}

export const EXCHANGES: ReadonlyArray<{ value: string; label: string }> = [
  { value: "NSE", label: "NSE — Equity" },
  { value: "NFO", label: "NFO — NSE Futures & Options" },
  { value: "BSE", label: "BSE — Equity" },
  { value: "BFO", label: "BFO — BSE Futures & Options" },
  { value: "MCX", label: "MCX — Commodities" },
  { value: "CDS", label: "CDS — NSE Currency" },
  { value: "BCD", label: "BCD — BSE Currency" },
  { value: "NSE_INDEX", label: "NSE_INDEX — NSE Indices" },
  { value: "BSE_INDEX", label: "BSE_INDEX — BSE Indices" },
];
