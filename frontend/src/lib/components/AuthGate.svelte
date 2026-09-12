<!--
  Sign-in, and the first-run administrator claim.

  A brand-new instance has no accounts at all: no password was generated and written to
  a log, so the first person to register becomes the administrator. That is the screen
  this shows when the server reports `setup_required`.
-->
<script lang="ts">
  import { app } from "$lib/stores/app.svelte";

  let email = $state("");
  let password = $state("");
  let name = $state("");
  let busy = $state(false);
  let failure = $state<string | null>(null);

  let setup = $derived(app.config?.auth.setup_required === true);

  async function submit(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    if (busy) return;
    busy = true;
    failure = null;
    try {
      if (setup) await app.signUp(email, password, name || email);
      else await app.signIn(email, password);
    } catch (error) {
      failure = error instanceof Error ? error.message : String(error);
    } finally {
      busy = false;
    }
  }
</script>

<div class="gate">
  <form onsubmit={submit}>
    <h1>{app.t("app.name")}</h1>
    <p class="hint tagline">{app.t("app.tagline")}</p>

    {#if setup}
      <p class="setup">{app.t("auth.createAdminHint")}</p>
      <div class="field">
        <label for="name">{app.t("auth.name")}</label>
        <input id="name" bind:value={name} placeholder={app.t("auth.namePlaceholder")} />
      </div>
    {/if}

    <div class="field">
      <label for="email">{app.t("auth.email")}</label>
      <input
        id="email"
        type="email"
        bind:value={email}
        placeholder={app.t("auth.emailPlaceholder")}
        required
        autocomplete="username"
      />
    </div>

    <div class="field">
      <label for="password">{app.t("auth.password")}</label>
      <input
        id="password"
        type="password"
        bind:value={password}
        required
        autocomplete={setup ? "new-password" : "current-password"}
      />
      {#if setup}<span class="hint">{app.t("auth.passwordHint")}</span>{/if}
    </div>

    {#if failure}<p class="failure">{failure}</p>{/if}

    <button class="btn btn-primary submit" disabled={busy} type="submit" data-testid="auth-submit">
      {#if busy}<span class="spinner"></span>{/if}
      {setup ? app.t("auth.create") : app.t("auth.signIn")}
    </button>
  </form>
</div>

<style>
  .gate {
    display: grid;
    place-items: center;
    height: 100%;
    padding: 1.5rem;
  }

  form {
    display: flex;
    flex-direction: column;
    gap: 0.85rem;
    width: 100%;
    max-width: 22rem;
    padding: 1.5rem;
    background: var(--bg-raised);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    box-shadow: var(--shadow);
  }

  h1 {
    margin: 0;
    font-size: 1.2rem;
  }

  .tagline {
    margin: -0.6rem 0 0.2rem;
  }

  .setup {
    margin: 0;
    padding: 0.55rem 0.7rem;
    font-size: 0.82rem;
    background: var(--accent-soft);
    border-radius: var(--radius-sm);
  }

  .failure {
    margin: 0;
    font-size: 0.82rem;
    color: var(--danger);
  }

  .submit {
    margin-top: 0.3rem;
  }
</style>
