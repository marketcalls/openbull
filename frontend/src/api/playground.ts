/**
 * Typed fetch wrappers for the playground backend helpers.
 *
 * All endpoints sit under ``/web/playground`` and are session-cookie authed.
 */

export interface PlaygroundEndpoint {
  name: string;
  method: "GET" | "POST" | "WS" | "PUT" | "DELETE" | "PATCH";
  path: string;
  body?: Record<string, unknown>;
  params?: Record<string, unknown>;
  description?: string;
}

export interface PlaygroundEndpointsByCategory {
  account: PlaygroundEndpoint[];
  orders: PlaygroundEndpoint[];
  data: PlaygroundEndpoint[];
  analytics: PlaygroundEndpoint[];
  utilities: PlaygroundEndpoint[];
  websocket: PlaygroundEndpoint[];
}

export async function getPlaygroundApiKey(): Promise<string> {
  const res = await fetch("/web/playground/api-key", {
    credentials: "include",
  });
  if (!res.ok) return "";
  const data = (await res.json()) as { api_key?: string };
  return data.api_key ?? "";
}

export async function getPlaygroundEndpoints(): Promise<PlaygroundEndpointsByCategory> {
  const res = await fetch("/web/playground/endpoints", {
    credentials: "include",
  });
  if (!res.ok) {
    throw new Error(`Failed to load playground endpoints: ${res.status}`);
  }
  return (await res.json()) as PlaygroundEndpointsByCategory;
}

export async function getPlaygroundHost(): Promise<string> {
  try {
    const res = await fetch("/web/playground/host", {
      credentials: "include",
    });
    if (!res.ok) return window.location.origin;
    const data = (await res.json()) as { host_server?: string };
    return data.host_server || window.location.origin;
  } catch {
    return window.location.origin;
  }
}
