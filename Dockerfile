# syntax=docker/dockerfile:1
#
# Two stages, one goal: a final image under 250 MB (ADR-0015).
#
# The builder installs dependencies into a self-contained virtual environment; the
# runtime stage copies only that environment. Build tools, caches and compilers never
# reach the final layer. There is no torch and no CUDA here and there never will be:
# embeddings run on ONNX Runtime and are downloaded on first use, into the data volume
# rather than baked into the image (ADR-0010).

FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /build

# Dependencies are resolved from the metadata alone, so editing source code does not
# invalidate this layer.
COPY pyproject.toml README.md ./
COPY src/velox_ui/__init__.py src/velox_ui/__init__.py
RUN uv venv /opt/venv \
 && VIRTUAL_ENV=/opt/venv uv pip install --no-cache .

COPY src ./src
COPY alembic.ini ./
RUN VIRTUAL_ENV=/opt/venv uv pip install --no-cache --no-deps . \
 && find /opt/venv -name '__pycache__' -type d -prune -exec rm -rf {} + \
 && find /opt/venv -name '*.dist-info' -type d -exec rm -rf {}/RECORD \; 2>/dev/null || true


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    VELOX_DATA_DIR=/data \
    VELOX_HOST=0.0.0.0 \
    VELOX_PORT=8080

# A non-root user owns the data volume. Running as root inside a container that a
# home server exposes to its LAN is a needless risk.
RUN groupadd --system --gid 1000 velox \
 && useradd --system --uid 1000 --gid velox --home /data --shell /usr/sbin/nologin velox \
 && mkdir -p /data \
 && chown velox:velox /data

COPY --from=builder /opt/venv /opt/venv

USER velox
WORKDIR /data
VOLUME ["/data"]
EXPOSE 8080

# The probe uses /ready, not /health: an instance whose database is unreachable or
# whose schema is mid-migration is alive but must not receive traffic.
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/ready', timeout=2).status == 200 else 1)"

ENTRYPOINT ["velox"]
CMD ["serve"]
