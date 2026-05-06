/**
 * Order management actions — typed wrappers over /api/v1/{cancel,modify,close}*.
 *
 * Used by the OrderBook + Positions pages for cancel / modify / close flows.
 * The `apikey` header is auto-resolved through `getApiKey` (cached after the
 * first call). Sandbox vs live dispatch happens server-side; the frontend
 * just calls these — same pattern as `placeOrder` and `placeBasketOrder`.
 */

import api from "@/config/api";
import { getApiKey } from "@/api/apikey";

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

export function clearOrdersCachedApiKey(): void {
  cachedApiKey = null;
}

// ─── Shapes ────────────────────────────────────────────────────────────

export interface CancelOrderRequest {
  orderid: string;
  strategy?: string;
}

export interface ModifyOrderRequest {
  orderid: string;
  quantity: number | string;
  price: number | string;
  pricetype: "MARKET" | "LIMIT" | "SL" | "SL-M";
  trigger_price?: number | string;
  disclosed_quantity?: number | string;
  /** OpenAlgo metadata fields — accepted by the API for shape parity but
   *  not forwarded to the broker (see backend/api/place_order.py:130). */
  symbol?: string;
  exchange?: string;
  action?: "BUY" | "SELL";
  product?: "MIS" | "NRML" | "CNC";
  strategy?: string;
}

export interface OrderActionResponse {
  status: "success" | "error";
  orderid?: string;
  message?: string;
}

export interface CancelAllResponse {
  status: "success" | "error";
  data?: {
    canceled: string[];
    failed: string[];
  };
  message?: string;
}

export interface CloseAllPositionsResponse {
  status: "success" | "error";
  message?: string;
}

// ─── Calls ─────────────────────────────────────────────────────────────

export async function cancelOrder(
  req: CancelOrderRequest,
): Promise<OrderActionResponse> {
  const apikey = await resolveApiKey();
  const response = await api.post<OrderActionResponse>("/api/v1/cancelorder", {
    apikey,
    ...req,
  });
  return response.data;
}

export async function cancelAllOrders(
  strategy?: string,
): Promise<CancelAllResponse> {
  const apikey = await resolveApiKey();
  const response = await api.post<CancelAllResponse>("/api/v1/cancelallorder", {
    apikey,
    ...(strategy ? { strategy } : {}),
  });
  return response.data;
}

export async function modifyOrder(
  req: ModifyOrderRequest,
): Promise<OrderActionResponse> {
  const apikey = await resolveApiKey();
  const response = await api.post<OrderActionResponse>("/api/v1/modifyorder", {
    apikey,
    ...req,
  });
  return response.data;
}

export async function closeAllPositions(
  strategy?: string,
): Promise<CloseAllPositionsResponse> {
  const apikey = await resolveApiKey();
  const response = await api.post<CloseAllPositionsResponse>(
    "/api/v1/closeposition",
    {
      apikey,
      ...(strategy ? { strategy } : {}),
    },
  );
  return response.data;
}
