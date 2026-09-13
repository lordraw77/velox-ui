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
  /** The newest page of the active branch, oldest first. */
  messages: Message[];
  /** Cursor for the page before `messages`, or null when they reach the start. */
  messages_cursor: string | null;
}

export interface MessagePage {
  items: Message[];
  next_cursor: string | null;
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
  parameter_size: string | null;
  capabilities: Capabilities;
}

/** Optional operations a backend supports; controls are shown only for these. */
export type ProviderFeature = "show" | "running" | "pull" | "create" | "delete" | "copy" | "unload";

export interface ProviderGroup {
  provider_id: string;
  name: string;
  is_local: boolean;
  supported_params: string[];
  features: ProviderFeature[];
  models: ModelEntry[];
}

export type HealthState = "up" | "down" | "degraded" | "unknown";

export interface Health {
  state: HealthState;
  latency_ms: number | null;
  detail: string | null;
}

export type ProviderOrigin = "config" | "autodiscovered" | "ui";

export interface ProviderInfo {
  provider_id: string;
  name: string;
  kind: string | null;
  preset: string | null;
  origin: ProviderOrigin;
  editable: boolean;
  is_local: boolean;
  base_url: string | null;
  credential_hint: string | null;
  features: ProviderFeature[];
  supported_params: string[];
  health: Health | null;
}

export interface Preset {
  key: string;
  kind: string;
  label: string;
  base_url: string;
  auth: "none" | "optional" | "required";
  local: boolean | null;
  docs_url: string;
}

export interface ProbeResult {
  reachable: boolean;
  kind: string | null;
  preset: string | null;
  base_url: string | null;
  models: number | null;
  latency_ms: number | null;
  detail: string | null;
}

export interface DiscoveredBackend {
  provider_id: string;
  preset: string;
  base_url: string;
  added: boolean;
}

export interface InstalledModel {
  name: string;
  family: string | null;
  parameter_size: string | null;
  quantization: string | null;
  size_bytes: number | null;
  context_window: number | null;
  modified_at_ms: number | null;
}

export interface ModelDetails {
  name: string;
  family: string | null;
  parameter_size: string | null;
  quantization: string | null;
  format: string | null;
  context_length: number | null;
  capabilities: string[];
  parameters: Record<string, unknown>;
  template: string | null;
  system: string | null;
  modified_at_ms: number | null;
  size_bytes: number | null;
  has_license: boolean;
}

export interface RunningModel {
  name: string;
  size_bytes: number | null;
  vram_bytes: number | null;
  expires_at_ms: number | null;
  context_length: number | null;
  busy: boolean | null;
}

export type JobState = "running" | "succeeded" | "failed" | "cancelled";

export interface JobSnapshot {
  id: string;
  kind: "pull" | "create";
  provider_id: string;
  model: string;
  state: JobState;
  status: string;
  completed_bytes: number | null;
  total_bytes: number | null;
  bytes_per_second: number | null;
  started_at: number;
  finished_at: number | null;
  error: ApiError | null;
}

export type ParamValue = number | string | boolean | string[];

export interface ModelParams {
  model_ref: string;
  params: Record<string, ParamValue>;
  supported_params: string[];
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
