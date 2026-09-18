// SMC win/loss model — score a candle series, get each SMC signal with P(win).
import api from "@/config/api";

export interface SmcSignal {
  i: number;
  timestamp: number;
  direction: "long" | "short";
  entry: number;
  sl: number;
  tp: number;
  rr: number;
  is_choch: boolean;
  win_prob: number;
}
export interface SmcMeta {
  trained_at?: string;
  base_win_rate?: number;
  auc?: number;
  n_total?: number;
  rr?: number;
  horizon?: number;
  intraday?: boolean;
  sources?: string;
  long_only?: boolean;
}
export interface SmcScoreResp {
  status: string;
  signals?: SmcSignal[];
  count?: number;
  meta?: SmcMeta;
  message?: string;
}
export interface CandleLite {
  timestamp: number; open: number; high: number; low: number; close: number; volume: number; oi: number;
}

export async function scoreSmc(candles: CandleLite[], rr?: number): Promise<SmcScoreResp> {
  try {
    const r = await api.post<SmcScoreResp>("/web/smc/score", { candles, rr, limit: 60 });
    return r.data;
  } catch (e) {
    const d = (e as { response?: { data?: SmcScoreResp } })?.response?.data;
    return d ?? { status: "error", message: "Failed to score SMC signals." };
  }
}
