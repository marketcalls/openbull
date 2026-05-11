/**
 * Strategy module API client (matches backend /web/strategy/* router).
 * Session-cookie authed via the shared axios instance.
 */

import api from "@/config/api";
import type {
  Strategy,
  StrategyCreate,
  StrategyCreateResponse,
  StrategyListItem,
  StrategyMode,
  StrategyStatus,
  StrategyUpdate,
  UniverseTab,
} from "@/types/strategy_module";

interface ListResponse {
  status: "success";
  strategies: StrategyListItem[];
}

interface SingleResponse {
  status: "success";
  strategy: Strategy;
}

export interface ListFilters {
  status?: StrategyStatus;
  universe_tab?: UniverseTab;
}

export async function listStrategies(
  filters: ListFilters = {},
): Promise<StrategyListItem[]> {
  const response = await api.get<ListResponse>("/web/strategy", {
    params: filters,
  });
  return response.data.strategies;
}

export async function getStrategy(id: number): Promise<Strategy> {
  const response = await api.get<SingleResponse>(`/web/strategy/${id}`);
  return response.data.strategy;
}

export async function createStrategy(
  payload: StrategyCreate,
): Promise<StrategyCreateResponse> {
  const response = await api.post<StrategyCreateResponse>(
    "/web/strategy",
    payload,
  );
  return response.data;
}

export async function updateStrategy(
  id: number,
  payload: StrategyUpdate,
): Promise<Strategy> {
  const response = await api.patch<SingleResponse>(
    `/web/strategy/${id}`,
    payload,
  );
  return response.data.strategy;
}

export async function deleteStrategy(id: number): Promise<void> {
  await api.delete(`/web/strategy/${id}`);
}

export async function rotateWebhookToken(
  id: number,
): Promise<StrategyCreateResponse> {
  const response = await api.post<StrategyCreateResponse>(
    `/web/strategy/${id}/rotate_webhook_token`,
  );
  return response.data;
}

// ---------------------------------------------------------------------------
// Phase 3 helper endpoints — wizard pickers
// ---------------------------------------------------------------------------

import type { ExpiryRank, OptionType, UniverseTab } from "@/types/strategy_module";

export interface UnderlyingChoice {
  symbol: string;
  name: string;
  exchange: string;
}

interface UnderlyingsResponse {
  status: "success";
  underlyings: UnderlyingChoice[];
}

export async function listUnderlyings(
  universe_tab: UniverseTab,
): Promise<UnderlyingChoice[]> {
  const response = await api.get<UnderlyingsResponse>(
    "/web/strategy/underlyings",
    { params: { universe_tab } },
  );
  return response.data.underlyings;
}

interface ExpiriesResponse {
  status: "success";
  data: string[]; // DD-MMM-YY
}

export async function listExpiries(
  underlying: string,
  underlying_exchange: string,
  instrument: "options" | "futures" = "options",
): Promise<string[]> {
  const response = await api.get<ExpiriesResponse>("/web/strategy/expiries", {
    params: { underlying, underlying_exchange, instrument },
  });
  return response.data.data;
}

interface StrikesResponse {
  status: "success";
  strikes: number[];
  underlying: string;
  exchange: string;
  expiry: string;
  option_type: string;
}

export async function listStrikes(params: {
  underlying: string;
  underlying_exchange: string;
  option_type: OptionType;
  expiry_rank?: ExpiryRank;
  expiry?: string;
}): Promise<StrikesResponse> {
  const response = await api.get<StrikesResponse>("/web/strategy/strikes", {
    params,
  });
  return response.data;
}

// ---------------------------------------------------------------------------
// Phase 4 — lifecycle + scoped views
// ---------------------------------------------------------------------------

export interface StrategyRun {
  id: number;
  strategy_id: number;
  mode: StrategyMode;
  broker: string;
  started_at: string;
  stopped_at: string | null;
  stop_reason: string | null;
  pnl_realized: number;
  pnl_peak: number;
  pnl_trough: number;
  trigger_source: string;
}

export interface StrategyOrder {
  id: number;
  leg_id: number;
  kind: string;
  broker_order_id: string | null;
  symbol: string;
  exchange: string;
  action: string;
  qty: number;
  pricetype: string;
  price: number;
  trigger_price: number;
  status: string;
  placed_at: string;
  filled_at: string | null;
  avg_fill_price: number | null;
  filled_qty: number | null;
  reject_reason: string | null;
}

export interface StrategyEvent {
  id: number;
  run_id: number | null;
  ts: string;
  kind: string;
  severity: "info" | "warn" | "critical";
  leg_id: number | null;
  message: string;
  payload: Record<string, unknown> | null;
}

export interface StartRunResponse {
  status: "success";
  run: StrategyRun;
  legs: Array<{
    leg_id: number;
    symbol: string;
    exchange: string;
    lotsize: number;
    broker_order_id: string | null;
    status: string;
    reject_reason: string | null;
  }>;
}

export interface ExitResponse {
  status: "success";
  run_id: number;
  stop_reason?: string;
  legs: Array<{
    leg_id: number;
    status: string;
    reason?: string;
    broker_order_id?: string | null;
  }>;
}

export async function startRun(
  id: number,
  mode: StrategyMode,
): Promise<StartRunResponse> {
  const response = await api.post<StartRunResponse>(`/web/strategy/${id}/start`, { mode });
  return response.data;
}

export async function stopRun(id: number): Promise<ExitResponse> {
  const response = await api.post<ExitResponse>(`/web/strategy/${id}/stop`);
  return response.data;
}

export async function closeAll(id: number): Promise<ExitResponse> {
  const response = await api.post<ExitResponse>(`/web/strategy/${id}/close_all`);
  return response.data;
}

export async function closeLeg(
  strategy_id: number,
  leg_id: number,
): Promise<ExitResponse> {
  const response = await api.post<ExitResponse>(
    `/web/strategy/${strategy_id}/legs/${leg_id}/close`,
  );
  return response.data;
}

export async function listOrders(
  strategy_id: number,
  run_id?: number,
): Promise<StrategyOrder[]> {
  const response = await api.get<{ status: "success"; orders: StrategyOrder[] }>(
    `/web/strategy/${strategy_id}/orders`,
    { params: run_id ? { run_id } : {} },
  );
  return response.data.orders;
}

export async function listRuns(strategy_id: number): Promise<StrategyRun[]> {
  const response = await api.get<{ status: "success"; runs: StrategyRun[] }>(
    `/web/strategy/${strategy_id}/runs`,
  );
  return response.data.runs;
}

export async function listEvents(
  strategy_id: number,
  run_id?: number,
  limit = 200,
): Promise<StrategyEvent[]> {
  const params: Record<string, number> = { limit };
  if (run_id) params.run_id = run_id;
  const response = await api.get<{ status: "success"; events: StrategyEvent[] }>(
    `/web/strategy/${strategy_id}/events`,
    { params },
  );
  return response.data.events;
}
