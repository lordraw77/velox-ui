<!--
  The conversation list.

  Paginated with the server's opaque keyset cursor, not by page number: the sidebar is
  an infinite scroll and `OFFSET` over ten thousand conversations is exactly the cost
  the schema was designed to avoid (ADR-0007).
-->
<script lang="ts">
  import { api } from "$lib/api/client";
  import { app } from "$lib/stores/app.svelte";
  import { conversation } from "$lib/stores/conversation.svelte";

  interface Props {
    onselect: (id: string | null) => void;
    onnavigate: (view: "models" | "providers") => void;
    /** The view currently shown, to mark its entry. */
    current: string;
  }

  let { onselect, onnavigate, current }: Props = $props();

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

  function onListScroll(event: Event): void {
    const element = event.currentTarget as HTMLElement;
    const remaining = element.scrollHeight - element.scrollTop - element.clientHeight;
    if (remaining < 200 && app.chatsCursor && !app.chatsLoading) void app.loadChats(false);
  }
</script>

<aside>
  <div class="head">
    <button class="btn btn-primary new" onclick={() => onselect(null)} type="button">
      {app.t("chat.newChat")}
    </button>
  </div>

  <nav onscroll={onListScroll}>
    {#if app.chats.length === 0 && !app.chatsLoading}
      <p class="hint empty">{app.t("chat.noChats")}</p>
    {/if}

    {#each app.chats as chat (chat.id)}
      <div class="row" class:active={conversation.id === chat.id}>
        <button class="title" onclick={() => onselect(chat.id)} type="button">
          {chat.title}
        </button>
        <button
          class="btn btn-ghost btn-icon remove"
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
    <button class="btn btn-ghost" class:active={current === "models"} onclick={() => onnavigate("models")} type="button" data-testid="nav-models">
      {app.t("nav.models")}
    </button>
    <button class="btn btn-ghost" class:active={current === "providers"} onclick={() => onnavigate("providers")} type="button" data-testid="nav-providers">
      {app.t("nav.providers")}
    </button>
  </div>
</aside>

<style>
  aside {
    display: flex;
    flex-direction: column;
    width: var(--sidebar-width);
    background: var(--bg-sunken);
    border-right: 1px solid var(--border);
  }

  .head {
    padding: 0.75rem;
    border-bottom: 1px solid var(--border);
  }

  .new {
    width: 100%;
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

  .remove {
    flex: none;
    font-size: 1rem;
    color: var(--text-faint);
    opacity: 0;
  }

  .row:hover .remove,
  .remove:focus-visible {
    opacity: 1;
  }

  .foot {
    display: flex;
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
</style>
