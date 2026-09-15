<!--
  Administrator user management: list, role, status, and creating accounts directly.

  Creating an account here bypasses `open_registration`: it is the one way to add
  users once self-service sign-up is closed (docs/design/03-http-api.md).

  Loaded lazily: it is not part of the initial bundle.
-->
<script lang="ts">
  import { onMount } from "svelte";
  import { api } from "$lib/api/client";
  import type { AdminUser, UserRole, UserStatus } from "$lib/api/types";
  import { formatDate } from "$lib/format";
  import { app } from "$lib/stores/app.svelte";

  const STATUS_LABEL: Record<UserStatus, string> = {
    active: "admin.statusActive",
    pending: "admin.statusPending",
    disabled: "admin.statusDisabled",
  };

  let users = $state<AdminUser[]>([]);
  let cursor = $state<string | null>(null);
  let loading = $state(true);
  let busy = $state(false);

  let draft = $state({ email: "", password: "", name: "", role: "user" as UserRole });

  onMount(load);

  async function load(): Promise<void> {
    loading = true;
    try {
      const page = await api.adminUsers();
      users = page.items;
      cursor = page.next_cursor;
    } catch (error) {
      app.report(error);
    } finally {
      loading = false;
    }
  }

  async function loadMore(): Promise<void> {
    if (!cursor) return;
    try {
      const page = await api.adminUsers(cursor);
      users = [...users, ...page.items];
      cursor = page.next_cursor;
    } catch (error) {
      app.report(error);
    }
  }

  async function createUser(): Promise<void> {
    busy = true;
    try {
      await api.adminCreateUser(draft.email, draft.password, draft.name, draft.role);
      draft = { email: "", password: "", name: "", role: "user" };
      await load();
    } catch (error) {
      app.report(error);
    } finally {
      busy = false;
    }
  }

  async function setRole(user: AdminUser, role: UserRole): Promise<void> {
    try {
      const updated = await api.adminSetRole(user.id, role);
      users = users.map((entry) => (entry.id === user.id ? updated : entry));
    } catch (error) {
      app.report(error);
    }
  }

  async function toggleStatus(user: AdminUser): Promise<void> {
    const next: UserStatus = user.status === "disabled" ? "active" : "disabled";
    try {
      const updated = await api.adminSetStatus(user.id, next);
      users = users.map((entry) => (entry.id === user.id ? updated : entry));
    } catch (error) {
      app.report(error);
    }
  }

  async function remove(user: AdminUser): Promise<void> {
    if (!confirm(app.t("admin.deleteConfirm"))) return;
    try {
      await api.adminDeleteUser(user.id);
      users = users.filter((entry) => entry.id !== user.id);
    } catch (error) {
      app.report(error);
    }
  }
</script>

<div class="panel" data-testid="admin-panel">
  <div class="panel-inner">
    <div class="title-row">
      <h1>{app.t("admin.title")}</h1>
      <a class="btn btn-ghost" href="#/">{app.t("nav.back")}</a>
    </div>

    {#if loading}
      <span class="spinner"></span>
    {:else}
      <section class="card">
        <div class="table-wrap">
          <table class="data">
            <thead>
              <tr>
                <th>{app.t("admin.email")}</th>
                <th>{app.t("admin.name")}</th>
                <th>{app.t("admin.role")}</th>
                <th>{app.t("admin.status")}</th>
                <th>{app.t("admin.lastSeen")}</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {#each users as user (user.id)}
                <tr>
                  <td class="mono" data-label={app.t("admin.email")}>{user.email}</td>
                  <td data-label={app.t("admin.name")}>{user.name}</td>
                  <td data-label={app.t("admin.role")}>
                    <select
                      value={user.role}
                      disabled={user.id === app.session?.user_id}
                      onchange={(event) => setRole(user, event.currentTarget.value as UserRole)}
                    >
                      <option value="user">{app.t("admin.roleUser")}</option>
                      <option value="admin">{app.t("admin.roleAdmin")}</option>
                    </select>
                  </td>
                  <td data-label={app.t("admin.status")}><span class="badge" class:badge-danger={user.status === "disabled"}>{app.t(STATUS_LABEL[user.status])}</span></td>
                  <td class="hint" data-label={app.t("admin.lastSeen")}>{user.last_seen_at ? formatDate(user.last_seen_at, app.locale) : app.t("admin.never")}</td>
                  <td class="row-actions">
                    <button
                      class="btn btn-ghost"
                      disabled={user.id === app.session?.user_id}
                      onclick={() => toggleStatus(user)}
                      type="button"
                    >
                      {user.status === "disabled" ? app.t("admin.enable") : app.t("admin.disable")}
                    </button>
                    <button
                      class="btn btn-ghost danger"
                      disabled={user.id === app.session?.user_id}
                      onclick={() => remove(user)}
                      type="button"
                    >
                      {app.t("admin.delete")}
                    </button>
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
        {#if cursor}
          <button class="btn btn-ghost" onclick={loadMore} type="button">{app.t("admin.more")}</button>
        {/if}
      </section>

      <section class="card">
        <h2>{app.t("admin.add")}</h2>
        <form
          class="inline-form"
          onsubmit={(event) => {
            event.preventDefault();
            void createUser();
          }}
        >
          <div class="field">
            <label for="admin-email">{app.t("admin.email")}</label>
            <input id="admin-email" type="email" bind:value={draft.email} required />
          </div>
          <div class="field">
            <label for="admin-name">{app.t("admin.name")}</label>
            <input id="admin-name" bind:value={draft.name} required />
          </div>
          <div class="field">
            <label for="admin-password">{app.t("auth.password")}</label>
            <input id="admin-password" type="password" bind:value={draft.password} autocomplete="off" required minlength="10" />
          </div>
          <div class="field">
            <label for="admin-role">{app.t("admin.role")}</label>
            <select id="admin-role" bind:value={draft.role}>
              <option value="user">{app.t("admin.roleUser")}</option>
              <option value="admin">{app.t("admin.roleAdmin")}</option>
            </select>
          </div>
          <div class="row-actions full">
            <button class="btn btn-primary" disabled={busy} type="submit">{app.t("admin.create")}</button>
          </div>
        </form>
      </section>
    {/if}
  </div>
</div>

<style>
  .row-actions.full {
    flex-basis: 100%;
    justify-content: flex-end;
  }

  .danger {
    color: var(--danger);
  }
</style>
