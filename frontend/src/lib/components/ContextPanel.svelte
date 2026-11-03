<!--
  The context of the conversation on screen, and its memory.

  A long chat outgrows its model's window. Compacting asks the model to write notes of
  what the older messages established; later turns send those notes in place of the
  messages, which stay in the conversation. The notes are plain text and can be edited
  or forgotten, so what the model keeps is never out of the reader's hands.
-->
<script lang="ts">
  import { formatTokens } from "$lib/format";
  import { app } from "$lib/stores/app.svelte";
  import { conversation } from "$lib/stores/conversation.svelte";

  let text = $state(conversation.memory);
  let saving = $state(false);
  let omitted = $state(0);

  // Follow the conversation: opening another chat, or a compaction, replaces the text.
  $effect(() => {
    text = conversation.memory;
  });

  let dirty = $derived(text !== conversation.memory);
  let usage = $derived(conversation.context);
  let canCompact = $derived(
    conversation.id !== null && app.modelRef !== null && !conversation.streaming && !conversation.compacting,
  );

  async function compact(): Promise<void> {
    const result = await conversation.compact();
    omitted = result?.omitted ?? 0;
  }

  async function save(value: string): Promise<void> {
    saving = true;
    try {
      await conversation.setMemory(value);
    } catch {
      // Already reported by the store.
    } finally {
      saving = false;
    }
  }
</script>

<section class="context-panel" data-testid="context-panel">
  <div class="head">
    <div>
      <strong>{app.t("context.title")}</strong>
      <p class="hint">{app.t("context.intro")}</p>
    </div>
    <button class="btn btn-ghost btn-icon" onclick={() => (app.contextOpen = false)} aria-label={app.t("context.close")} title={app.t("context.close")} type="button">×</button>
  </div>

  {#if usage}
    <dl class="figures" data-testid="context-figures">
      <div><dt>{app.t("context.used")}</dt><dd>{formatTokens(usage.used)}{usage.window ? ` / ${formatTokens(usage.window)}` : ""}</dd></div>
      <div><dt>{app.t("context.memory")}</dt><dd>{formatTokens(usage.memory_tokens)}</dd></div>
      <div><dt>{app.t("context.inContext")}</dt><dd>{usage.messages_in_context} / {usage.messages_total}</dd></div>
      <div><dt>{app.t("context.summarizedLabel")}</dt><dd>{usage.messages_summarized}</dd></div>
      {#if usage.messages_dropped > 0}
        <div class="warn"><dt>{app.t("context.droppedLabel")}</dt><dd>{usage.messages_dropped}</dd></div>
      {/if}
    </dl>
    <p class="hint">{app.t("context.estimate")}</p>
  {/if}

  <label class="label" for="memory-input">{app.t("context.memoryTitle")}</label>
  <textarea
    id="memory-input"
    rows="6"
    bind:value={text}
    placeholder={app.t("context.placeholder")}
    disabled={conversation.id === null}
    data-testid="memory-input"
  ></textarea>

  {#if omitted > 0}
    <p class="warn-text">{app.t("context.omitted", { count: omitted })}</p>
  {/if}

  <div class="actions">
    <button class="btn" onclick={() => save("")} disabled={saving || (!text && !conversation.memory)} type="button" data-testid="memory-forget">
      {app.t("context.forget")}
    </button>
    <button class="btn" onclick={() => save(text)} disabled={saving || !dirty} type="button" data-testid="memory-save">
      {app.t("context.save")}
    </button>
    <button class="btn btn-primary" onclick={compact} disabled={!canCompact} type="button" data-testid="memory-compact">
      {conversation.compacting ? app.t("context.compacting") : app.t("context.compact")}
    </button>
  </div>
</section>

<style>
  .context-panel {
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

  .figures {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem 1.5rem;
    margin: 0.5rem 0 0.25rem;
  }

  .figures div {
    display: flex;
    gap: 0.4rem;
    font-size: 0.82rem;
  }

  .figures dt {
    color: var(--text-muted);
  }

  .figures dd {
    margin: 0;
    font-variant-numeric: tabular-nums;
  }

  .figures .warn dd,
  .warn-text {
    color: var(--warn);
  }

  .warn-text {
    margin: 0.4rem 0 0;
    font-size: 0.8rem;
  }

  .label {
    display: block;
    margin: 0.75rem 0 0.25rem;
    font-size: 0.82rem;
    font-weight: 600;
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

  .actions {
    display: flex;
    gap: 0.5rem;
    align-items: center;
    justify-content: flex-end;
    margin-top: 0.75rem;
  }
</style>
