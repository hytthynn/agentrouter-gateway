/** Типы API AgentRouter Gateway (соответствуют server/src/gateway_app) */

export type AccountStatus = "active" | "needs_relogin" | "error" | "disabled";

export interface Account {
  id: number;
  name: string;
  base_url: string;
  github_login: string;
  provider_user_id: number | null;
  status: AccountStatus;
  group_name: string | null;
  balance_quota: number | null;
  used_quota: number | null;
  balance_usd: number | null;
  last_balance_at: string | null;
  last_checkin_at: string | null;
  last_checkin_reward_usd: number | null;
  error_message: string | null;
  created_at: string;
}

export interface ProviderKey {
  id: number;
  account_id: number;
  account_name: string;
  name: string;
  key_masked: string;
  provider_token_id: number | null;
  unlimited_quota: boolean;
  remain_quota: number | null;
  expired_time: number;
  model_limits_enabled: boolean;
  model_limits: string[] | null;
  status: "active" | "disabled";
  created_via: "auto" | "manual";
  used_quota: number | null;
  created_at: string;
  last_used_at: string | null;
}

export interface CatalogModel {
  model_id: string;
  enabled: boolean;
  accounts_available: number;
  accounts_total: number;
  no_active_accounts: boolean;
  added_from: string | null;
  created_at: string;
}

export interface GatewayKey {
  id: number;
  name: string;
  key_masked: string;
  token_limit: number | null;
  tokens_used: number;
  request_count: number;
  allowed_models: string[];
  expires_at: string | null;
  status: "active" | "disabled";
  created_at: string;
  last_used_at: string | null;
  full_key?: string; // только при создании / reveal
}

export interface RequestLog {
  id: number;
  ts: string;
  gateway_key_id: number | null;
  account_id: number | null;
  model: string;
  stream: boolean;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  estimated: boolean;
  cost_quota: number | null;
  cost_usd: number | null;
  duration_ms: number;
  first_token_ms: number | null;
  status: "success" | "error" | "client_abort";
  http_status: number | null;
  error_code: string | null;
  error_message: string | null;
  attempts: { account_id: number; status: string; code: string }[] | null;
  client_ua: string | null;
}

export interface SystemLog {
  id: number;
  ts: string;
  level: "info" | "warn" | "error";
  category: string;
  account_id: number | null;
  message: string;
  details: Record<string, unknown> | null;
}

export interface Dashboard {
  total_balance_usd: number;
  total_balance_quota: number;
  accounts_active: number;
  accounts_total: number;
  requests_24h: number;
  tokens_24h: number;
  errors_24h: number;
  next_checkin: string;
  sparkline: { hour: string; requests: number; tokens: number }[];
  accounts: Account[];
  recent_logs: SystemLog[];
}

export interface Health {
  version: string;
  db: string;
  encryption: string;
  upstream_reachable: boolean;
  provider_version: string | null;
  upstream_error?: string;
  master_key_source: string;
}

export interface TaskStep {
  key: string;
  title: string;
  status: "pending" | "running" | "done" | "error";
}

export interface TaskSnapshot {
  task_id: string;
  kind: string;
  account_id: number | null;
  state: "running" | "waiting_user_code" | "success" | "error";
  steps: TaskStep[];
  logs: string[];
  error: string | null;
  result: Record<string, unknown> | null;
}

export interface Profile {
  name: string;
  user_agent: string;
  extra_headers: Record<string, string>;
  is_default: boolean;
}
