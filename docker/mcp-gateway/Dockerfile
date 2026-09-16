# syntax=docker/dockerfile:1
#
# lordraw/velox-ui-mcp-gateway -- one image for every MCP sidecar velox-ui talks to.
#
# Published separately from lordraw/velox-ui on purpose. The two have opposite
# dependency shapes: velox-ui is a Python runtime that must stay small and carries no
# package manager, while a gateway's whole job is to run somebody else's server, which
# means shipping the toolchains that server needs. Merging them would put Node, uv and
# git back into the image ADR-0015 keeps under 250 MB, for a feature most deployments
# never enable.
#
# The velox-ui runtime carries no Node and no package manager (see the main Dockerfile
# and ADR-0015), so an MCP server published on npm or PyPI cannot be launched as a
# `stdio` server from inside it -- there is no `npx`, no `uvx`. This image is the other
# half: it runs those servers and republishes each one as Streamable HTTP, the
# transport `mcp/http_sse.py` speaks (ADR-0020).
#
# What is installed here and why:
#   node + npm  -- npm-published servers, and supergateway itself
#   uv          -- PyPI-published servers; uv fetches its own CPython, so there is no
#                  system python to keep in sync
#   git         -- not optional: some servers pin a dependency to a git repository
#                  (discogs-mcp-server does this with `dotenv`), and npm shells out to
#                  git to resolve it. Without git the child exits 254 and the gateway
#                  answers with an empty stream, which reaches velox-ui as the
#                  unhelpful "the MCP server sent no response".
#
# Servers are installed at build time, not fetched by `npx -y` / `uvx` on every start:
# a restart then needs no network, no writable package cache, and the version that ran
# yesterday is the version that runs today.
#
#   docker build -f docker/mcp-gateway/Dockerfile \
#     -t lordraw/velox-ui-mcp-gateway:latest \
#     --build-arg NPM_PACKAGES="discogs-mcp-server@0.5.7" \
#     --build-arg UV_TOOLS="mcp-justwatch==0.0.1" .
#
# The published image ships with no MCP server installed: which servers you want is a
# deployment decision, and baking a set into a public image would make everyone carry
# everyone else's. Build on top of it instead -- see docs/mcp-gateway.md.
#
# Both lists are space-separated and both may be empty. Pin versions: an MCP server is
# a tool surface a model calls, and it should not change shape under a `docker compose
# pull`.
FROM node:22-slim

LABEL org.opencontainers.image.title="velox-ui-mcp-gateway" \
      org.opencontainers.image.description="Runs stdio MCP servers and republishes them as Streamable HTTP for velox-ui." \
      org.opencontainers.image.source="https://github.com/lordraw77/velox-ui" \
      org.opencontainers.image.documentation="https://github.com/lordraw77/velox-ui/blob/main/docs/mcp-gateway.md" \
      org.opencontainers.image.licenses="MIT"

ARG SUPERGATEWAY_VERSION=3.4.0
ARG NPM_PACKAGES=""
ARG UV_TOOLS=""

# Tool venvs live under /opt so they survive nothing in particular -- they simply must
# not sit in a home directory that the unprivileged runtime user may not own. The
# console scripts land on the default PATH.
ENV UV_TOOL_DIR=/opt/uv/tools \
    UV_TOOL_BIN_DIR=/usr/local/bin \
    UV_PYTHON_INSTALL_DIR=/opt/uv/python

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && npm install -g --no-audit --no-fund "supergateway@${SUPERGATEWAY_VERSION}" ${NPM_PACKAGES} \
 && npm cache clean --force \
 && for tool in ${UV_TOOLS}; do uv tool install "$tool"; done \
 && chmod -R a+rX /opt/uv 2>/dev/null || true

# An unprivileged user, and a working directory it owns: a server may write next to
# itself at import time (mcp-justwatch opens ./output.log), and a read-only CWD would
# kill it before the handshake.
USER node
WORKDIR /home/node

EXPOSE 8000
ENTRYPOINT ["supergateway"]
