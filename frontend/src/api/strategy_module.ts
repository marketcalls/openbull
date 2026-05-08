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
