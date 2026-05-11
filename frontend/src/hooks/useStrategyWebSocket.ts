/**
 * WebSocket hook for /ws/strategy/{id}.
 *
 * Connection lifecycle:
 *   idle -> connecting -> open -> closed -> reconnecting (exponential backoff)
 *
 * Auth: relies on the existing session cookie (`access_token`). The WS
 * endpoint validates ownership before sending the first snapshot, so a
 * cross-tenant connect closes immediately with 1008.
 *
 * Backoff: 1s, 2s, 4s, 8s, capped at 30s. Resets to 0 on a successful
 * snapshot frame.
 */

import { useCallback, useEffect, useRef, useState } from "react";

export type WsStatus =
  | "idle"
  | "connecting"
  | "open"
  | "closed"
  | "reconnecting"
  | "error";

export interface StrategySnapshot {
  type: "snapshot";
  ts_ist: string;
  ts_ms_utc: number;
  strategy_id: number;
  run_id: number | null;
  status: string;
  mode: string | null;
  mtm_realized: number;
  mtm_unrealized: number;
  mtm_total: number;
  peak: number;
  trough: number;
  legs: Array<Record<string, unknown>>;
}

export interface StrategyDelta {
  type: "delta";
  ts_ist: string;
  ts_ms_utc: number;
  mtm_realized?: number;
  mtm_unrealized?: number;
  mtm_total?: number;
  peak?: number;
  trough?: number;
  legs?: Array<Record<string, unknown>>;
}

export interface StrategyWsEvent {
  type: "event";
  ts_ist: string;
  ts_ms_utc: number;
  kind: string;
  severity: "info" | "warn" | "critical";
  leg_id: number | null;
  message: string;
  payload: Record<string, unknown>;
}

export interface UseStrategyWebSocketResult {
  status: WsStatus;
  snapshot: StrategySnapshot | null;
  /** Live state, snapshot merged with all received deltas. */
  liveState: StrategySnapshot | null;
  events: StrategyWsEvent[];
  reconnect: () => void;
}

function mergeLegs(
  prev: Array<Record<string, unknown>>,
  patch: Array<Record<string, unknown>>,
): Array<Record<string, unknown>> {
  const byId = new Map<number, Record<string, unknown>>();
  for (const l of prev) {
    const id = Number(l.leg_id);
    byId.set(id, { ...l });
  }
  for (const p of patch) {
    const id = Number(p.leg_id);
    if (byId.has(id)) {
      byId.set(id, { ...byId.get(id)!, ...p });
    } else {
      byId.set(id, p);
    }
  }
  return Array.from(byId.values()).sort(
    (a, b) => Number(a.leg_id) - Number(b.leg_id),
  );
}

export function useStrategyWebSocket(
  strategyId: number | null,
  enabled = true,
): UseStrategyWebSocketResult {
  const [status, setStatus] = useState<WsStatus>("idle");
  const [snapshot, setSnapshot] = useState<StrategySnapshot | null>(null);
  const [liveState, setLiveState] = useState<StrategySnapshot | null>(null);
  const [events, setEvents] = useState<StrategyWsEvent[]>([]);

  const wsRef = useRef<WebSocket | null>(null);
  const retryRef = useRef<number>(0);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const shouldRunRef = useRef<boolean>(enabled);
  shouldRunRef.current = enabled && strategyId !== null;

  const closeSocket = useCallback(() => {
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    const ws = wsRef.current;
    if (ws) {
      try {
        ws.close();
      } catch {
        /* ignore */
      }
      wsRef.current = null;
    }
  }, []);

  const connect = useCallback(() => {
    if (!shouldRunRef.current || strategyId === null) return;
    if (wsRef.current && wsRef.current.readyState <= WebSocket.OPEN) return;

    setStatus("connecting");
    // Same origin — Vite dev server proxies via the backend; production
    // serves both from one host.
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${window.location.host}/ws/strategy/${strategyId}`;
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      setStatus("open");
    };

    ws.onmessage = (evt) => {
      let msg: Record<string, unknown>;
      try {
        msg = JSON.parse(evt.data as string);
      } catch {
        return;
      }
      const type = msg.type as string;
      if (type === "snapshot") {
        const snap = msg as unknown as StrategySnapshot;
        setSnapshot(snap);
        setLiveState(snap);
        retryRef.current = 0;
      } else if (type === "delta") {
        const delta = msg as unknown as StrategyDelta;
        setLiveState((prev) => {
          if (prev === null) return prev;
          const nextLegs = delta.legs
            ? mergeLegs(prev.legs, delta.legs)
            : prev.legs;
          return {
            ...prev,
            ts_ist: delta.ts_ist,
            ts_ms_utc: delta.ts_ms_utc,
            mtm_realized: delta.mtm_realized ?? prev.mtm_realized,
            mtm_unrealized: delta.mtm_unrealized ?? prev.mtm_unrealized,
            mtm_total: delta.mtm_total ?? prev.mtm_total,
            peak: delta.peak ?? prev.peak,
            trough: delta.trough ?? prev.trough,
            legs: nextLegs,
          };
        });
      } else if (type === "event") {
        const ev = msg as unknown as StrategyWsEvent;
        setEvents((prev) => [ev, ...prev].slice(0, 500));
      } else if (type === "ping") {
        // server heartbeat — ignore
      }
    };

    ws.onerror = () => {
      setStatus("error");
    };

    ws.onclose = () => {
      wsRef.current = null;
      if (!shouldRunRef.current) {
        setStatus("closed");
        return;
      }
      // Exponential backoff
      const delayMs = Math.min(30_000, 1_000 * Math.pow(2, retryRef.current));
      retryRef.current += 1;
      setStatus("reconnecting");
      reconnectTimerRef.current = setTimeout(connect, delayMs);
    };
  }, [strategyId]);

  // (Re-)connect when strategyId or `enabled` flips
  useEffect(() => {
    if (!enabled || strategyId === null) {
      shouldRunRef.current = false;
      closeSocket();
      setStatus("idle");
      setSnapshot(null);
      setLiveState(null);
      setEvents([]);
      return;
    }
    shouldRunRef.current = true;
    connect();
    return () => {
      shouldRunRef.current = false;
      closeSocket();
    };
  }, [enabled, strategyId, connect, closeSocket]);

  const reconnect = useCallback(() => {
    retryRef.current = 0;
    closeSocket();
    if (shouldRunRef.current) {
      setTimeout(connect, 100);
    }
  }, [closeSocket, connect]);

  return { status, snapshot, liveState, events, reconnect };
}
