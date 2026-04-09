export interface UserInfo {
  username: string;
  email: string;
  is_admin: boolean;
  broker: string | null;
  broker_authenticated: boolean;
}

export interface SetupRequest {
  username: string;
  email: string;
  password: string;
}

export interface LoginRequest {
  username: string;
  password: string;
}

export interface AuthResponse {
  status: string;
  message: string;
}

export interface SetupCheckResponse {
  needs_setup: boolean;
}
