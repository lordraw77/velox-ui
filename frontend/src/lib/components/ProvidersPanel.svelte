<!--
  Inference backends: what is configured, where it came from, and adding more.

  Backends from `velox.toml` or the environment are listed but not editable here, so a
  deployment's configuration is never silently overridden from the browser. A stored
  API key is shown only as its masked hint; the field for changing it starts empty.

  "Test connection" identifies what actually answers at an address before anything is
  saved, and offers to switch preset when the address turns out to be a different kind
  of backend than the one chosen — pasting an Ollama URL into the LM Studio form is an
  easy mistake.

  Loaded lazily: it is not part of the initial bundle.
-->
<script lang="ts">
  import { onMount } from "svelte";
  import { api } from "$lib/api/client";
  import type { DiscoveredBackend, Preset, ProbeResult, ProviderInfo, ProviderOrigin } from "$lib/api/types";
  import { app } from "$lib/stores/app.svelte";

  let providers = $state<ProviderInfo[]>([]);
  let presets = $state<Preset[]>([]);
  let loading = $state(true);

  let draft = $state({ preset: "ollama", base_url: "", id: "", name: "", api_key: "" });
  let probe = $state<ProbeResult | null>(null);
  let busy = $state(false);

  let editing = $state<string | null>(null);
  let edit = $state({ name: "", base_url: "", api_key: "" });
  let discovered = $state<DiscoveredBackend[] | null>(null);

  let preset = $derived(presets.find((entry) => entry.key === draft.preset) ?? null);

  const ORIGIN: Record<ProviderOrigin, string> = {
    config: "providers.originConfig",
    autodiscovered: "providers.originAutodiscovered",
    ui: "providers.originUi",
  };

  const HEALTH: Record<string, string> = {
    up: "providers.online",
    down: "providers.offline",
    degraded: "providers.degraded",
    unknown: "providers.unknown",
  };

  onMount(async () => {
    try {
      const [listing, catalogue] = await Promise.all([api.providers(), api.presets()]);
      providers = listing.providers;
      presets = catalogue.presets;
      choosePreset(draft.preset);
    } catch (error) {
      app.report(error);
    } finally {
      loading = false;
    }
  });

  async function reload(): Promise<void> {
    try {
      providers = (await api.providers()).providers;
      await app.loadModels(true);
    } catch (error) {
      app.report(error);
    }
  }

  function choosePreset(key: string): void {
    draft.preset = key;
    draft.base_url = presets.find((entry) => entry.key === key)?.base_url ?? "";
    probe = null;
  }

  async function run(task: () => Promise<void>): Promise<void> {
    busy = true;
    try {
      await task();
    } catch (error) {
      app.report(error);
    } finally {
      busy = false;
    }
  }

  function test(): void {
    void run(async () => {
      probe = null;
      probe = await api.probe(draft.base_url, draft.api_key);
    });
  }

  function adoptProbe(): void {
    if (!probe?.preset || !probe.base_url) return;
    const address = probe.base_url;
    if (probe.kind !== preset?.kind) choosePreset(probe.preset);
    draft.base_url = address;
  }

  function add(): void {
    void run(async () => {
      await api.createProvider({ ...draft });
      draft = { preset: draft.preset, base_url: preset?.base_url ?? "", id: "", name: "", api_key: "" };
      probe = null;
      await reload();
    });
  }

  function startEdit(provider: ProviderInfo): void {
    editing = provider.provider_id;
    edit = { name: provider.name, base_url: provider.base_url ?? "", api_key: "" };
  }

  function saveEdit(): void {
    const id = editing;
    if (!id) return;
    void run(async () => {
      await api.updateProvider(id, {
        name: edit.name,
        base_url: edit.base_url,
        ...(edit.api_key ? { api_key: edit.api_key } : {}),
      });
      editing = null;
      await reload();
    });
  }

  function clearKey(provider: ProviderInfo): void {
    void run(async () => {
      await api.updateProvider(provider.provider_id, { api_key: "" });
      await reload();
    });
  }

  function remove(provider: ProviderInfo): void {
    if (!confirm(app.t("providers.removeConfirm", { name: provider.name }))) return;
    void run(async () => {
      await api.deleteProvider(provider.provider_id);
      await reload();
    });
  }

  function discover(): void {
    void run(async () => {
      discovered = (await api.autodiscover()).found;
      await reload();
    });
  }

  function presetLabel(key: string | null): string {
    return presets.find((entry) => entry.key === key)?.label ?? key ?? "";
  }
</script>

<div class="panel" data-testid="providers-panel">
  <div class="panel-inner">
    <div class="title-row">
      <h1>{app.t("providers.manage")}</h1>
      <a class="btn btn-ghost" href="#/">{app.t("nav.back")}</a>
    </div>
    <p class="hint">{app.t("providers.intro")}</p>

    {#if loading}
      <span class="spinner"></span>
    {:else}
      <section class="card">
        <div class="table-wrap">
          <table class="data">
            <tbody>
              {#each providers as provider (provider.provider_id)}
                <tr>
                  <td>
                    <strong>{provider.name}</strong>
                    <div class="mono hint">{provider.provider_id}</div>
                  </td>
                  <td>
                    <div class="chips">
                      <span class="badge">{presetLabel(provider.preset)}</span>
                      <span class="badge">{app.t(ORIGIN[provider.origin])}</span>
                      {#if provider.is_local}<span class="badge badge-local">{app.t("models.local")}</span>{/if}
                    </div>
                  </td>
                  <td>
                    {#if editing === provider.provider_id}
                      <div class="edit">
                        <input bind:value={edit.name} aria-label={app.t("providers.name")} />
                        <input bind:value={edit.base_url} aria-label={app.t("providers.baseUrl")} />
                        <input type="password" bind:value={edit.api_key} placeholder={app.t("providers.apiKeyKeep")} aria-label={app.t("providers.apiKey")} autocomplete="off" />
                      </div>
                    {:else}
                      <div class="mono">{provider.base_url}</div>
                      {#if provider.credential_hint}
                        <div class="hint">{app.t("providers.key", { hint: provider.credential_hint })}</div>
                      {/if}
                    {/if}
                  </td>
                  <td>
                    {#if provider.health}
                      <span class="badge" class:badge-local={provider.health.state === "up"} class:badge-offline={provider.health.state === "down"} title={provider.health.detail ?? ""}>
                        {app.t(HEALTH[provider.health.state] ?? "providers.unknown")}
                        {#if provider.health.latency_ms !== null}· {Math.round(provider.health.latency_ms)} ms{/if}
                      </span>
                    {/if}
                  </td>
                  <td class="row-actions">
                    {#if app.isAdmin && provider.editable}
                      {#if editing === provider.provider_id}
                        <button class="btn btn-ghost" onclick={() => (editing = null)} type="button">{app.t("providers.cancel")}</button>
                        <button class="btn btn-primary" onclick={saveEdit} disabled={busy} type="button">{app.t("providers.update")}</button>
                      {:else}
                        <button class="btn btn-ghost" onclick={() => startEdit(provider)} type="button">{app.t("providers.edit")}</button>
                        {#if provider.credential_hint}
                          <button class="btn btn-ghost" onclick={() => clearKey(provider)} type="button">{app.t("providers.clearKey")}</button>
                        {/if}
                        <button class="btn btn-ghost danger" onclick={() => remove(provider)} type="button">{app.t("providers.remove")}</button>
                      {/if}
                    {/if}
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      </section>

      {#if !app.isAdmin}
        <p class="hint">{app.t("providers.readOnly")}</p>
      {:else}
        <section class="card">
          <div class="title-row">
            <h2>{app.t("providers.add")}</h2>
            <button class="btn" onclick={discover} disabled={busy} type="button">{app.t("providers.discover")}</button>
          </div>
          {#if discovered !== null}
            <p class="hint">
              {discovered.length === 0
                ? app.t("providers.discoveredNone")
                : app.t("providers.discovered", {
                    count: discovered.length,
                    list: discovered.map((entry) => `${entry.provider_id} (${entry.base_url})`).join(", "),
                  })}
            </p>
          {/if}

          <form class="add" onsubmit={(event) => { event.preventDefault(); add(); }}>
            <div class="field">
              <label for="provider-preset">{app.t("providers.preset")}</label>
              <select id="provider-preset" value={draft.preset} onchange={(event) => choosePreset(event.currentTarget.value)}>
                {#each presets as entry (entry.key)}
                  <option value={entry.key}>{entry.label}</option>
                {/each}
              </select>
              {#if preset?.docs_url}
                <a class="hint" href={preset.docs_url} target="_blank" rel="noreferrer noopener">{app.t("providers.docs")}</a>
              {/if}
            </div>
            <div class="field wide">
              <label for="provider-url">{app.t("providers.baseUrl")}</label>
              <input id="provider-url" bind:value={draft.base_url} placeholder="http://192.168.1.10:11434" required />
            </div>
            {#if preset?.auth !== "none"}
              <div class="field">
                <label for="provider-key">{app.t("providers.apiKey")}</label>
                <input id="provider-key" type="password" bind:value={draft.api_key} autocomplete="off" required={preset?.auth === "required"} />
                <span class="hint">{app.t("providers.apiKeyHint")}</span>
              </div>
            {/if}
            <div class="field">
              <label for="provider-id">{app.t("providers.id")}</label>
              <input id="provider-id" bind:value={draft.id} placeholder={draft.preset.replace("_", "-")} pattern="[a-z0-9][a-z0-9\-]{'{0,39}'}" />
              <span class="hint">{app.t("providers.idHint")}</span>
            </div>
            <div class="field">
              <label for="provider-name">{app.t("providers.name")}</label>
              <input id="provider-name" bind:value={draft.name} placeholder={preset?.label ?? ""} />
            </div>

            {#if probe}
              <div class="probe" class:failed={!probe.reachable}>
                {#if probe.reachable && probe.models !== null}
                  {app.t("providers.probeOk", { kind: presetLabel(probe.preset), models: probe.models, latency: Math.round(probe.latency_ms ?? 0) })}
                {:else}
                  {app.t("providers.probeFailed", { detail: probe.detail ?? "" })}
                {/if}
                {#if probe.reachable && probe.base_url && (probe.base_url !== draft.base_url || probe.kind !== preset?.kind)}
                  <button class="btn btn-ghost" onclick={adoptProbe} type="button">{app.t("providers.probeUse")}</button>
                {/if}
              </div>
            {/if}

            <div class="row-actions full">
              <button class="btn" onclick={test} disabled={busy || !draft.base_url} type="button" data-testid="provider-test">
                {app.t("providers.test")}
              </button>
              <button class="btn btn-primary" disabled={busy || !draft.base_url} type="submit">{app.t("providers.save")}</button>
            </div>
          </form>
        </section>
      {/if}
    {/if}
  </div>
</div>

<style>
  .title-row {
    display: flex;
    gap: 0.5rem;
    align-items: center;
    justify-content: space-between;
  }

  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 0.25rem;
  }

  .edit {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
  }

  .edit input {
    padding: 0.3rem 0.45rem;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
  }

  .add {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(14rem, 1fr));
    gap: 0.75rem;
  }

  .wide {
    grid-column: span 2;
  }

  .full,
  .probe {
    grid-column: 1 / -1;
  }

  .probe {
    display: flex;
    gap: 0.5rem;
    align-items: center;
    padding: 0.45rem 0.6rem;
    font-size: 0.84rem;
    color: var(--ok);
    background: var(--bg-sunken);
    border-radius: var(--radius-sm);
  }

  .probe.failed {
    color: var(--danger);
  }

  .danger {
    color: var(--danger);
  }

  @media (width <= 720px) {
    .wide {
      grid-column: auto;
    }
  }
</style>
