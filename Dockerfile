# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app

# Fabric/SSH needs an OpenSSH client; ca-certificates for TLS.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        openssh-client \
    && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    DATABASE_URL="sqlite:////data/asm_cleanup.db"

# Install dependencies first for better layer caching.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --extra web --no-install-project --no-dev

COPY pyproject.toml uv.lock README.md alembic.ini ./
COPY asm_cleanup ./asm_cleanup
COPY migrations ./migrations
COPY scripts/docker-demo-entrypoint.sh ./scripts/docker-demo-entrypoint.sh

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --extra web --no-dev

RUN mkdir -p /data /app/logs /demo \
    && chmod +x /app/scripts/docker-demo-entrypoint.sh \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin app \
    && chown -R app:app /app /data /demo

USER app

EXPOSE 8000

CMD ["asm-cleanup", "web", "--host", "0.0.0.0", "--port", "8000"]
