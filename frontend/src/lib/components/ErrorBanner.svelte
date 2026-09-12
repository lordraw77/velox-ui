<!--
  Typed errors, rendered as sentences.

  The server returns a closed set of codes precisely so the interface can say something
  useful instead of echoing a message from a backend. `backend_offline` becomes "is it
  running?" rather than a connection-refused trace, and `model_loading` says to wait
  rather than offering a retry that would make things worse (ADR-0008).
-->
<script lang="ts">
  import type { ApiError } from "$lib/api/types";
  import { app } from "$lib/stores/app.svelte";

  interface Props {
    error: ApiError;
    ondismiss: () => void;
    onretry?: () => void;
  }

  let { error, ondismiss, onretry }: Props = $props();

  const EXPLANATIONS: Record<string, string> = {
    backend_offline: "error.backendOffline",
    model_loading: "error.modelLoading",
    out_of_memory: "error.outOfMemory",
    context_overflow: "error.contextOverflow",
  };

  let explanation = $derived(EXPLANATIONS[error.code] ? app.t(EXPLANATIONS[error.code]!) : null);
  // A retry is offered only when the server said one could work. Retrying a loading
  // model asks a busy host to load the same weights twice.
  let canRetry = $derived(Boolean(onretry) && error.retryable);
</script>

<div class="banner" role="alert">
  <div class="text">
    <strong>{explanation ?? error.message}</strong>
    {#if explanation}<span class="detail">{error.message}</span>{/if}
    {#if error.provider}<span class="detail">{error.provider}</span>{/if}
  </div>
  <div class="actions">
    {#if canRetry}
      <button class="btn" onclick={onretry} type="button">{app.t("error.retry")}</button>
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
    align-items: flex-start;
    justify-content: space-between;
    width: 100%;
    max-width: var(--content-width);
    margin: 0.5rem auto;
    padding: 0.6rem 0.8rem;
    font-size: 0.86rem;
    background: var(--danger-soft);
    border: 1px solid color-mix(in srgb, var(--danger) 35%, transparent);
    border-radius: var(--radius);
  }

  .text {
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
    min-width: 0;
  }

  strong {
    color: var(--danger);
    font-weight: 600;
  }

  .detail {
    font-size: 0.78rem;
    color: var(--text-muted);
    overflow-wrap: anywhere;
  }

  .actions {
    display: flex;
    flex: none;
    gap: 0.35rem;
  }
</style>
