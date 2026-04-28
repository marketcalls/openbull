import api from "@/config/api";
import { getApiKey } from "@/api/apikey";

export interface VolSurfaceExpiry {
  date: string;
  dte: number;
}

export interface VolSurfaceData {
  underlying: string;
  underlying_ltp: number;
  atm_strike: number;
  strikes: number[];
  expiries: VolSurfaceExpiry[];
  surface: (number | null)[][]; // surface[expiry_idx][strike_idx]
}

export interface VolSurfaceResponse {
  status: "success" | "error";
  data?: VolSurfaceData;
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

export async function fetchVolSurface(params: {
  underlying: string;
  exchange: "NFO" | "BFO";
  expiry_dates: string[];
  strike_count: number;
}): Promise<VolSurfaceResponse> {
  const apikey = await resolveApiKey();
  const response = await api.post<VolSurfaceResponse>("/api/v1/volsurface", {
    apikey,
    ...params,
  });
  return response.data;
}
