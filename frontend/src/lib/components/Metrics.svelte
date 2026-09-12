<!--
  Per-reply speed and cost.

  These are shown by default rather than hidden behind a setting, because on
  self-hosted hardware they are the answer to the question people actually have: is
  this model fast enough on this machine? Everything here comes from the backend's own
  counters (`eval_count`, `eval_duration`, `prompt_eval_*`); nothing is inferred.
-->
<script lang="ts">
  import type { Timings } from "$lib/api/types";
  import { app } from "$lib/stores/app.svelte";

  interface Props {
    timings: Timings | null;
    tokensIn: number | null;
    tokensOut: number | null;
    costMicros: number | null;
  }

  let { timings, tokensIn, tokensOut, costMicros }: Props = $props();

  function ms(value: number | null | undefined): string {
    if (value === null || value === undefined) return "";
    return value >= 1000 ? `${(value / 1000).toFixed(2)}s` : `${Math.round(value)}ms`;
  }

  /** Micro-cents to something a person can read, or "free" for a local model. */
  function cost(micros: number | null): string | null {
    if (micros === null) return null;
    if (micros === 0) return app.t("metrics.free");
    const cents = micros / 1_000_000;
    return cents < 1 ? `<$0.01` : `$${(cents / 100).toFixed(4)}`;
  }

  let rate = $derived(timings?.tok_per_s ?? null);
  let costLabel = $derived(cost(costMicros));
</script>

<div class="metrics">
  {#if rate !== null}
    <span title={app.t("metrics.generation", { value: ms(timings?.eval_ms) })}>
      {app.t("metrics.tokensPerSecond", { value: rate.toFixed(1) })}
    </span>
  {/if}
  {#if timings?.ttft_ms != null}
    <span>{app.t("metrics.timeToFirstToken", { value: ms(timings.ttft_ms) })}</span>
  {/if}
  {#if tokensIn !== null && tokensOut !== null}
    <span title={app.t("metrics.promptEval", { value: ms(timings?.prompt_eval_ms) })}>
      {app.t("metrics.tokens", { input: tokensIn, output: tokensOut })}
    </span>
  {/if}
  {#if costLabel}
    <span class:free={costMicros === 0}>{costLabel}</span>
  {/if}
</div>

<style>
  .metrics {
    display: flex;
    flex-wrap: wrap;
    gap: 0.7rem;
    margin-top: 0.35rem;
    font-family: var(--font-mono);
    font-size: 0.72rem;
    color: var(--text-faint);
  }

  .free {
    color: var(--ok);
  }
</style>
