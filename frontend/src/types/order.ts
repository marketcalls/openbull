export interface FundsData {
  availablecash: number;
  collateral: number;
  m2munrealized: number;
  m2mrealized: number;
  utiliseddebits: number;
}

export interface OrderbookItem {
  symbol: string;
  exchange: string;
  action: string;
  product: string;
  price_type: string;
  quantity: number;
  price: number;
  status: string;
}

export interface TradebookItem {
  symbol: string;
  exchange: string;
  action: string;
  product: string;
  price_type: string;
  quantity: number;
  price: number;
  trade_value: number;
}

export interface PositionItem {
  symbol: string;
  exchange: string;
  product: string;
  quantity: number;
  average_price: number;
  ltp: number;
  pnl: number;
}

export interface HoldingItem {
  symbol: string;
  exchange: string;
  quantity: number;
  average_price: number;
  ltp: number;
  pnl: number;
  pnl_percent: number;
}
