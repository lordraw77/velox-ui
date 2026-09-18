<!--
  A panel whose code this page can no longer fetch.

  Shown only when a reload has already been tried (lib/upgrade.ts): the page is older
  than the server, and reloading is what fixes it, so the offer is a button rather than
  another automatic reload.
-->
<script lang="ts">
  import { app } from "$lib/stores/app.svelte";
</script>

<div class="unavailable">
  <p>{app.t("app.panelStale")}</p>
  <button class="btn btn-primary" onclick={() => location.reload()} type="button">
    {app.t("app.reload")}
  </button>
</div>

<style>
  .unavailable {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
    align-items: center;
    justify-content: center;
    padding: 2rem 1rem;
    color: var(--text-muted);
    text-align: center;
  }
</style>
