<!--
  The message input.

  Enter sends, Shift+Enter inserts a newline: the convention people already have from
  every other chat client. The textarea grows with its content up to a cap, then
  scrolls, so a long paste does not push the conversation off the screen.
-->
<script lang="ts">
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
</script>

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
    padding: 0.75rem 2.5rem 1rem;
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
</style>
