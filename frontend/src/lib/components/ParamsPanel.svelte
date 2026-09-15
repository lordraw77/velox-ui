<!--
  Advanced parameters for the selected model.

  Values are saved per model and applied by the server to every turn, so they follow
  the user to another tab or device. Only the parameters the backend accepts are shown
  (`supported_params`), and when the backend can describe the model its own defaults
  appear as placeholders — "model default: 0.6" is more useful than an empty box.

  Loaded lazily: most turns never open it.
-->
<script lang="ts">
  import { api } from "$lib/api/client";
  import { PARAM_GROUPS, PARAM_SPECS, fromForm, toForm, type ParamForm, type ParamSpec } from "$lib/params";
  import { app } from "$lib/stores/app.svelte";

  let form = $state<ParamForm>({});
  let supported = $state<string[]>([]);
  let defaults = $state<Record<string, unknown>>({});
  let loading = $state(true);
  let saving = $state(false);
  let invalid = $state<string[]>([]);
  let saved = $state(false);

  let visible = $derived(PARAM_SPECS.filter((spec) => supported.includes(spec.name)));

  $effect(() => {
    const ref = app.modelRef;
    if (ref) void load(ref);
  });

  async function load(ref: string): Promise<void> {
    loading = true;
    invalid = [];
    saved = false;
    try {
      const result = await api.modelParams(ref);
      if (app.modelRef !== ref) return;
      supported = result.supported_params;
      form = toForm(result.params);
      defaults = {};

      const group = app.providers.find((entry) => ref.startsWith(`${entry.provider_id}:`));
      if (group?.features.includes("show")) {
        const details = await api.showModel(group.provider_id, ref.slice(group.provider_id.length + 1));
        if (app.modelRef === ref) defaults = details.parameters;
      }
    } catch (error) {
      app.report(error);
    } finally {
      loading = false;
    }
  }

  async function save(): Promise<void> {
    const ref = app.modelRef;
    if (!ref) return;
    const result = fromForm(form, visible);
    invalid = result.invalid;
    if (invalid.length > 0) return;
    saving = true;
    try {
      form = toForm((await api.saveModelParams(ref, result.values)).params);
      saved = true;
    } catch (error) {
      app.report(error);
    } finally {
      saving = false;
    }
  }

  async function reset(): Promise<void> {
    const ref = app.modelRef;
    if (!ref) return;
    try {
      await api.resetModelParams(ref);
      form = {};
      invalid = [];
      saved = false;
    } catch (error) {
      app.report(error);
    }
  }

  function placeholder(spec: ParamSpec): string {
    const value = defaults[spec.name];
    if (value === undefined) return app.t("params.default");
    return app.t("params.modelDefault", {
      value: Array.isArray(value) ? value.join(" ") : String(value),
    });
  }

  function update(name: string, value: string): void {
    form[name] = value;
    saved = false;
  }
</script>

<section class="params" data-testid="params-panel">
  <div class="head">
    <div>
      <strong>{app.t("params.title")}</strong>
      <span class="mono model">{app.model?.display_name}</span>
      <p class="hint">{app.t("params.intro")}</p>
    </div>
    <button class="btn btn-ghost btn-icon" onclick={() => (app.paramsOpen = false)} aria-label={app.t("params.close")} title={app.t("params.close")} type="button">×</button>
  </div>

  {#if loading}
    <span class="spinner"></span>
  {:else if visible.length === 0}
    <p class="hint">{app.t("params.unsupported")}</p>
  {:else}
    {#each PARAM_GROUPS as group (group.key)}
      {@const specs = visible.filter((spec) => spec.group === group.key)}
      {#if specs.length > 0}
        <fieldset>
          <legend>{app.t(group.label)}</legend>
          <div class="grid">
            {#each specs as spec (spec.name)}
              <label class="param" class:invalid={invalid.includes(spec.name)}>
                <span class="mono name">{spec.name}</span>
                {#if spec.kind === "bool"}
                  <select value={form[spec.name] ?? ""} onchange={(event) => update(spec.name, event.currentTarget.value)}>
                    <option value="">{placeholder(spec)}</option>
                    <option value="true">{app.t("params.on")}</option>
                    <option value="false">{app.t("params.off")}</option>
                  </select>
                {:else if spec.kind === "choice"}
                  <select value={form[spec.name] ?? ""} onchange={(event) => update(spec.name, event.currentTarget.value)}>
                    <option value="">{placeholder(spec)}</option>
                    {#each spec.choices ?? [] as choice (choice)}
                      <option value={String(choice)}>{choice}</option>
                    {/each}
                  </select>
                {:else if spec.kind === "list"}
                  <textarea rows="2" value={form[spec.name] ?? ""} placeholder={placeholder(spec)} oninput={(event) => update(spec.name, event.currentTarget.value)}></textarea>
                {:else}
                  <input
                    type={spec.kind === "text" ? "text" : "number"}
                    step={spec.step}
                    min={spec.min}
                    max={spec.max}
                    value={form[spec.name] ?? ""}
                    placeholder={placeholder(spec)}
                    oninput={(event) => update(spec.name, event.currentTarget.value)}
                  />
                {/if}
                <span class="help">{app.t(spec.help)}</span>
              </label>
            {/each}
          </div>
        </fieldset>
      {/if}
    {/each}

    <div class="actions">
      {#if invalid.length > 0}<span class="error">{app.t("params.invalid")}</span>{/if}
      {#if saved}<span class="ok">{app.t("params.saved")}</span>{/if}
      <button class="btn" onclick={reset} type="button">{app.t("params.reset")}</button>
      <button class="btn btn-primary" onclick={save} disabled={saving} type="button" data-testid="params-save">
        {app.t("params.save")}
      </button>
    </div>
  {/if}
</section>

<style>
  .params {
    /* `dvh`, not `vh`: on iOS `vh` is the *large* viewport, so 60vh of it can be
       taller than what is actually visible with the URL bar showing. */
    max-height: 60dvh;
    overflow-y: auto;
    padding: 0.75rem 1rem 1rem;
    background: var(--bg-raised);
    border-bottom: 1px solid var(--border);
    box-shadow: var(--shadow);
  }

  .head {
    display: flex;
    justify-content: space-between;
    gap: 1rem;
  }

  .head p {
    margin: 0.2rem 0 0.4rem;
  }

  .model {
    margin-left: 0.5rem;
    color: var(--text-muted);
  }

  fieldset {
    margin: 0.5rem 0 0;
    padding: 0.5rem 0 0;
    border: 0;
    border-top: 1px solid var(--border);
  }

  legend {
    padding-right: 0.5rem;
    font-size: 0.78rem;
    font-weight: 600;
    color: var(--text-muted);
  }

  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(15rem, 1fr));
    gap: 0.6rem 1rem;
  }

  .param {
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
  }

  .param input,
  .param select,
  .param textarea {
    padding: 0.35rem 0.5rem;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
  }

  .param textarea {
    font-family: var(--font-mono);
    font-size: 0.8rem;
    resize: vertical;
  }

  .param.invalid input,
  .param.invalid select,
  .param.invalid textarea {
    border-color: var(--danger);
  }

  .name {
    font-size: 0.78rem;
  }

  .help {
    font-size: 0.72rem;
    color: var(--text-faint);
  }

  .actions {
    display: flex;
    gap: 0.5rem;
    align-items: center;
    justify-content: flex-end;
    margin-top: 0.75rem;
  }

  .error {
    font-size: 0.8rem;
    color: var(--danger);
  }

  .ok {
    font-size: 0.8rem;
    color: var(--ok);
  }
</style>
