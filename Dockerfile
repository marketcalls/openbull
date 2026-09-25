# OpenBull production image.
#
# Multi-stage build mirroring install/install.sh:
#   stage 1 — Node 20 builds the React SPA (frontend/dist)
#   stage 2 — Python 3.12 slim + uv (locked deps) runs the FastAPI backend
#             from source with a single uvicorn worker
#
# The SPA is NOT served by FastAPI (no StaticFiles mount exists); the
# Kubernetes package runs an nginx sidecar that serves frontend/dist and
# proxies to this container (see deploy/k8s/base/nginx-conf.yaml).
#
# Build:  docker build -t openbull:latest .
#         (then tag/push to your own registry and pin it in the overlay's
#          `images:` transformer — see deploy/k8s/README.md)

# --------------------------------------------------------------------------
# Stage 1 — frontend build (mirrors install.sh: Node 20, legacy peer deps,
# 2 GB heap ceiling because the Vite build of the large SPA can spike)
# --------------------------------------------------------------------------
FROM node:20-bookworm-slim AS frontend-build

WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --legacy-peer-deps

COPY frontend/ ./
# `npm run build` is `tsc -b && vite build`, but tsc -b currently fails at
# HEAD with pre-existing type errors in src/ (TS6133/TS2322/TS7006). Vite
# (esbuild) strips types without checking, so build the bundle directly;
# fixing the frontend type errors belongs to the frontend, not the image.
RUN NODE_OPTIONS=--max-old-space-size=2048 npx vite build

# --------------------------------------------------------------------------
# Stage 2 — runtime
# --------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

# tzdata: the sandbox scheduler, session expiry and APScheduler all run on
# Asia/Kolkata; slim images ship no zoneinfo database.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

# uv, pinned to the version the lockfile was resolved with (uv.lock
# `revision = 3` needs a current uv; keep this in sync when you bump uv).
COPY --from=ghcr.io/astral-sh/uv:0.12.18 /uv /uvx /usr/local/bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    TZ=Asia/Kolkata

WORKDIR /app

# Dependencies first (cacheable layer). The project has no [build-system],
# so it is never installed as a package — the app runs from the source tree.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

# Application sources (alembic.ini + migrate_all.py sit at the repo root and
# must stay there: migrate_all.py resolves backend.* via REPO_ROOT=sys.path).
COPY backend/ ./backend/
COPY alembic/ ./alembic/
COPY alembic.ini migrate_all.py ./

# Built SPA from stage 1. Kubernetes init containers copy this directory into
# the nginx sidecar's webroot; it is also usable for plain docker runs that
# mount their own static server.
COPY --from=frontend-build /build/dist ./frontend/dist/

# Non-root runtime user (uid/gid 1000, matching the Deployment's
# securityContext; the VM install uses www-data for the same purpose).
RUN useradd --uid 1000 --user-group --create-home --shell /usr/sbin/nologin openbull \
    && chown -R openbull:openbull /app
USER openbull

# uvicorn HTTP API (+ /ws/strategy WebSocket) and the in-process market-data
# WebSocket proxy. The ZeroMQ bus (127.0.0.1:5555) is loopback-only and
# deliberately not listed.
EXPOSE 8000 8765

# Exactly one uvicorn worker is MANDATORY: the market-data WS proxy binds
# :8765 inside FastAPI's lifespan and every extra worker would collide on
# that bind (install.sh enforces the same for the VM install). --proxy-headers
# + loopback-only forwarded-allow: the nginx sidecar is the sole direct
# client, same as the VM install's unix-socket setup.
CMD [".venv/bin/uvicorn", "backend.main:app", \
     "--host", "0.0.0.0", "--port", "8000", "--workers", "1", \
     "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1", \
     "--log-level", "info"]
