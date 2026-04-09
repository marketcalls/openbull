import api from "@/config/api";

export interface ApiKeyResponse {
  api_key: string;
}

export async function getApiKey(): Promise<ApiKeyResponse> {
  const response = await api.get<ApiKeyResponse>("/web/apikey");
  return response.data;
}

export async function generateApiKey(): Promise<ApiKeyResponse> {
  const response = await api.post<ApiKeyResponse>("/web/apikey");
  return response.data;
}
