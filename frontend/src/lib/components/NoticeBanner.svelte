<!--
  A passing message that is not an error: a reply that finished while the reader was
  in another conversation, or another tab.

  Deliberately in-app as well as on the desktop. A system notification needs a secure
  context, which a plain-HTTP instance on a home network does not have (lib/notify.ts),
  so this is the channel that always works.
-->
<script lang="ts">
  import { app } from "$lib/stores/app.svelte";

  interface Props {
    text: string;
    chatId: string | null;
    ondismiss: () => void;
  }

  let { text, chatId, ondismiss }: Props = $props();

  function open(): void {
    if (chatId) location.hash = `#/chat/${chatId}`;
    ondismiss();
  }
</script>

<div class="banner" role="status">
  <span class="text">{text}</span>
  <div class="actions">
    {#if chatId}
      <button class="btn" onclick={open} type="button">{app.t("chat.openIt")}</button>
    {/if}
    <button class="btn btn-ghost" onclick={ondismiss} type="button">
      {app.t("error.dismiss")}
    </button>
  </div>
</div>

<style>
  .banner {
    display: flex;
    gap: 0.75rem;
    align-items: center;
    justify-content: space-between;
    width: 100%;
    max-width: var(--content-width);
    margin: 0.5rem auto;
    padding: 0.6rem 0.8rem;
    font-size: 0.86rem;
    background: var(--bg-raised);
    border: 1px solid var(--border);
    border-radius: var(--radius);
  }

  .text {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .actions {
    display: flex;
    flex-shrink: 0;
    gap: 0.4rem;
  }
</style>
