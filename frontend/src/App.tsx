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
