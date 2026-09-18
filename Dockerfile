# OpenBull backend (FastAPI + uvicorn) — production image.
# uv base image ships uv + Python 3.12; we sync the locked deps and run uvicorn.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install deps first (better layer caching) from the lockfile only.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# App source
COPY . .
RUN uv sync --frozen --no-dev

# 8000 = HTTP/API + strategy WS. 8765 (broker WS proxy) and 5555 (ZMQ) stay
# internal to this container — the browser never talks to them directly.
EXPOSE 8000

ENTRYPOINT ["/app/deploy/docker-entrypoint.sh"]
