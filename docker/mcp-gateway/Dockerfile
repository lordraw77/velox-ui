# syntax=docker/dockerfile:1
#
# lordraw/velox-ui-mcp-gateway -- runs any stdio MCP server, chosen at run time, and
# republishes it as Streamable HTTP for velox-ui.
#
#   docker run lordraw/velox-ui-mcp-gateway npx -y discogs-mcp-server@0.5.7
#
# Published separately from lordraw/velox-ui on purpose. velox-ui is a Python runtime
# that stays small and carries no package manager (ADR-0015); a gateway's whole job is
# running somebody else's server, which means carrying every toolchain those servers
# are published for.
#
# One image, every engine, no rebuild per server. The container's arguments are the
# server's own command line -- the `command` + `args` of an MCP client config -- and
# entrypoint.sh hands them to supergateway. What is on PATH, and why:
#
#   npx, node      npm-published servers, by far the most common; also supergateway
#   uvx, uv        PyPI-published servers
#   python         servers started as `python -m ...`; a uv-managed CPython, installed
#                  here so the first `uvx` does not download an interpreter
#   bunx, bun      servers published for Bun
#   deno           servers published on JSR or as Deno scripts
#   git            not optional: some servers pin a dependency to a git repository
#                  (discogs-mcp-server does this with `dotenv`) and npm shells out to
#                  git to resolve it. Without it the child exits 254 and the gateway
#                  answers with an empty stream.
#
# Deliberately absent: the docker CLI. A server launched as `docker run -i ...` would
# need the host's /var/run/docker.sock, which is root on the host with no mediation,
# handed to a process whose tool arguments a model chooses.
#
# Packages are fetched when the container starts, not baked in. Two consequences to
# plan for, both covered in docs/mcp-gateway.md: the first start needs network, and an
# unpinned `npx -y package` is whatever version is newest that day -- pin it in the
# command. Mount a volume at /cache and restarts reuse what was downloaded.
FROM oven/bun:1-slim AS bun
FROM denoland/deno:bin AS deno

FROM node:22-slim

LABEL org.opencontainers.image.title="velox-ui-mcp-gateway" \
      org.opencontainers.image.description="Runs stdio MCP servers and republishes them as Streamable HTTP for velox-ui." \
      org.opencontainers.image.source="https://github.com/lordraw77/velox-ui" \
      org.opencontainers.image.documentation="https://github.com/lordraw77/velox-ui/blob/main/docs/mcp-gateway.md" \
      org.opencontainers.image.licenses="MIT"

ARG SUPERGATEWAY_VERSION=3.4.0
ARG PYTHON_VERSION=3.12

# Every package manager's cache under one directory, so a single volume at /cache
# makes a restart reuse the previous download for all of them. npx never prompts: a
# container has no one to answer "Ok to proceed?".
ENV npm_config_cache=/cache/npm \
    npm_config_yes=true \
    npm_config_update_notifier=false \
    npm_config_fund=false \
    npm_config_audit=false \
    UV_CACHE_DIR=/cache/uv \
    UV_TOOL_DIR=/cache/uv-tools \
    UV_PYTHON_INSTALL_DIR=/opt/uv/python \
    UV_PYTHON_BIN_DIR=/usr/local/bin \
    BUN_INSTALL_CACHE_DIR=/cache/bun \
    DENO_DIR=/cache/deno

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
COPY --from=bun /usr/local/bin/bun /usr/local/bin/bun
COPY --from=deno /deno /usr/local/bin/deno

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && ln -s bun /usr/local/bin/bunx \
 && npm install -g --no-audit --no-fund "supergateway@${SUPERGATEWAY_VERSION}" \
 && npm cache clean --force \
 && uv python install --default "${PYTHON_VERSION}" \
 && mkdir -p /cache \
 # The runtime user owns the cache, and the Python install directory too, so a server
 # that asks uv for a different interpreter can still download one.
 && chown -R node:node /cache /opt/uv

COPY docker/mcp-gateway/entrypoint.sh /usr/local/bin/mcp-gateway

USER node
WORKDIR /home/node

EXPOSE 8000

# Liveness, not readiness: any HTTP answer means supergateway is up. The MCP server
# behind it starts per session, so "healthy" cannot promise the package download
# succeeded -- the first velox-ui connect is what proves that. The start period covers
# supergateway's own boot, not a package install.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:' + (process.env.MCP_PORT || 8000) + '/healthz').then(() => process.exit(0), () => process.exit(1))"

ENTRYPOINT ["mcp-gateway"]
