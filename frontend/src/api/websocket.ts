import api from "@/config/api";

export interface WebSocketConfig {
  status: string;
  websocket_url: string;
  original_url: string;
  is_secure: boolean;
}

export interface WebSocketHealth {
  status: string;
  healthy: boolean;
  connected: boolean;
  authenticated: boolean;
  last_data_timestamp: number;
  last_data_age_seconds: number;
  data_flow_healthy: boolean;
  cache_size: number;
  total_subscribers: number;
  critical_subscribers: number;
  total_updates_processed: number;
  validation_errors: number;
  stale_data_events: number;
  reconnect_count: number;
  uptime_seconds: number;
  message: string;
  trade_management_safe: boolean;
  trade_management_reason: string;
}

export async function getWebSocketConfig(): Promise<WebSocketConfig> {
  const response = await api.get<WebSocketConfig>("/api/websocket/config");
  return response.data;
}

export async function getWebSocketApiKey(): Promise<string> {
  const response = await api.get<{ status: string; api_key: string }>(
    "/api/websocket/apikey"
  );
  return response.data.api_key;
}

export async function getWebSocketHealth(): Promise<WebSocketHealth> {
  const response = await api.get<WebSocketHealth>("/api/websocket/health");
  return response.data;
}
