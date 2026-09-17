<!--
  MCP servers: add/edit/delete, connect to refresh the cached tool list, and set the
  approval mode a chat's tool calls against this server are gated by
  (services/chat.py, mcp/manager.py). Loaded lazily, same as every other management
  panel (App.svelte).
-->
<script lang="ts">
  import { onMount } from "svelte";
  import { api } from "$lib/api/client";
  import type { McpApproval, McpServer, McpTool, McpTransport } from "$lib/api/types";
  import { app } from "$lib/stores/app.svelte";

  let servers = $state<McpServer[]>([]);
  let loading = $state(true);
  let creating = $state(false);

  let newName = $state("");
  let newTransport = $state<McpTransport>("stdio");
  let newCommand = $state("");
  let newArgs = $state("");
  let newUrl = $state("");
  let newAuthToken = $state("");
  let newAuthEnv = $state("");
  let newApproval = $state<McpApproval>("always");

  let connecting = $state<string | null>(null);
  let toolsByServer = $state<Record<string, McpTool[]>>({});

  // The stored values are terse and one of them is historical (`http_sse` means
  // Streamable HTTP), so the interface shows what each one is.
  const transportLabels: Record<McpTransport, string> = {
    stdio: "mcp.transportStdio",
    http_sse: "mcp.transportStreamable",
    sse: "mcp.transportSse",
    http_auto: "mcp.transportAuto",
  };

  let importing = $state(false);
  let importRaw = $state("");
  let importBusy = $state(false);
  let importDroppedCwd = $state<string[]>([]);

  onMount(load);

  async function load(): Promise<void> {
    loading = true;
    try {
      servers = await api.mcpServers();
    } catch (error) {
      app.report(error);
    } finally {
      loading = false;
    }
  }

  function configFor(): Record<string, unknown> {
    if (newTransport === "stdio") {
      const args = newArgs
        .split(/\s+/)
        .map((part) => part.trim())
        .filter(Boolean);
      const config: Record<string, unknown> = { command: newCommand.trim(), args };
      if (newAuthEnv.trim()) config.auth_env = newAuthEnv.trim();
      return config;
    }
    return { url: newUrl.trim() };
  }

  async function createServer(): Promise<void> {
    const name = newName.trim();
    if (!name) return;
    try {
      await api.createMcpServer({
        name,
        transport: newTransport,
        config: configFor(),
        auth_token: newAuthToken.trim() || undefined,
        approval: newApproval,
      });
      newName = "";
      newCommand = "";
      newArgs = "";
      newUrl = "";
      newAuthToken = "";
      newAuthEnv = "";
      creating = false;
      await load();
    } catch (error) {
      app.report(error);
    }
  }

  async function importServers(): Promise<void> {
    const raw = importRaw.trim();
    if (!raw || importBusy) return;
    importBusy = true;
    try {
      const result = await api.importMcpServers(raw);
      importRaw = "";
      importing = false;
      importDroppedCwd = result.dropped_cwd;
      await load();
    } catch (error) {
      app.report(error);
    } finally {
      importBusy = false;
    }
  }

  async function setApproval(server: McpServer, approval: McpApproval): Promise<void> {
    try {
      await api.updateMcpServer(server.id, { approval });
      await load();
    } catch (error) {
      app.report(error);
    }
  }

  async function toggleEnabled(server: McpServer): Promise<void> {
    try {
      await api.updateMcpServer(server.id, { enabled: !server.enabled });
      await load();
    } catch (error) {
      app.report(error);
    }
  }

  async function removeServer(server: McpServer): Promise<void> {
    if (!confirm(app.t("mcp.deleteConfirm"))) return;
    try {
      await api.deleteMcpServer(server.id);
      delete toolsByServer[server.id];
      await load();
    } catch (error) {
      app.report(error);
    }
  }

  async function connect(server: McpServer): Promise<void> {
    connecting = server.id;
    try {
      const tools = await api.connectMcpServer(server.id);
      toolsByServer = { ...toolsByServer, [server.id]: tools };
      await load();
    } catch (error) {
      app.report(error);
    } finally {
      connecting = null;
    }
  }
</script>

<div class="panel" data-testid="mcp-panel">
  <div class="panel-inner">
    <div class="title-row">
      <h1>{app.t("mcp.title")}</h1>
      <a class="btn btn-ghost" href="#/">{app.t("nav.back")}</a>
    </div>
    <p class="hint">{app.t("mcp.intro")}</p>

    {#if loading}
      <span class="spinner"></span>
    {:else}
      <section class="card">
        <div class="table-wrap">
          <table class="data">
            <tbody>
              {#each servers as server (server.id)}
                <tr>
                  <td>
                    <strong>{server.name}</strong>
                    <div class="mono hint">
                      {app.t(transportLabels[server.transport] ?? server.transport)} · {server.tool_count}
                      {app.t("mcp.tools")}
                      {#if server.auth_hint}· {app.t("mcp.credentialSet")}{/if}
                    </div>
                    {#if toolsByServer[server.id]}
                      <ul class="tool-list">
                        {#each toolsByServer[server.id] as tool (tool.name)}
                          <li><code>{tool.name}</code> — {tool.description}</li>
                        {/each}
                      </ul>
                    {/if}
                  </td>
                  <td>
                    <select
                      value={server.approval}
                      onchange={(event) =>
                        setApproval(server, (event.target as HTMLSelectElement).value as McpApproval)}
                    >
                      <option value="always">{app.t("mcp.approvalAlways")}</option>
                      <option value="once">{app.t("mcp.approvalOnce")}</option>
                      <option value="never">{app.t("mcp.approvalNever")}</option>
                    </select>
                  </td>
                  <td class="row-actions">
                    <button class="btn btn-ghost" onclick={() => toggleEnabled(server)} type="button">
                      {server.enabled ? app.t("mcp.disable") : app.t("mcp.enable")}
                    </button>
                    <button
                      class="btn btn-ghost"
                      disabled={connecting === server.id}
                      onclick={() => connect(server)}
                      type="button"
                    >
                      {connecting === server.id ? app.t("mcp.connecting") : app.t("mcp.connect")}
                    </button>
                    <button class="btn btn-ghost danger" onclick={() => removeServer(server)} type="button">
                      {app.t("mcp.delete")}
                    </button>
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
        {#if servers.length === 0}
          <p class="hint">{app.t("mcp.none")}</p>
        {/if}
      </section>

      {#if creating}
        <section class="card">
          <form
            class="stack"
            onsubmit={(event) => {
              event.preventDefault();
              void createServer();
            }}
          >
            <div class="field">
              <label for="mcp-name">{app.t("mcp.name")}</label>
              <input id="mcp-name" bind:value={newName} required />
            </div>
            <div class="field">
              <label for="mcp-transport">{app.t("mcp.transport")}</label>
              <select id="mcp-transport" bind:value={newTransport}>
                <option value="stdio">{app.t("mcp.transportStdio")}</option>
                <option value="http_auto">{app.t("mcp.transportAuto")}</option>
                <option value="http_sse">{app.t("mcp.transportStreamable")}</option>
                <option value="sse">{app.t("mcp.transportSse")}</option>
              </select>
              {#if newTransport !== "stdio"}
                <p class="hint">{app.t("mcp.transportHint")}</p>
              {/if}
            </div>
            {#if newTransport === "stdio"}
              <div class="field">
                <label for="mcp-command">{app.t("mcp.command")}</label>
                <input id="mcp-command" bind:value={newCommand} placeholder="npx" required />
              </div>
              <div class="field">
                <label for="mcp-args">{app.t("mcp.args")}</label>
                <input
                  id="mcp-args"
                  bind:value={newArgs}
                  placeholder="-y @modelcontextprotocol/server-filesystem /data"
                />
              </div>
            {:else}
              <div class="field">
                <label for="mcp-url">{app.t("mcp.url")}</label>
                <input id="mcp-url" bind:value={newUrl} placeholder="https://example.com/mcp" required />
              </div>
            {/if}
            <div class="field">
              <label for="mcp-token">{app.t("mcp.authToken")}</label>
              <input id="mcp-token" type="password" bind:value={newAuthToken} autocomplete="off" />
              <p class="hint">{app.t("mcp.authTokenHint")}</p>
            </div>
            {#if newTransport === "stdio"}
              <div class="field">
                <label for="mcp-auth-env">{app.t("mcp.authEnv")}</label>
                <input id="mcp-auth-env" bind:value={newAuthEnv} placeholder="MCP_AUTH_TOKEN" />
                <p class="hint">{app.t("mcp.authEnvHint")}</p>
              </div>
            {/if}
            <div class="field">
              <label for="mcp-approval">{app.t("mcp.approval")}</label>
              <select id="mcp-approval" bind:value={newApproval}>
                <option value="always">{app.t("mcp.approvalAlways")}</option>
                <option value="once">{app.t("mcp.approvalOnce")}</option>
                <option value="never">{app.t("mcp.approvalNever")}</option>
              </select>
            </div>
            <div class="row-actions">
              <button class="btn btn-ghost" onclick={() => (creating = false)} type="button">
                {app.t("mcp.cancel")}
              </button>
              <button class="btn btn-primary" type="submit">{app.t("mcp.save")}</button>
            </div>
          </form>
        </section>
      {:else}
        <div class="row-actions">
          <button class="btn btn-primary" onclick={() => (creating = true)} type="button">
            {app.t("mcp.add")}
          </button>
          <button class="btn btn-ghost" onclick={() => (importing = true)} type="button">
            {app.t("mcp.import")}
          </button>
        </div>
      {/if}

      {#if importDroppedCwd.length > 0}
        <p class="hint">
          {app.t("mcp.importDroppedCwd", { names: importDroppedCwd.join(", ") })}
        </p>
      {/if}

      {#if importing}
        <section class="card">
          <form
            class="stack"
            onsubmit={(event) => {
              event.preventDefault();
              void importServers();
            }}
          >
            <div class="field">
              <label for="mcp-import-raw">{app.t("mcp.importLabel")}</label>
              <textarea
                id="mcp-import-raw"
                bind:value={importRaw}
                rows="8"
                placeholder={"{\n  \"mcpServers\": {\n    \"discogs\": {\n      \"command\": \"npx\",\n      \"args\": [\"-y\", \"discogs-mcp-server\"],\n      \"env\": {\"DISCOGS_PERSONAL_ACCESS_TOKEN\": \"...\"}\n    }\n  }\n}"}
              ></textarea>
              <p class="hint">{app.t("mcp.importHint")}</p>
            </div>
            <div class="row-actions">
              <button class="btn btn-ghost" onclick={() => (importing = false)} type="button">
                {app.t("mcp.cancel")}
              </button>
              <button class="btn btn-primary" disabled={importBusy} type="submit">
                {importBusy ? app.t("mcp.importing") : app.t("mcp.importSubmit")}
              </button>
            </div>
          </form>
        </section>
      {/if}
    {/if}
  </div>
</div>

<style>
  .danger {
    color: var(--danger);
  }

  .stack {
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
  }

  .tool-list {
    margin: 0.3rem 0 0;
    padding-left: 1.1rem;
    font-size: 0.85em;
  }
</style>
