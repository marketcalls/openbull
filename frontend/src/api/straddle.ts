import api from "@/config/api";
import { getApiKey } from "@/api/apikey";

export interface StraddlePoint {
  time: number; // unix seconds
  spot: number;
  atm_strike: number;
  ce_price: number;
  pe_price: number;
  straddle: number;
  synthetic_future: number;
}

export interface StraddleData {
  underlying: string;
  underlying_ltp: number;
  expiry_date: string;
  interval: string;
  days_to_expiry: number;
  series: StraddlePoint[];
}

export interface StraddleResponse {
  status: "success" | "error";
  data?: StraddleData;
  message?: string;
}

let cachedApiKey: string | null = null;

async function resolveApiKey(): Promise<string> {
  if (cachedApiKey) return cachedApiKey;
  const { api_key } = await getApiKey();
  if (!api_key) throw new Error("No API key found. Generate one on the API Key page first.");
  cachedApiKey = api_key;
  return api_key;
}

export async function fetchStraddleChart(params: {
  underlying: string;
  exchange: "NFO" | "BFO";
  expiry_date: string;
  interval: string;
  days: number;
}): Promise<StraddleResponse> {
  const apikey = await resolveApiKey();
  const response = await api.post<StraddleResponse>("/api/v1/straddle", {
    apikey,
    ...params,
  });
  return response.data;
}
