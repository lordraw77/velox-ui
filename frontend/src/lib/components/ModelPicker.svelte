<!--
  The model selector.

  Local models are listed first and their provider is labelled, because on a
  self-hosted instance they are the default rather than the fallback (ADR-0008). When
  no backend is reachable the empty state says what to do about it instead of leaving
  an empty dropdown, since "no models" almost always means "Ollama is not running".
-->
<script lang="ts">
  import { app } from "$lib/stores/app.svelte";

  function label(value: number | null): string {
    if (value === null) return "";
    return value >= 1000 ? `${Math.round(value / 1024)}k` : String(value);
  }
</script>

<div class="picker">
  {#if app.models.length === 0}
    <div class="empty">
      <strong>{app.t("models.none")}</strong>
      <span class="hint">{app.t("models.noneHint")}</span>
    </div>
  {:else}
    <select
      value={app.modelRef}
      onchange={(event) => app.selectModel((event.currentTarget as HTMLSelectElement).value)}
      aria-label={app.t("models.picker")}
      data-testid="model-select"
    >
      {#each app.providers as group (group.provider_id)}
        {#if group.models.length > 0}
          <optgroup label={group.is_local ? `${group.provider_id} · ${app.t("models.local")}` : group.provider_id}>
            {#each group.models as entry (entry.model_ref)}
              <option value={entry.model_ref}>{entry.display_name}</option>
            {/each}
          </optgroup>
        {/if}
      {/each}
    </select>

    {#if app.model}
      <div class="facts">
        {#if app.model.capabilities.context_window}
          <span class="badge">
            {app.t("models.contextWindow", { value: label(app.model.capabilities.context_window) })}
          </span>
        {/if}
        {#if app.model.capabilities.quantization}
          <span class="badge">{app.model.capabilities.quantization}</span>
        {/if}
        {#if app.model.capabilities.vision}
          <span class="badge">{app.t("models.vision")}</span>
        {/if}
        {#if app.model.capabilities.tools !== "none"}
          <span class="badge">{app.t("models.tools")}</span>
        {/if}
        {#if app.model.capabilities.reasoning}
          <span class="badge">{app.t("models.reasoning")}</span>
        {/if}
      </div>
    {/if}
  {/if}

  <button
    class="btn btn-ghost btn-icon"
    onclick={() => app.loadModels(true)}
    disabled={app.modelsLoading}
    title={app.t("models.refresh")}
    aria-label={app.t("models.refresh")}
    type="button"
  >
    {#if app.modelsLoading}<span class="spinner"></span>{:else}↻{/if}
  </button>
</div>

<style>
  .picker {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    align-items: center;
    min-width: 0;
  }

  select {
    max-width: 22rem;
    padding: 0.3rem 0.5rem;
    background: var(--bg-raised);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
  }

  .facts {
    display: flex;
    flex-wrap: wrap;
    gap: 0.3rem;
  }

  .empty {
    display: flex;
    flex-direction: column;
    max-width: 32rem;
    font-size: 0.82rem;
  }
</style>
