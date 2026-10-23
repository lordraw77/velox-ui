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

/** Which models the picker lists: everything (favorites first) or only the favorites. */
export type ModelView = "all" | "favorites";

const STORAGE_KEYS = {
  session: "velox.session",
  model: "velox.model",
  modelView: "velox.modelView",
  sidebarCollapsed: "velox.sidebarCollapsed",
  navCollapsed: "velox.navCollapsed",
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
  favoriteRefs = $state<string[]>([]);
  modelView = $state<ModelView>("all");
  /** Desktop layout: the whole sidebar is folded away to the left. */
  sidebarCollapsed = $state(false);
  /** The navigation block (Search, Models, …) at the bottom of the sidebar is folded down. */
  navCollapsed = $state(false);

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
  /** A passing, non-error message: a reply that finished while you were elsewhere. */
  notice = $state<{ text: string; chatId: string | null } | null>(null);
  booted = $state(false);

  /** Whether the advanced parameter panel is open. */
  paramsOpen = $state(false);

  /** Whether the system prompt panel of the current conversation is open. */
  systemPromptOpen = $state(false);

  /** Every model across every provider, local first. */
  get models(): ModelEntry[] {
    return this.providers.flatMap((group) => group.models);
  }

  /** Starred models that are currently offered, in the order they were starred. */
  get favoriteModels(): ModelEntry[] {
    const byRef = new Map(this.models.map((entry) => [entry.model_ref, entry]));
    return this.favoriteRefs.flatMap((ref) => byRef.get(ref) ?? []);
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

  /**
   * Whether the builtin web-search/browsing tool is enabled instance-wide.
   *
   * Unlike `tool_server_ids` (MCP servers, opt in per custom model), this offers
   * web_search/web_browse to every chat once the plugin is configured — the same
   * "one global toggle" shape images/voice already use, not an opt-in per model.
   */
  get toolsEnabled(): boolean {
    return this.plugins.some((p) => p.kind === "tools" && p.enabled);
  }

  /** Translate with the active locale. */
  t = (key: string, values?: Record<string, string | number>): string =>
    translate(this.locale, key, values);

  async boot(): Promise<void> {
    this.locale = (readStored(STORAGE_KEYS.locale) as Locale | null) ?? detectLocale();
    this.setTheme((readStored(STORAGE_KEYS.theme) as Theme | null) ?? "auto");
    this.modelView = readStored(STORAGE_KEYS.modelView) === "favorites" ? "favorites" : "all";
    this.sidebarCollapsed = readStored(STORAGE_KEYS.sidebarCollapsed) === "1";
    this.navCollapsed = readStored(STORAGE_KEYS.navCollapsed) === "1";

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
      this.loadFavoriteModels(),
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

  async loadFavoriteModels(): Promise<void> {
    try {
      this.favoriteRefs = (await api.favoriteModels()).model_refs;
    } catch (error) {
      this.report(error);
    }
  }

  /** Star or un-star a model. Applied at once and rolled back if the server refuses. */
  async toggleFavoriteModel(modelRef: string): Promise<void> {
    const before = this.favoriteRefs;
    const favorite = !before.includes(modelRef);
    this.favoriteRefs = favorite
      ? [...before, modelRef]
      : before.filter((ref) => ref !== modelRef);
    try {
      this.favoriteRefs = (await api.setFavoriteModel(modelRef, favorite)).model_refs;
    } catch (error) {
      this.favoriteRefs = before;
      this.report(error);
    }
  }

  setSidebarCollapsed(collapsed: boolean): void {
    this.sidebarCollapsed = collapsed;
    writeStored(STORAGE_KEYS.sidebarCollapsed, collapsed ? "1" : null);
  }

  setNavCollapsed(collapsed: boolean): void {
    this.navCollapsed = collapsed;
    writeStored(STORAGE_KEYS.navCollapsed, collapsed ? "1" : null);
  }

  setModelView(view: ModelView): void {
    this.modelView = view;
    writeStored(STORAGE_KEYS.modelView, view);
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
    const rest = this.chats.filter((entry) => entry.id !== id);
    // Pinned chats stay ahead of everything else, as the server orders them.
    const firstUnpinned = rest.findIndex((entry) => !entry.pinned);
    const at = updated.pinned ? 0 : firstUnpinned === -1 ? rest.length : firstUnpinned;
    this.chats = [...rest.slice(0, at), updated, ...rest.slice(at)];
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
    this.favoriteRefs = [];
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

  /** Show a notice, replacing any previous one. */
  notify(text: string, chatId: string | null = null): void {
    this.notice = { text, chatId };
  }

  dismissNotice(): void {
    this.notice = null;
  }

  #adoptSession(session: Session): void {
    this.session = session;
    api.session = session;
    writeStored(STORAGE_KEYS.session, JSON.stringify(session));
  }
}

export const app = new AppStore();
