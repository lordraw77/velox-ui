<!--
  An uploaded image, shown from its stored bytes.

  An `<img src="/api/files/...">` would go out without the bearer token, so the bytes are
  fetched through the API client and shown from an object URL, revoked when the
  component goes away. The element has a fixed box from the start so the message does
  not change height when the image arrives, which would disturb the virtual list's
  measurements.
-->
<script lang="ts">
  import { api } from "$lib/api/client";
  import { app } from "$lib/stores/app.svelte";
  import type { Attachment } from "$lib/api/types";

  interface Props {
    attachment: Attachment;
  }

  let { attachment }: Props = $props();

  let src = $state<string | null>(null);
  let failed = $state(false);

  $effect(() => {
    const id = attachment.id;
    let url: string | null = null;
    let cancelled = false;
    src = null;
    failed = false;
    api
      .fileBlob(id)
      .then((blob) => {
        if (cancelled) return;
        url = URL.createObjectURL(blob);
        src = url;
      })
      .catch(() => {
        if (!cancelled) failed = true;
      });
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  });
</script>

<span class="thumb" class:failed>
  {#if src}
    <img {src} alt={attachment.filename} />
  {:else if failed}
    <span class="note">{app.t("chat.attachmentMissing")}</span>
  {/if}
</span>

<style>
  .thumb {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 160px;
    height: 160px;
    overflow: hidden;
    color: var(--text-faint);
    background: var(--bg-sunken);
    border: 1px solid var(--border);
    border-radius: var(--radius);
  }

  img {
    width: 100%;
    height: 100%;
    object-fit: cover;
  }

  .note {
    padding: 0.5rem;
    font-size: 0.8rem;
    text-align: center;
  }
</style>
