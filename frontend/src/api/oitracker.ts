import api from "@/config/api";
import { getApiKey } from "@/api/apikey";

export interface OITrackerStrike {
  strike: number;
  ce_oi: number;
  pe_oi: number;
}

export interface OITrackerResponse {
  status: "success" | "error";
  underlying: string;
  spot_price: number;
  futures_price: number | null;
  lot_size: number;
  pcr_oi: number;
  pcr_volume: number;
  total_ce_oi: number;
  total_pe_oi: number;
  atm_strike: number;
  expiry_date: string;
  chain: OITrackerStrike[];
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

export async function fetchOITracker(params: {
  underlying: string;
  exchange: "NFO" | "BFO";
  expiry_date: string;
}): Promise<OITrackerResponse> {
  const apikey = await resolveApiKey();
  const response = await api.post<OITrackerResponse>("/api/v1/oitracker", {
    apikey,
    ...params,
  });
  return response.data;
}
