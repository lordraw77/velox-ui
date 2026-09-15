<!--
  Image generation, voice (STT/TTS) and builtin tools (web search/browsing) plugins:
  enable, point at a backend, and validate the connection (ADR-0014, ADR-0021). Each
  is a singleton — at most one configured backend per kind — so this is a form per
  kind, not a list.
-->
<script lang="ts">
  import { onMount } from "svelte";
  import { api } from "$lib/api/client";
  import type { PluginInfo, PluginKind } from "$lib/api/types";
  import { app } from "$lib/stores/app.svelte";

  const KINDS = ["images", "voice", "tools"] as const;
  const TITLE_KEY: Record<PluginKind, string> = {
    images: "plugins.images",
    voice: "plugins.voice",
    tools: "plugins.tools",
  };
  const HINT_KEY: Record<PluginKind, string> = {
    images: "plugins.imagesHint",
    voice: "plugins.voiceHint",
    tools: "plugins.toolsHint",
  };

  let plugins = $state<Record<PluginKind, PluginInfo | null>>({
    images: null,
    voice: null,
    tools: null,
  });
  let loading = $state(true);

  let baseUrl = $state<Record<PluginKind, string>>({ images: "", voice: "", tools: "" });
  let model = $state<Record<PluginKind, string>>({ images: "", voice: "", tools: "" });
  let ttsVoice = $state("");
  let apiKey = $state<Record<PluginKind, string>>({ images: "", voice: "", tools: "" });

  let saving = $state<PluginKind | null>(null);
  let validating = $state<PluginKind | null>(null);
  let validation = $state<Record<PluginKind, { ok: boolean; detail: string } | null>>({
    images: null,
    voice: null,
    tools: null,
  });

  onMount(load);

  async function load(): Promise<void> {
    loading = true;
    try {
      const list = await api.plugins();
      for (const info of list) {
        plugins[info.kind] = info;
        baseUrl[info.kind] = info.base_url ?? "";
        model[info.kind] = info.model ?? "";
      }
    } catch (error) {
      app.report(error);
    } finally {
      loading = false;
    }
  }

  async function toggle(kind: PluginKind, enabled: boolean): Promise<void> {
    saving = kind;
    try {
      const patch: Parameters<typeof api.updatePlugin>[1] = {
        enabled,
        base_url: baseUrl[kind].trim() || null,
        model: model[kind].trim() || null,
      };
      if (kind === "voice" && ttsVoice.trim()) patch.tts_voice = ttsVoice.trim();
      if (apiKey[kind].trim()) patch.api_key = apiKey[kind].trim();
      plugins[kind] = await api.updatePlugin(kind, patch);
      apiKey[kind] = "";
      validation[kind] = null;
      await app.loadPlugins();
    } catch (error) {
      app.report(error);
    } finally {
      saving = null;
    }
  }

  async function validate(kind: PluginKind): Promise<void> {
    validating = kind;
    try {
      validation[kind] = await api.validatePlugin(kind);
    } catch (error) {
      app.report(error);
    } finally {
      validating = null;
    }
  }
</script>

<div class="panel" data-testid="plugins-panel">
  <div class="panel-inner">
    <div class="title-row">
      <h1>{app.t("plugins.title")}</h1>
      <a class="btn btn-ghost" href="#/">{app.t("nav.back")}</a>
    </div>
    <p class="hint">{app.t("plugins.intro")}</p>

    {#if loading}
      <span class="spinner"></span>
    {:else}
      {#each KINDS as kind}
        <section class="card">
          <div class="title-row">
            <h2>{app.t(TITLE_KEY[kind])}</h2>
            {#if plugins[kind]?.enabled}
              <span class="badge">{app.t("plugins.enabled")}</span>
            {/if}
          </div>
          <p class="hint">{app.t(HINT_KEY[kind])}</p>

          <form
            class="stack"
            onsubmit={(event) => {
              event.preventDefault();
              void toggle(kind, true);
            }}
          >
            <div class="field">
              <label for="plugin-{kind}-url">{app.t("plugins.baseUrl")}</label>
              <!-- Optional for tools: leaving it empty still enables the tools that
                   need no backend of their own, such as the date and time. -->
              <input
                id="plugin-{kind}-url"
                bind:value={baseUrl[kind]}
                placeholder={kind === "tools" ? "http://localhost:8080" : "http://localhost:9000"}
                required={kind !== "tools"}
              />
            </div>
            {#if kind !== "tools"}
              <div class="field">
                <label for="plugin-{kind}-model">{app.t("plugins.model")}</label>
                <input id="plugin-{kind}-model" bind:value={model[kind]} />
              </div>
            {/if}
            {#if kind === "voice"}
              <div class="field">
                <label for="plugin-voice-tts">{app.t("plugins.ttsVoice")}</label>
                <input id="plugin-voice-tts" bind:value={ttsVoice} placeholder="alloy" />
              </div>
            {/if}
            {#if kind !== "tools"}
              <div class="field">
                <label for="plugin-{kind}-key">{app.t("plugins.apiKey")}</label>
                <input
                  id="plugin-{kind}-key"
                  type="password"
                  bind:value={apiKey[kind]}
                  autocomplete="off"
                />
                <p class="hint">
                  {plugins[kind]?.auth_hint
                    ? app.t("plugins.apiKeySet", { hint: plugins[kind]?.auth_hint ?? "" })
                    : app.t("plugins.apiKeyHint")}
                </p>
              </div>
            {/if}
            <div class="row-actions">
              {#if plugins[kind]?.enabled}
                <button
                  class="btn btn-ghost"
                  disabled={saving === kind}
                  onclick={() => toggle(kind, false)}
                  type="button"
                >
                  {app.t("plugins.disable")}
                </button>
              {/if}
              <button
                class="btn btn-ghost"
                disabled={validating === kind}
                onclick={() => validate(kind)}
                type="button"
              >
                {validating === kind ? app.t("plugins.validating") : app.t("plugins.validate")}
              </button>
              <button class="btn btn-primary" disabled={saving === kind} type="submit">
                {app.t("plugins.save")}
              </button>
            </div>
            {#if validation[kind]}
              <p class="hint" class:danger={!validation[kind]?.ok}>
                {validation[kind]?.detail}
              </p>
            {/if}
          </form>
        </section>
      {/each}
    {/if}
  </div>
</div>

<style>
  .danger {
    color: var(--danger);
  }

  .stack {
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
  }

  .badge {
    padding: 0.1rem 0.5rem;
    font-size: 0.78rem;
    color: var(--text-muted);
    background: var(--bg-active);
    border-radius: var(--radius-sm);
  }
</style>
