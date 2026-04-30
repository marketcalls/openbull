import { lazy, Suspense } from "react";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "@/contexts/AuthContext";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { TradingModeProvider } from "@/contexts/TradingModeContext";
import { ProtectedRoute } from "@/components/auth/ProtectedRoute";
import { AppLayout } from "@/components/layout/AppLayout";
import Home from "@/pages/Home";
import Login from "@/pages/Login";
import Setup from "@/pages/Setup";
import Dashboard from "@/pages/Dashboard";
import BrokerConfig from "@/pages/BrokerConfig";
import BrokerSelect from "@/pages/BrokerSelect";
import BrokerAngelLogin from "@/pages/BrokerAngelLogin";
import ApiKey from "@/pages/ApiKey";
import OrderBook from "@/pages/OrderBook";
import TradeBook from "@/pages/TradeBook";
import Positions from "@/pages/Positions";
import Holdings from "@/pages/Holdings";
import Search from "@/pages/Search";
import WebSocketTest from "@/pages/WebSocketTest";
import Logs from "@/pages/Logs";
import Sandbox from "@/pages/Sandbox";
import SandboxMyPnL from "@/pages/SandboxMyPnL";
import Tools from "@/pages/Tools";
import NotFound from "@/pages/NotFound";
import { Toaster } from "@/components/ui/sonner";

// Code-split heavy tool pages — Plotly weighs ~600 KB gz, only fetch it when
// the user navigates to a chart tool.
const OptionChain = lazy(() => import("@/pages/tools/OptionChain"));
const OITracker = lazy(() => import("@/pages/tools/OITracker"));
const MaxPain = lazy(() => import("@/pages/tools/MaxPain"));
const OptionGreeks = lazy(() => import("@/pages/tools/OptionGreeks"));
const IVSmile = lazy(() => import("@/pages/tools/IVSmile"));
const VolSurface = lazy(() => import("@/pages/tools/VolSurface"));
const StraddleChart = lazy(() => import("@/pages/tools/StraddleChart"));
const GEXDashboard = lazy(() => import("@/pages/tools/GEXDashboard"));
const StrategyBuilder = lazy(() => import("@/pages/tools/StrategyBuilder"));
const StrategyPortfolio = lazy(() => import("@/pages/tools/StrategyPortfolio"));

function ToolFallback() {
  return (
    <div className="flex h-[500px] items-center justify-center">
      <div className="h-8 w-8 animate-spin rounded-full border-4 border-muted border-t-primary" />
    </div>
  );
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <BrowserRouter>
          <AuthProvider>
            <TradingModeProvider>
              <Routes>
              {/* Public routes */}
              <Route path="/" element={<Home />} />
              <Route path="/login" element={<Login />} />
              <Route path="/setup" element={<Setup />} />

              {/* Broker select (protected, no layout) */}
              <Route
                path="/broker/select"
                element={
                  <ProtectedRoute>
                    <BrokerSelect />
                  </ProtectedRoute>
                }
              />

              {/* Angel One credentials/TOTP login (no OAuth) */}
              <Route
                path="/broker/angel/totp"
                element={
                  <ProtectedRoute>
                    <BrokerAngelLogin />
                  </ProtectedRoute>
                }
              />

              {/* Protected routes with layout */}
              <Route
                element={
                  <ProtectedRoute>
                    <AppLayout />
                  </ProtectedRoute>
                }
              >
                <Route
                  path="/dashboard"
                  element={
                    <ProtectedRoute requiresBroker>
                      <Dashboard />
                    </ProtectedRoute>
                  }
                />
                <Route path="/broker/config" element={<BrokerConfig />} />
                <Route path="/apikey" element={<ApiKey />} />
                <Route
                  path="/search"
                  element={
                    <ProtectedRoute requiresBroker>
                      <Search />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path="/orderbook"
                  element={
                    <ProtectedRoute requiresBroker>
                      <OrderBook />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path="/tradebook"
                  element={
                    <ProtectedRoute requiresBroker>
                      <TradeBook />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path="/positions"
                  element={
                    <ProtectedRoute requiresBroker>
                      <Positions />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path="/holdings"
                  element={
                    <ProtectedRoute requiresBroker>
                      <Holdings />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path="/websocket/test"
                  element={
                    <ProtectedRoute requiresBroker>
                      <WebSocketTest />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path="/logs"
                  element={
                    <ProtectedRoute>
                      <Logs />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path="/sandbox"
                  element={
                    <ProtectedRoute>
                      <Sandbox />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path="/sandbox/mypnl"
                  element={
                    <ProtectedRoute>
                      <SandboxMyPnL />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path="/tools"
                  element={
                    <ProtectedRoute requiresBroker>
                      <Tools />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path="/tools/optionchain"
                  element={
                    <ProtectedRoute requiresBroker>
                      <Suspense fallback={<ToolFallback />}>
                        <OptionChain />
                      </Suspense>
                    </ProtectedRoute>
                  }
                />
                <Route
                  path="/tools/oitracker"
                  element={
                    <ProtectedRoute requiresBroker>
                      <Suspense fallback={<ToolFallback />}>
                        <OITracker />
                      </Suspense>
                    </ProtectedRoute>
                  }
                />
                <Route
                  path="/tools/maxpain"
                  element={
                    <ProtectedRoute requiresBroker>
                      <Suspense fallback={<ToolFallback />}>
                        <MaxPain />
                      </Suspense>
                    </ProtectedRoute>
                  }
                />
                <Route
                  path="/tools/greeks"
                  element={
                    <ProtectedRoute requiresBroker>
                      <Suspense fallback={<ToolFallback />}>
                        <OptionGreeks />
                      </Suspense>
                    </ProtectedRoute>
                  }
                />
                <Route
                  path="/tools/ivsmile"
                  element={
                    <ProtectedRoute requiresBroker>
                      <Suspense fallback={<ToolFallback />}>
                        <IVSmile />
                      </Suspense>
                    </ProtectedRoute>
                  }
                />
                <Route
                  path="/tools/volsurface"
                  element={
                    <ProtectedRoute requiresBroker>
                      <Suspense fallback={<ToolFallback />}>
                        <VolSurface />
                      </Suspense>
                    </ProtectedRoute>
                  }
                />
                <Route
                  path="/tools/straddle"
                  element={
                    <ProtectedRoute requiresBroker>
                      <Suspense fallback={<ToolFallback />}>
                        <StraddleChart />
                      </Suspense>
                    </ProtectedRoute>
                  }
                />
                <Route
                  path="/tools/gex"
                  element={
                    <ProtectedRoute requiresBroker>
                      <Suspense fallback={<ToolFallback />}>
                        <GEXDashboard />
                      </Suspense>
                    </ProtectedRoute>
                  }
                />
                <Route
                  path="/tools/strategybuilder"
                  element={
                    <ProtectedRoute requiresBroker>
                      <Suspense fallback={<ToolFallback />}>
                        <StrategyBuilder />
                      </Suspense>
                    </ProtectedRoute>
                  }
                />
                <Route
                  path="/tools/strategyportfolio"
                  element={
                    <ProtectedRoute requiresBroker>
                      <Suspense fallback={<ToolFallback />}>
                        <StrategyPortfolio />
                      </Suspense>
                    </ProtectedRoute>
                  }
                />
              </Route>

              {/* Catch-all */}
              <Route path="*" element={<NotFound />} />
            </Routes>
            <Toaster />
            </TradingModeProvider>
          </AuthProvider>
        </BrowserRouter>
      </ThemeProvider>
    </QueryClientProvider>
  );
}

export default App;
