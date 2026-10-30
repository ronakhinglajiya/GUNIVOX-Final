// Mirror of backend Pydantic schemas. Keep narrow — only fields the UI uses.

export type Role = "admin" | "counsellor";

export interface User {
  id: number;
  email: string;
  name: string;
  role: Role;
  phone?: string | null;
  is_available: boolean;
  is_active: boolean;
  created_at: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: "bearer";
  user: User;
}

export interface Lead {
  id: number;
  name?: string | null;
  phone: string;
  email?: string | null;
  program_interest?: string | null;
  preferred_language?: string | null;
  source?: string | null;
  status: string;
  dnd: boolean;
  attempt_count: number;
  last_attempted_at?: string | null;
  created_at: string;
  updated_at: string;
  notes?: string | null;
}

export interface Turn {
  id: number;
  turn_index: number;
  role: "user" | "assistant" | "system";
  text?: string | null;
  audio_path?: string | null;
  latency_ms?: number | null;
  created_at: string;
}

export type LeadTemperature = "hot" | "warm" | "cold";

export interface Call {
  id: number;
  lead_id: number;
  campaign_id?: number | null;
  assigned_counsellor_id?: number | null;
  vobiz_call_id?: string | null;
  started_at?: string | null;
  ended_at?: string | null;
  duration_s?: number | null;
  language?: string | null;
  outcome?: string | null;
  summary?: string | null;
  recording_path?: string | null;
  ttfa_p50_ms?: number | null;
  // Post-call lead-flow classifier output (backend already writes these
  // since 2026-05-18 — see [[gunivox-delivery-ready]]).
  lead_temperature?: LeadTemperature | null;
  lead_program?: string | null;
  lead_reasons?: string | null;
  caller_name?: string | null;
  created_at: string;
}

export interface CallDetail extends Call {
  turns: Turn[];
}

export interface Campaign {
  id: number;
  name: string;
  status: "draft" | "active" | "paused" | "done";
  created_by_id: number;
  working_hours?: Record<string, unknown> | null;
  retry_policy?: Record<string, unknown> | null;
  script_template?: string | null;
  created_at: string;
  updated_at: string;
}

export interface Callback {
  id: number;
  lead_id: number;
  counsellor_id?: number | null;
  scheduled_at: string;
  status: string;
  notes?: string | null;
  created_at: string;
  updated_at: string;
}

export interface DashboardStats {
  total_leads: number;
  total_calls: number;
  pending_callbacks: number;
  dnd_count: number;
  // Backend started returning this on 2026-05-18 — count of completed calls
  // bucketed by the post-call classifier's output.
  lead_temperature?: { hot: number; warm: number; cold: number };
}

export interface PaginatedList<T> {
  total: number;
  items: T[];
}

export type LeadsList = PaginatedList<Lead>;
export type CallsList = PaginatedList<Call>;
export type CampaignsList = PaginatedList<Campaign>;
export type CallbacksList = PaginatedList<Callback>;
