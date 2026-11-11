<!--
  The conversation list.

  Paginated with the server's opaque keyset cursor, not by page number: the sidebar is
  an infinite scroll and `OFFSET` over ten thousand conversations is exactly the cost
  the schema was designed to avoid (ADR-0007).

  Folders are a collapsible tree (starred folders first) holding their chats; chats
  move between folders with the row's folder picker or by dragging onto a folder.
  The tree is built client-side from the chats already loaded, so a folder shows what
  has been scrolled in so far, like the rest of the list. Tags remain a flat filter.
-->
<script lang="ts">
  import { api } from "$lib/api/client";
  import type { ChatSummary, Folder } from "$lib/api/types";
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

  let selecting = $state(false);
  let selected = $state<string[]>([]);
  let busy = $state(false);

  /** Pinned first, whatever order the list was patched into locally; stable within each group. */
  function pinnedFirst<T extends { pinned: boolean }>(items: T[]): T[] {
    return [...items.filter((item) => item.pinned), ...items.filter((item) => !item.pinned)];
  }

  let folderIds = $derived(new Set(app.folders.map((folder) => folder.id)));

  /** Chats grouped by folder; `null` holds the unfiled ones (and orphans of an unknown folder). */
  let chatsByFolder = $derived.by(() => {
    const groups = new Map<string | null, ChatSummary[]>();
    for (const chat of pinnedFirst(app.chats)) {
      const key = chat.folder_id && folderIds.has(chat.folder_id) ? chat.folder_id : null;
      const group = groups.get(key);
      if (group) group.push(chat);
      else groups.set(key, [chat]);
    }
    return groups;
  });

  /** Child folders by parent; a folder whose parent is unknown is shown at the top level. */
  let foldersByParent = $derived.by(() => {
    const groups = new Map<string | null, Folder[]>();
    for (const folder of pinnedFirst(app.folders)) {
      const key = folder.parent_id && folderIds.has(folder.parent_id) ? folder.parent_id : null;
      const group = groups.get(key);
      if (group) group.push(folder);
      else groups.set(key, [folder]);
    }
    return groups;
  });

  /** On the archive shelf or under a tag filter, only folders that hold something are shown. */
  let filtering = $derived(app.chatsArchived || app.activeTagId !== null);

  let populated = $derived.by(() => {
    const result = new Set<string>();
    const mark = (id: string): boolean => {
      let has = (chatsByFolder.get(id)?.length ?? 0) > 0;
      for (const child of foldersByParent.get(id) ?? []) has = mark(child.id) || has;
      if (has) result.add(id);
      return has;
    };
    for (const folder of foldersByParent.get(null) ?? []) mark(folder.id);
    return result;
  });

  function visibleFolders(parent: string | null): Folder[] {
    const children = foldersByParent.get(parent) ?? [];
    return filtering ? children.filter((folder) => populated.has(folder.id)) : children;
  }

  const OPEN_KEY = "velox.openFolders";
  let openFolders = $state<string[]>(readOpen());

  function readOpen(): string[] {
    try {
      const parsed: unknown = JSON.parse(localStorage.getItem(OPEN_KEY) ?? "[]");
      return Array.isArray(parsed) ? parsed.filter((id): id is string => typeof id === "string") : [];
    } catch {
      return [];
    }
  }

  function setOpen(id: string, open: boolean): void {
    openFolders = open ? [...openFolders.filter((entry) => entry !== id), id] : openFolders.filter((entry) => entry !== id);
    try {
      localStorage.setItem(OPEN_KEY, JSON.stringify(openFolders));
    } catch {
      /* private mode: the tree still works, it just forgets what was open */
    }
  }

  /** A filtered view opens every folder it shows, since closed ones would hide the matches. */
  function isOpen(id: string): boolean {
    return filtering || openFolders.includes(id);
  }

  let movingId: string | null = $state(null);
  let dropTarget: string | null | undefined = $state(undefined);

  /** Folders as a flat, indented list for the "move to" picker. */
  let folderChoices = $derived.by(() => {
    const out: { id: string; label: string }[] = [];
    const walk = (parent: string | null, depth: number): void => {
      for (const folder of foldersByParent.get(parent) ?? []) {
        out.push({ id: folder.id, label: `${"– ".repeat(depth)}${folder.name}` });
        walk(folder.id, depth + 1);
      }
    };
    walk(null, 0);
    return out;
  });

  function moveChat(id: string, folderId: string | null): void {
    movingId = null;
    const chat = app.chats.find((entry) => entry.id === id);
    if (!chat || chat.folder_id === folderId) return;
    void app.patchChat(id, { folder_id: folderId });
    if (folderId) setOpen(folderId, true);
  }

  function onMovePick(id: string, event: Event): void {
    const value = (event.currentTarget as HTMLSelectElement).value;
    moveChat(id, value || null);
  }

  function onDragStart(id: string, event: DragEvent): void {
    event.dataTransfer?.setData("application/x-velox-chat", id);
    if (event.dataTransfer) event.dataTransfer.effectAllowed = "move";
  }

  function onDragOver(target: string | null, event: DragEvent): void {
    if (!event.dataTransfer?.types.includes("application/x-velox-chat")) return;
    event.preventDefault();
    event.stopPropagation();
    dropTarget = target;
  }

  function onDrop(target: string | null, event: DragEvent): void {
    const id = event.dataTransfer?.getData("application/x-velox-chat");
    dropTarget = undefined;
    if (!id) return;
    event.preventDefault();
    event.stopPropagation();
    moveChat(id, target);
  }

  async function newSubfolder(parentId: string | null): Promise<void> {
    const name = prompt(app.t("folder.namePrompt"))?.trim();
    if (!name) return;
    try {
      await api.createFolder(name, parentId);
      await app.loadFolders();
      if (parentId) setOpen(parentId, true);
    } catch (error) {
      app.report(error);
    }
  }

  async function renameFolder(folder: Folder): Promise<void> {
    const name = prompt(app.t("folder.namePrompt"), folder.name)?.trim();
    if (!name || name === folder.name) return;
    await patchFolder(folder.id, { name });
  }

  async function patchFolder(id: string, patch: { name?: string; pinned?: boolean }): Promise<void> {
    try {
      const updated = await api.updateFolder(id, patch);
      app.folders = app.folders.map((folder) => (folder.id === id ? updated : folder));
    } catch (error) {
      app.report(error);
    }
  }

  async function removeFolder(folder: Folder): Promise<void> {
    if (!confirm(app.t("folder.deleteConfirm"))) return;
    try {
      await api.deleteFolder(folder.id);
      // The server detaches the chats (and cascades subfolders); mirror that locally.
      const gone = new Set<string>([folder.id]);
      for (let grew = true; grew; ) {
        grew = false;
        for (const entry of app.folders) {
          if (entry.parent_id && gone.has(entry.parent_id) && !gone.has(entry.id)) {
            gone.add(entry.id);
            grew = true;
          }
        }
      }
      app.folders = app.folders.filter((entry) => !gone.has(entry.id));
      app.chats = app.chats.map((chat) =>
        chat.folder_id && gone.has(chat.folder_id) ? { ...chat, folder_id: null } : chat,
      );
    } catch (error) {
      app.report(error);
    }
  }

  let allSelected = $derived(app.chats.length > 0 && selected.length === app.chats.length);

  function toggleSelecting(): void {
    selecting = !selecting;
    selected = [];
  }

  function toggleSelected(id: string): void {
    selected = selected.includes(id) ? selected.filter((entry) => entry !== id) : [...selected, id];
  }

  /** Selects what is loaded in the list; chats further down the scroll are not included. */
  function toggleAll(): void {
    selected = allSelected ? [] : app.chats.map((chat) => chat.id);
  }

  /** Apply one request per selected chat; those that succeed leave the list, failures stay selected. */
  async function applyToSelected(
    action: (id: string) => Promise<unknown>,
  ): Promise<void> {
    if (busy || selected.length === 0) return;
    busy = true;
    const ids = [...selected];
    const results = await Promise.allSettled(ids.map(action));
    const done = ids.filter((_, index) => results[index]?.status === "fulfilled");
    const failed = results.find((result) => result.status === "rejected");
    app.chats = app.chats.filter((chat) => !done.includes(chat.id));
    selected = selected.filter((id) => !done.includes(id));
    if (conversation.id && done.includes(conversation.id)) onselect(null);
    if (failed?.status === "rejected") app.report(failed.reason);
    busy = false;
  }

  function archiveSelected(): Promise<void> {
    return applyToSelected((id) => api.updateChat(id, { archived: !app.chatsArchived }));
  }

  async function deleteSelected(): Promise<void> {
    if (selected.length === 0) return;
    if (!confirm(app.t("chat.deleteSelectedConfirm", { count: selected.length }))) return;
    await applyToSelected((id) => api.deleteChat(id));
  }

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

  let renamingId: string | null = $state(null);
  let renameDraft = $state("");

  function startRename(id: string, title: string, event: MouseEvent): void {
    event.stopPropagation();
    renamingId = id;
    renameDraft = title;
  }

  function commitRename(): void {
    const id = renamingId;
    if (id === null) return;
    renamingId = null;
    const title = renameDraft.trim();
    const current = app.chats.find((chat) => chat.id === id);
    if (!title || !current || title === current.title) return;
    void app.patchChat(id, { title });
  }

  function onRenameKey(event: KeyboardEvent): void {
    if (event.key === "Enter") {
      event.preventDefault();
      commitRename();
    } else if (event.key === "Escape") {
      renamingId = null;
    }
  }

  function focusSelect(node: HTMLInputElement | HTMLSelectElement): void {
    node.focus();
    if (node instanceof HTMLInputElement) node.select();
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

  function onTagChange(event: Event): void {
    const value = (event.currentTarget as HTMLSelectElement).value;
    void app.setChatFilter({ tagId: value || null });
  }

  function toggleArchivedShelf(): void {
    void app.setChatFilter({ archived: !app.chatsArchived });
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

{#snippet chatRow(chat: ChatSummary, depth: number)}
      <div
        class="row"
        class:active={conversation.id === chat.id}
        style:--depth={depth}
        draggable={!selecting && renamingId !== chat.id}
        ondragstart={(event) => onDragStart(chat.id, event)}
        role="presentation"
      >
        {#if selecting}
          <input
            class="check"
            type="checkbox"
            checked={selected.includes(chat.id)}
            onchange={() => toggleSelected(chat.id)}
            aria-label={chat.title}
          />
        {/if}
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
        {#if renamingId === chat.id}
          <input
            class="rename"
            bind:value={renameDraft}
            onkeydown={onRenameKey}
            onblur={commitRename}
            maxlength="200"
            aria-label={app.t("chat.rename")}
            use:focusSelect
          />
        {:else}
          <button class="title" onclick={() => onselect(chat.id)} type="button">
            {chat.title}
          </button>
          <button
            class="btn btn-ghost btn-icon action"
            onclick={(event) => startRename(chat.id, chat.title, event)}
            title={app.t("chat.rename")}
            aria-label={app.t("chat.rename")}
            type="button"
            data-testid="rename-chat"
          >
            ✎
          </button>
        {/if}
        {#if movingId === chat.id}
          <select
            class="movepick"
            value={chat.folder_id ?? ""}
            onchange={(event) => onMovePick(chat.id, event)}
            onblur={() => (movingId = null)}
            aria-label={app.t("folder.moveChat")}
            use:focusSelect
          >
            <option value="">{app.t("folder.noFolder")}</option>
            {#each folderChoices as choice (choice.id)}
              <option value={choice.id}>{choice.label}</option>
            {/each}
          </select>
        {:else if app.folders.length > 0}
          <button
            class="btn btn-ghost btn-icon action"
            onclick={(event) => { event.stopPropagation(); movingId = chat.id; }}
            title={app.t("folder.moveChat")}
            aria-label={app.t("folder.moveChat")}
            type="button"
            data-testid="move-chat"
          >
            📁
          </button>
        {/if}
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
{/snippet}

{#snippet folderNode(folder: Folder, depth: number)}
  {@const open = isOpen(folder.id)}
  {@const chats = chatsByFolder.get(folder.id) ?? []}
  {@const children = visibleFolders(folder.id)}
  <div class="folder" data-testid="folder">
    <div
      class="row frow"
      class:drop={dropTarget === folder.id}
      style:--depth={depth}
      ondragover={(event) => onDragOver(folder.id, event)}
      ondragleave={() => (dropTarget = undefined)}
      ondrop={(event) => onDrop(folder.id, event)}
      role="presentation"
    >
      <button
        class="btn btn-ghost btn-icon pin"
        class:pinned={folder.pinned}
        onclick={() => patchFolder(folder.id, { pinned: !folder.pinned })}
        title={app.t(folder.pinned ? "folder.unstar" : "folder.star")}
        aria-label={app.t(folder.pinned ? "folder.unstar" : "folder.star")}
        type="button"
        data-testid="star-folder"
      >
        {folder.pinned ? "★" : "☆"}
      </button>
      <button
        class="title ftitle"
        onclick={() => setOpen(folder.id, !open)}
        aria-expanded={open}
        title={app.t(open ? "folder.collapse" : "folder.expand")}
        type="button"
      >
        <span class="caret">{open ? "▾" : "▸"}</span> 📁 {folder.name}
        <span class="count">{chats.length}</span>
      </button>
      {#if !filtering}
        <button class="btn btn-ghost btn-icon action" onclick={() => newSubfolder(folder.id)} title={app.t("folder.addSub")} aria-label={app.t("folder.addSub")} type="button">+</button>
        <button class="btn btn-ghost btn-icon action" onclick={() => renameFolder(folder)} title={app.t("folder.rename")} aria-label={app.t("folder.rename")} type="button">✎</button>
        <button class="btn btn-ghost btn-icon action" onclick={() => removeFolder(folder)} title={app.t("folder.delete")} aria-label={app.t("folder.delete")} type="button" data-testid="delete-folder">×</button>
      {/if}
    </div>
    {#if open}
      {#each children as child (child.id)}
        {@render folderNode(child, depth + 1)}
      {/each}
      {#each chats as chat (chat.id)}
        {@render chatRow(chat, depth + 1)}
      {/each}
      {#if chats.length === 0 && children.length === 0}
        <p class="hint fempty" style:--depth={depth + 1}>{app.t("folder.empty")}</p>
      {/if}
    {/if}
  </div>
{/snippet}

<!-- `inert` only when the drawer is closed *and* we are actually in drawer mode:
     off-canvas content must not be focusable, but the permanent desktop sidebar
     is never inert. The caller decides, since it owns the media query. -->
<aside inert={inert}>
  <div class="head">
    <button class="btn btn-primary new" onclick={() => onselect(null)} type="button">
      {app.t("chat.newChat")}
    </button>
    <!-- Desktop-only: folds the whole sidebar away to the left. -->
    <button
      class="btn btn-ghost btn-icon collapse"
      onclick={() => app.setSidebarCollapsed(true)}
      aria-label={app.t("nav.collapseSidebar")}
      title={app.t("nav.collapseSidebar")}
      type="button"
      data-testid="sidebar-collapse"
    >
      «
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
    <button class="btn btn-ghost addfolder" onclick={() => newSubfolder(null)} title={app.t("folder.add")} type="button" data-testid="add-folder">
      📁+ {app.t("folder.add")}
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

  <div class="bulk">
    <button class="btn btn-ghost" class:active={selecting} onclick={toggleSelecting} type="button" data-testid="select-mode">
      {selecting ? app.t("chat.selectDone") : app.t("chat.select")}
    </button>
    {#if selecting}
      <button class="btn btn-ghost" onclick={toggleAll} type="button" data-testid="select-all">
        {allSelected ? app.t("chat.selectNone") : app.t("chat.selectAll")}
      </button>
      <button class="btn btn-ghost" disabled={busy || selected.length === 0} onclick={archiveSelected} type="button" data-testid="archive-selected">
        {app.chatsArchived ? app.t("chat.unarchiveSelected") : app.t("chat.archiveSelected")}
      </button>
      <button class="btn btn-ghost danger" disabled={busy || selected.length === 0} onclick={deleteSelected} type="button" data-testid="delete-selected">
        {app.t("chat.deleteSelected")}
      </button>
    {/if}
  </div>

  <nav
    onscroll={onListScroll}
    class:drop={dropTarget === null}
    ondragover={(event) => onDragOver(null, event)}
    ondragleave={() => (dropTarget = undefined)}
    ondrop={(event) => onDrop(null, event)}
  >
    {#if app.chats.length === 0 && visibleFolders(null).length === 0 && !app.chatsLoading}
      <p class="hint empty">{app.t("chat.noChats")}</p>
    {/if}

    {#each visibleFolders(null) as folder (folder.id)}
      {@render folderNode(folder, 0)}
    {/each}
    {#each chatsByFolder.get(null) ?? [] as chat (chat.id)}
      {@render chatRow(chat, 0)}
    {/each}

    {#if app.chatsLoading}
      <p class="hint empty"><span class="spinner"></span></p>
    {/if}
  </nav>

  <button
    class="btn btn-ghost navtoggle"
    onclick={() => app.setNavCollapsed(!app.navCollapsed)}
    aria-expanded={!app.navCollapsed}
    aria-label={app.t(app.navCollapsed ? "nav.expandNav" : "nav.collapseNav")}
    title={app.t(app.navCollapsed ? "nav.expandNav" : "nav.collapseNav")}
    type="button"
    data-testid="nav-collapse"
  >
    {app.navCollapsed ? "▴" : "▾"}
  </button>

  <div class="foot" hidden={app.navCollapsed}>
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

  .navtoggle {
    flex: none;
    padding: 0.1rem;
    font-size: 0.8rem;
    color: var(--text-faint);
    border-top: 1px solid var(--border);
    border-radius: 0;
  }

  .foot[hidden] {
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

  .addfolder {
    flex: none;
    font-size: 0.78rem;
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

  .bulk {
    display: flex;
    flex-wrap: wrap;
    gap: 0.25rem;
    margin: 0.4rem 0.75rem 0;
  }

  .bulk .btn {
    flex: 1;
    font-size: 0.78rem;
  }

  .bulk .active {
    background: var(--bg-active);
  }

  .bulk .danger:not(:disabled) {
    color: var(--danger);
  }

  .check {
    flex: none;
    margin: 0 0.1rem 0 0.4rem;
  }

  nav {
    flex: 1;
    overflow-y: auto;
    padding: 0.4rem;
  }

  .row {
    padding-left: calc(var(--depth, 0) * 0.9rem);
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

  nav.drop,
  .row.drop {
    outline: 1px dashed var(--border-strong);
    outline-offset: -2px;
  }

  .ftitle {
    font-weight: 600;
  }

  .caret {
    display: inline-block;
    width: 0.9em;
    color: var(--text-faint);
  }

  .count {
    margin-left: 0.3rem;
    font-size: 0.72rem;
    font-weight: 400;
    color: var(--text-faint);
  }

  .fempty {
    padding: 0.2rem 0.5rem 0.2rem calc(var(--depth, 0) * 0.9rem + 2rem);
    font-size: 0.78rem;
  }

  .movepick {
    flex: none;
    max-width: 8rem;
    padding: 0.2rem;
    font-size: 0.78rem;
    background: var(--bg-raised);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
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

  .rename {
    flex: 1;
    min-width: 0;
    padding: 0.35rem 0.5rem;
    font-size: 0.86rem;
    background: var(--bg);
    border: 1px solid var(--border-strong);
    border-radius: var(--radius-sm);
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
    .collapse {
      display: none;
    }

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
