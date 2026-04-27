import api from "@/config/api";
import { getApiKey } from "@/api/apikey";
import type {
  ExpiryResponse,
  OptionChainResponse,
  PlaceOrderRequest,
  PlaceOrderResponse,
  UnderlyingsResponse,
} from "@/types/optionchain";

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

export async function fetchOptionChain(params: {
  underlying: string;
  exchange: string;
  expiry_date: string;
  strike_count: number;
}): Promise<OptionChainResponse> {
  const apikey = await resolveApiKey();
  const response = await api.post<OptionChainResponse>("/api/v1/optionchain", {
    apikey,
    ...params,
  });
  return response.data;
}

export async function fetchExpiries(params: {
  symbol: string;
  exchange: string;
  instrumenttype?: string;
}): Promise<ExpiryResponse> {
  const apikey = await resolveApiKey();
  const response = await api.post<ExpiryResponse>("/api/v1/expiry", {
    apikey,
    instrumenttype: params.instrumenttype ?? "options",
    ...params,
  });
  return response.data;
}

export async function fetchUnderlyings(exchange: "NFO" | "BFO"): Promise<UnderlyingsResponse> {
  const response = await api.get<UnderlyingsResponse>("/web/symbols/underlyings", {
    params: { exchange },
  });
  return response.data;
}

export async function placeOrder(req: Omit<PlaceOrderRequest, "apikey">): Promise<PlaceOrderResponse> {
  const apikey = await resolveApiKey();
  const response = await api.post<PlaceOrderResponse>("/api/v1/placeorder", {
    apikey,
    ...req,
  });
  return response.data;
}
