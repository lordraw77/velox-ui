/**
 * Application state.
 *
 * Svelte 5 runes, in plain `.svelte.ts` modules rather than stores: the reactivity is
 * fine-grained enough that appending to a streaming message updates one text node
 * instead of re-running the conversation's template (ADR-0012).
 *
 * Anything persisted lives in `localStorage` and every access is guarded. A private
 * window, cleared site data, or a browser configured to block storage all make these
 * calls throw, and none of them is a reason to fail to render a chat.
 */

import { api, VeloxApiError } from "$lib/api/client";
import type {
  ApiError,
  ChatSummary,
  ClientConfig,
  Folder,
  ModelEntry,
  PluginInfo,
  ProviderGroup,
  Session,
  Tag,
} from "$lib/api/types";
import { detectLocale, translate, type Locale } from "$lib/i18n";

export type Theme = "light" | "dark" | "auto";

const STORAGE_KEYS = {
  session: "velox.session",
  model: "velox.model",
  theme: "velox.theme",
  locale: "velox.locale",
} as const;

function readStored(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStored(key: string, value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    // Storage is unavailable. The interface still works; it just forgets.
  }
}

class AppStore {
  config = $state<ClientConfig | null>(null);
  session = $state<Session | null>(null);
  locale = $state<Locale>("en");
  theme = $state<Theme>("auto");

  providers = $state<ProviderGroup[]>([]);
  modelRef = $state<string | null>(null);
  modelsLoading = $state(false);

  chats = $state<ChatSummary[]>([]);
  chatsCursor = $state<string | null>(null);
  chatsLoading = $state(false);
  chatsArchived = $state(false);
  activeFolderId = $state<string | null>(null);
  activeTagId = $state<string | null>(null);

  folders = $state<Folder[]>([]);
  tags = $state<Tag[]>([]);
  plugins = $state<PluginInfo[]>([]);

  error = $state<ApiError | null>(null);
  booted = $state(false);

  /** Whether the advanced parameter panel is open. */
  paramsOpen = $state(false);

  /** Every model across every provider, local first. */
  get models(): ModelEntry[] {
    return this.providers.flatMap((group) => group.models);
  }

  get model(): ModelEntry | null {
    return this.models.find((entry) => entry.model_ref === this.modelRef) ?? null;
  }

  /**
   * Whether the caller may change backends and models.
   *
   * With authentication disabled the single local account is the administrator. The
   * server enforces this on every request; here it only decides which controls to show.
   */
  get isAdmin(): boolean {
    return this.config?.auth.enabled === false || this.session?.role === "admin";
  }

  get authenticated(): boolean {
    return this.session !== null || this.config?.auth.enabled === false;
  }

  get imagesEnabled(): boolean {
    return this.plugins.some((p) => p.kind === "images" && p.enabled);
  }

  get voiceEnabled(): boolean {
    return this.plugins.some((p) => p.kind === "voice" && p.enabled);
  }

  /** Translate with the active locale. */
  t = (key: string, values?: Record<string, string | number>): string =>
    translate(this.locale, key, values);

  async boot(): Promise<void> {
    this.locale = (readStored(STORAGE_KEYS.locale) as Locale | null) ?? detectLocale();
    this.setTheme((readStored(STORAGE_KEYS.theme) as Theme | null) ?? "auto");

    const stored = readStored(STORAGE_KEYS.session);
    if (stored) {
      try {
        const session = JSON.parse(stored) as Session;
        this.session = session;
        api.session = session;
      } catch {
        writeStored(STORAGE_KEYS.session, null);
      }
    }

    try {
      this.config = await api.config();
    } catch (error) {
      this.report(error);
      this.booted = true;
      return;
    }

    if (this.config.auth.enabled === false) {
      // Single-user mode: there is no session to hold and no login to show.
      this.session = null;
    }

    if (this.authenticated) await this.loadWorkspace();
    this.booted = true;
  }

  /** Load everything a signed-in user needs to start: models and the chat list. */
  async loadWorkspace(): Promise<void> {
    // All independent, so serialising them would show an empty interface for extra
    // round trips with no benefit.
    await Promise.all([
      this.loadModels(),
      this.loadChats(true),
      this.loadFolders(),
      this.loadTags(),
      this.loadPlugins(),
    ]);
  }

  async loadPlugins(): Promise<void> {
    try {
      this.plugins = await api.plugins();
    } catch (error) {
      this.report(error);
    }
  }

  async loadFolders(): Promise<void> {
    try {
      this.folders = await api.folders();
    } catch (error) {
      this.report(error);
    }
  }

  async loadTags(): Promise<void> {
    try {
      this.tags = await api.tags();
    } catch (error) {
      this.report(error);
    }
  }

  async loadModels(refresh = false): Promise<void> {
    this.modelsLoading = true;
    try {
      const { providers } = await api.models(refresh);
      this.providers = providers;

      const stored = readStored(STORAGE_KEYS.model);
      const available = this.models;
      const chosen =
        available.find((entry) => entry.model_ref === stored) ??
        available.find((entry) => entry.model_ref === this.modelRef) ??
        available[0];
      this.modelRef = chosen?.model_ref ?? null;
    } catch (error) {
      this.report(error);
    } finally {
      this.modelsLoading = false;
    }
  }

  selectModel(modelRef: string): void {
    this.modelRef = modelRef;
    writeStored(STORAGE_KEYS.model, modelRef);
  }

  async loadChats(reset = false): Promise<void> {
    if (this.chatsLoading) return;
    this.chatsLoading = true;
    try {
      const filter = {
        archived: this.chatsArchived,
        folderId: this.activeFolderId ?? undefined,
        tagId: this.activeTagId ?? undefined,
      };
      const page = await api.chats(reset ? null : this.chatsCursor, filter);
      this.chats = reset ? page.items : [...this.chats, ...page.items];
      this.chatsCursor = page.next_cursor;
    } catch (error) {
      this.report(error);
    } finally {
      this.chatsLoading = false;
    }
  }

  /** Switch which shelf, folder or tag the sidebar lists, and reload it. */
  async setChatFilter(filter: {
    archived?: boolean;
    folderId?: string | null;
    tagId?: string | null;
  }): Promise<void> {
    if (filter.archived !== undefined) this.chatsArchived = filter.archived;
    if (filter.folderId !== undefined) this.activeFolderId = filter.folderId;
    if (filter.tagId !== undefined) this.activeTagId = filter.tagId;
    await this.loadChats(true);
  }

  /** Move a conversation to the top of the sidebar after it is used. */
  touchChat(id: string, title?: string): void {
    const index = this.chats.findIndex((chat) => chat.id === id);
    if (index === -1) return;
    const chat = this.chats[index];
    if (!chat) return;
    const updated = { ...chat, updated_at: Date.now(), title: title ?? chat.title };
    this.chats = [updated, ...this.chats.filter((entry) => entry.id !== id)];
  }

  /** Pin, archive or move a conversation, applying the server's response locally. */
  async patchChat(
    id: string,
    patch: { pinned?: boolean; archived?: boolean; folder_id?: string | null; title?: string },
  ): Promise<void> {
    try {
      const updated = await api.updateChat(id, patch);
      if (updated.archived !== this.chatsArchived) {
        // The conversation left the shelf currently shown; drop it from the list.
        this.chats = this.chats.filter((chat) => chat.id !== id);
        return;
      }
      this.chats = this.chats.map((chat) =>
        chat.id === id
          ? {
              ...chat,
              pinned: updated.pinned,
              archived: updated.archived,
              folder_id: updated.folder_id,
              title: updated.title,
            }
          : chat,
      );
    } catch (error) {
      this.report(error);
    }
  }

  async signIn(email: string, password: string): Promise<void> {
    this.#adoptSession(await api.login(email, password));
    await this.loadWorkspace();
  }

  async signUp(email: string, password: string, name: string): Promise<void> {
    this.#adoptSession(await api.register(email, password, name));
    if (this.config) this.config = { ...this.config, auth: { ...this.config.auth, setup_required: false } };
    await this.loadWorkspace();
  }

  async signOut(): Promise<void> {
    try {
      await api.logout();
    } catch {
      // Signing out must never fail from the user's point of view.
    }
    this.session = null;
    api.session = null;
    writeStored(STORAGE_KEYS.session, null);
    this.chats = [];
    this.providers = [];
    this.folders = [];
    this.tags = [];
  }

  setTheme(theme: Theme): void {
    this.theme = theme;
    writeStored(STORAGE_KEYS.theme, theme);
    document.documentElement.dataset.theme = theme;
  }

  setLocale(locale: Locale): void {
    this.locale = locale;
    writeStored(STORAGE_KEYS.locale, locale);
    document.documentElement.lang = locale;
  }

  /** Turn any thrown value into the server's error envelope. */
  report(error: unknown): void {
    if (error instanceof VeloxApiError) {
      this.error = error.detail;
    } else if (error instanceof Error) {
      this.error = { code: "internal", message: error.message, retryable: false };
    } else {
      this.error = { code: "internal", message: String(error), retryable: false };
    }
  }

  dismissError(): void {
    this.error = null;
  }

  #adoptSession(session: Session): void {
    this.session = session;
    api.session = session;
    writeStored(STORAGE_KEYS.session, JSON.stringify(session));
  }
}

export const app = new AppStore();
