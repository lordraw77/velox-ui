<!--
  The message input.

  Enter sends, Shift+Enter inserts a newline: the convention people already have from
  every other chat client. The textarea grows with its content up to a cap, then
  scrolls, so a long paste does not push the conversation off the screen.
-->
<script lang="ts">
  import { Recorder } from "$lib/audio/record";
  import { api } from "$lib/api/client";
  import type { Attachment } from "$lib/api/types";
  import { app } from "$lib/stores/app.svelte";

  interface Props {
    disabled: boolean;
    streaming: boolean;
    onsend: (text: string, attachments: Attachment[]) => void;
    onstop: () => void;
  }

  let { disabled, streaming, onsend, onstop }: Props = $props();

  let text = $state("");
  let textarea: HTMLTextAreaElement | undefined = $state();
  let recorder = new Recorder();
  let recording = $state(false);
  let transcribing = $state(false);

  let imagePanelOpen = $state(false);
  let imagePrompt = $state("");
  let generating = $state(false);
  let generatedImages = $state<string[]>([]);

  /** Images picked for the next message, shown as soon as they are chosen. */
  interface Pending {
    key: number;
    preview: string;
    name: string;
    /** Set once the upload finishes; the message can be sent only when all are set. */
    attachment: Attachment | null;
  }

  const MAX_ATTACHMENTS = 4;
  const IMAGE_TYPES = "image/png,image/jpeg,image/gif,image/webp";

  let pending = $state<Pending[]>([]);
  let fileInput: HTMLInputElement | undefined = $state();
  let nextKey = 0;

  let uploading = $derived(pending.some((entry) => entry.attachment === null));
  let hasContent = $derived(text.trim() !== "" || pending.length > 0);

  function addFiles(files: Iterable<File>): void {
    for (const file of files) {
      if (!IMAGE_TYPES.split(",").includes(file.type)) continue;
      if (pending.length >= MAX_ATTACHMENTS) {
        app.report(new Error(app.t("chat.attachmentLimit", { max: MAX_ATTACHMENTS })));
        break;
      }
      const entry: Pending = {
        key: nextKey++,
        preview: URL.createObjectURL(file),
        name: file.name,
        attachment: null,
      };
      pending.push(entry);
      api
        .uploadAttachment(file)
        .then((attachment) => {
          const current = pending.find((item) => item.key === entry.key);
          if (current) current.attachment = attachment;
        })
        .catch((error) => {
          removePending(entry.key);
          app.report(error);
        });
    }
  }

  function removePending(key: number): void {
    const entry = pending.find((item) => item.key === key);
    if (entry) URL.revokeObjectURL(entry.preview);
    pending = pending.filter((item) => item.key !== key);
  }

  function clearPending(): void {
    for (const entry of pending) URL.revokeObjectURL(entry.preview);
    pending = [];
  }

  function onPaste(event: ClipboardEvent): void {
    const files = [...(event.clipboardData?.files ?? [])].filter((file) =>
      file.type.startsWith("image/"),
    );
    if (files.length === 0) return;
    // A pasted screenshot has no text to insert; keep any text paste untouched.
    event.preventDefault();
    addFiles(files);
  }

  $effect(() => clearPending);

  const MAX_HEIGHT = 220;

  function resize(): void {
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, MAX_HEIGHT)}px`;
  }

  function submit(): void {
    const value = text.trim();
    if ((!value && pending.length === 0) || uploading || disabled || streaming) return;
    const attachments = pending.flatMap((entry) => (entry.attachment ? [entry.attachment] : []));
    onsend(value, attachments);
    text = "";
    clearPending();
    queueMicrotask(resize);
  }

  function onKeydown(event: KeyboardEvent): void {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      submit();
    }
  }

  async function toggleRecording(): Promise<void> {
    if (recording) {
      recording = false;
      const audio = await recorder.stop();
      if (!audio) return;
      transcribing = true;
      try {
        const { text: transcribed } = await api.transcribeAudio(audio);
        text = text ? `${text} ${transcribed}` : transcribed;
        queueMicrotask(resize);
      } catch (error) {
        app.report(error);
      } finally {
        transcribing = false;
      }
      return;
    }
    try {
      await recorder.start();
      recording = true;
    } catch (error) {
      app.report(error);
    }
  }

  async function generateImage(): Promise<void> {
    const prompt = imagePrompt.trim();
    if (!prompt || generating) return;
    generating = true;
    try {
      const { images } = await api.generateImages(prompt);
      generatedImages = [
        ...images.map((image) => `/api/files/${image.file_id}/content`),
        ...generatedImages,
      ];
      imagePrompt = "";
    } catch (error) {
      app.report(error);
    } finally {
      generating = false;
    }
  }
</script>

{#if imagePanelOpen && app.imagesEnabled}
  <div class="image-panel">
    <form
      class="image-form"
      onsubmit={(event) => {
        event.preventDefault();
        void generateImage();
      }}
    >
      <input
        bind:value={imagePrompt}
        placeholder={app.t("chat.imagePromptPlaceholder")}
        aria-label={app.t("chat.imagePromptPlaceholder")}
      />
      <button class="btn btn-primary" disabled={generating || !imagePrompt.trim()} type="submit">
        {generating ? app.t("chat.imageGenerating") : app.t("chat.imageGenerate")}
      </button>
    </form>
    {#if generatedImages.length > 0}
      <div class="image-results">
        {#each generatedImages as src (src)}
          <img {src} alt={app.t("chat.imageGenerated")} />
        {/each}
      </div>
    {/if}
  </div>
{/if}

{#if pending.length > 0}
  <div class="pending" data-testid="pending-attachments">
    {#each pending as entry (entry.key)}
      <span class="chip" class:uploading={entry.attachment === null}>
        <img src={entry.preview} alt={entry.name} />
        <button
          class="remove"
          onclick={() => removePending(entry.key)}
          aria-label={app.t("chat.attachmentRemove")}
          title={app.t("chat.attachmentRemove")}
          type="button"
        >
          ×
        </button>
      </span>
    {/each}
  </div>
{/if}

<div class="composer">
  <input
    bind:this={fileInput}
    type="file"
    accept={IMAGE_TYPES}
    multiple
    hidden
    onchange={(event) => {
      addFiles(event.currentTarget.files ?? []);
      event.currentTarget.value = "";
    }}
    data-testid="attachment-input"
  />
  <button
    class="btn btn-ghost"
    onclick={() => fileInput?.click()}
    disabled={disabled || streaming || pending.length >= MAX_ATTACHMENTS}
    title={app.t("chat.attach")}
    aria-label={app.t("chat.attach")}
    type="button"
    data-testid="attach"
  >
    📎
  </button>

  <textarea
    bind:this={textarea}
    bind:value={text}
    oninput={resize}
    onkeydown={onKeydown}
    onpaste={onPaste}
    placeholder={disabled ? app.t("chat.placeholderNoModel") : app.t("chat.placeholder")}
    disabled={disabled && !streaming}
    rows="1"
    aria-label={app.t("chat.placeholder")}
    data-testid="composer"
  ></textarea>

  {#if app.imagesEnabled}
    <button
      class="btn btn-ghost"
      class:active={imagePanelOpen}
      onclick={() => (imagePanelOpen = !imagePanelOpen)}
      title={app.t("chat.imageToggle")}
      aria-label={app.t("chat.imageToggle")}
      type="button"
      data-testid="image-toggle"
    >
      🖼
    </button>
  {/if}

  {#if app.voiceEnabled}
    <button
      class="btn btn-ghost"
      class:active={recording}
      disabled={transcribing}
      onclick={toggleRecording}
      title={app.t(recording ? "chat.micStop" : "chat.micStart")}
      aria-label={app.t(recording ? "chat.micStop" : "chat.micStart")}
      type="button"
      data-testid="mic"
    >
      {#if transcribing}
        <span class="spinner"></span>
      {:else}
        🎙
      {/if}
    </button>
  {/if}

  {#if streaming}
    <button class="btn" onclick={onstop} type="button" data-testid="stop">
      {app.t("chat.stop")}
    </button>
  {:else}
    <button
      class="btn btn-primary"
      onclick={submit}
      disabled={disabled || !hasContent || uploading}
      type="button"
      data-testid="send"
    >
      {app.t("chat.send")}
    </button>
  {/if}
</div>

<style>
  .composer {
    display: flex;
    gap: 0.6rem;
    align-items: flex-end;
    width: 100%;
    max-width: var(--content-width);
    margin: 0 auto;
    /* The bottom padding also clears the iPhone home indicator; `max` keeps the
       desktop spacing where the inset is zero. */
    padding: 0.75rem var(--gutter) max(1rem, env(safe-area-inset-bottom));
  }

  textarea {
    flex: 1;
    min-height: 2.5rem;
    padding: 0.6rem 0.75rem;
    resize: none;
    background: var(--bg-raised);
    border: 1px solid var(--border);
    border-radius: var(--radius);
  }

  textarea:focus {
    border-color: var(--accent);
    outline: none;
  }

  textarea:disabled {
    color: var(--text-faint);
    background: var(--bg-sunken);
  }

  button {
    height: 2.5rem;
    padding-inline: 1rem;
  }

  button.active {
    color: var(--danger);
  }

  .pending {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    width: 100%;
    max-width: var(--content-width);
    margin: 0 auto;
    padding: 0.5rem var(--gutter) 0;
  }

  .chip {
    position: relative;
    display: inline-flex;
  }

  .chip img {
    width: 64px;
    height: 64px;
    object-fit: cover;
    border: 1px solid var(--border);
    border-radius: var(--radius);
  }

  .chip.uploading img {
    opacity: 0.45;
  }

  .remove {
    position: absolute;
    top: -0.4rem;
    right: -0.4rem;
    width: 1.3rem;
    height: 1.3rem;
    padding: 0;
    line-height: 1;
    background: var(--bg-raised);
    border: 1px solid var(--border);
    border-radius: 50%;
  }

  .image-panel {
    width: 100%;
    max-width: var(--content-width);
    margin: 0 auto;
    padding: 0 var(--gutter);
  }

  .image-form {
    display: flex;
    gap: 0.5rem;
    margin-bottom: 0.5rem;
  }

  .image-form input {
    flex: 1;
    padding: 0.5rem 0.7rem;
    background: var(--bg-raised);
    border: 1px solid var(--border);
    border-radius: var(--radius);
  }

  .image-results {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin-bottom: 0.5rem;
  }

  .image-results img {
    max-width: 160px;
    max-height: 160px;
    border: 1px solid var(--border);
    border-radius: var(--radius);
  }

  /*
   * At 390px the gutter plus a labelled send button plus up to three icon
   * buttons overflows the row. The labels go; the icons stay.
   */
  @media (width <= 900px) {
    .composer {
      gap: 0.35rem;
    }

    button {
      padding-inline: 0.6rem;
    }
  }
</style>
