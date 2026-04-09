import api from "@/config/api";
import type { BrokerListItem, BrokerConfigData, BrokerConfigResponse, BrokerRedirectResponse } from "@/types/broker";

export async function listBrokers(): Promise<BrokerListItem[]> {
  const response = await api.get<BrokerListItem[]>("/web/broker/list");
  return response.data;
}

export async function getBrokerCredentials(name: string): Promise<BrokerConfigData> {
  const response = await api.get<BrokerConfigData>(`/web/broker/credentials/${name}`);
  return response.data;
}

export async function saveBrokerCredentials(data: BrokerConfigData): Promise<BrokerConfigResponse> {
  const response = await api.put<BrokerConfigResponse>("/web/broker/credentials", data);
  return response.data;
}

export async function getBrokerRedirectUrl(broker: string): Promise<BrokerRedirectResponse> {
  const response = await api.get<BrokerRedirectResponse>("/auth/broker-redirect", {
    params: { broker },
  });
  return response.data;
}
