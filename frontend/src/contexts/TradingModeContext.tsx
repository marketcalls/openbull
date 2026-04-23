import { createContext, useCallback, useContext, useEffect } from "react";
import type { ReactNode } from "react";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  getTradingMode,
  setTradingMode as setTradingModeApi,
  type TradingMode,
} from "@/api/trading_mode";
import { useAuth } from "@/contexts/AuthContext";

interface TradingModeContextType {
  mode: TradingMode;
  isSandbox: boolean;
  isLoading: boolean;
  setMode: (mode: TradingMode) => Promise<TradingMode>;
  refresh: () => Promise<void>;
}

const TradingModeContext = createContext<TradingModeContextType | null>(null);

/**
 * Provider for the global Live / Sandbox toggle.
 *
 * The mode is paint-critical: pages like OrderBook, Positions, Funds etc.
 * will eventually read from sandbox tables vs the broker API based on this
 * value. Switching flushes every cached query so stale live data doesn't
 * linger on sandbox pages (and vice-versa).
 *
 * We also sync the current mode onto ``<html data-trading-mode="...">`` so
 * CSS can tint the whole chrome amber while in sandbox.
 */
export function TradingModeProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const queryClient = useQueryClient();

  // Only fetch once authenticated; the endpoint requires a session cookie.
  const { data, isLoading } = useQuery({
    queryKey: ["trading-mode"],
    queryFn: getTradingMode,
    enabled: !!user,
    retry: false,
    staleTime: 30 * 1000,
  });

  const mode: TradingMode = data ?? "live";

  // Keep the html data-attribute in sync so the theme tint stays on-screen
  // through SPA route changes without a reload.
  useEffect(() => {
    const root = document.documentElement;
    root.dataset.tradingMode = mode;
    return () => {
      // Reset to "live" when the provider unmounts (e.g. on logout) so the
      // login screen never inherits sandbox tinting.
      root.dataset.tradingMode = "live";
    };
  }, [mode]);

  const mutation = useMutation({
    mutationFn: (next: TradingMode) => setTradingModeApi(next),
    onSuccess: (next) => {
      queryClient.setQueryData(["trading-mode"], next);
      // Invalidate mode-sensitive caches. Broad strokes are fine for Phase 1 —
      // once the per-mode fetchers exist we'll be more surgical.
      queryClient.invalidateQueries({
        predicate: (q) => {
          const k = q.queryKey[0];
          return (
            k === "orderbook" ||
            k === "tradebook" ||
            k === "positions" ||
            k === "holdings" ||
            k === "funds" ||
            k === "dashboard"
          );
        },
      });
    },
  });

  const setMode = useCallback(
    async (next: TradingMode) => {
      return await mutation.mutateAsync(next);
    },
    [mutation]
  );

  const refresh = useCallback(async () => {
    await queryClient.invalidateQueries({ queryKey: ["trading-mode"] });
  }, [queryClient]);

  return (
    <TradingModeContext.Provider
      value={{
        mode,
        isSandbox: mode === "sandbox",
        isLoading,
        setMode,
        refresh,
      }}
    >
      {children}
    </TradingModeContext.Provider>
  );
}

export function useTradingMode(): TradingModeContextType {
  const ctx = useContext(TradingModeContext);
  if (!ctx) {
    throw new Error("useTradingMode must be used within TradingModeProvider");
  }
  return ctx;
}
