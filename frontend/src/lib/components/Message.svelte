<!--
  One turn of the conversation.

  The element reports its own height upward so the virtual list can replace its
  estimate with a real measurement. A `ResizeObserver` rather than a one-off read: a
  streaming reply grows continuously, and an image or a code block can change height
  after the message first appears.
-->
<script lang="ts">
  import type { RenderedMessage } from "$lib/stores/conversation.svelte";
  import { app } from "$lib/stores/app.svelte";
  import Markdown from "./Markdown.svelte";
  import Metrics from "./Metrics.svelte";

  interface Props {
    message: RenderedMessage;
    onmeasure: (height: number) => void;
  }

  let { message, onmeasure }: Props = $props();
  let element: HTMLElement | undefined = $state();

  $effect(() => {
    if (!element) return;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) onmeasure(entry.target.getBoundingClientRect().height);
    });
    observer.observe(element);
    return () => observer.disconnect();
  });

  let isUser = $derived(message.role === "user");
  let hasError = $derived(message.status === "error");
</script>

<article bind:this={element} class="message" class:user={isUser} data-role={message.role}>
  <header>
    <span class="who">{isUser ? app.t("chat.you") : app.t("chat.assistant")}</span>
    {#if message.model_ref && !isUser}
      <span class="model">{message.model_ref}</span>
    {/if}
    {#if message.sibling_count > 1}
      <span class="badge">
        {app.t("chat.branch", {
          current: message.sibling_index + 1,
          total: message.sibling_count,
        })}
      </span>
    {/if}
    {#if message.status === "stopped"}
      <span class="badge">{app.t("status.stopped")}</span>
    {/if}
  </header>

  {#if message.reasoning}
    <details class="reasoning">
      <summary>{app.t("chat.reasoning")}</summary>
      <pre>{message.reasoning}</pre>
    </details>
  {/if}

  <div class="body" class:error={hasError}>
    {#if isUser}
      <!-- User text is never parsed as markdown: it is shown exactly as typed. -->
      <p class="plain">{message.content}</p>
    {:else}
      <Markdown html={message.html} tail={message.tail} />
    {/if}
  </div>

  {#if !isUser && message.citations.length > 0}
    <details class="citations">
      <summary>{app.t("chat.citations", { count: message.citations.length })}</summary>
      <ul>
        {#each message.citations as citation (citation.chunk_id)}
          <li class="mono">{citation.document_id}</li>
        {/each}
      </ul>
    </details>
  {/if}

  {#if !isUser && message.timings}
    <Metrics
      timings={message.timings}
      tokensIn={message.tokens_in}
      tokensOut={message.tokens_out}
      costMicros={message.cost_micros}
    />
  {/if}
</article>

<style>
  .message {
    padding: 0.9rem 0;
    border-bottom: 1px solid var(--border);
  }

  header {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    align-items: center;
    margin-bottom: 0.35rem;
  }

  .who {
    font-size: 0.78rem;
    font-weight: 600;
    color: var(--text-muted);
    letter-spacing: 0.01em;
  }

  .user .who {
    color: var(--accent);
  }

  .model {
    font-family: var(--font-mono);
    font-size: 0.7rem;
    color: var(--text-faint);
  }

  .plain {
    margin: 0;
    white-space: pre-wrap;
  }

  .body.error {
    color: var(--danger);
  }

  .reasoning {
    margin-bottom: 0.5rem;
    font-size: 0.85rem;
  }

  .reasoning summary {
    color: var(--text-muted);
    cursor: pointer;
  }

  .reasoning pre {
    margin: 0.4rem 0 0;
    overflow-x: auto;
    padding: 0.6rem 0.75rem;
    color: var(--text-muted);
    white-space: pre-wrap;
    background: var(--bg-sunken);
    border-radius: var(--radius-sm);
  }

  .citations {
    margin-top: 0.5rem;
    font-size: 0.85rem;
  }

  .citations summary {
    color: var(--text-muted);
    cursor: pointer;
  }

  .citations ul {
    margin: 0.4rem 0 0;
    padding-left: 1.2rem;
    color: var(--text-muted);
  }
</style>
