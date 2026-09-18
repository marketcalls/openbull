#!/usr/bin/env bash
# One-shot provisioner for a fresh Ubuntu VM (e.g. Oracle Cloud Always Free).
# Installs Docker, clones your fork, helps you fill secrets, and brings the
# whole OpenBull stack up (Postgres + Redis + FastAPI + nginx + Caddy).
#
# Usage on the VM:
#   curl -fsSL https://raw.githubusercontent.com/<you>/openbull/deploy/cloud-and-smc/deploy/provision.sh -o provision.sh
#   REPO_URL=https://github.com/<you>/openbull.git BRANCH=deploy/cloud-and-smc bash provision.sh
# or after cloning manually, just: bash deploy/provision.sh
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/cshah911/openbull.git}"
BRANCH="${BRANCH:-deploy/cloud-and-smc}"
APP_DIR="${APP_DIR:-$HOME/openbull}"
COMPOSE="docker compose -f docker-compose.prod.yml --env-file .env.prod"

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

# 1. Docker ------------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  log "Installing Docker"
  curl -fsSL https://get.docker.com | sh
  sudo usermod -aG docker "$USER" || true
  echo "NOTE: log out/in (or run 'newgrp docker') if you hit a permission error."
fi

# 2. Clone / update ----------------------------------------------------------
if [ -d "$APP_DIR/.git" ]; then
  log "Updating existing checkout in $APP_DIR"
  git -C "$APP_DIR" fetch origin "$BRANCH"
  git -C "$APP_DIR" checkout "$BRANCH"
  git -C "$APP_DIR" pull --ff-only origin "$BRANCH"
else
  log "Cloning $REPO_URL ($BRANCH) -> $APP_DIR"
  git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi
cd "$APP_DIR"

# 3. Secrets -----------------------------------------------------------------
if [ ! -f .env.prod ]; then
  log "Creating .env.prod from template with generated secrets"
  cp .env.prod.example .env.prod
  SK=$(python3 -c "import secrets; print(secrets.token_hex(32))")
  PP=$(python3 -c "import secrets; print(secrets.token_hex(32))")
  DBPW=$(python3 -c "import secrets; print(secrets.token_hex(16))")
  # portable in-place edits
  sed -i "s|^APP_SECRET_KEY=.*|APP_SECRET_KEY=${SK}|" .env.prod
  sed -i "s|^ENCRYPTION_PEPPER=.*|ENCRYPTION_PEPPER=${PP}|" .env.prod
  sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${DBPW}|" .env.prod
  sed -i "s|^DATABASE_URL=.*|DATABASE_URL=postgresql+asyncpg://postgres:${DBPW}@postgres:5432/openbull|" .env.prod
  echo
  echo "Generated APP_SECRET_KEY, ENCRYPTION_PEPPER, POSTGRES_PASSWORD/DATABASE_URL."
  echo "NOW EDIT .env.prod to set:"
  echo "  - DOMAIN         (your hostname for auto-HTTPS, or leave :80 for plain HTTP)"
  echo "  - FRONTEND_URL / CORS_ORIGINS"
  echo "  - DATABRICKS_*   (only if you use the export/DB Hist Chart)"
  echo
  read -r -p "Press Enter once you've edited .env.prod (or Ctrl-C to stop and edit first)..."
else
  log ".env.prod already present — leaving it as is"
fi

# 4. Launch ------------------------------------------------------------------
log "Building and starting the stack"
$COMPOSE up -d --build

log "Waiting for backend health"
for i in $(seq 1 60); do
  if docker compose -f docker-compose.prod.yml exec -T backend \
       python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3)" 2>/dev/null; then
    echo "backend healthy"; break
  fi
  sleep 3
done

IP=$(curl -fsS ifconfig.me 2>/dev/null || echo "<VM_PUBLIC_IP>")
log "Done."
echo "Open:  http://${IP}/   (or your DOMAIN over HTTPS)"
echo "First visit: create your user account in the app."
echo "Logs:  $COMPOSE logs -f backend"
echo "Stop:  $COMPOSE down    (add -v to also wipe the database volume)"
