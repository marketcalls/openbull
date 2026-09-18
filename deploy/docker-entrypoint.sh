#!/usr/bin/env bash
# Validate required secrets, wait for Postgres, run migrations, then launch
# uvicorn (no --reload in prod).
set -euo pipefail

# ---- preflight: fail fast on missing/placeholder secrets ----
missing=()
for var in APP_SECRET_KEY ENCRYPTION_PEPPER DATABASE_URL REDIS_URL; do
  val="${!var:-}"
  if [ -z "$val" ]; then
    missing+=("$var (unset)")
  elif [[ "$val" == *change_me* ]]; then
    missing+=("$var (still a placeholder)")
  fi
done
if [ "${#missing[@]}" -gt 0 ]; then
  echo "FATAL: fix these in .env.prod before starting:" >&2
  printf '  - %s\n' "${missing[@]}" >&2
  echo "Generate secrets with: python3 -c \"import secrets; print(secrets.token_hex(32))\"" >&2
  exit 1
fi

# Derive host:port from DATABASE_URL for a readiness probe (best-effort).
DB_HOSTPORT="$(uv run python - <<'PY'
import os, re
u = os.environ.get("DATABASE_URL", "")
m = re.search(r"@([^/:]+):(\d+)", u)
print(f"{m.group(1)} {m.group(2)}" if m else "postgres 5432")
PY
)"
DB_HOST="${DB_HOSTPORT% *}"; DB_PORT="${DB_HOSTPORT#* }"

echo "Waiting for Postgres at ${DB_HOST}:${DB_PORT} ..."
for i in $(seq 1 60); do
  if uv run python -c "import socket; s=socket.socket(); s.settimeout(2); s.connect(('${DB_HOST}', ${DB_PORT}))" 2>/dev/null; then
    echo "Postgres reachable."; break
  fi
  sleep 2
done

echo "Running migrations ..."
uv run python migrate_all.py || { echo "Migrations failed"; exit 1; }

echo "Starting uvicorn on 0.0.0.0:${BACKEND_PORT:-8000} ..."
exec uv run uvicorn backend.main:app --host 0.0.0.0 --port "${BACKEND_PORT:-8000}"
