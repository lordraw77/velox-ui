<!--
  The conversation list.

  Paginated with the server's opaque keyset cursor, not by page number: the sidebar is
  an infinite scroll and `OFFSET` over ten thousand conversations is exactly the cost
  the schema was designed to avoid (ADR-0007).

  Folders and tags are flat pickers, not a drag-and-drop tree: the schema supports
  arbitrary folder nesting, but the sidebar only needs "which shelf am I looking at"
  (phase 6 scope decision — see the phase report).
-->
<script lang="ts">
  import { api } from "$lib/api/client";
  import { app } from "$lib/stores/app.svelte";
  import { conversation } from "$lib/stores/conversation.svelte";

  type View =
    | "chat"
    | "search"
    | "models"
    | "providers"
    | "custom-models"
    | "knowledge"
    | "mcp"
    | "plugins"
    | "admin";

  interface Props {
    onselect: (id: string | null) => void;
    onnavigate: (view: Exclude<View, "chat">) => void;
    /** The view currently shown, to mark its entry. */
    current: string;
    /**
     * Whether the off-canvas contents should be taken out of the focus order —
     * true only when the layout is in drawer mode and the drawer is closed.
     */
    inert?: boolean;
    /** Close the drawer. Called after anything that navigates away. */
    onclose?: () => void;
  }

  let { onselect, onnavigate, current, inert = false, onclose }: Props = $props();

  async function remove(id: string, event: MouseEvent): Promise<void> {
    event.stopPropagation();
    if (!confirm(app.t("chat.deleteConfirm"))) return;
    try {
      await api.deleteChat(id);
      app.chats = app.chats.filter((chat) => chat.id !== id);
      if (conversation.id === id) onselect(null);
    } catch (error) {
      app.report(error);
    }
  }

  function togglePin(id: string, pinned: boolean, event: MouseEvent): void {
    event.stopPropagation();
    void app.patchChat(id, { pinned: !pinned });
  }

  function archive(id: string, event: MouseEvent): void {
    event.stopPropagation();
    void app.patchChat(id, { archived: true });
    if (conversation.id === id) onselect(null);
  }

  function unarchive(id: string, event: MouseEvent): void {
    event.stopPropagation();
    void app.patchChat(id, { archived: false });
  }

  function onListScroll(event: Event): void {
    const element = event.currentTarget as HTMLElement;
    const remaining = element.scrollHeight - element.scrollTop - element.clientHeight;
    if (remaining < 200 && app.chatsCursor && !app.chatsLoading) void app.loadChats(false);
  }

  function onFolderChange(event: Event): void {
    const value = (event.currentTarget as HTMLSelectElement).value;
    void app.setChatFilter({ folderId: value || null });
  }

  function onTagChange(event: Event): void {
    const value = (event.currentTarget as HTMLSelectElement).value;
    void app.setChatFilter({ tagId: value || null });
  }

  function toggleArchivedShelf(): void {
    void app.setChatFilter({ archived: !app.chatsArchived });
  }

  async function addFolder(): Promise<void> {
    const name = prompt(app.t("folder.namePrompt"));
    if (!name) return;
    try {
      await api.createFolder(name);
      await app.loadFolders();
    } catch (error) {
      app.report(error);
    }
  }

  async function addTag(): Promise<void> {
    const name = prompt(app.t("tag.namePrompt"));
    if (!name) return;
    try {
      await api.createTag(name);
      await app.loadTags();
    } catch (error) {
      app.report(error);
    }
  }
</script>

<!-- `inert` only when the drawer is closed *and* we are actually in drawer mode:
     off-canvas content must not be focusable, but the permanent desktop sidebar
     is never inert. The caller decides, since it owns the media query. -->
<aside inert={inert}>
  <div class="head">
    <button class="btn btn-primary new" onclick={() => onselect(null)} type="button">
      {app.t("chat.newChat")}
    </button>
    <!-- Drawer-only: hidden above the breakpoint, where the sidebar is permanent. -->
    <button
      class="btn btn-ghost btn-icon close"
      onclick={() => onclose?.()}
      aria-label={app.t("nav.closeMenu")}
      title={app.t("nav.closeMenu")}
      type="button"
    >
      ×
    </button>
  </div>

  <div class="filters">
    <select value={app.activeFolderId ?? ""} onchange={onFolderChange} aria-label={app.t("folder.filterLabel")}>
      <option value="">{app.t("folder.all")}</option>
      {#each app.folders as folder (folder.id)}
        <option value={folder.id}>{folder.name}</option>
      {/each}
    </select>
    <button class="btn btn-ghost btn-icon" onclick={addFolder} title={app.t("folder.add")} type="button">
      +
    </button>

    <select value={app.activeTagId ?? ""} onchange={onTagChange} aria-label={app.t("tag.filterLabel")}>
      <option value="">{app.t("tag.all")}</option>
      {#each app.tags as tag (tag.id)}
        <option value={tag.id}>{tag.name}</option>
      {/each}
    </select>
    <button class="btn btn-ghost btn-icon" onclick={addTag} title={app.t("tag.add")} type="button">
      +
    </button>
  </div>

  <button
    class="btn btn-ghost shelf"
    class:active={app.chatsArchived}
    onclick={toggleArchivedShelf}
    type="button"
  >
    {app.chatsArchived ? app.t("chat.showActive") : app.t("chat.showArchived")}
  </button>

  <nav onscroll={onListScroll}>
    {#if app.chats.length === 0 && !app.chatsLoading}
      <p class="hint empty">{app.t("chat.noChats")}</p>
    {/if}

    {#each app.chats as chat (chat.id)}
      <div class="row" class:active={conversation.id === chat.id}>
        <button
          class="btn btn-ghost btn-icon pin"
          class:pinned={chat.pinned}
          onclick={(event) => togglePin(chat.id, chat.pinned, event)}
          title={app.t(chat.pinned ? "chat.unpin" : "chat.pin")}
          aria-label={app.t(chat.pinned ? "chat.unpin" : "chat.pin")}
          type="button"
        >
          {chat.pinned ? "★" : "☆"}
        </button>
        <button class="title" onclick={() => onselect(chat.id)} type="button">
          {chat.title}
        </button>
        {#if app.chatsArchived}
          <button
            class="btn btn-ghost btn-icon action"
            onclick={(event) => unarchive(chat.id, event)}
            title={app.t("chat.unarchive")}
            aria-label={app.t("chat.unarchive")}
            type="button"
          >
            ⤴
          </button>
        {:else}
          <button
            class="btn btn-ghost btn-icon action"
            onclick={(event) => archive(chat.id, event)}
            title={app.t("chat.archive")}
            aria-label={app.t("chat.archive")}
            type="button"
          >
            ⤓
          </button>
        {/if}
        <button
          class="btn btn-ghost btn-icon action"
          onclick={(event) => remove(chat.id, event)}
          title={app.t("chat.delete")}
          aria-label={app.t("chat.delete")}
          type="button"
        >
          ×
        </button>
      </div>
    {/each}

    {#if app.chatsLoading}
      <p class="hint empty"><span class="spinner"></span></p>
    {/if}
  </nav>

  <div class="foot">
    <button class="btn btn-ghost" class:active={current === "search"} onclick={() => onnavigate("search")} type="button" data-testid="nav-search">
      {app.t("nav.search")}
    </button>
    <button class="btn btn-ghost" class:active={current === "models"} onclick={() => onnavigate("models")} type="button" data-testid="nav-models">
      {app.t("nav.models")}
    </button>
    <button class="btn btn-ghost" class:active={current === "providers"} onclick={() => onnavigate("providers")} type="button" data-testid="nav-providers">
      {app.t("nav.providers")}
    </button>
    <button class="btn btn-ghost" class:active={current === "custom-models"} onclick={() => onnavigate("custom-models")} type="button" data-testid="nav-custom-models">
      {app.t("nav.customModels")}
    </button>
    <button class="btn btn-ghost" class:active={current === "knowledge"} onclick={() => onnavigate("knowledge")} type="button" data-testid="nav-knowledge">
      {app.t("nav.knowledge")}
    </button>
    <button class="btn btn-ghost" class:active={current === "mcp"} onclick={() => onnavigate("mcp")} type="button" data-testid="nav-mcp">
      {app.t("nav.mcp")}
    </button>
    {#if app.isAdmin}
      <button class="btn btn-ghost" class:active={current === "plugins"} onclick={() => onnavigate("plugins")} type="button" data-testid="nav-plugins">
        {app.t("nav.plugins")}
      </button>
      <button class="btn btn-ghost" class:active={current === "admin"} onclick={() => onnavigate("admin")} type="button" data-testid="nav-admin">
        {app.t("nav.admin")}
      </button>
    {/if}
    <!-- Drawer-only: the header carries sign-out on the desktop layout, but hides
         it at narrow widths, so it has to remain reachable from somewhere. -->
    {#if app.session}
      <button class="btn btn-ghost signout" onclick={() => app.signOut()} type="button">
        {app.t("auth.signOut")}
      </button>
    {/if}
  </div>
</aside>

<style>
  aside {
    display: flex;
    flex: none;
    flex-direction: column;
    width: var(--sidebar-width);
    background: var(--bg-sunken);
    border-right: 1px solid var(--border);
  }

  .head {
    display: flex;
    gap: 0.5rem;
    align-items: center;
    padding: 0.75rem;
    border-bottom: 1px solid var(--border);
  }

  /* Both belong to the drawer only: the permanent sidebar has nothing to close,
     and the header still carries sign-out at desktop widths. */
  .close,
  .signout {
    display: none;
  }

  .new {
    flex: 1;
    width: 100%;
  }

  .filters {
    display: flex;
    gap: 0.25rem;
    align-items: center;
    padding: 0.5rem 0.75rem;
    border-bottom: 1px solid var(--border);
  }

  .filters select {
    flex: 1;
    min-width: 0;
    padding: 0.2rem 0.3rem;
    font-size: 0.78rem;
    background: var(--bg-raised);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
  }

  .shelf {
    margin: 0.4rem 0.75rem 0;
    font-size: 0.78rem;
  }

  .shelf.active {
    background: var(--bg-active);
  }

  nav {
    flex: 1;
    overflow-y: auto;
    padding: 0.4rem;
  }

  .row {
    display: flex;
    gap: 0.15rem;
    align-items: center;
    border-radius: var(--radius-sm);
  }

  .row:hover {
    background: var(--bg-hover);
  }

  .row.active {
    background: var(--bg-active);
  }

  .title {
    flex: 1;
    min-width: 0;
    padding: 0.45rem 0.5rem;
    overflow: hidden;
    font-size: 0.86rem;
    text-align: left;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .pin {
    flex: none;
    font-size: 0.85rem;
    color: var(--text-faint);
  }

  .pin.pinned {
    color: var(--text);
  }

  .action {
    flex: none;
    font-size: 1rem;
    color: var(--text-faint);
    opacity: 0;
  }

  .row:hover .action,
  .action:focus-visible {
    opacity: 1;
  }

  .foot {
    display: flex;
    flex-wrap: wrap;
    gap: 0.25rem;
    padding: 0.5rem;
    border-top: 1px solid var(--border);
  }

  .foot .btn {
    flex: 1;
  }

  .foot .active {
    background: var(--bg-active);
  }

  .empty {
    padding: 0.6rem 0.5rem;
    text-align: center;
  }

  @media (width <= 900px) {
    .close {
      display: inline-flex;
      font-size: 1.2rem;
    }

    .signout {
      display: inline-flex;
      grid-column: 1 / -1;
    }

    /*
     * The eight nav entries wrap into ragged rows when they are flex items with
     * `flex: 1`. In the drawer there is room to give them an even two-column
     * grid instead, which also makes each one a comfortably large target.
     */
    .foot {
      display: grid;
      grid-template-columns: 1fr 1fr;
    }

    .foot .btn {
      justify-content: flex-start;
    }
  }
</style>
