/**
 * Basket-order client — typed wrapper over /api/v1/basketorder.
 *
 * Used by the Strategy Builder's "Execute Basket" flow. Takes raw
 * symbols + exchanges per leg (no offset re-resolution at execute time)
 * so what the user saved in the builder is exactly what gets fired.
 *
 * BUY-before-SELL ordering is enforced server-side — we don't need to
 * pre-sort here. The backend also validates exchange/action/pricetype/
 * product up front and returns 400 with a leg-indexed error if any leg
 * is malformed, before placing any orders.
 */

import { getApiKey } from "@/api/apikey";
import api from "@/config/api";

export type Action = "BUY" | "SELL";
export type PriceType = "MARKET" | "LIMIT" | "SL" | "SL-M";
export type Product = "MIS" | "NRML" | "CNC";

export interface BasketOrderLeg {
  symbol: string;
  exchange: string;
  action: Action;
  /** Total quantity in shares — ``lots × lot_size`` for options. */
  quantity: number;
  pricetype: PriceType;
  product: Product;
  /** Required for LIMIT/SL/SL-M; backend defaults to 0 for MARKET. */
  price?: number;
  trigger_price?: number;
  disclosed_quantity?: number;
}

export interface BasketOrderRequest {
  /** Strategy tag — appears in orderbook as the placed-by label. Optional. */
  strategy?: string;
  orders: BasketOrderLeg[];
}

export interface BasketLegResult {
  symbol: string;
  status: "success" | "error";
  orderid?: string;
  message?: string;
}

export interface BasketOrderResponse {
  status: "success" | "error";
  results?: BasketLegResult[];
  /** Set on top-level validation failure (legs look bad, broker module
   *  missing, etc.). When set, ``results`` is absent. */
  message?: string;
}

let cachedApiKey: string | null = null;

async function resolveApiKey(): Promise<string> {
  if (cachedApiKey) return cachedApiKey;
  const { api_key } = await getApiKey();
  if (!api_key) {
    throw new Error("No API key found. Generate one on the API Key page first.");
  }
  cachedApiKey = api_key;
  return api_key;
}

export function clearCachedApiKey(): void {
  cachedApiKey = null;
}

export async function placeBasketOrder(
  payload: BasketOrderRequest,
): Promise<BasketOrderResponse> {
  const apikey = await resolveApiKey();
  const response = await api.post<BasketOrderResponse>("/api/v1/basketorder", {
    apikey,
    ...payload,
  });
  return response.data;
}

// ────────────────────────────────────────────────────────────────────────
// Margin requirement (POST /api/v1/margin)
// ────────────────────────────────────────────────────────────────────────

export interface MarginPosition {
  symbol: string;
  exchange: string;
  action: Action;
  quantity: number;
  product: Product;
  pricetype: PriceType;
  price?: number;
}

export interface MarginResponseData {
  /** Net margin required after hedge benefits — what the broker would
   *  block in the user's account. */
  total_margin_required: number;
  span_margin?: number;
  exposure_margin?: number;
  /** initial.total − final.total — premium saved vs treating each leg
   *  as standalone. */
  margin_benefit?: number;
}

export interface MarginResponse {
  status: "success" | "error";
  data?: MarginResponseData;
  message?: string;
}

export async function getBasketMargin(
  positions: MarginPosition[],
): Promise<MarginResponse> {
  const apikey = await resolveApiKey();
  const response = await api.post<MarginResponse>("/api/v1/margin", {
    apikey,
    positions,
  });
  return response.data;
}
