import { useEffect, useMemo, useRef, useState } from "react";
import { getWebSocketApiKey, getWebSocketConfig } from "@/api/websocket";

export type SubscriptionMode = "LTP" | "Quote" | "Depth";

export interface DepthLevel {
  price?: number;
  quantity?: number;
  orders?: number;
}

export interface MarketTickData {
  ltp?: number;
  open?: number;
  high?: number;
  low?: number;
  close?: number;
  volume?: number;
  oi?: number;
  bid_price?: number;
  ask_price?: number;
  bid_size?: number;
  ask_size?: number;
  depth?: { buy?: DepthLevel[]; sell?: DepthLevel[] };
  change?: number;
  change_percent?: number;
}

export interface SymbolData {
  symbol: string;
  exchange: string;
  data: MarketTickData;
  lastUpdate: number;
}

export type ConnectionState =
  | "idle"
  | "connecting"
  | "connected"
  | "authenticating"
  | "authenticated"
  | "error"
  | "closed";

interface UseMarketDataOptions {
  symbols: Array<{ symbol: string; exchange: string }>;
  mode?: SubscriptionMode;
  enabled?: boolean;
}

interface UseMarketDataReturn {
  data: Map<string, SymbolData>;
  isConnected: boolean;
  isAuthenticated: boolean;
  state: ConnectionState;
  error: string | null;
}

const symKey = (sym: string, exch: string) => `${exch}:${sym}`;

function normalizeMode(mode: SubscriptionMode): "LTP" | "QUOTE" | "DEPTH" {
  if (mode === "Quote") return "QUOTE";
  if (mode === "Depth") return "DEPTH";
  return "LTP";
}

/**
 * Single-WS hook for live market data — mirrors the protocol exposed by the
 * OpenBull WebSocket proxy (see backend/websocket_proxy + WebSocketTest.tsx).
 *
 * Intended for one consumer at a time per page (option chain). Opens a fresh
 * WS on mount, authenticates, subscribes to `symbols`, and emits the latest
 * tick per symbol via the returned `data` map. Closes on unmount.
 */
export function useMarketData({
  symbols,
  mode = "LTP",
  enabled = true,
}: UseMarketDataOptions): UseMarketDataReturn {
  const [data, setData] = useState<Map<string, SymbolData>>(new Map());
  const [state, setState] = useState<ConnectionState>("idle");
  const [error, setError] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const subscribedRef = useRef<Set<string>>(new Set());
  const pendingSubsRef = useRef<Array<{ symbol: string; exchange: string }>>([]);

  // Stable key for the current symbol set — used to drive resubscription.
  const symbolsKey = useMemo(
    () => symbols.map((s) => symKey(s.symbol, s.exchange)).sort().join(","),
    [symbols]
  );

  const wireMode = normalizeMode(mode);

  // Connect once, and tear down on unmount.
  useEffect(() => {
    if (!enabled) {
      setState("idle");
      return;
    }

    let cancelled = false;

    const connect = async () => {
      setState("connecting");
      setError(null);
      let url: string;
      let apiKey: string;
      try {
        const [cfg, key] = await Promise.all([
          getWebSocketConfig(),
          getWebSocketApiKey(),
        ]);
        url = cfg.websocket_url;
        apiKey = key;
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : "Failed to fetch WS config");
        setState("error");
        return;
      }
      if (cancelled) return;

      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        setState("authenticating");
        ws.send(JSON.stringify({ action: "authenticate", api_key: apiKey }));
      };

      ws.onmessage = (evt) => {
        let msg: Record<string, unknown>;
        try {
          msg = JSON.parse(evt.data as string);
        } catch {
          return;
        }

        if (msg.type === "auth") {
          if (msg.status === "success") {
            setState("authenticated");
            // Flush any queued subscriptions
            const queued = pendingSubsRef.current;
            pendingSubsRef.current = [];
            if (queued.length > 0) {
              ws.send(
                JSON.stringify({ action: "subscribe", symbols: queued, mode: wireMode })
              );
              for (const s of queued) subscribedRef.current.add(symKey(s.symbol, s.exchange));
            }
          } else {
            setError(String(msg.message ?? "Authentication failed"));
            setState("error");
          }
          return;
        }

        if (msg.type === "market_data") {
          const symbol = String(msg.symbol ?? "");
          const exchange = String(msg.exchange ?? "");
          const tick = (msg.data as MarketTickData) ?? {};
          if (!symbol || !exchange) return;
          const key = symKey(symbol, exchange);
          setData((prev) => {
            const next = new Map(prev);
            const existing = next.get(key)?.data ?? {};
            next.set(key, {
              symbol,
              exchange,
              data: { ...existing, ...tick },
              lastUpdate: Date.now(),
            });
            return next;
          });
        }
      };

      ws.onerror = () => {
        if (cancelled) return;
        setError("WebSocket error");
        setState("error");
      };

      ws.onclose = () => {
        if (cancelled) return;
        setState("closed");
        wsRef.current = null;
        subscribedRef.current.clear();
      };
    };

    connect();

    return () => {
      cancelled = true;
      const ws = wsRef.current;
      if (ws) {
        try {
          ws.close();
        } catch {
          /* ignore */
        }
      }
      wsRef.current = null;
      subscribedRef.current.clear();
      pendingSubsRef.current = [];
      setData(new Map());
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled]);

  // Diff subscriptions when the symbol set or mode changes.
  useEffect(() => {
    if (!enabled) return;
    const ws = wsRef.current;
    const desired = new Set(symbols.map((s) => symKey(s.symbol, s.exchange)));
    const current = subscribedRef.current;

    const toAdd = symbols.filter((s) => !current.has(symKey(s.symbol, s.exchange)));
    const toRemove = [...current]
      .filter((k) => !desired.has(k))
      .map((k) => {
        const idx = k.indexOf(":");
        return { exchange: k.slice(0, idx), symbol: k.slice(idx + 1) };
      });

    if (!ws || ws.readyState !== WebSocket.OPEN) {
      // Queue everything for after auth
      pendingSubsRef.current = [...symbols];
      return;
    }
    if (state !== "authenticated") {
      pendingSubsRef.current = [...symbols];
      return;
    }

    if (toAdd.length > 0) {
      ws.send(JSON.stringify({ action: "subscribe", symbols: toAdd, mode: wireMode }));
      for (const s of toAdd) current.add(symKey(s.symbol, s.exchange));
    }
    if (toRemove.length > 0) {
      ws.send(JSON.stringify({ action: "unsubscribe", symbols: toRemove, mode: wireMode }));
      for (const s of toRemove) current.delete(symKey(s.symbol, s.exchange));
      // Drop stale entries from the data map
      setData((prev) => {
        const next = new Map(prev);
        for (const s of toRemove) next.delete(symKey(s.symbol, s.exchange));
        return next;
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbolsKey, wireMode, enabled, state]);

  return {
    data,
    state,
    isConnected: state === "connected" || state === "authenticated" || state === "authenticating",
    isAuthenticated: state === "authenticated",
    error,
  };
}
