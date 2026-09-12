/**
 * Wire types, mirroring the server's own shapes.
 *
 * These are hand-written rather than generated. The server's hot endpoints
 * deliberately declare no response model (ADR-0001), so there is no schema to generate
 * from; keeping these in one file makes the coupling explicit instead of scattering
 * `any` through the components.
 */

export type Role = "system" | "user" | "assistant" | "tool";

export type MessageStatus = "complete" | "streaming" | "stopped" | "error";

/** Timing and token metrics, as the backend reported them. */
export interface Timings {
  ttft_ms: number | null;
  duration_ms: number | null;
  tok_per_s: number | null;
  prompt_eval_ms: number | null;
  eval_ms: number | null;
}

export interface Message {
  id: string;
  parent_id: string | null;
  role: Role;
  content: string;
  reasoning: string | null;
  status: MessageStatus;
  model_ref: string | null;
  depth: number;
  tokens_in: number | null;
  tokens_out: number | null;
  cost_micros: number | null;
  timings: Timings | null;
  error: ApiError | null;
  created_at: number;
  sibling_index: number;
  sibling_count: number;
}

export interface ChatSummary {
  id: string;
  title: string;
  pinned: boolean;
  archived: boolean;
  folder_id: string | null;
  model_ref: string | null;
  message_count: number;
  updated_at: number;
}

export interface Chat extends ChatSummary {
  active_leaf_id: string | null;
  created_at: number;
  messages: Message[];
}

export interface ChatPage {
  items: ChatSummary[];
  next_cursor: string | null;
}

export interface Capabilities {
  streaming: boolean;
  tools: "native" | "emulated" | "none";
  vision: boolean;
  json_mode: boolean;
  grammar: boolean;
  reasoning: boolean;
  embeddings: boolean;
  context_window: number | null;
  max_output_tokens: number | null;
  price_in_ppm: number | null;
  price_out_ppm: number | null;
  quantization: string | null;
  size_bytes: number | null;
}

export interface ModelEntry {
  key: string;
  model_ref: string;
  display_name: string;
  family: string | null;
  loaded: boolean | null;
  capabilities: Capabilities;
}

export interface ProviderGroup {
  provider_id: string;
  is_local: boolean;
  models: ModelEntry[];
}

export type HealthState = "up" | "down" | "degraded" | "unknown";

export interface ProviderHealth {
  provider_id: string;
  is_local: boolean;
  base_url: string | null;
  health: { state: HealthState; latency_ms: number | null; detail: string | null } | null;
}

/** The one error shape every endpoint returns (docs/design/03-http-api.md). */
export interface ApiError {
  code: string;
  message: string;
  retryable: boolean;
  request_id?: string;
  provider?: string;
  model?: string;
  retry_after_s?: number;
  fields?: { field: string; message: string; type: string }[];
}

export interface Session {
  access_token: string;
  expires_at: number;
  user_id: string;
  role: string;
}

export interface ClientConfig {
  version: string;
  auth: { enabled: boolean; open_registration: boolean; setup_required: boolean };
  features: { metrics: boolean };
}

/** Phases a backend reports before or between tokens. */
export type StreamPhase = "loading_model" | "prompt_eval" | "generating" | "tool_wait";

export interface UsageEvent {
  tokens_in: number;
  tokens_out: number;
  cost_micros: number;
  ttft_ms: number | null;
  tok_per_s: number | null;
  prompt_eval_ms: number | null;
  eval_ms: number | null;
  duration_ms: number;
}

export interface StartEvent {
  message_id: string;
  user_message_id: string;
  parent_id: string | null;
  model_ref: string;
}
