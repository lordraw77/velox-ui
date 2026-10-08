<!--
  System prompt of the conversation on screen.

  Bound to this chat alone: it is stored on the chat and sent with every turn, and a
  new conversation starts without one. Before the first message the text is a draft
  that is saved together with the chat.
-->
<script lang="ts">
  import { app } from "$lib/stores/app.svelte";
  import { conversation } from "$lib/stores/conversation.svelte";

  let text = $state(conversation.systemPrompt);
  let saving = $state(false);
  let saved = $state(false);

  // Follow the conversation: opening another chat, or starting a new one, replaces the text.
  $effect(() => {
    text = conversation.systemPrompt;
  });

  let dirty = $derived(text !== conversation.systemPrompt);

  async function save(value: string): Promise<void> {
    saving = true;
    try {
      await conversation.setSystemPrompt(value);
      text = conversation.systemPrompt;
      saved = true;
    } catch {
      // Already reported by the store.
    } finally {
      saving = false;
    }
  }
</script>

<section class="system-prompt" data-testid="system-prompt-panel">
  <div class="head">
    <div>
      <strong>{app.t("systemPrompt.title")}</strong>
      <span class="state" class:on={conversation.systemPrompt.trim() !== ""} data-testid="system-prompt-state">
        {conversation.systemPrompt.trim() ? app.t("systemPrompt.set") : app.t("systemPrompt.unset")}
      </span>
      <p class="hint">{app.t("systemPrompt.intro")}</p>
    </div>
    <button class="btn btn-ghost btn-icon" onclick={() => (app.systemPromptOpen = false)} aria-label={app.t("systemPrompt.close")} title={app.t("systemPrompt.close")} type="button">×</button>
  </div>

  <textarea
    rows="4"
    bind:value={text}
    oninput={() => (saved = false)}
    placeholder={app.t("systemPrompt.placeholder")}
    data-testid="system-prompt-input"
  ></textarea>

  <div class="actions">
    {#if saved && !dirty}<span class="ok">{app.t("systemPrompt.saved")}</span>{/if}
    <button class="btn" onclick={() => save("")} disabled={saving || (!text && !conversation.systemPrompt)} type="button">
      {app.t("systemPrompt.clear")}
    </button>
    <button class="btn btn-primary" onclick={() => save(text)} disabled={saving || !dirty} type="button" data-testid="system-prompt-save">
      {app.t("systemPrompt.save")}
    </button>
  </div>
</section>

<style>
  .system-prompt {
    max-height: 60dvh;
    overflow-y: auto;
    padding: 0.75rem 1rem 1rem;
    background: var(--bg-raised);
    border-bottom: 1px solid var(--border);
    box-shadow: var(--shadow);
  }

  .head {
    display: flex;
    justify-content: space-between;
    gap: 1rem;
  }

  .head p {
    margin: 0.2rem 0 0.4rem;
  }

  textarea {
    width: 100%;
    padding: 0.4rem 0.5rem;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    font-family: var(--font-mono);
    font-size: 0.85rem;
    resize: vertical;
  }

  .state {
    margin-left: 0.5rem;
    font-size: 0.78rem;
    color: var(--text-muted);
  }

  .state.on {
    color: var(--ok);
  }

  .actions {
    display: flex;
    gap: 0.5rem;
    align-items: center;
    justify-content: flex-end;
    margin-top: 0.75rem;
  }

  .ok {
    font-size: 0.8rem;
    color: var(--ok);
  }
</style>
