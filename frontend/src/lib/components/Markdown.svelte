<!--
  Renders one message body.

  The stable prefix is injected as HTML, which is safe only because the worker escaped
  raw HTML before parsing and checked every link target (see lib/markdown/sanitize.ts).
  The in-progress tail is bound as text, never as HTML, so a half-written tag cannot
  be interpreted even momentarily.
-->
<script lang="ts">
  interface Props {
    html: string;
    tail: string;
  }

  let { html, tail }: Props = $props();
</script>

<div class="md">
  <!-- eslint-disable-next-line svelte/no-at-html-tags -->
  {@html html}
  {#if tail}<span class="md-tail">{tail}</span>{/if}
</div>
