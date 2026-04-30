export interface BrokerListItem {
  name: string;
  display_name: string;
  supported_exchanges: string[];
  is_configured: boolean;
  is_active: boolean;
  oauth_type: string;
}

export interface BrokerConfigData {
  broker: string;
  api_key: string;
  api_secret: string;
  redirect_url: string;
  client_id?: string;
}

export interface BrokerConfigResponse {
  status: string;
  message: string;
}

export interface BrokerRedirectResponse {
  url: string;
  kind?: "internal" | "external";
}

export interface AngelLoginPayload {
  clientcode: string;
  broker_pin: string;
  totp_code: string;
}
