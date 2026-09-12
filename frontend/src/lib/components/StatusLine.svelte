<!--
  What the backend is doing right now.

  "Loading model" is a different sentence from "generating", and both are different
  from nothing happening (ADR-0008). On a cold start a local model can take a minute to
  read from disk, and a spinner that says nothing for a minute is indistinguishable
  from a hang — so the loading state says what is happening and why it is slow.
-->
<script lang="ts">
  import type { StreamPhase } from "$lib/api/types";
  import { app } from "$lib/stores/app.svelte";

  interface Props {
    phase: StreamPhase | null;
  }

  let { phase }: Props = $props();

  const LABELS: Record<StreamPhase, string> = {
    loading_model: "status.loadingModel",
    prompt_eval: "status.promptEval",
    generating: "status.generating",
    tool_wait: "status.toolWait",
  };
</script>

{#if phase}
  <div class="status" class:loading={phase === "loading_model"}>
    <span class="spinner"></span>
    <span>{app.t(LABELS[phase])}</span>
    {#if phase === "loading_model"}
      <span class="why">{app.t("status.loadingModelHint")}</span>
    {/if}
  </div>
{/if}

<style>
  .status {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    align-items: center;
    padding: 0.4rem 0;
    font-size: 0.82rem;
    color: var(--text-muted);
  }

  .loading {
    color: var(--warn);
  }

  .why {
    flex-basis: 100%;
    font-size: 0.75rem;
    color: var(--text-faint);
  }
</style>
