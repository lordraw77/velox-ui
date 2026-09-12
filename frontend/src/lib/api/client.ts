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
  ApiError,
  Chat,
  ChatPage,
  ClientConfig,
  ProviderGroup,
  ProviderHealth,
  Session,
} from "./types";

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

  /** Open a streaming POST, returning the raw response for an SSE reader. */
  async stream(path: string, body: unknown, signal?: AbortSignal): Promise<Response> {
    let response = await this.#send(path, {
      method: "POST",
      body: JSON.stringify(body),
      signal,
    });

    if (response.status === 401 && this.#session && (await this.#tryRefresh())) {
      response = await this.#send(path, { method: "POST", body: JSON.stringify(body), signal });
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
    if (init.body !== undefined && !headers["content-type"]) {
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

  providers(): Promise<{ providers: ProviderHealth[] }> {
    return this.request("/api/providers");
  }

  chats(cursor?: string | null): Promise<ChatPage> {
    const query = cursor ? `?cursor=${encodeURIComponent(cursor)}` : "";
    return this.request<ChatPage>(`/api/chats${query}`);
  }

  chat(id: string, branch?: string): Promise<Chat> {
    const query = branch ? `?branch=${encodeURIComponent(branch)}` : "";
    return this.request<Chat>(`/api/chats/${id}${query}`);
  }

  createChat(title: string, modelRef: string | null): Promise<{ id: string; title: string }> {
    return this.request("/api/chats", {
      method: "POST",
      body: JSON.stringify({ title, model_ref: modelRef }),
    });
  }

  deleteChat(id: string): Promise<void> {
    return this.request<void>(`/api/chats/${id}`, { method: "DELETE" });
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
