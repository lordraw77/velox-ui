<!--
  Knowledge bases (RAG collections): create/rename/delete a collection, upload and
  ingest documents with live status/progress, and a debug/preview query box.

  Ingestion is a background job (services/rag_jobs.py); this polls the document list
  rather than following the job's SSE stream, matching the simple, list-shaped UI the
  rest of this panel already uses (no other panel here opens an SSE connection outside
  the chat stream itself).

  Loaded lazily: it is not part of the initial bundle.
-->
<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import { api } from "$lib/api/client";
  import type { Collection, DocumentStatus, RagDocument, RetrievedChunk } from "$lib/api/types";
  import { app } from "$lib/stores/app.svelte";

  const STATUS_LABEL: Record<DocumentStatus, string> = {
    pending: "knowledge.statusPending",
    parsing: "knowledge.statusParsing",
    embedding: "knowledge.statusEmbedding",
    ready: "knowledge.statusReady",
    failed: "knowledge.statusFailed",
  };

  let collections = $state<Collection[]>([]);
  let loading = $state(true);
  let selected = $state<Collection | null>(null);
  let documents = $state<RagDocument[]>([]);
  let creating = $state(false);
  let newName = $state("");
  let uploading = $state(false);
  let fileInput = $state<HTMLInputElement | undefined>(undefined);
  let pollHandle: ReturnType<typeof setInterval> | undefined;

  let queryText = $state("");
  let queryResults = $state<RetrievedChunk[] | null>(null);
  let querying = $state(false);

  onMount(load);
  onDestroy(stopPolling);

  async function load(): Promise<void> {
    loading = true;
    try {
      collections = await api.collections();
    } catch (error) {
      app.report(error);
    } finally {
      loading = false;
    }
  }

  function stopPolling(): void {
    if (pollHandle !== undefined) clearInterval(pollHandle);
    pollHandle = undefined;
  }

  async function select(collection: Collection): Promise<void> {
    selected = collection;
    queryResults = null;
    stopPolling();
    await loadDocuments();
    pollHandle = setInterval(() => void loadDocuments(), 1500);
  }

  function back(): void {
    selected = null;
    stopPolling();
  }

  async function loadDocuments(): Promise<void> {
    if (!selected) return;
    try {
      documents = await api.documents(selected.id);
      if (documents.every((doc) => doc.status === "ready" || doc.status === "failed")) {
        stopPolling();
      }
    } catch (error) {
      app.report(error);
    }
  }

  async function createCollection(): Promise<void> {
    const name = newName.trim();
    if (!name) return;
    try {
      await api.createCollection({ name });
      newName = "";
      creating = false;
      await load();
    } catch (error) {
      app.report(error);
    }
  }

  async function removeCollection(collection: Collection): Promise<void> {
    if (!confirm(app.t("knowledge.deleteConfirm"))) return;
    try {
      await api.deleteCollection(collection.id);
      if (selected?.id === collection.id) back();
      await load();
    } catch (error) {
      app.report(error);
    }
  }

  async function uploadAndIngest(files: FileList | null): Promise<void> {
    if (!files || files.length === 0 || !selected) return;
    uploading = true;
    try {
      for (const file of Array.from(files)) {
        const uploaded = await api.uploadFile(file);
        await api.ingestDocument(selected.id, uploaded.id, file.name);
      }
      if (fileInput) fileInput.value = "";
      await loadDocuments();
      if (pollHandle === undefined) pollHandle = setInterval(() => void loadDocuments(), 1500);
    } catch (error) {
      app.report(error);
    } finally {
      uploading = false;
    }
  }

  async function removeDocument(document: RagDocument): Promise<void> {
    try {
      await api.deleteDocument(document.id);
      await loadDocuments();
    } catch (error) {
      app.report(error);
    }
  }

  async function runQuery(): Promise<void> {
    if (!selected || !queryText.trim()) return;
    querying = true;
    try {
      const result = await api.queryCollection(selected.id, queryText.trim());
      queryResults = result.items;
    } catch (error) {
      app.report(error);
    } finally {
      querying = false;
    }
  }
</script>

<div class="panel" data-testid="knowledge-panel">
  <div class="panel-inner">
    {#if !selected}
      <div class="title-row">
        <h1>{app.t("knowledge.title")}</h1>
        <a class="btn btn-ghost" href="#/">{app.t("nav.back")}</a>
      </div>
      <p class="hint">{app.t("knowledge.intro")}</p>

      {#if loading}
        <span class="spinner"></span>
      {:else}
        <section class="card">
          <div class="table-wrap">
            <table class="data">
              <tbody>
                {#each collections as collection (collection.id)}
                  <tr>
                    <td>
                      <button class="btn btn-ghost" onclick={() => select(collection)} type="button">
                        <strong>{collection.name}</strong>
                      </button>
                      <div class="mono hint">{collection.embedder_ref}</div>
                    </td>
                    <td class="row-actions">
                      <button
                        class="btn btn-ghost danger"
                        onclick={() => removeCollection(collection)}
                        type="button"
                      >
                        {app.t("knowledge.delete")}
                      </button>
                    </td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
          {#if collections.length === 0}
            <p class="hint">{app.t("knowledge.none")}</p>
          {/if}
        </section>

        {#if creating}
          <section class="card">
            <form
              class="inline-form"
              onsubmit={(event) => {
                event.preventDefault();
                void createCollection();
              }}
            >
              <div class="field grow">
                <label for="kb-name">{app.t("knowledge.name")}</label>
                <input id="kb-name" bind:value={newName} required />
              </div>
              <button class="btn btn-ghost" onclick={() => (creating = false)} type="button">
                {app.t("knowledge.cancel")}
              </button>
              <button class="btn btn-primary" type="submit">{app.t("knowledge.save")}</button>
            </form>
          </section>
        {:else}
          <button class="btn btn-primary" onclick={() => (creating = true)} type="button">
            {app.t("knowledge.add")}
          </button>
        {/if}
      {/if}
    {:else}
      <div class="title-row">
        <h1>{selected.name}</h1>
        <button class="btn btn-ghost" onclick={back} type="button">{app.t("nav.back")}</button>
      </div>

      <section class="card">
        <h2>{app.t("knowledge.documents")}</h2>
        <div class="table-wrap">
          <table class="data">
            <tbody>
              {#each documents as document (document.id)}
                <tr>
                  <td>
                    {document.title}
                    <div class="hint">
                      {app.t(STATUS_LABEL[document.status])}
                      {#if document.status !== "ready" && document.status !== "failed"}
                        ({document.progress}%)
                      {/if}
                      {#if document.status === "ready"}
                        · {document.chunk_count} {app.t("knowledge.chunks")}
                      {/if}
                    </div>
                    {#if document.error}
                      <div class="hint danger">{document.error}</div>
                    {/if}
                  </td>
                  <td class="row-actions">
                    <button
                      class="btn btn-ghost danger"
                      onclick={() => removeDocument(document)}
                      type="button"
                    >
                      {app.t("knowledge.delete")}
                    </button>
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
        {#if documents.length === 0}
          <p class="hint">{app.t("knowledge.noDocuments")}</p>
        {/if}

        <label class="btn btn-primary upload">
          {uploading ? app.t("knowledge.uploading") : app.t("knowledge.upload")}
          <input
            bind:this={fileInput}
            type="file"
            multiple
            accept=".txt,.md,.markdown,.pdf"
            disabled={uploading}
            onchange={(event) => void uploadAndIngest((event.target as HTMLInputElement).files)}
          />
        </label>
      </section>

      <section class="card">
        <h2>{app.t("knowledge.query")}</h2>
        <form
          class="inline-form"
          onsubmit={(event) => {
            event.preventDefault();
            void runQuery();
          }}
        >
          <div class="field grow">
            <input bind:value={queryText} placeholder={app.t("knowledge.queryPlaceholder")} />
          </div>
          <button class="btn btn-primary" disabled={querying || !queryText.trim()} type="submit">
            {app.t("knowledge.run")}
          </button>
        </form>
        {#if queryResults !== null}
          <ul class="results">
            {#each queryResults as hit (hit.chunk_id)}
              <li>
                <div class="hint">{hit.document_title} · {hit.score.toFixed(3)}</div>
                <p class="snippet">{hit.content}</p>
              </li>
            {:else}
              <li class="hint">{app.t("knowledge.noResults")}</li>
            {/each}
          </ul>
        {/if}
      </section>
    {/if}
  </div>
</div>

<style>
  .grow {
    flex: 1;
    min-width: 14rem;
  }

  .danger {
    color: var(--danger);
  }

  .upload {
    position: relative;
    display: inline-block;
    overflow: hidden;
    cursor: pointer;
  }

  .upload input {
    position: absolute;
    inset: 0;
    opacity: 0;
    cursor: pointer;
  }

  .results {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    margin: 0.6rem 0 0;
    padding: 0;
    list-style: none;
  }

  .snippet {
    margin: 0.2rem 0 0;
    padding: 0.5rem 0.6rem;
    background: var(--bg-sunken);
    border-radius: var(--radius-sm);
    white-space: pre-wrap;
  }
</style>
