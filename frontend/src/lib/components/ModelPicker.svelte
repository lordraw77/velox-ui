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

  let menuOpen = $state(false);
  let query = $state("");

  let favoriteSet = $derived(new Set(app.favoriteRefs));
  let favorites = $derived(app.favoriteModels);
  /** The current model stays selectable in favorites view even when it is not starred. */
  let strayModel = $derived(
    app.modelView === "favorites" && app.model && !favoriteSet.has(app.model.model_ref)
      ? app.model
      : null,
  );

  let filtered = $derived.by(() => {
    const needle = query.trim().toLowerCase();
    return app.providers
      .map((group) => ({
        group,
        models: needle
          ? group.models.filter((entry) =>
              `${entry.display_name} ${entry.model_ref}`.toLowerCase().includes(needle),
            )
          : group.models,
      }))
      .filter((entry) => entry.models.length > 0);
  });

  function closeMenu(): void {
    menuOpen = false;
    query = "";
  }

  function onWindowKey(event: KeyboardEvent): void {
    if (event.key === "Escape" && menuOpen) closeMenu();
  }

  function onWindowPointer(event: PointerEvent): void {
    if (menuOpen && !(event.target as Element).closest?.(".favorites")) closeMenu();
  }
</script>

<svelte:window onkeydown={onWindowKey} onpointerdown={onWindowPointer} />

<div class="picker">
  {#if app.models.length > 0}
    <div class="view" role="group" aria-label={app.t("models.view")}>
      <button
        class="seg"
        class:on={app.modelView === "all"}
        aria-pressed={app.modelView === "all"}
        onclick={() => app.setModelView("all")}
        type="button"
        data-testid="models-view-all"
      >
        {app.t("models.viewAll")}
      </button>
      <button
        class="seg"
        class:on={app.modelView === "favorites"}
        aria-pressed={app.modelView === "favorites"}
        onclick={() => app.setModelView("favorites")}
        type="button"
        data-testid="models-view-favorites"
      >
        {app.t("models.viewFavorites")}
      </button>
    </div>

    <div class="favorites">
      <button
        class="btn btn-ghost btn-icon"
        onclick={() => (menuOpen ? closeMenu() : (menuOpen = true))}
        title={app.t("models.manageFavorites")}
        aria-label={app.t("models.manageFavorites")}
        aria-expanded={menuOpen}
        type="button"
        data-testid="favorites-menu"
      >
        ★
      </button>
      {#if menuOpen}
        <div class="popover" role="dialog" aria-label={app.t("models.manageFavorites")}>
          <input
            type="search"
            bind:value={query}
            placeholder={app.t("models.search")}
            aria-label={app.t("models.search")}
          />
          <div class="list">
            {#each filtered as { group, models } (group.provider_id)}
              <div class="group-name">{group.name}</div>
              {#each models as entry (entry.model_ref)}
                {@const starred = favoriteSet.has(entry.model_ref)}
                <button
                  class="item"
                  class:starred
                  onclick={() => app.toggleFavoriteModel(entry.model_ref)}
                  aria-pressed={starred}
                  title={app.t(starred ? "models.unstar" : "models.star")}
                  type="button"
                >
                  <span class="star">{starred ? "★" : "☆"}</span>
                  <span class="name">{entry.display_name}</span>
                </button>
              {/each}
            {:else}
              <p class="hint">{app.t("models.noMatches")}</p>
            {/each}
          </div>
        </div>
      {/if}
    </div>
  {/if}

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
      {#if favorites.length > 0}
        <optgroup label={`★ ${app.t("models.favorites")}`}>
          {#each favorites as entry (entry.model_ref)}
            <option value={entry.model_ref}>{entry.display_name}</option>
          {/each}
        </optgroup>
      {/if}
      {#if strayModel}
        <optgroup label={app.t("models.selected")}>
          <option value={strayModel.model_ref}>{strayModel.display_name}</option>
        </optgroup>
      {/if}
      {#each app.modelView === "all" ? app.providers : [] as group (group.provider_id)}
        {#if group.models.length > 0}
          <optgroup label={group.is_local ? `${group.provider_id} · ${app.t("models.local")}` : group.provider_id}>
            {#each group.models as entry (entry.model_ref)}
              <option value={entry.model_ref}>{entry.display_name}</option>
            {/each}
          </optgroup>
        {/if}
      {/each}
    </select>

    {#if app.modelView === "favorites" && favorites.length === 0}
      <span class="hint">{app.t("models.noFavorites")}</span>
    {/if}

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

  .view {
    display: inline-flex;
    overflow: hidden;
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
  }

  .seg {
    padding: 0.3rem 0.6rem;
    font-size: 0.8rem;
    color: var(--text-faint);
  }

  .seg.on {
    color: var(--text);
    background: var(--bg-active);
  }

  .favorites {
    position: relative;
  }

  .popover {
    position: absolute;
    top: calc(100% + 0.25rem);
    left: 0;
    z-index: 30;
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
    width: min(22rem, calc(100vw - 2rem));
    padding: 0.5rem;
    background: var(--bg-raised);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    box-shadow: 0 6px 18px rgb(0 0 0 / 0.15);
  }

  .popover input {
    padding: 0.4rem 0.5rem;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
  }

  .list {
    max-height: min(24rem, 55dvh);
    overflow-y: auto;
    overscroll-behavior: contain;
  }

  .group-name {
    padding: 0.4rem 0.25rem 0.15rem;
    font-size: 0.72rem;
    color: var(--text-faint);
    text-transform: uppercase;
  }

  .item {
    display: flex;
    gap: 0.5rem;
    align-items: center;
    width: 100%;
    padding: 0.45rem 0.35rem;
    text-align: left;
    border-radius: var(--radius-sm);
  }

  .item:hover {
    background: var(--bg-hover);
  }

  .star {
    flex: none;
    color: var(--text-faint);
  }

  .item.starred .star {
    color: var(--accent);
  }

  .name {
    min-width: 0;
    overflow: hidden;
    font-size: 0.86rem;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .empty {
    display: flex;
    flex-direction: column;
    max-width: 32rem;
    font-size: 0.82rem;
  }
</style>
