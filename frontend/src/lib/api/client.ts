/**
 * The typed HTTP client.
 *
 * Two things it does that a bare `fetch` wrapper does not:
 *
 * - It normalises every failure into the server's own {@link ApiError} envelope, so a
 *   component never has to guess whether it got a typed error or an HTML error page
 *   from a reverse proxy.
 * - It refreshes the access token once, transparently, when a request comes back 401
 *   with `expired`. The refresh token lives in an `HttpOnly` cookie the page cannot
 *   read, which is the point: a scripting bug cannot steal the long-lived credential.
 */

import type {
  AdminUser,
  AdminUserPage,
  ApiError,
  AvailableTool,
  Chat,
  ChatPage,
  ClientConfig,
  Collection,
  CollectionVisibility,
  CustomModel,
  CustomModelVisibility,
  DiscoveredBackend,
  FallbackEntry,
  Folder,
  InstalledModel,
  JobSnapshot,
  McpApproval,
  McpServer,
  McpTool,
  McpTransport,
  MessagePage,
  ModelDetails,
  ModelParams,
  ParamValue,
  Preset,
  ProbeResult,
  ProviderGroup,
  ProviderInfo,
  RagDocument,
  RetrievedChunk,
  RunningModel,
  SearchResult,
  Session,
  Tag,
  UploadedFile,
  UserRole,
  UserStatus,
} from "./types";

/**
 * Encode a model name or reference for a path parameter.
 *
 * Model names contain `/` and `:` (`hf.co/org/model:Q4_K_M`). Each segment is encoded,
 * the slashes are kept, and the server's path parameter matches the whole thing.
 */
export function pathOf(name: string): string {
  return name.split("/").map(encodeURIComponent).join("/");
}

/** An error carrying the server's typed envelope. */
export class VeloxApiError extends Error {
  readonly detail: ApiError;
  readonly status: number;

  constructor(status: number, detail: ApiError) {
    super(detail.message);
    this.name = "VeloxApiError";
    this.status = status;
    this.detail = detail;
  }

  /** Whether retrying the identical request could plausibly succeed. */
  get retryable(): boolean {
    return this.detail.retryable;
  }
}

const FALLBACK_ERROR: ApiError = {
  code: "internal",
  message: "The server returned an unexpected response.",
  retryable: false,
};

export class ApiClient {
  #session: Session | null = null;
  #refreshing: Promise<boolean> | null = null;

  get session(): Session | null {
    return this.#session;
  }

  set session(value: Session | null) {
    this.#session = value;
  }

  get authHeaders(): Record<string, string> {
    return this.#session ? { authorization: `Bearer ${this.#session.access_token}` } : {};
  }

  /**
   * Perform a request, refreshing the session once on an expired token.
   *
   * @param path - Path beginning with `/`.
   * @param init - Standard fetch options.
   * @returns The parsed JSON body.
   * @throws {VeloxApiError} On any non-2xx response.
   */
  async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    let response = await this.#send(path, init);

    if (response.status === 401 && this.#session && (await this.#tryRefresh())) {
      response = await this.#send(path, init);
    }

    if (!response.ok) throw new VeloxApiError(response.status, await readError(response));
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }

  /** Open a streaming request, returning the raw response for an SSE reader. */
  async stream(path: string, body: unknown, signal?: AbortSignal): Promise<Response> {
    const init: RequestInit =
      body === undefined
        ? { method: "GET", signal }
        : { method: "POST", body: JSON.stringify(body), signal };
    let response = await this.#send(path, init);

    if (response.status === 401 && this.#session && (await this.#tryRefresh())) {
      response = await this.#send(path, init);
    }

    if (!response.ok || !response.body) {
      throw new VeloxApiError(response.status, await readError(response));
    }
    return response;
  }

  #send(path: string, init: RequestInit): Promise<Response> {
    const headers: Record<string, string> = {
      ...this.authHeaders,
      ...((init.headers as Record<string, string>) ?? {}),
    };
    // FormData sets its own multipart boundary; letting fetch compute the header is
    // required for file uploads (POST /api/files, POST .../documents).
    if (init.body !== undefined && !headers["content-type"] && !(init.body instanceof FormData)) {
      headers["content-type"] = "application/json";
    }
    return fetch(path, { ...init, headers, credentials: "same-origin" });
  }

  /** Refresh the access token, collapsing concurrent attempts into one request. */
  async #tryRefresh(): Promise<boolean> {
    this.#refreshing ??= (async () => {
      try {
        const response = await fetch("/api/auth/refresh", {
          method: "POST",
          credentials: "same-origin",
        });
        if (!response.ok) {
          this.#session = null;
          return false;
        }
        this.#session = (await response.json()) as Session;
        return true;
      } finally {
        this.#refreshing = null;
      }
    })();
    return this.#refreshing;
  }

  // --- endpoints ---------------------------------------------------------------

  config(): Promise<ClientConfig> {
    return this.request<ClientConfig>("/api/config");
  }

  register(email: string, password: string, name: string): Promise<Session> {
    return this.request<Session>("/api/auth/register", {
      method: "POST",
      body: JSON.stringify({ email, password, name }),
    });
  }

  login(email: string, password: string): Promise<Session> {
    return this.request<Session>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
  }

  logout(): Promise<void> {
    return this.request<void>("/api/auth/logout", { method: "POST" });
  }

  models(refresh = false): Promise<{ providers: ProviderGroup[] }> {
    return this.request(`/api/models${refresh ? "?refresh=true" : ""}`);
  }

  providers(): Promise<{ providers: ProviderInfo[] }> {
    return this.request("/api/providers");
  }

  presets(): Promise<{ presets: Preset[] }> {
    return this.request("/api/providers/presets");
  }

  probe(baseUrl: string, apiKey: string): Promise<ProbeResult> {
    return this.#json("POST", "/api/providers/probe", { base_url: baseUrl, api_key: apiKey });
  }

  autodiscover(): Promise<{ found: DiscoveredBackend[] }> {
    return this.#json("POST", "/api/providers/autodiscover");
  }

  createProvider(body: {
    preset: string;
    base_url: string;
    id: string;
    name: string;
    api_key: string;
  }): Promise<ProviderInfo> {
    return this.#json("POST", "/api/providers", body);
  }

  updateProvider(
    id: string,
    patch: { name?: string; base_url?: string; api_key?: string },
  ): Promise<ProviderInfo> {
    return this.#json("PATCH", `/api/providers/${encodeURIComponent(id)}`, patch);
  }

  deleteProvider(id: string): Promise<void> {
    return this.request<void>(`/api/providers/${encodeURIComponent(id)}`, { method: "DELETE" });
  }

  installed(providerId: string): Promise<{ models: InstalledModel[] }> {
    return this.request(`/api/providers/${encodeURIComponent(providerId)}/local/models`);
  }

  showModel(providerId: string, name: string): Promise<ModelDetails> {
    return this.request(
      `/api/providers/${encodeURIComponent(providerId)}/local/models/${pathOf(name)}`,
    );
  }

  running(providerId: string): Promise<{ models: RunningModel[] }> {
    return this.request(`/api/providers/${encodeURIComponent(providerId)}/local/running`);
  }

  unload(providerId: string, name: string): Promise<void> {
    return this.#json("POST", `/api/providers/${encodeURIComponent(providerId)}/local/unload`, {
      name,
    });
  }

  deleteModel(providerId: string, name: string): Promise<void> {
    return this.request<void>(
      `/api/providers/${encodeURIComponent(providerId)}/local/models/${pathOf(name)}`,
      { method: "DELETE" },
    );
  }

  copyModel(providerId: string, source: string, destination: string): Promise<void> {
    return this.#json("POST", `/api/providers/${encodeURIComponent(providerId)}/local/copy`, {
      source,
      destination,
    });
  }

  /** Start (or join) a download on the server. Progress is followed separately. */
  startPull(providerId: string, name: string): Promise<JobSnapshot> {
    return this.#json(
      "POST",
      `/api/providers/${encodeURIComponent(providerId)}/local/pull?detach=true`,
      { name },
    );
  }

  startCreate(providerId: string, name: string, modelfile: string): Promise<JobSnapshot> {
    return this.#json(
      "POST",
      `/api/providers/${encodeURIComponent(providerId)}/local/create?detach=true`,
      { name, modelfile },
    );
  }

  jobs(): Promise<{ jobs: JobSnapshot[] }> {
    return this.request("/api/model-jobs");
  }

  followJob(id: string, signal: AbortSignal): Promise<Response> {
    return this.stream(`/api/model-jobs/${encodeURIComponent(id)}/events`, undefined, signal);
  }

  cancelJob(id: string): Promise<void> {
    return this.request<void>(`/api/model-jobs/${encodeURIComponent(id)}`, { method: "DELETE" });
  }

  modelParams(modelRef: string): Promise<ModelParams> {
    return this.request(`/api/model-params/${pathOf(modelRef)}`);
  }

  saveModelParams(modelRef: string, params: Record<string, ParamValue>): Promise<ModelParams> {
    return this.#json("PUT", `/api/model-params/${pathOf(modelRef)}`, params);
  }

  resetModelParams(modelRef: string): Promise<void> {
    return this.request<void>(`/api/model-params/${pathOf(modelRef)}`, { method: "DELETE" });
  }

  #json<T>(method: string, path: string, body?: unknown): Promise<T> {
    return this.request<T>(path, {
      method,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  }

  chats(
    cursor?: string | null,
    filter?: { archived?: boolean; folderId?: string; tagId?: string },
  ): Promise<ChatPage> {
    const params = new URLSearchParams();
    if (cursor) params.set("cursor", cursor);
    if (filter?.archived) params.set("archived", "true");
    if (filter?.folderId) params.set("folder", filter.folderId);
    if (filter?.tagId) params.set("tag", filter.tagId);
    const query = params.toString();
    return this.request<ChatPage>(`/api/chats${query ? `?${query}` : ""}`);
  }

  chat(id: string, branch?: string): Promise<Chat> {
    const query = branch ? `?branch=${encodeURIComponent(branch)}` : "";
    return this.request<Chat>(`/api/chats/${id}${query}`);
  }

  /** The page of messages before `cursor`, oldest first. */
  messages(id: string, cursor: string): Promise<MessagePage> {
    return this.request<MessagePage>(
      `/api/chats/${id}/messages?cursor=${encodeURIComponent(cursor)}`,
    );
  }

  createChat(
    title: string,
    modelRef: string | null,
    customModelId?: string | null,
  ): Promise<{ id: string; title: string; custom_model_id: string | null }> {
    return this.request("/api/chats", {
      method: "POST",
      body: JSON.stringify({ title, model_ref: modelRef, custom_model_id: customModelId ?? null }),
    });
  }

  /** Rename, pin, archive or move a conversation. Only the given fields are patched. */
  updateChat(
    id: string,
    patch: { title?: string; pinned?: boolean; archived?: boolean; folder_id?: string | null },
  ): Promise<Chat> {
    return this.#json("PATCH", `/api/chats/${id}`, patch);
  }

  deleteChat(id: string): Promise<void> {
    return this.request<void>(`/api/chats/${id}`, { method: "DELETE" });
  }

  // --- folders -------------------------------------------------------------------

  folders(): Promise<Folder[]> {
    return this.request<Folder[]>("/api/folders");
  }

  createFolder(name: string, parentId?: string | null): Promise<Folder> {
    return this.#json("POST", "/api/folders", { name, parent_id: parentId ?? null });
  }

  renameFolder(id: string, name: string): Promise<Folder> {
    return this.#json("PATCH", `/api/folders/${id}`, { name });
  }

  moveFolder(id: string, parentId: string | null, sortOrder?: number): Promise<Folder> {
    return this.#json("PUT", `/api/folders/${id}/move`, {
      parent_id: parentId,
      sort_order: sortOrder ?? null,
    });
  }

  deleteFolder(id: string): Promise<void> {
    return this.request<void>(`/api/folders/${id}`, { method: "DELETE" });
  }

  // --- tags ------------------------------------------------------------------

  tags(): Promise<Tag[]> {
    return this.request<Tag[]>("/api/tags");
  }

  createTag(name: string, color?: string | null): Promise<Tag> {
    return this.#json("POST", "/api/tags", { name, color: color ?? null });
  }

  deleteTag(id: string): Promise<void> {
    return this.request<void>(`/api/tags/${id}`, { method: "DELETE" });
  }

  attachTag(tagId: string, chatId: string): Promise<void> {
    return this.request<void>(`/api/tags/${tagId}/chats/${chatId}`, { method: "PUT" });
  }

  detachTag(tagId: string, chatId: string): Promise<void> {
    return this.request<void>(`/api/tags/${tagId}/chats/${chatId}`, { method: "DELETE" });
  }

  tagsForChat(chatId: string): Promise<Tag[]> {
    return this.request<Tag[]>(`/api/tags/for-chat/${chatId}`);
  }

  // --- search ----------------------------------------------------------------

  search(query: string, cursor?: string | null): Promise<SearchResult> {
    const params = new URLSearchParams({ q: query });
    if (cursor) params.set("cursor", cursor);
    return this.request<SearchResult>(`/api/search?${params.toString()}`);
  }

  // --- custom models -----------------------------------------------------------

  customModels(): Promise<CustomModel[]> {
    return this.request<CustomModel[]>("/api/custom-models");
  }

  createCustomModel(body: {
    slug: string;
    name: string;
    description?: string | null;
    system_prompt?: string | null;
    params?: Record<string, ParamValue> | null;
    knowledge_ids?: string[];
    tools?: string[];
    fallback_chain?: FallbackEntry[];
    visibility?: CustomModelVisibility;
  }): Promise<CustomModel> {
    return this.#json("POST", "/api/custom-models", body);
  }

  updateCustomModel(
    id: string,
    patch: {
      name?: string;
      description?: string | null;
      system_prompt?: string | null;
      params?: Record<string, ParamValue> | null;
      knowledge_ids?: string[];
      tools?: string[];
      fallback_chain?: FallbackEntry[];
      visibility?: CustomModelVisibility;
    },
  ): Promise<CustomModel> {
    return this.#json("PATCH", `/api/custom-models/${id}`, patch);
  }

  deleteCustomModel(id: string): Promise<void> {
    return this.request<void>(`/api/custom-models/${id}`, { method: "DELETE" });
  }

  // --- files and RAG -------------------------------------------------------------

  async uploadFile(file: File): Promise<UploadedFile> {
    const form = new FormData();
    form.append("upload", file);
    return this.request<UploadedFile>("/api/files", { method: "POST", body: form });
  }

  deleteFile(fileId: string): Promise<void> {
    return this.request<void>(`/api/files/${fileId}`, { method: "DELETE" });
  }

  collections(): Promise<Collection[]> {
    return this.request<Collection[]>("/api/collections");
  }

  createCollection(body: {
    name: string;
    description?: string | null;
    embedder_ref?: string;
    dim?: number;
    max_tokens?: number;
    overlap_tokens?: number;
    visibility?: CollectionVisibility;
  }): Promise<Collection> {
    return this.#json("POST", "/api/collections", body);
  }

  updateCollection(
    id: string,
    patch: { name?: string; description?: string | null; visibility?: CollectionVisibility },
  ): Promise<Collection> {
    return this.#json("PATCH", `/api/collections/${id}`, patch);
  }

  deleteCollection(id: string): Promise<void> {
    return this.request<void>(`/api/collections/${id}`, { method: "DELETE" });
  }

  documents(collectionId: string): Promise<RagDocument[]> {
    return this.request<RagDocument[]>(`/api/collections/${collectionId}/documents`);
  }

  async ingestDocument(
    collectionId: string,
    fileId: string,
    title?: string,
  ): Promise<{ document: RagDocument; job_id: string }> {
    const form = new FormData();
    form.append("file_id", fileId);
    if (title) form.append("title", title);
    return this.request<{ document: RagDocument; job_id: string }>(
      `/api/collections/${collectionId}/documents`,
      { method: "POST", body: form },
    );
  }

  deleteDocument(documentId: string): Promise<void> {
    return this.request<void>(`/api/documents/${documentId}`, { method: "DELETE" });
  }

  queryCollection(collectionId: string, query: string, k = 5): Promise<{ items: RetrievedChunk[] }> {
    return this.#json("POST", `/api/collections/${collectionId}/query`, { query, k });
  }

  // --- admin -------------------------------------------------------------------

  adminUsers(cursor?: string | null): Promise<AdminUserPage> {
    const query = cursor ? `?cursor=${encodeURIComponent(cursor)}` : "";
    return this.request<AdminUserPage>(`/api/admin/users${query}`);
  }

  adminCreateUser(email: string, password: string, name: string, role: UserRole): Promise<AdminUser> {
    return this.#json("POST", "/api/admin/users", { email, password, name, role });
  }

  adminSetRole(id: string, role: UserRole): Promise<AdminUser> {
    return this.#json("PATCH", `/api/admin/users/${id}/role`, { role });
  }

  adminSetStatus(id: string, status: UserStatus): Promise<AdminUser> {
    return this.#json("PATCH", `/api/admin/users/${id}/status`, { status });
  }

  adminDeleteUser(id: string): Promise<void> {
    return this.request<void>(`/api/admin/users/${id}`, { method: "DELETE" });
  }

  // --- MCP servers and tools -----------------------------------------------------

  mcpServers(): Promise<McpServer[]> {
    return this.request<McpServer[]>("/api/mcp/servers");
  }

  createMcpServer(body: {
    name: string;
    transport: McpTransport;
    config: Record<string, unknown>;
    auth_token?: string;
    approval?: McpApproval;
    enabled?: boolean;
  }): Promise<McpServer> {
    return this.#json("POST", "/api/mcp/servers", body);
  }

  updateMcpServer(
    id: string,
    patch: {
      name?: string;
      config?: Record<string, unknown>;
      auth_token?: string;
      enabled?: boolean;
      approval?: McpApproval;
    },
  ): Promise<McpServer> {
    return this.#json("PATCH", `/api/mcp/servers/${id}`, patch);
  }

  deleteMcpServer(id: string): Promise<void> {
    return this.request<void>(`/api/mcp/servers/${id}`, { method: "DELETE" });
  }

  connectMcpServer(id: string): Promise<McpTool[]> {
    return this.#json("POST", `/api/mcp/servers/${id}/connect`);
  }

  mcpServerTools(id: string): Promise<McpTool[]> {
    return this.request<McpTool[]>(`/api/mcp/servers/${id}/tools`);
  }

  availableTools(): Promise<AvailableTool[]> {
    return this.request<AvailableTool[]>("/api/tools");
  }

  approveToolCall(callId: string, approved: boolean, remember = false): Promise<{ ok: boolean }> {
    return this.#json("POST", "/api/tools/approve", { call_id: callId, approved, remember });
  }
}

async function readError(response: Response): Promise<ApiError> {
  try {
    const body = (await response.json()) as { error?: ApiError };
    return body.error ?? FALLBACK_ERROR;
  } catch {
    // A proxy timing out, or an HTML error page. Either way there is no envelope.
    return { ...FALLBACK_ERROR, message: `The server returned ${response.status}.` };
  }
}

export const api = new ApiClient();
