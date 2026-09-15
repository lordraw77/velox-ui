<!--
  The application shell.

  Routing is a single piece of state, not a router: the interface has a conversation
  view and two management pages, and a router library would be among the largest
  things in a 200 KB budget for no benefit. The location lives in the URL hash
  (`#/chat/<id>`, `#/models`, `#/providers`) so a link is shareable and the back button
  works.

  The management pages and the parameter panel are imported on demand. A person
  opening a conversation downloads none of them.
-->
<script lang="ts">
  import AuthGate from "$lib/components/AuthGate.svelte";
  import Composer from "$lib/components/Composer.svelte";
  import ErrorBanner from "$lib/components/ErrorBanner.svelte";
  import Header from "$lib/components/Header.svelte";
  import MessageList from "$lib/components/MessageList.svelte";
  import Sidebar from "$lib/components/Sidebar.svelte";
  import StatusLine from "$lib/components/StatusLine.svelte";
  import { app } from "$lib/stores/app.svelte";
  import { conversation } from "$lib/stores/conversation.svelte";
  import { trackVisualViewport } from "$lib/viewport";

  type View =
    | "chat"
    | "search"
    | "models"
    | "providers"
    | "custom-models"
    | "knowledge"
    | "mcp"
    | "plugins"
    | "admin";

  let view = $state<View>("chat");
  let lastSent = $state<string | null>(null);

  function route(): void {
    const hash = location.hash;
    if (hash.startsWith("#/models")) {
      view = "models";
    } else if (hash.startsWith("#/providers")) {
      view = "providers";
    } else if (hash.startsWith("#/search")) {
      view = "search";
    } else if (hash.startsWith("#/custom-models")) {
      view = "custom-models";
    } else if (hash.startsWith("#/knowledge")) {
      view = "knowledge";
    } else if (hash.startsWith("#/mcp")) {
      view = "mcp";
    } else if (hash.startsWith("#/plugins")) {
      view = app.isAdmin ? "plugins" : "chat";
    } else if (hash.startsWith("#/admin")) {
      view = app.isAdmin ? "admin" : "chat";
    } else {
      view = "chat";
      const id = hash.replace(/^#\/?chat\//, "");
      if (id && id !== hash && id !== conversation.id) void conversation.open(id);
    }
  }

  $effect(() => {
    void app.boot().then(route);
    window.addEventListener("hashchange", route);
    return () => window.removeEventListener("hashchange", route);
  });

  function select(id: string | null): void {
    view = "chat";
    closeDrawer();
    if (id === null) {
      conversation.reset();
      history.replaceState(null, "", "#");
      return;
    }
    history.replaceState(null, "", `#/chat/${id}`);
    void conversation.open(id);
  }

  function navigate(target: Exclude<View, "chat">): void {
    closeDrawer();
    location.hash = `#/${target}`;
  }

  function send(text: string): void {
    lastSent = text;
    void conversation.send(text);
  }

  function retry(): void {
    if (lastSent) void conversation.send(lastSent);
  }

  let canSend = $derived(app.modelRef !== null);

  // Only meaningful below the drawer breakpoint; above it the sidebar is always
  // on screen and this state is simply never read.
  let drawerOpen = $state(false);
  // Mirrors the CSS breakpoint. Needed in JS, not just CSS, because an
  // off-canvas drawer must also leave the focus order — which is an attribute,
  // not a style.
  let narrow = $state(false);

  $effect(() => trackVisualViewport());

  $effect(() => {
    const query = window.matchMedia("(width <= 900px)");
    const sync = () => {
      narrow = query.matches;
      if (!narrow) drawerOpen = false;
    };
    sync();
    query.addEventListener("change", sync);
    return () => query.removeEventListener("change", sync);
  });

  function closeDrawer(): void {
    drawerOpen = false;
  }

  function onShellKeydown(event: KeyboardEvent): void {
    if (event.key === "Escape" && drawerOpen) closeDrawer();
  }
</script>

<svelte:window onkeydown={onShellKeydown} />

{#if !app.booted}
  <div class="boot"><span class="spinner"></span></div>
{:else if !app.authenticated}
  <AuthGate />
{:else}
  <div class="shell" class:drawer-open={drawerOpen}>
    <Sidebar
      onselect={select}
      onnavigate={navigate}
      current={view}
      inert={narrow && !drawerOpen}
      onclose={closeDrawer}
    />

    <!-- Only hit-testable while the drawer is open (see `.backdrop` below), so it
         never intercepts clicks on the desktop layout. -->
    <button
      class="backdrop"
      aria-label={app.t("nav.closeMenu")}
      tabindex={drawerOpen ? 0 : -1}
      onclick={closeDrawer}
      type="button"
    ></button>

    <main>
      <Header onmenu={() => (drawerOpen = true)} />

      {#if app.error}
        <ErrorBanner error={app.error} ondismiss={() => app.dismissError()} />
      {/if}

      {#if view === "models"}
        {#await import("$lib/components/ModelsPanel.svelte") then { default: ModelsPanel }}
          <ModelsPanel />
        {/await}
      {:else if view === "providers"}
        {#await import("$lib/components/ProvidersPanel.svelte") then { default: ProvidersPanel }}
          <ProvidersPanel />
        {/await}
      {:else if view === "search"}
        {#await import("$lib/components/SearchPanel.svelte") then { default: SearchPanel }}
          <SearchPanel onopen={select} />
        {/await}
      {:else if view === "custom-models"}
        {#await import("$lib/components/CustomModelsPanel.svelte") then { default: CustomModelsPanel }}
          <CustomModelsPanel onstart={() => (view = "chat")} />
        {/await}
      {:else if view === "knowledge"}
        {#await import("$lib/components/KnowledgePanel.svelte") then { default: KnowledgePanel }}
          <KnowledgePanel />
        {/await}
      {:else if view === "mcp"}
        {#await import("$lib/components/McpServersPanel.svelte") then { default: McpServersPanel }}
          <McpServersPanel />
        {/await}
      {:else if view === "plugins"}
        {#await import("$lib/components/PluginsPanel.svelte") then { default: PluginsPanel }}
          <PluginsPanel />
        {/await}
      {:else if view === "admin"}
        {#await import("$lib/components/AdminPanel.svelte") then { default: AdminPanel }}
          <AdminPanel />
        {/await}
      {:else}
        {#if app.paramsOpen && app.model}
          {#await import("$lib/components/ParamsPanel.svelte") then { default: ParamsPanel }}
            <ParamsPanel />
          {/await}
        {/if}

        {#if conversation.loading}
          <div class="boot"><span class="spinner"></span></div>
        {:else if conversation.isEmpty}
          <div class="welcome">
            <p>{app.t("chat.empty")}</p>
            <p class="hint">{app.t("chat.emptyHint")}</p>
          </div>
        {:else}
          <MessageList
            messages={conversation.messages}
            streaming={conversation.streaming}
            loadingOlder={conversation.loadingOlder}
            onreachtop={() => conversation.loadOlder()}
          />
        {/if}

        <div class="footer">
          {#if conversation.streamError}
            <ErrorBanner
              error={conversation.streamError}
              ondismiss={() => (conversation.streamError = null)}
              onretry={retry}
            />
          {/if}
          <div class="statusline"><StatusLine phase={conversation.phase} /></div>
          <Composer
            disabled={!canSend}
            streaming={conversation.streaming}
            onsend={send}
            onstop={() => conversation.stop()}
          />
        </div>
      {/if}
    </main>
  </div>
{/if}

<style>
  .shell {
    display: flex;
    /* `--viewport-height` is set by `lib/viewport.ts` only while a visual
       viewport is reported (i.e. the keyboard is up on iOS); otherwise this
       falls back to the dynamic viewport height. */
    height: var(--viewport-height, 100dvh);
    overflow: hidden;
  }

  main {
    display: flex;
    flex: 1;
    flex-direction: column;
    min-width: 0;
  }

  .boot {
    display: grid;
    flex: 1;
    place-items: center;
  }

  .welcome {
    display: flex;
    flex: 1;
    flex-direction: column;
    gap: 0.2rem;
    align-items: center;
    justify-content: center;
    color: var(--text-muted);
  }

  .welcome p {
    margin: 0;
  }

  .footer {
    border-top: 1px solid var(--border);
  }

  .statusline {
    width: 100%;
    max-width: var(--content-width);
    margin: 0 auto;
    padding: 0 var(--gutter);
  }

  /* Inert on the desktop layout: no size, not focusable, nothing to click. */
  .backdrop {
    display: none;
  }

  /*
   * Below the breakpoint the sidebar leaves the flex flow and becomes an
   * off-canvas drawer. It stays mounted and is moved with `transform` rather
   * than unmounted, because its conversation list holds keyset-pagination
   * scroll state that would be thrown away and re-fetched on every open.
   */
  @media (width <= 900px) {
    .shell :global(aside) {
      position: fixed;
      inset: 0 auto 0 0;
      z-index: 20;
      width: min(var(--sidebar-width), 84vw);
      transform: translateX(-100%);
      transition: transform 0.18s ease;
      box-shadow: var(--shadow);
    }

    .shell.drawer-open :global(aside) {
      transform: translateX(0);
    }

    .shell.drawer-open .backdrop {
      display: block;
      position: fixed;
      inset: 0;
      z-index: 10;
      background: rgb(0 0 0 / 40%);
      border: none;
    }
  }

  /* The drawer slides; honour a reduced-motion preference. */
  @media (prefers-reduced-motion: reduce) {
    .shell :global(aside) {
      transition: none;
    }
  }
</style>
