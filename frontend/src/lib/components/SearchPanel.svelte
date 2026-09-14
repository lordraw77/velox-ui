<!--
  Search across chat titles and message content.

  Results are ordered by recency, not relevance score (the repository's own trade-off:
  see `db/repositories/search.py`), so the newest matching chat or message is always
  the first result.

  Loaded lazily: it is not part of the initial bundle.
-->
<script lang="ts">
  import { api } from "$lib/api/client";
  import type { SearchHit } from "$lib/api/types";
  import { formatDate } from "$lib/format";
  import { app } from "$lib/stores/app.svelte";

  interface Props {
    onopen: (chatId: string) => void;
  }

  let { onopen }: Props = $props();

  const KIND_LABEL: Record<SearchHit["kind"], string> = {
    message: "search.kindMessage",
    chat_title: "search.kindTitle",
  };

  let query = $state("");
  let items = $state<SearchHit[]>([]);
  let cursor = $state<string | null>(null);
  let loading = $state(false);
  let searched = $state(false);

  async function run(reset = true): Promise<void> {
    const trimmed = query.trim();
    if (!trimmed) {
      items = [];
      searched = false;
      return;
    }
    loading = true;
    try {
      const result = await api.search(trimmed, reset ? null : cursor);
      items = reset ? result.items : [...items, ...result.items];
      cursor = result.next_cursor;
      searched = true;
    } catch (error) {
      app.report(error);
    } finally {
      loading = false;
    }
  }
</script>

<div class="panel" data-testid="search-panel">
  <div class="panel-inner">
    <div class="title-row">
      <h1>{app.t("search.title")}</h1>
      <a class="btn btn-ghost" href="#/">{app.t("nav.back")}</a>
    </div>

    <form
      class="inline-form"
      onsubmit={(event) => {
        event.preventDefault();
        void run(true);
      }}
    >
      <div class="field grow">
        <label for="search-query">{app.t("search.query")}</label>
        <input id="search-query" bind:value={query} placeholder={app.t("search.placeholder")} />
      </div>
      <button class="btn btn-primary" disabled={loading || !query.trim()} type="submit">
        {app.t("search.run")}
      </button>
    </form>

    {#if loading && items.length === 0}
      <span class="spinner"></span>
    {:else if searched && items.length === 0}
      <p class="hint">{app.t("search.none")}</p>
    {:else if items.length > 0}
      <section class="card">
        <ul class="results">
          {#each items as hit (hit.message_id ?? hit.chat_id)}
            <li>
              <button class="result" onclick={() => onopen(hit.chat_id)} type="button">
                <span class="chat-title">{hit.chat_title}</span>
                <span class="badge">{app.t(KIND_LABEL[hit.kind])}</span>
                {#if hit.kind === "message"}
                  <span class="snippet">{hit.snippet}</span>
                {/if}
                <span class="hint">{formatDate(hit.created_at, app.locale)}</span>
              </button>
            </li>
          {/each}
        </ul>
        {#if cursor}
          <button class="btn btn-ghost" onclick={() => run(false)} disabled={loading} type="button">
            {app.t("search.more")}
          </button>
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

  .results {
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
    margin: 0 0 0.6rem;
    padding: 0;
    list-style: none;
  }

  .result {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
    align-items: center;
    width: 100%;
    padding: 0.5rem;
    text-align: left;
    border-radius: var(--radius-sm);
  }

  .result:hover {
    background: var(--bg-hover);
  }

  .chat-title {
    font-weight: 500;
  }

  .snippet {
    flex-basis: 100%;
    font-size: 0.84rem;
    color: var(--text-muted);
  }
</style>
