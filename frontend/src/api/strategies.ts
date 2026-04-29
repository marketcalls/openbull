/**
 * Saved-strategies CRUD client (matches /web/strategies/* router).
 *
 * Session-cookie authed via the shared axios instance. No API key needed
 * because these are internal UI endpoints, not external API.
 */

import api from "@/config/api";
import type {
  Strategy,
  StrategyCreate,
  StrategyMode,
  StrategyStatus,
  StrategyUpdate,
} from "@/types/strategy";

interface ListResponse {
  status: "success";
  strategies: Strategy[];
}

interface SingleResponse {
  status: "success";
  strategy: Strategy;
}

export interface ListFilters {
  mode?: StrategyMode;
  status?: StrategyStatus;
  underlying?: string;
}

export async function listStrategies(
  filters: ListFilters = {},
): Promise<Strategy[]> {
  const response = await api.get<ListResponse>("/web/strategies", {
    params: filters,
  });
  return response.data.strategies;
}

export async function getStrategy(id: number): Promise<Strategy> {
  const response = await api.get<SingleResponse>(`/web/strategies/${id}`);
  return response.data.strategy;
}

export async function createStrategy(
  payload: StrategyCreate,
): Promise<Strategy> {
  const response = await api.post<SingleResponse>("/web/strategies", payload);
  return response.data.strategy;
}

export async function updateStrategy(
  id: number,
  payload: StrategyUpdate,
): Promise<Strategy> {
  const response = await api.put<SingleResponse>(
    `/web/strategies/${id}`,
    payload,
  );
  return response.data.strategy;
}

export async function deleteStrategy(id: number): Promise<void> {
  await api.delete(`/web/strategies/${id}`);
}
