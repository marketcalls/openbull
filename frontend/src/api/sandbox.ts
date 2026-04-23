import api from "@/config/api";

export interface SandboxConfigEntry {
  value: string;
  description: string;
  is_editable: boolean;
}

export type SandboxConfigMap = Record<string, SandboxConfigEntry>;

export interface SandboxSummary {
  total_orders: number;
  funds: {
    availablecash: number;
    utiliseddebits: number;
    collateral: number;
    m2munrealized: number;
    m2mrealized: number;
  };
}

export async function getSandboxConfigs(): Promise<SandboxConfigMap> {
  const response = await api.get<{ status: string; data: SandboxConfigMap }>(
    "/web/sandbox/config"
  );
  return response.data.data;
}

export async function updateSandboxConfig(key: string, value: string): Promise<void> {
  await api.post("/web/sandbox/config", { key, value });
}

export async function resetSandbox(): Promise<void> {
  await api.post("/web/sandbox/reset");
}

export async function getSandboxSummary(): Promise<SandboxSummary> {
  const response = await api.get<{ status: string; data: SandboxSummary }>(
    "/web/sandbox/summary"
  );
  return response.data.data;
}

export interface SandboxDailyPnLRow {
  date: string;
  starting_capital: number;
  available: number;
  used_margin: number;
  realized_pnl: number;
  unrealized_pnl: number;
  total_pnl: number;
  positions_pnl: number;
  holdings_pnl: number;
  trades_count: number;
}

export async function getSandboxMyPnL(limit = 180): Promise<SandboxDailyPnLRow[]> {
  const response = await api.get<{ status: string; data: SandboxDailyPnLRow[] }>(
    "/web/sandbox/mypnl",
    { params: { limit } }
  );
  return response.data.data;
}

export async function squareoffNow(
  bucket: "nse_nfo_bse_bfo" | "cds" | "mcx" = "nse_nfo_bse_bfo"
): Promise<{ placed: number }> {
  const response = await api.post<{ status: string; placed: number; bucket: string }>(
    "/web/sandbox/squareoff-now",
    null,
    { params: { bucket } }
  );
  return { placed: response.data.placed };
}

export async function settleNow(): Promise<{
  holdings_moved: number;
  pnl_snapshots_written: number;
}> {
  const response = await api.post<{
    status: string;
    holdings_moved: number;
    pnl_snapshots_written: number;
  }>("/web/sandbox/settle-now");
  return {
    holdings_moved: response.data.holdings_moved,
    pnl_snapshots_written: response.data.pnl_snapshots_written,
  };
}
