<!--
  One turn of the conversation.

  The element reports its own height upward so the virtual list can replace its
  estimate with a real measurement. A `ResizeObserver` rather than a one-off read: a
  streaming reply grows continuously, and an image or a code block can change height
  after the message first appears.
-->
<script lang="ts">
  import { conversation, type RenderedMessage } from "$lib/stores/conversation.svelte";
  import { api } from "$lib/api/client";
  import { app } from "$lib/stores/app.svelte";
  import Markdown from "./Markdown.svelte";
  import Metrics from "./Metrics.svelte";

  interface Props {
    message: RenderedMessage;
    onmeasure: (height: number) => void;
  }

  let { message, onmeasure }: Props = $props();
  let approving = $state(false);
  let speaking = $state(false);

  async function speak(): Promise<void> {
    if (speaking) return;
    speaking = true;
    try {
      const audio = await api.speak(message.content);
      const url = URL.createObjectURL(audio);
      const player = new Audio(url);
      player.addEventListener("ended", () => URL.revokeObjectURL(url), { once: true });
      await player.play();
    } catch (error) {
      app.report(error);
    } finally {
      speaking = false;
    }
  }

  async function respond(approved: boolean): Promise<void> {
    const call = message.pendingApproval;
    if (!call || approving) return;
    approving = true;
    try {
      await conversation.approveTool(message.id, call.id, approved);
    } finally {
      approving = false;
    }
  }

  let copied = $state(false);

  /** The reply's markdown source as the model wrote it, not the rendered HTML. */
  async function copyMarkdown(): Promise<void> {
    try {
      await navigator.clipboard.writeText(message.content);
      copied = true;
      setTimeout(() => (copied = false), 1500);
    } catch (error) {
      app.report(error);
    }
  }
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
    {#if !isUser && app.voiceEnabled && message.content}
      <button
        class="btn btn-ghost btn-icon speak"
        disabled={speaking}
        onclick={speak}
        title={app.t("chat.speak")}
        aria-label={app.t("chat.speak")}
        type="button"
        data-testid="speak"
      >
        {speaking ? "…" : "🔊"}
      </button>
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

  {#if !isUser && message.toolTrace.length > 0}
    <details class="tool-trace" open>
      <summary>{app.t("chat.toolCalls", { count: message.toolTrace.length })}</summary>
      <ul>
        {#each message.toolTrace as call (call.id)}
          <li>
            <div class="tool-name">
              <code>{call.name}</code>
              <span class="badge" class:danger={!call.ok}>
                {call.ok ? app.t("chat.toolOk") : app.t("chat.toolFailed")}
              </span>
            </div>
            <pre class="tool-args">{JSON.stringify(call.args)}</pre>
            <pre class="tool-result">{call.content}</pre>
          </li>
        {/each}
      </ul>
    </details>
  {/if}

  {#if !isUser && message.pendingApproval}
    <div class="approval card">
      <p>
        {app.t("chat.approvalRequired", { name: message.pendingApproval.name })}
      </p>
      <pre class="tool-args">{JSON.stringify(message.pendingApproval.args)}</pre>
      <div class="row-actions">
        <button class="btn btn-ghost" disabled={approving} onclick={() => respond(false)} type="button">
          {app.t("chat.reject")}
        </button>
        <button class="btn btn-primary" disabled={approving} onclick={() => respond(true)} type="button">
          {app.t("chat.approve")}
        </button>
      </div>
    </div>
  {/if}

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

  {#if !isUser && (message.timings || (message.content && !conversation.streaming))}
    <Metrics
      timings={message.timings}
      tokensIn={message.tokens_in}
      tokensOut={message.tokens_out}
      costMicros={message.cost_micros}
    >
      {#if message.content && !conversation.streaming}
        <span class="actions">
          <button
            class="icon"
            onclick={() => conversation.regenerate(message.id)}
            title={app.t("chat.regenerate")}
            aria-label={app.t("chat.regenerate")}
            type="button"
            data-testid="regenerate"
          >
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M21 12a9 9 0 1 1-3-6.7" />
              <path d="M21 3v6h-6" />
            </svg>
          </button>
          <button
            class="icon"
            onclick={copyMarkdown}
            title={copied ? app.t("chat.copiedMarkdown") : app.t("chat.copyMarkdown")}
            aria-label={app.t("chat.copyMarkdown")}
            type="button"
            data-testid="copy-markdown"
          >
            {#if copied}
              <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="M20 6 9 17l-5-5" />
              </svg>
            {:else}
              <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <rect x="9" y="9" width="12" height="12" rx="2" />
                <path d="M5 15V5a2 2 0 0 1 2-2h10" />
              </svg>
            {/if}
          </button>
        </span>
      {/if}
    </Metrics>
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

  .tool-trace {
    margin-top: 0.5rem;
    font-size: 0.85rem;
  }

  .tool-trace summary {
    color: var(--text-muted);
    cursor: pointer;
  }

  .tool-trace ul {
    margin: 0.4rem 0 0;
    padding: 0;
    list-style: none;
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }

  .tool-name {
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }

  .tool-args,
  .tool-result {
    margin: 0.2rem 0 0;
    padding: 0.5rem 0.6rem;
    overflow-x: auto;
    white-space: pre-wrap;
    color: var(--text-muted);
    background: var(--bg-sunken);
    border-radius: var(--radius-sm);
  }

  .badge.danger {
    color: var(--danger);
  }

  .speak {
    margin-left: auto;
    font-size: 0.9rem;
  }

  .actions {
    display: inline-flex;
    gap: 0.15rem;
  }

  .icon {
    display: inline-flex;
    padding: 0.2rem;
    color: inherit;
    cursor: pointer;
    background: none;
    border: 0;
    border-radius: var(--radius-sm);
  }

  .icon:hover {
    color: var(--text);
    background: var(--bg-sunken);
  }

  .approval {
    margin-top: 0.5rem;
  }
</style>
