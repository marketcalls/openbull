import api from "@/config/api";
import type {
  AngelLoginPayload,
  BrokerListItem,
  BrokerConfigData,
  BrokerConfigResponse,
  BrokerRedirectResponse,
} from "@/types/broker";

export async function listBrokers(): Promise<BrokerListItem[]> {
  const response = await api.get<BrokerListItem[]>("/web/broker/list");
  return response.data;
}

export async function getBrokerCredentials(name: string): Promise<BrokerConfigData> {
  const response = await api.get<BrokerConfigData>(`/web/broker/credentials/${name}`);
  return response.data;
}

export async function saveBrokerCredentials(data: BrokerConfigData): Promise<BrokerConfigResponse> {
  const response = await api.put<BrokerConfigResponse>("/web/broker/credentials", {
    broker_name: data.broker,
    api_key: data.api_key,
    api_secret: data.api_secret,
    redirect_url: data.redirect_url,
    client_id: data.client_id ?? null,
  });
  return response.data;
}

export async function getBrokerRedirectUrl(broker: string): Promise<BrokerRedirectResponse> {
  const response = await api.get<BrokerRedirectResponse>("/auth/broker-redirect", {
    params: { broker },
  });
  return response.data;
}

export async function angelLogin(payload: AngelLoginPayload): Promise<{ status: string; broker: string }> {
  const response = await api.post<{ status: string; broker: string }>("/angel/login", payload);
  return response.data;
}
