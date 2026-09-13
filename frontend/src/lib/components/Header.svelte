<!--
  The top bar: model selection, backend health, theme and locale.

  Backend health is shown here permanently rather than surfaced only on failure. A
  local host that is switched off is a normal state, and a badge is the honest way to
  say so — better than discovering it by sending a message that fails (ADR-0008).
-->
<script lang="ts">
  import type { HealthState } from "$lib/api/types";
  import { api } from "$lib/api/client";
  import { app } from "$lib/stores/app.svelte";
  import type { Locale } from "$lib/i18n";
  import { LOCALES } from "$lib/i18n";
  import ModelPicker from "./ModelPicker.svelte";

  let health = $state<Record<string, HealthState>>({});

  const THEMES = ["auto", "light", "dark"] as const;

  const THEME_LABEL = { auto: "theme.auto", light: "theme.light", dark: "theme.dark" } as const;

  $effect(() => {
    // Health is polled rather than pushed for now; the interval is deliberately slow
    // because an offline host must cost nothing to keep noticing.
    let cancelled = false;
    const poll = async () => {
      try {
        const { providers } = await api.providers();
        if (cancelled) return;
        health = Object.fromEntries(
          providers.map((entry) => [entry.provider_id, entry.health?.state ?? "unknown"]),
        );
      } catch {
        // An unreachable server is already reported elsewhere; do not double-report.
      }
    };
    void poll();
    const timer = setInterval(poll, 30_000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  });

  const HEALTH_LABEL: Record<HealthState, string> = {
    up: "providers.online",
    down: "providers.offline",
    degraded: "providers.degraded",
    unknown: "providers.unknown",
  };

  function cycleTheme(): void {
    const index = THEMES.indexOf(app.theme);
    app.setTheme(THEMES[(index + 1) % THEMES.length]!);
  }
</script>

<header>
  <ModelPicker />

  <div class="right">
    <div class="health" title={app.t("providers.title")}>
      {#each Object.entries(health) as [providerId, state] (providerId)}
        <span class="badge" class:badge-offline={state === "down"} class:badge-local={state === "up"}>
          {providerId}: {app.t(HEALTH_LABEL[state])}
        </span>
      {/each}
    </div>

    {#if app.model}
      <button
        class="btn btn-ghost"
        class:open={app.paramsOpen}
        onclick={() => (app.paramsOpen = !app.paramsOpen)}
        aria-pressed={app.paramsOpen}
        type="button"
        data-testid="params-toggle"
      >
        {app.t("params.title")}
      </button>
    {/if}

    <select
      value={app.locale}
      onchange={(event) => app.setLocale((event.currentTarget as HTMLSelectElement).value as Locale)}
      aria-label="Language"
    >
      {#each LOCALES as locale (locale.code)}
        <option value={locale.code}>{locale.label}</option>
      {/each}
    </select>

    <button
      class="btn btn-ghost btn-icon"
      onclick={cycleTheme}
      title={`${app.t("theme.toggle")} — ${app.t(THEME_LABEL[app.theme])}`}
      aria-label={`${app.t("theme.toggle")} — ${app.t(THEME_LABEL[app.theme])}`}
      type="button"
    >
      {app.theme === "dark" ? "◐" : app.theme === "light" ? "○" : "◑"}
    </button>

    {#if app.session}
      <button class="btn btn-ghost" onclick={() => app.signOut()} type="button">
        {app.t("auth.signOut")}
      </button>
    {/if}
  </div>
</header>

<style>
  header {
    display: flex;
    flex-wrap: wrap;
    gap: 0.75rem;
    align-items: center;
    justify-content: space-between;
    padding: 0.55rem 1rem;
    background: var(--bg-raised);
    border-bottom: 1px solid var(--border);
  }

  .right {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
    align-items: center;
  }

  .open {
    background: var(--bg-active);
  }

  .health {
    display: flex;
    flex-wrap: wrap;
    gap: 0.3rem;
  }

  select {
    padding: 0.25rem 0.4rem;
    font-size: 0.8rem;
    background: var(--bg-raised);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
  }
</style>
