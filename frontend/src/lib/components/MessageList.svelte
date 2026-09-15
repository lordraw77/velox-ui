<!--
  The virtualised conversation.

  Only the visible window of messages exists in the DOM, which is what makes opening a
  five-thousand-message conversation a bounded amount of work rather than a
  proportional one.

  Two behaviours are subtler than the virtualisation itself:

  **Scroll correction.** When a message above the viewport is measured and turns out
  taller than estimated, everything below it shifts. Left alone, the text under the
  reader's eyes jumps. The list reports the delta and `scrollTop` is adjusted by the
  same amount, so nothing appears to move.

  **Bottom anchoring, but only when wanted.** A conversation is read at its end, so a
  streaming reply should keep the view pinned there. If the reader has scrolled up to
  re-read something, it must not yank them back down — so anchoring is tracked as
  state, set by whether they are near the bottom, and dropped the moment they scroll
  away.
-->
<script lang="ts">
  import { VirtualList } from "$lib/virtual/list";
  import type { RenderedMessage } from "$lib/stores/conversation.svelte";
  import Message from "./Message.svelte";

  interface Props {
    messages: RenderedMessage[];
    /** Set while a reply is streaming, so the view follows the new text. */
    streaming: boolean;
    /** Set while an earlier page is being fetched. */
    loadingOlder?: boolean;
    /** Called when the reader nears the top, to fetch the page before it. */
    onreachtop?: () => void;
  }

  let { messages, streaming, loadingOlder = false, onreachtop }: Props = $props();

  /** How close to the top, in pixels, the next page starts loading. */
  const LOAD_OLDER_MARGIN = 800;

  const list = new VirtualList({ estimatedItemHeight: 132, overscan: 3 });

  let viewport: HTMLElement | undefined = $state();
  let viewportHeight = $state(0);
  let scrollTop = $state(0);
  let anchored = $state(true);

  /**
   * Bumped whenever a measurement changes the layout.
   *
   * `VirtualList` is a plain object, deliberately: it is pure, unit-tested, and knows
   * nothing about Svelte. That means mutating it is invisible to the reactivity graph,
   * so without this counter a height measurement would update the list's internals and
   * nothing would recompute -- which showed up as the last message being clipped
   * behind the composer once its final formatting made it taller.
   */
  let revision = $state(0);

  /**
   * Older pages are inserted at the start. Detected by the first message changing to
   * one that precedes the previous first, so the list can shift its measurements
   * instead of treating it as a different conversation. Plain variables, not state:
   * they are bookkeeping between two renders, and making them reactive would loop.
   */
  let firstId: string | null = null;
  let pendingShift = 0;

  let window_ = $derived.by(() => {
    void revision;
    const first = messages[0]?.id ?? null;
    if (firstId !== null && first !== firstId && messages.length > list.count) {
      const shifted = messages.findIndex((message) => message.id === firstId);
      if (shifted > 0) pendingShift += list.prepend(shifted);
    }
    firstId = first;
    list.setCount(messages.length);
    return list.windowFor(scrollTop, viewportHeight);
  });

  /** Keep the message under the reader's eyes still when a page is prepended. */
  $effect(() => {
    void window_.totalHeight;
    if (pendingShift === 0 || !viewport) return;
    const shift = pendingShift;
    pendingShift = 0;
    viewport.scrollTop += shift;
    scrollTop = viewport.scrollTop;
  });

  let visible = $derived(messages.slice(window_.start, window_.end));

  /** Track the viewport's own size; a window resize changes what is visible. */
  $effect(() => {
    if (!viewport) return;
    let lastWidth = viewport.clientWidth;
    const observer = new ResizeObserver(() => {
      viewportHeight = viewport?.clientHeight ?? 0;
      const width = viewport?.clientWidth ?? lastWidth;
      if (width === lastWidth) return;
      // A width change invalidates every measured height — messages reflow, so
      // the cached heights describe a layout that no longer exists. Only the
      // rendered items would otherwise re-measure, leaving the rest wrong and
      // the scroll position drifting (rotating a phone mid-conversation).
      lastWidth = width;
      list.invalidateMeasurements();
      revision += 1;
      if (anchored && viewport) {
        queueMicrotask(() => {
          if (viewport && anchored) viewport.scrollTop = list.bottomScrollTop(viewport.clientHeight);
        });
      }
    });
    observer.observe(viewport);
    viewportHeight = viewport.clientHeight;
    return () => observer.disconnect();
  });

  /** Follow the end of the conversation while anchored. */
  $effect(() => {
    // Reading these keeps the effect subscribed to growth during streaming.
    void messages.length;
    void window_.totalHeight;
    void streaming;
    if (!anchored || !viewport) return;
    queueMicrotask(() => {
      if (!viewport || !anchored) return;
      viewport.scrollTop = list.bottomScrollTop(viewport.clientHeight);
    });
  });

  function onScroll(): void {
    if (!viewport) return;
    scrollTop = viewport.scrollTop;
    // Re-deciding on every scroll is what lets the reader take control by scrolling
    // up and hand it back by scrolling down, with no button to press.
    anchored = list.isAtBottom(scrollTop, viewport.clientHeight);
    if (scrollTop < LOAD_OLDER_MARGIN) onreachtop?.();
  }

  function measure(index: number, height: number): void {
    const delta = list.measure(index, height);
    if (delta === 0) return;
    revision += 1;
    if (!viewport) return;
    // Only a change above the viewport shifts what is on screen. A change below it
    // moves content the reader cannot see.
    if (list.offsetOf(index) < scrollTop) {
      viewport.scrollTop += delta;
      scrollTop = viewport.scrollTop;
    }
  }
</script>

{#if loadingOlder}
  <div class="older" role="status"><span class="spinner"></span></div>
{/if}
<div class="viewport" bind:this={viewport} onscroll={onScroll}>
  <div class="content" style:height="{window_.totalHeight}px">
    <div class="window" style:transform="translateY({window_.offsetTop}px)">
      {#each visible as message, offset (message.id)}
        <Message
          {message}
          onmeasure={(height) => measure(window_.start + offset, height)}
        />
      {/each}
    </div>
  </div>
</div>

<style>
  .older {
    display: flex;
    justify-content: center;
    padding: 0.35rem;
    color: var(--text-faint);
  }

  .viewport {
    flex: 1;
    overflow-y: auto;
    overscroll-behavior: contain;
  }

  .content {
    position: relative;
    width: 100%;
    max-width: var(--content-width);
    margin: 0 auto;
    /* Same gutter as the composer and the status line, so message text lines up
       with the input below it at every width. */
    padding: 0 var(--gutter);
  }

  .window {
    /* Absolutely positioned inside the padded `.content`, so it already starts at the
       content edge; repeating the padding here would inset the text twice. */
    position: absolute;
    top: 0;
    left: 0;
    width: 100%;
  }
</style>
