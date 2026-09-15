<!--
  Local model management: what is loaded, what is installed, downloads and creation.

  Every control is enabled from the backend's `features` list, never from its name, so
  llama.cpp shows what it has loaded while Ollama additionally offers downloads.
  Changing anything is for administrators; everyone else sees the same page read-only.

  Nothing here polls the network. Lists refresh when the page opens, after an action,
  and when a followed job finishes; the one-second timer only redraws the "unloads in"
  countdown from a timestamp already received.

  Loaded lazily: it is not part of the initial bundle.
-->
<script lang="ts">
  import { api } from "$lib/api/client";
  import type { InstalledModel, JobSnapshot, ModelDetails, ProviderFeature, RunningModel } from "$lib/api/types";
  import { etaSeconds, formatBytes, formatDate, formatDuration, formatRate, gpuShare, progressFraction } from "$lib/format";
  import { app } from "$lib/stores/app.svelte";
  import { jobs } from "$lib/stores/jobs.svelte";

  let managed = $derived(
    app.providers.filter((group) => group.features.includes("running") || group.features.includes("pull")),
  );
  let selectedId = $state<string | null>(null);
  let group = $derived(managed.find((entry) => entry.provider_id === selectedId) ?? managed[0] ?? null);

  let installed = $state<InstalledModel[]>([]);
  let running = $state<RunningModel[]>([]);
  let loading = $state(false);
  let details = $state<Record<string, ModelDetails>>({});
  let pullName = $state("");
  let createName = $state("");
  let modelfile = $state("");
  let now = $state(Date.now());

  const JOB_STATE: Record<JobSnapshot["state"], string> = {
    running: "jobs.running",
    succeeded: "jobs.succeeded",
    failed: "jobs.failed",
    cancelled: "jobs.cancelled",
  };

  const YEAR_MS = 365 * 24 * 3600 * 1000;

  function can(feature: ProviderFeature): boolean {
    return app.isAdmin && Boolean(group?.features.includes(feature));
  }

  $effect(() => {
    const id = group?.provider_id;
    if (id) void load(id);
  });

  $effect(() => {
    if (app.isAdmin) void jobs.refresh();
    jobs.onFinished = (job) => {
      if (job.provider_id === group?.provider_id) void load(job.provider_id);
      void app.loadModels(true);
    };
    const timer = setInterval(() => (now = Date.now()), 1000);
    return () => {
      clearInterval(timer);
      jobs.onFinished = null;
      jobs.stopFollowing();
    };
  });

  async function load(providerId: string): Promise<void> {
    loading = true;
    try {
      const features = managed.find((entry) => entry.provider_id === providerId)?.features ?? [];
      const [listing, loaded] = await Promise.all([
        api.installed(providerId),
        features.includes("running") ? api.running(providerId) : Promise.resolve({ models: [] }),
      ]);
      if (group?.provider_id !== providerId) return;
      installed = listing.models;
      running = loaded.models;
    } catch (error) {
      app.report(error);
    } finally {
      loading = false;
    }
  }

  async function act(action: () => Promise<void>, refreshPicker = false): Promise<void> {
    if (!group) return;
    const providerId = group.provider_id;
    try {
      await action();
      await load(providerId);
      if (refreshPicker) await app.loadModels(true);
    } catch (error) {
      app.report(error);
    }
  }

  function unload(name: string): void {
    void act(() => api.unload(group!.provider_id, name));
  }

  function remove(name: string): void {
    if (!confirm(app.t("local.deleteConfirm", { name }))) return;
    void act(() => api.deleteModel(group!.provider_id, name), true);
  }

  function copy(name: string): void {
    const destination = prompt(app.t("local.copyPrompt"), `${name.split(":")[0]}-copy`);
    if (!destination) return;
    void act(() => api.copyModel(group!.provider_id, name, destination.trim()), true);
  }

  async function toggleDetails(name: string): Promise<void> {
    if (details[name]) {
      delete details[name];
      return;
    }
    try {
      details[name] = await api.showModel(group!.provider_id, name);
    } catch (error) {
      app.report(error);
    }
  }

  function startFrom(model: ModelDetails): void {
    const lines = [`FROM ${model.name}`];
    for (const [key, value] of Object.entries(model.parameters)) {
      for (const item of Array.isArray(value) ? value : [value]) {
        lines.push(`PARAMETER ${key} ${typeof item === "string" ? JSON.stringify(item) : String(item)}`);
      }
    }
    lines.push(`SYSTEM """${model.system ?? ""}"""`);
    modelfile = lines.join("\n");
    createName = `${model.name.split(":")[0]}-custom`;
  }

  function pull(): void {
    const name = pullName.trim();
    if (!group || !name) return;
    void jobs.pull(group.provider_id, name);
    pullName = "";
  }

  function create(): void {
    if (!group || !createName.trim() || !modelfile.trim()) return;
    void jobs.create(group.provider_id, createName.trim(), modelfile);
  }

  function useInChat(name: string): void {
    app.selectModel(`${group!.provider_id}:${name}`);
    location.hash = "#";
  }

  function processor(model: RunningModel): string {
    const share = gpuShare(model.vram_bytes, model.size_bytes);
    if (share === null) return "";
    if (share === 0) return app.t("local.cpu");
    const gpu = Math.round(share * 100);
    return gpu === 100 ? app.t("local.gpu", { percent: gpu }) : app.t("local.split", { gpu, cpu: 100 - gpu });
  }

  function expiry(model: RunningModel): string {
    if (!model.expires_at_ms) return "";
    const remaining = model.expires_at_ms - now;
    if (remaining > YEAR_MS) return app.t("local.neverExpires");
    return app.t("local.expiresIn", { value: formatDuration(Math.max(0, remaining) / 1000) });
  }
</script>

<div class="panel" data-testid="models-panel">
  <div class="panel-inner">
    <div class="title-row">
      <h1>{app.t("local.title")}</h1>
      <a class="btn btn-ghost" href="#/">{app.t("nav.back")}</a>
    </div>

    {#if managed.length === 0}
      <p class="hint">{app.t("local.none")}</p>
    {:else}
      <div class="inline-form">
        <div class="field">
          <label for="local-provider">{app.t("local.provider")}</label>
          <select id="local-provider" value={group?.provider_id} onchange={(event) => (selectedId = event.currentTarget.value)}>
            {#each managed as entry (entry.provider_id)}
              <option value={entry.provider_id}>{entry.provider_id} · {entry.name}</option>
            {/each}
          </select>
        </div>
        <button class="btn" onclick={() => group && load(group.provider_id)} disabled={loading} type="button">
          {#if loading}<span class="spinner"></span>{/if}{app.t("local.refresh")}
        </button>
      </div>

      {#if app.isAdmin && jobs.jobs.length > 0}
        <section class="card">
          <h2>{app.t("local.jobs")}</h2>
          <ul class="jobs">
            {#each jobs.jobs as job (job.id)}
              {@const fraction = progressFraction(job.completed_bytes, job.total_bytes)}
              <li data-testid="job">
                <div class="job-line">
                  <span class="mono">{job.model}</span>
                  <span class="hint">{job.provider_id}</span>
                  <span class="badge" class:badge-local={job.state === "succeeded"} class:badge-danger={job.state === "failed"}>
                    {app.t(JOB_STATE[job.state])}
                  </span>
                  <span class="hint status">{job.error?.message ?? job.status}</span>
                  {#if job.state === "running"}
                    <button class="btn btn-ghost" onclick={() => jobs.cancel(job.id)} type="button">{app.t("local.cancel")}</button>
                  {/if}
                </div>
                {#if job.state === "running" && fraction !== null}
                  <progress max="1" value={fraction}></progress>
                  <div class="hint mono">
                    {Math.floor(fraction * 100)}% · {formatBytes(job.completed_bytes)} / {formatBytes(job.total_bytes)}
                    {#if job.bytes_per_second}
                      · {formatRate(job.bytes_per_second)} · {app.t("jobs.eta", { value: formatDuration(etaSeconds(job.completed_bytes, job.total_bytes, job.bytes_per_second)) })}
                    {/if}
                  </div>
                {/if}
              </li>
            {/each}
          </ul>
        </section>
      {/if}

      {#if group?.features.includes("running")}
        <section class="card">
          <h2>{app.t("local.running")}</h2>
          {#if running.length === 0}
            <p class="hint">{app.t("local.runningNone")}</p>
          {:else}
            <div class="table-wrap">
              <table class="data">
                <thead>
                  <tr>
                    <th>{app.t("local.colName")}</th>
                    <th>{app.t("local.colMemory")}</th>
                    <th>{app.t("local.colProcessor")}</th>
                    <th>{app.t("local.colContext")}</th>
                    <th></th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {#each running as model (model.name)}
                    <tr>
                      <td class="mono" data-label={app.t("local.colName")}>{model.name}</td>
                      <td data-label={app.t("local.colMemory")}>{formatBytes(model.size_bytes)}</td>
                      <td data-label={app.t("local.colProcessor")}>{processor(model)}</td>
                      <td data-label={app.t("local.colContext")}>{model.context_length ?? ""}</td>
                      <td class="hint">
                        {#if model.busy !== null}{app.t(model.busy ? "local.busy" : "local.idle")}{/if}
                        {expiry(model)}
                      </td>
                      <td class="row-actions">
                        {#if can("unload")}
                          <button class="btn btn-ghost" onclick={() => unload(model.name)} type="button">{app.t("local.unload")}</button>
                        {/if}
                      </td>
                    </tr>
                  {/each}
                </tbody>
              </table>
            </div>
          {/if}
        </section>
      {/if}

      {#if can("pull")}
        <section class="card">
          <h2>{app.t("local.pull")}</h2>
          <form class="inline-form" onsubmit={(event) => { event.preventDefault(); pull(); }}>
            <div class="field grow">
              <input bind:value={pullName} placeholder={app.t("local.pullPlaceholder")} aria-label={app.t("local.pull")} data-testid="pull-name" />
            </div>
            <button class="btn btn-primary" disabled={!pullName.trim()} type="submit">{app.t("local.pullButton")}</button>
          </form>
          <p class="hint">{app.t("local.pullHint")}</p>
        </section>
      {/if}

      <section class="card">
        <h2>{app.t("local.installed")}</h2>
        {#if installed.length === 0}
          <p class="hint">{app.t("local.installedNone")}</p>
        {:else}
          <div class="table-wrap">
            <table class="data">
              <thead>
                <tr>
                  <th>{app.t("local.colName")}</th>
                  <th>{app.t("local.colParams")}</th>
                  <th>{app.t("local.colQuant")}</th>
                  <th>{app.t("local.colSize")}</th>
                  <th>{app.t("local.colContext")}</th>
                  <th>{app.t("local.colModified")}</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {#each installed as model (model.name)}
                  <tr>
                    <td class="mono" data-label={app.t("local.colName")}>{model.name}</td>
                    <td data-label={app.t("local.colParams")}>{model.parameter_size ?? ""}</td>
                    <td data-label={app.t("local.colQuant")}>{model.quantization ?? ""}</td>
                    <td data-label={app.t("local.colSize")}>{formatBytes(model.size_bytes)}</td>
                    <td data-label={app.t("local.colContext")}>{model.context_window ?? ""}</td>
                    <td class="hint" data-label={app.t("local.colModified")}>{formatDate(model.modified_at_ms, app.locale)}</td>
                    <td class="row-actions">
                      <button class="btn btn-ghost" onclick={() => useInChat(model.name)} type="button">{app.t("local.useInChat")}</button>
                      {#if group?.features.includes("show")}
                        <button class="btn btn-ghost" onclick={() => toggleDetails(model.name)} type="button">{app.t("local.details")}</button>
                      {/if}
                      {#if can("copy")}
                        <button class="btn btn-ghost" onclick={() => copy(model.name)} type="button">{app.t("local.copy")}</button>
                      {/if}
                      {#if can("delete")}
                        <button class="btn btn-ghost danger" onclick={() => remove(model.name)} type="button">{app.t("local.delete")}</button>
                      {/if}
                    </td>
                  </tr>
                  {#if details[model.name]}
                    {@const info = details[model.name]!}
                    <tr class="details">
                      <td colspan="7">
                        <div class="detail-grid">
                          <div>
                            <h3>{app.t("local.capabilities")}</h3>
                            <div class="chips">
                              {#each info.capabilities as capability (capability)}<span class="badge">{capability}</span>{/each}
                              {#if info.has_license}<span class="badge">{app.t("local.license")}</span>{/if}
                            </div>
                            <h3>{app.t("local.parameters")}</h3>
                            <pre>{Object.entries(info.parameters).map(([key, value]) => `${key} ${JSON.stringify(value)}`).join("\n")}</pre>
                            {#if info.system}
                              <h3>{app.t("local.system")}</h3>
                              <pre>{info.system}</pre>
                            {/if}
                          </div>
                          <div>
                            <h3>{app.t("local.template")}</h3>
                            <pre>{info.template ?? ""}</pre>
                          </div>
                        </div>
                        {#if can("create")}
                          <button class="btn" onclick={() => startFrom(info)} type="button">{app.t("local.startFrom")}</button>
                        {/if}
                      </td>
                    </tr>
                  {/if}
                {/each}
              </tbody>
            </table>
          </div>
        {/if}
      </section>

      {#if can("create")}
        <section class="card">
          <h2>{app.t("local.create")}</h2>
          <p class="hint">{app.t("local.createHint")}</p>
          <div class="field">
            <label for="create-name">{app.t("local.createName")}</label>
            <input id="create-name" bind:value={createName} />
          </div>
          <div class="field">
            <textarea rows="7" bind:value={modelfile} placeholder={"FROM qwen3:1.7b\nPARAMETER temperature 0.6\nSYSTEM \"\"\"You are terse.\"\"\""} aria-label="Modelfile"></textarea>
          </div>
          <div class="row-actions">
            <button class="btn btn-primary" onclick={create} disabled={!createName.trim() || !modelfile.trim()} type="button">
              {app.t("local.createButton")}
            </button>
          </div>
        </section>
      {/if}
    {/if}
  </div>
</div>

<style>
  .title-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  .grow {
    flex: 1;
    min-width: 14rem;
  }

  .jobs {
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
    margin: 0;
    padding: 0;
    list-style: none;
  }

  .job-line {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    align-items: center;
  }

  .status {
    flex: 1;
    min-width: 8rem;
    overflow-wrap: anywhere;
  }

  progress {
    width: 100%;
    height: 0.4rem;
    accent-color: var(--accent);
  }

  .danger {
    color: var(--danger);
  }

  .details td {
    background: var(--bg-sunken);
  }

  .detail-grid {
    display: grid;
    /* 14rem, not 18: at 390px minus the panel gutters, an 18rem track is wider
       than the column it has to fit into and overflows. */
    grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr));
    gap: 1rem;
  }

  h3 {
    margin: 0.4rem 0 0.25rem;
    font-size: 0.75rem;
    font-weight: 600;
    color: var(--text-muted);
  }

  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 0.25rem;
  }

  pre {
    max-height: 14rem;
    margin: 0;
    overflow: auto;
    padding: 0.5rem;
    font-size: 0.75rem;
    white-space: pre-wrap;
    background: var(--bg);
    border-radius: var(--radius-sm);
  }
</style>
