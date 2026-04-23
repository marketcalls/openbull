import api from "@/config/api";

export interface ApiLogRow {
  id: number;
  created_at: string | null;
  user_id: number | null;
  auth_method: string | null;
  mode: "live" | "sandbox" | null;
  method: string;
  path: string;
  status_code: number;
  duration_ms: number;
  client_ip: string | null;
  user_agent: string | null;
  request_id: string | null;
  request_body: string | null;
  response_body: string | null;
  error: string | null;
}

export interface ApiLogListResponse {
  count: number;
  next_cursor: number | null;
  items: ApiLogRow[];
}

export interface ApiLogStats {
  total: number;
  ok_2xx: number;
  client_errors_4xx: number;
  server_errors_5xx: number;
}

export interface ListApiLogsParams {
  limit?: number;
  before_id?: number | null;
  method?: string;
  mode?: "live" | "sandbox";
  status?: number;
  status_class?: "1xx" | "2xx" | "3xx" | "4xx" | "5xx";
  path_contains?: string;
  start?: string;
  end?: string;
  user_id?: number;
}

function cleanParams(params: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") out[k] = v;
  }
  return out;
}

export async function listApiLogs(params: ListApiLogsParams = {}): Promise<ApiLogListResponse> {
  const response = await api.get<ApiLogListResponse>("/web/logs", {
    params: cleanParams(params as Record<string, unknown>),
  });
  return response.data;
}

export async function getApiLog(id: number): Promise<ApiLogRow> {
  const response = await api.get<ApiLogRow>(`/web/logs/${id}`);
  return response.data;
}

export async function getApiLogStats(start?: string, end?: string): Promise<ApiLogStats> {
  const response = await api.get<ApiLogStats>("/web/logs/stats", {
    params: cleanParams({ start, end }),
  });
  return response.data;
}

export function buildExportUrl(params: ListApiLogsParams = {}): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(cleanParams(params as Record<string, unknown>))) {
    q.set(k, String(v));
  }
  const s = q.toString();
  return `/web/logs/export.csv${s ? `?${s}` : ""}`;
}
