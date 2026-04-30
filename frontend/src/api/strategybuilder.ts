/**
 * Strategy Builder live + historical client (matches
 * /web/strategybuilder/* router).
 *
 * Two endpoints:
 *   - getStrategySnapshot — single round-trip live pricing for the leg set
 *     (spot + per-leg LTP + IV + Greeks + position totals). Driven by the
 *     Refresh button and any leg-set change. Deliberately NOT subscribed to
 *     underlying ticks via WebSocket; per-leg LTP streaming is wired
 *     separately in the live P&L tab via useMarketData.
 *   - getStrategyChart — historical combined-premium time series with
 *     intersection-correct timestamps and an optional underlying overlay.
 */

import api from "@/config/api";
import type {
  ChartRequest,
  ChartResponse,
  ChartResponseData,
  MultiStrikeOIData,
  MultiStrikeOIRequest,
  MultiStrikeOIResponse,
  SnapshotRequest,
  SnapshotResponse,
} from "@/types/strategy";

export async function getStrategySnapshot(
  payload: SnapshotRequest,
): Promise<SnapshotResponse> {
  const response = await api.post<SnapshotResponse>(
    "/web/strategybuilder/snapshot",
    payload,
  );
  return response.data;
}

export async function getStrategyChart(
  payload: ChartRequest,
): Promise<ChartResponseData> {
  const response = await api.post<ChartResponse>(
    "/web/strategybuilder/chart",
    payload,
  );
  return response.data.data;
}

export async function getMultiStrikeOI(
  payload: MultiStrikeOIRequest,
): Promise<MultiStrikeOIData> {
  const response = await api.post<MultiStrikeOIResponse>(
    "/web/strategybuilder/multi-strike-oi",
    payload,
  );
  return response.data.data;
}
