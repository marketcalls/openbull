import api from "@/config/api";
import { getApiKey } from "@/api/apikey";

export interface IVChartPoint {
  time: number; // unix seconds
  iv: number | null;
  delta: number | null;
  gamma: number | null;
  theta: number | null;
  vega: number | null;
  option_price: number;
  underlying_price: number;
}

export interface IVChartSeries {
  symbol: string;
  option_type: "CE" | "PE";
  strike: number;
  iv_data: IVChartPoint[];
}

export interface IVChartData {
  underlying: string;
  underlying_ltp: number;
  atm_strike: number;
  ce_symbol: string;
  pe_symbol: string;
  interval: string;
  days: number;
  expiry_date: string;
  interest_rate: number;
  series: IVChartSeries[];
}

export interface IVChartResponse {
  status: "success" | "error";
  data?: IVChartData;
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

export async function fetchIVChart(params: {
  underlying: string;
  exchange: "NFO" | "BFO";
  expiry_date: string;
  interval: string;
  days: number;
  interest_rate?: number;
}): Promise<IVChartResponse> {
  const apikey = await resolveApiKey();
  const response = await api.post<IVChartResponse>("/api/v1/ivchart", {
    apikey,
    ...params,
  });
  return response.data;
}
