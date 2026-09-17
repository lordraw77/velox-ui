#!/bin/bash
#
# Run the MCP server named by the container's arguments, bridged to Streamable HTTP.
#
#   docker run lordraw/velox-ui-mcp-gateway npx -y discogs-mcp-server@0.5.7
#   docker run lordraw/velox-ui-mcp-gateway uvx mcp-justwatch==0.0.1
#
# The arguments are the server's own command line -- the same `command` + `args` an MCP
# client config already has -- so moving a server from a desktop client into a
# container is a copy, not a translation.
#
# Tuning, all optional:
#   MCP_PORT                port to listen on                         (8000)
#   MCP_STATEFUL            one child per session, "true" or "false"  (true)
#   MCP_SESSION_TIMEOUT_MS  idle session lifetime                     (600000)
#   SUPERGATEWAY_ARGS       extra supergateway flags, space-separated

set -euo pipefail

if [[ $# -eq 0 ]]; then
  cat >&2 <<'USAGE'
velox-ui-mcp-gateway: no MCP server to run.

Pass the server's command line as the container's arguments, for example:

  docker run -p 8000:8000 lordraw/velox-ui-mcp-gateway npx -y @modelcontextprotocol/server-everything
  docker run -p 8000:8000 lordraw/velox-ui-mcp-gateway uvx mcp-server-time

Engines on PATH: npx, node, uvx, uv, python, bunx, bun, deno, git.
USAGE
  exit 64
fi

# Supergateway's own flags as the arguments: hand them over untouched. Compose files
# written for the first version of this image started with `--stdio`, and they keep
# working.
if [[ "$1" == --* ]]; then
  exec supergateway "$@"
fi

# supergateway runs --stdio through a shell, so the arguments are rejoined into one
# string. Each is single-quoted, with embedded single quotes closed, escaped and
# reopened, which is the one quoting rule every POSIX shell agrees on: an argument with
# a space, a `$` or a quote reaches the server exactly as it was given.
quote() {
  local escaped=${1//\'/\'\\\'\'}
  printf "'%s'" "$escaped"
}
command_line=""
for argument in "$@"; do
  command_line+="${command_line:+ }$(quote "$argument")"
done

gateway=(
  --stdio "$command_line"
  --outputTransport streamableHttp
  --port "${MCP_PORT:-8000}"
  --healthEndpoint /healthz
)

# Stateful by default. A stateless gateway starts a fresh child per request, so
# `tools/list` reaches a server that never saw `initialize`, and a strict one (FastMCP)
# answers -32602. velox-ui returns the Mcp-Session-Id, so sessions cost nothing.
if [[ "${MCP_STATEFUL:-true}" == "true" ]]; then
  gateway+=(--stateful --sessionTimeout "${MCP_SESSION_TIMEOUT_MS:-600000}")
fi

if [[ -n "${SUPERGATEWAY_ARGS:-}" ]]; then
  read -r -a extra <<< "$SUPERGATEWAY_ARGS"
  gateway+=("${extra[@]}")
fi

exec supergateway "${gateway[@]}"
