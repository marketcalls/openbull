import api from "@/config/api";
import type { SetupRequest, LoginRequest, AuthResponse, SetupCheckResponse, UserInfo } from "@/types/auth";

export async function checkSetup(): Promise<SetupCheckResponse> {
  const response = await api.get<SetupCheckResponse>("/auth/check-setup");
  return response.data;
}

export async function setup(data: SetupRequest): Promise<AuthResponse> {
  const response = await api.post<AuthResponse>("/auth/setup", data);
  return response.data;
}

export async function login(data: LoginRequest): Promise<AuthResponse> {
  const response = await api.post<AuthResponse>("/auth/login", data);
  return response.data;
}

export async function logout(): Promise<AuthResponse> {
  const response = await api.post<AuthResponse>("/auth/logout");
  return response.data;
}

export async function getMe(): Promise<UserInfo> {
  const response = await api.get<UserInfo>("/auth/me");
  return response.data;
}
