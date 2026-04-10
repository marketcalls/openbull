export interface BrokerListItem {
  name: string;
  display_name: string;
  supported_exchanges: string[];
  is_configured: boolean;
  is_active: boolean;
}

export interface BrokerConfigData {
  broker: string;
  api_key: string;
  api_secret: string;
  redirect_url: string;
}

export interface BrokerConfigResponse {
  status: string;
  message: string;
}

export interface BrokerRedirectResponse {
  url: string;
}
