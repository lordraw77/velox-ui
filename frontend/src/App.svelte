<!--
  The application shell.

  Routing is a single piece of state, not a router: the whole interface is one screen
  with a selected conversation, and a router library would be among the largest things
  in a 200 KB budget for no benefit. The conversation id lives in the URL hash so a
  link is shareable and the back button works.
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

  let lastSent = $state<string | null>(null);

  $effect(() => {
    void app.boot().then(() => {
      const id = location.hash.replace(/^#\/?chat\//, "");
      if (id && id !== location.hash) void conversation.open(id);
    });
  });

  function select(id: string | null): void {
    if (id === null) {
      conversation.reset();
      history.replaceState(null, "", "#");
      return;
    }
    history.replaceState(null, "", `#/chat/${id}`);
    void conversation.open(id);
  }

  function send(text: string): void {
    lastSent = text;
    void conversation.send(text);
  }

  function retry(): void {
    if (lastSent) void conversation.send(lastSent);
  }

  let canSend = $derived(app.modelRef !== null);
</script>

{#if !app.booted}
  <div class="boot"><span class="spinner"></span></div>
{:else if !app.authenticated}
  <AuthGate />
{:else}
  <div class="shell">
    <Sidebar onselect={select} />

    <main>
      <Header />

      {#if app.error}
        <ErrorBanner error={app.error} ondismiss={() => app.dismissError()} />
      {/if}

      {#if conversation.loading}
        <div class="boot"><span class="spinner"></span></div>
      {:else if conversation.isEmpty}
        <div class="welcome">
          <p>{app.t("chat.empty")}</p>
          <p class="hint">{app.t("chat.emptyHint")}</p>
        </div>
      {:else}
        <MessageList messages={conversation.messages} streaming={conversation.streaming} />
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
    </main>
  </div>
{/if}

<style>
  .shell {
    display: flex;
    height: 100%;
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
    padding: 0 2.5rem;
  }

  @media (width <= 720px) {
    .shell {
      flex-direction: column;
    }
  }
</style>
