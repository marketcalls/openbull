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
