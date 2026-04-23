import api from "@/config/api";

export type TradingMode = "live" | "sandbox";

export interface TradingModeResponse {
  mode: TradingMode;
}

export async function getTradingMode(): Promise<TradingMode> {
  const response = await api.get<TradingModeResponse>("/web/trading-mode");
  return response.data.mode;
}

export async function setTradingMode(mode: TradingMode): Promise<TradingMode> {
  const response = await api.post<TradingModeResponse>("/web/trading-mode", { mode });
  return response.data.mode;
}
