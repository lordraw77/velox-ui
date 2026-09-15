<!--
  The message input.

  Enter sends, Shift+Enter inserts a newline: the convention people already have from
  every other chat client. The textarea grows with its content up to a cap, then
  scrolls, so a long paste does not push the conversation off the screen.
-->
<script lang="ts">
  import { Recorder } from "$lib/audio/record";
  import { api } from "$lib/api/client";
  import { app } from "$lib/stores/app.svelte";

  interface Props {
    disabled: boolean;
    streaming: boolean;
    onsend: (text: string) => void;
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

  const MAX_HEIGHT = 220;

  function resize(): void {
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, MAX_HEIGHT)}px`;
  }

  function submit(): void {
    const value = text.trim();
    if (!value || disabled || streaming) return;
    onsend(value);
    text = "";
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

<div class="composer">
  <textarea
    bind:this={textarea}
    bind:value={text}
    oninput={resize}
    onkeydown={onKeydown}
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
      disabled={disabled || text.trim() === ""}
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
