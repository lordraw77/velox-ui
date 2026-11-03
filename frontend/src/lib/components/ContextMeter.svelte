<!--
  How full the model's window is, in one line above the composer.

  The figure is an estimate (services/context.py), so it is shown as one: a thin bar and
  rounded counts, never a precise-looking number. With no window known there is no limit
  to be near, and the bar gives way to a plain count.
-->
<script lang="ts">
  import { formatTokens } from "$lib/format";
  import { app } from "$lib/stores/app.svelte";
  import { conversation } from "$lib/stores/conversation.svelte";

  // The window belongs to the model, so picking another one measures again.
  $effect(() => {
    void app.modelRef;
    void conversation.refreshContext();
  });

  let usage = $derived(conversation.context);
  let fraction = $derived(usage?.window ? Math.min(1, usage.used / usage.window) : 0);
  let level = $derived(fraction >= 0.9 ? "full" : fraction >= 0.7 ? "high" : "ok");
</script>

{#if usage}
  <button
    class="meter {level}"
    onclick={() => (app.contextOpen = !app.contextOpen)}
    aria-pressed={app.contextOpen}
    title={app.t("context.title")}
    type="button"
    data-testid="context-meter"
  >
    {#if usage.window}
      <span class="bar" aria-hidden="true"><span class="fill" style:width="{fraction * 100}%"></span></span>
      <span>{formatTokens(usage.used)} / {formatTokens(usage.window)}</span>
    {:else}
      <span>{app.t("context.tokens", { count: formatTokens(usage.used) })}</span>
    {/if}
    {#if usage.messages_dropped > 0}
      <span class="note">{app.t("context.dropped", { count: usage.messages_dropped })}</span>
    {:else if usage.messages_summarized > 0}
      <span class="note">{app.t("context.summarized", { count: usage.messages_summarized })}</span>
    {/if}
  </button>
{/if}

<style>
  .meter {
    display: inline-flex;
    gap: 0.5rem;
    align-items: center;
    padding: 0.15rem 0;
    font-size: 0.75rem;
    color: var(--text-muted);
    background: none;
    border: 0;
    cursor: pointer;
  }

  .bar {
    width: 4rem;
    height: 0.3rem;
    overflow: hidden;
    background: var(--border);
    border-radius: 999px;
  }

  .fill {
    display: block;
    height: 100%;
    background: var(--ok);
  }

  .high .fill {
    background: var(--warn);
  }

  .full .fill {
    background: var(--danger);
  }

  .full,
  .high {
    color: var(--warn);
  }

  .note {
    color: var(--text-faint);
  }
</style>
