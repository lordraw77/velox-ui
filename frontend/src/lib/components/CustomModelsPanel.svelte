<!--
  Custom models (personas): a system prompt and parameter overrides bound to a slug.

  Parameter overrides are kept to the three fields people actually reach for
  (temperature, top_p, max_tokens) rather than the full advanced-parameter set in
  `params.ts` — a persona is meant to be quick to define, and the model's own
  ParamsPanel still applies once the chat is running.

  Loaded lazily: it is not part of the initial bundle.
-->
<script lang="ts">
  import { onMount } from "svelte";
  import { api } from "$lib/api/client";
  import type {
    Collection,
    CustomModel,
    CustomModelVisibility,
    FallbackEntry,
    McpServer,
  } from "$lib/api/types";
  import { app } from "$lib/stores/app.svelte";
  import { conversation } from "$lib/stores/conversation.svelte";

  interface Props {
    onstart?: () => void;
  }

  let { onstart }: Props = $props();

  let models = $state<CustomModel[]>([]);
  let collections = $state<Collection[]>([]);
  let mcpServers = $state<McpServer[]>([]);
  let loading = $state(true);
  let busy = $state(false);
  let editingId = $state<string | null>(null);

  interface Draft {
    slug: string;
    name: string;
    description: string;
    system_prompt: string;
    temperature: string;
    top_p: string;
    max_tokens: string;
    knowledge_ids: string[];
    tools: string[];
    plugins: string[];
    fallback_chain: FallbackEntry[];
    visibility: CustomModelVisibility;
  }

  function emptyDraft(): Draft {
    return {
      slug: "",
      name: "",
      description: "",
      system_prompt: "",
      temperature: "",
      top_p: "",
      max_tokens: "",
      knowledge_ids: [],
      tools: [],
      plugins: [],
      fallback_chain: [],
      visibility: "private",
    };
  }

  let draft = $state<Draft>(emptyDraft());

  onMount(load);

  async function load(): Promise<void> {
    loading = true;
    try {
      [models, collections, mcpServers] = await Promise.all([
        api.customModels(),
        api.collections(),
        api.mcpServers(),
      ]);
    } catch (error) {
      app.report(error);
    } finally {
      loading = false;
    }
  }

  function startCreate(): void {
    editingId = "new";
    draft = emptyDraft();
  }

  function startEdit(model: CustomModel): void {
    editingId = model.id;
    draft = {
      slug: model.slug,
      name: model.name,
      description: model.description ?? "",
      system_prompt: model.system_prompt ?? "",
      temperature: model.params?.temperature !== undefined ? String(model.params.temperature) : "",
      top_p: model.params?.top_p !== undefined ? String(model.params.top_p) : "",
      max_tokens: model.params?.max_tokens !== undefined ? String(model.params.max_tokens) : "",
      knowledge_ids: [...model.knowledge_ids],
      tools: [...model.tools],
      plugins: [...model.plugins],
      fallback_chain: [...model.fallback_chain],
      visibility: model.visibility,
    };
  }

  function toggleKnowledge(collectionId: string, checked: boolean): void {
    draft.knowledge_ids = checked
      ? [...draft.knowledge_ids, collectionId]
      : draft.knowledge_ids.filter((id) => id !== collectionId);
  }

  function toggleTool(serverId: string, checked: boolean): void {
    draft.tools = checked
      ? [...draft.tools, serverId]
      : draft.tools.filter((id) => id !== serverId);
  }

  function togglePlugin(kind: string, checked: boolean): void {
    draft.plugins = checked
      ? [...draft.plugins, kind]
      : draft.plugins.filter((k) => k !== kind);
  }

  function cancelEdit(): void {
    editingId = null;
  }

  function addFallback(): void {
    draft.fallback_chain = [...draft.fallback_chain, { provider_id: "", model_key: "" }];
  }

  function removeFallback(index: number): void {
    draft.fallback_chain = draft.fallback_chain.filter((_, i) => i !== index);
  }

  function draftParams(): Record<string, number> | null {
    const params: Record<string, number> = {};
    if (draft.temperature.trim()) params.temperature = Number(draft.temperature);
    if (draft.top_p.trim()) params.top_p = Number(draft.top_p);
    if (draft.max_tokens.trim()) params.max_tokens = Number(draft.max_tokens);
    return Object.keys(params).length > 0 ? params : null;
  }

  async function save(): Promise<void> {
    busy = true;
    try {
      const body = {
        name: draft.name,
        description: draft.description || null,
        system_prompt: draft.system_prompt || null,
        params: draftParams(),
        knowledge_ids: draft.knowledge_ids,
        tools: draft.tools,
        plugins: draft.plugins,
        fallback_chain: draft.fallback_chain.filter((entry) => entry.provider_id && entry.model_key),
        visibility: draft.visibility,
      };
      if (editingId === "new") {
        await api.createCustomModel({ ...body, slug: draft.slug });
      } else if (editingId) {
        await api.updateCustomModel(editingId, body);
      }
      editingId = null;
      await load();
    } catch (error) {
      app.report(error);
    } finally {
      busy = false;
    }
  }

  async function remove(model: CustomModel): Promise<void> {
    if (!confirm(app.t("custommodel.deleteConfirm"))) return;
    try {
      await api.deleteCustomModel(model.id);
      await load();
    } catch (error) {
      app.report(error);
    }
  }

  function startChat(model: CustomModel): void {
    conversation.startFromCustomModel(model);
    history.replaceState(null, "", "#");
    onstart?.();
  }
</script>

<div class="panel" data-testid="custom-models-panel">
  <div class="panel-inner">
    <div class="title-row">
      <h1>{app.t("custommodel.title")}</h1>
      <a class="btn btn-ghost" href="#/">{app.t("nav.back")}</a>
    </div>
    <p class="hint">{app.t("custommodel.intro")}</p>

    {#if loading}
      <span class="spinner"></span>
    {:else}
      <section class="card">
        <div class="table-wrap">
          <table class="data">
            <tbody>
              {#each models as model (model.id)}
                <tr>
                  <td>
                    <strong>{model.name}</strong>
                    <div class="mono hint">{model.slug}</div>
                  </td>
                  <td><span class="badge">{app.t(`custommodel.visibility.${model.visibility}`)}</span></td>
                  <td class="row-actions">
                    <button class="btn btn-ghost" onclick={() => startChat(model)} type="button">
                      {app.t("custommodel.start")}
                    </button>
                    {#if model.owner_id === null || model.owner_id === app.session?.user_id}
                      <button class="btn btn-ghost" onclick={() => startEdit(model)} type="button">
                        {app.t("custommodel.edit")}
                      </button>
                      <button class="btn btn-ghost danger" onclick={() => remove(model)} type="button">
                        {app.t("custommodel.delete")}
                      </button>
                    {/if}
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
        {#if models.length === 0}
          <p class="hint">{app.t("custommodel.none")}</p>
        {/if}
      </section>

      {#if editingId === null}
        <button class="btn btn-primary" onclick={startCreate} type="button">
          {app.t("custommodel.add")}
        </button>
      {:else}
        <section class="card">
          <h2>{editingId === "new" ? app.t("custommodel.add") : app.t("custommodel.edit")}</h2>
          <form
            class="inline-form"
            onsubmit={(event) => {
              event.preventDefault();
              void save();
            }}
          >
            {#if editingId === "new"}
              <div class="field">
                <label for="cm-slug">{app.t("custommodel.slug")}</label>
                <input id="cm-slug" bind:value={draft.slug} pattern="[a-z0-9][a-z0-9\-]*" required />
              </div>
            {/if}
            <div class="field">
              <label for="cm-name">{app.t("custommodel.name")}</label>
              <input id="cm-name" bind:value={draft.name} required />
            </div>
            <div class="field">
              <label for="cm-visibility">{app.t("custommodel.visibility.label")}</label>
              <select id="cm-visibility" bind:value={draft.visibility}>
                <option value="private">{app.t("custommodel.visibility.private")}</option>
                <option value="shared">{app.t("custommodel.visibility.shared")}</option>
                <option value="public">{app.t("custommodel.visibility.public")}</option>
              </select>
            </div>
            <div class="field wide">
              <label for="cm-description">{app.t("custommodel.description")}</label>
              <input id="cm-description" bind:value={draft.description} />
            </div>
            <div class="field wide">
              <label for="cm-prompt">{app.t("custommodel.systemPrompt")}</label>
              <textarea id="cm-prompt" bind:value={draft.system_prompt} rows="4"></textarea>
            </div>
            <div class="field">
              <label for="cm-temperature">{app.t("params.temperature")}</label>
              <input id="cm-temperature" type="number" step="0.05" bind:value={draft.temperature} />
            </div>
            <div class="field">
              <label for="cm-top-p">{app.t("params.top_p")}</label>
              <input id="cm-top-p" type="number" step="0.01" bind:value={draft.top_p} />
            </div>
            <div class="field">
              <label for="cm-max-tokens">{app.t("params.max_tokens")}</label>
              <input id="cm-max-tokens" type="number" step="1" bind:value={draft.max_tokens} />
            </div>

            <div class="field wide">
              <span>{app.t("custommodel.knowledge")}</span>
              {#if collections.length === 0}
                <p class="hint">{app.t("custommodel.noKnowledge")}</p>
              {:else}
                <div class="knowledge-list">
                  {#each collections as collection (collection.id)}
                    <label class="knowledge-item">
                      <input
                        type="checkbox"
                        checked={draft.knowledge_ids.includes(collection.id)}
                        onchange={(event) =>
                          toggleKnowledge(collection.id, (event.target as HTMLInputElement).checked)}
                      />
                      {collection.name}
                    </label>
                  {/each}
                </div>
              {/if}
            </div>

            <div class="field wide">
              <span>{app.t("custommodel.tools")}</span>
              {#if mcpServers.length === 0}
                <p class="hint">{app.t("custommodel.noTools")}</p>
              {:else}
                <div class="knowledge-list">
                  {#each mcpServers as server (server.id)}
                    <label class="knowledge-item">
                      <input
                        type="checkbox"
                        checked={draft.tools.includes(server.id)}
                        onchange={(event) =>
                          toggleTool(server.id, (event.target as HTMLInputElement).checked)}
                      />
                      {server.name}
                    </label>
                  {/each}
                </div>
              {/if}
            </div>

            <div class="field wide">
              <label class="knowledge-item">
                <input
                  type="checkbox"
                  checked={draft.plugins.includes("tools")}
                  onchange={(event) =>
                    togglePlugin("tools", (event.target as HTMLInputElement).checked)}
                />
                {app.t("custommodel.webTools")}
              </label>
              <p class="hint">{app.t("custommodel.webToolsHint")}</p>
            </div>

            <div class="field wide">
              <span>{app.t("custommodel.fallbackChain")}</span>
              {#each draft.fallback_chain as entry, index (index)}
                <div class="inline-form">
                  <input bind:value={entry.provider_id} placeholder={app.t("custommodel.providerId")} />
                  <input bind:value={entry.model_key} placeholder={app.t("custommodel.modelKey")} />
                  <button class="btn btn-ghost" onclick={() => removeFallback(index)} type="button">
                    {app.t("custommodel.remove")}
                  </button>
                </div>
              {/each}
              <button class="btn btn-ghost" onclick={addFallback} type="button">
                {app.t("custommodel.addFallback")}
              </button>
            </div>

            <div class="row-actions full">
              <button class="btn btn-ghost" onclick={cancelEdit} type="button">
                {app.t("custommodel.cancel")}
              </button>
              <button class="btn btn-primary" disabled={busy} type="submit">
                {app.t("custommodel.save")}
              </button>
            </div>
          </form>
        </section>
      {/if}
    {/if}
  </div>
</div>

<style>
  .row-actions.full {
    flex-basis: 100%;
    justify-content: flex-end;
  }

  .field.wide {
    flex-basis: 100%;
  }

  .danger {
    color: var(--danger);
  }

  .knowledge-list {
    display: flex;
    flex-wrap: wrap;
    gap: 0.6rem;
    margin-top: 0.3rem;
  }

  .knowledge-item {
    display: flex;
    gap: 0.35rem;
    align-items: center;
    font-weight: normal;
  }
</style>
