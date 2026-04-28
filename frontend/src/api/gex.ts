import api from "@/config/api";
import { getApiKey } from "@/api/apikey";

export interface GEXStrike {
  strike: number;
  ce_oi: number;
  pe_oi: number;
  ce_gamma: number;
  pe_gamma: number;
  ce_gex: number;
  pe_gex: number;
  net_gex: number;
}

export interface GEXResponse {
  status: "success" | "error";
  underlying: string;
  spot_price: number;
  futures_price: number | null;
  lot_size: number;
  atm_strike: number;
  expiry_date: string;
  pcr_oi: number;
  total_ce_oi: number;
  total_pe_oi: number;
  total_ce_gex: number;
  total_pe_gex: number;
  total_net_gex: number;
  chain: GEXStrike[];
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

export async function fetchGEX(params: {
  underlying: string;
  exchange: "NFO" | "BFO";
  expiry_date: string;
}): Promise<GEXResponse> {
  const apikey = await resolveApiKey();
  const response = await api.post<GEXResponse>("/api/v1/gex", {
    apikey,
    ...params,
  });
  return response.data;
}
