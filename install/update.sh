#!/bin/bash
# Note: `set -e` intentionally NOT used — harmless `[ ] && echo` patterns
# and transient apt warnings would kill the script silently. Errors are
# handled explicitly via check_status() below.
export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a

# ============================================================================
# OpenBull - Update Script
# Pulls latest GitHub code, syncs deps, runs migrations, rebuilds frontend,
# restarts services. Optionally flushes Redis cache.
#
# Usage: sudo bash update.sh
# ============================================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_step()  { echo -e "\n${CYAN}=== $1 ===${NC}\n"; }

check_status() {
    if [ $? -eq 0 ]; then
        log_info "$1"
    else
        log_error "$1 failed"
        exit 1
    fi
}

# Fixed paths (must match install.sh)
APP_ROOT="/var/www/openbull"
FRONTEND_DIR="$APP_ROOT/frontend"
LOG_DIR="/var/log/openbull"
SERVICE_NAME="openbull"

# ============================================================================
# Pre-flight
# ============================================================================

if [ "$EUID" -ne 0 ]; then
    log_error "Please run as root (sudo bash update.sh)"
    exit 1
fi

if [ ! -d "$APP_ROOT/.git" ]; then
    log_error "OpenBull is not installed at $APP_ROOT"
    log_error "Run install.sh first."
    exit 1
fi

if [ ! -f "$APP_ROOT/.env" ]; then
    log_error ".env not found at $APP_ROOT/.env — cannot update without it"
    exit 1
fi

# Logs
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
UPDATE_LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$UPDATE_LOG_DIR"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
UPDATE_LOG="$UPDATE_LOG_DIR/update_${TIMESTAMP}.log"
exec > >(tee -a "$UPDATE_LOG") 2>&1

log_step "OpenBull Update"
log_info "App root:   $APP_ROOT"
log_info "Service:    $SERVICE_NAME"
log_info "Update log: $UPDATE_LOG"
echo ""

# Redis cache flush prompt (safer default: no)
FLUSH_REDIS="no"
read -rp "Flush Redis cache after update? (y/N): " choice
if [[ "$choice" =~ ^[Yy]$ ]]; then
    FLUSH_REDIS="yes"
fi

# Ensure uv available under sudo
if ! command -v uv &>/dev/null; then
    for p in /usr/local/bin/uv /root/.local/bin/uv "$HOME/.local/bin/uv"; do
        [ -x "$p" ] && export PATH="$(dirname "$p"):$PATH" && break
    done
fi

# ============================================================================
# Step 1: Backup .env and alembic.ini
# ============================================================================

log_step "Step 1: Backing up configuration"

BACKUP_DIR="$APP_ROOT/backups/$TIMESTAMP"
mkdir -p "$BACKUP_DIR"
cp "$APP_ROOT/.env"        "$BACKUP_DIR/.env"
cp "$APP_ROOT/alembic.ini" "$BACKUP_DIR/alembic.ini" 2>/dev/null || true
log_info "Backup saved to $BACKUP_DIR"

# Read current DATABASE_URL to restore into alembic.ini after git pull
CURRENT_DB_URL=$(grep -E '^DATABASE_URL\s*=' "$APP_ROOT/.env" | head -1 | sed 's/^[^=]*=[ \t]*"\?//; s/"\?[ \t]*$//')

# ============================================================================
# Step 2: Pull latest code
# ============================================================================

log_step "Step 2: Pulling latest code from GitHub"

cd "$APP_ROOT"

# Git sees root accessing a www-data-owned repo → "dubious ownership".
# Whitelist the path for both the invoking user and the www-data user
# so git operations work from either context.
git config --global --add safe.directory "$APP_ROOT" 2>/dev/null || true
sudo -u www-data git config --global --add safe.directory "$APP_ROOT" 2>/dev/null || true

# Stash local edits (.env/alembic.ini already backed up in Step 1).
# The `-u` includes untracked files. The named stash makes it easy to
# recover with `git stash list | grep update-$TIMESTAMP`.
STASHED=false
if ! git diff --quiet HEAD 2>/dev/null || [ -n "$(git status --porcelain 2>/dev/null)" ]; then
    log_info "Stashing local changes as 'update-$TIMESTAMP'"
    git stash push -u -m "update-$TIMESTAMP" >/dev/null 2>&1 && STASHED=true
fi

git fetch origin
if git rev-parse --verify origin/main >/dev/null 2>&1; then
    git checkout main 2>/dev/null || true
    git pull origin main
elif git rev-parse --verify origin/master >/dev/null 2>&1; then
    git checkout master 2>/dev/null || true
    git pull origin master
else
    log_error "Could not find main or master branch on origin"
    exit 1
fi
check_status "Code updated"

if [ "$STASHED" = true ]; then
    log_info "Local changes saved in stash. Recover with: git stash list | grep update-$TIMESTAMP"
fi

# Restore .env (git pull shouldn't touch it, but be safe)
cp "$BACKUP_DIR/.env" "$APP_ROOT/.env"

# Restore alembic.ini sqlalchemy.url from .env (sync driver)
if [ -n "$CURRENT_DB_URL" ] && [ -f "$APP_ROOT/alembic.ini" ]; then
    SYNC_DB_URL="${CURRENT_DB_URL/postgresql+asyncpg/postgresql}"
    SYNC_DB_URL="${SYNC_DB_URL/postgresql+psycopg/postgresql}"
    sed -i "s|^sqlalchemy.url = .*|sqlalchemy.url = $SYNC_DB_URL|" "$APP_ROOT/alembic.ini"
    log_info "alembic.ini sqlalchemy.url restored from .env"
fi

# ============================================================================
# Step 3: Sync Python dependencies
# ============================================================================

log_step "Step 3: Syncing Python dependencies (uv sync)"

cd "$APP_ROOT"
if command -v uv &>/dev/null; then
    uv sync
    check_status "Python dependencies synced"
elif [ -f "$APP_ROOT/.venv/bin/pip" ]; then
    log_warn "uv not found, falling back to pip"
    "$APP_ROOT/.venv/bin/pip" install -e "$APP_ROOT"
    check_status "Python dependencies synced (pip)"
else
    log_error "Neither uv nor .venv/bin/pip available"
    exit 1
fi

# ============================================================================
# Step 4: Database migrations (Alembic)
# ============================================================================

log_step "Step 4: Running database migrations"

cd "$APP_ROOT"

# `uv sync` installs deps but not openbull as a package (no [build-system]
# in pyproject), so PYTHONPATH is required for alembic env.py to find
# `backend.models`.
export PYTHONPATH="$APP_ROOT"
if [ -x "$APP_ROOT/.venv/bin/alembic" ]; then
    ALEMBIC_CMD=("$APP_ROOT/.venv/bin/alembic")
else
    ALEMBIC_CMD=()
fi

if [ ${#ALEMBIC_CMD[@]} -eq 0 ]; then
    log_warn "Alembic not available, skipping migrations"
else
    # Pull DB creds from .env for psql probe (matches what the app uses).
    DB_URL=$(grep -E '^DATABASE_URL\s*=' "$APP_ROOT/.env" | head -1 | sed 's/^[^=]*=[ \t]*"\?//; s/"\?[ \t]*$//')
    # Convert asyncpg URL to psycopg for psql. Regex: driver://user:pass@host:port/db
    PG_CREDS=$(echo "$DB_URL" | sed -E 's|^postgresql(\+[a-z]+)?://||')
    PG_USER=$(echo "$PG_CREDS" | cut -d: -f1)
    PG_PASS=$(echo "$PG_CREDS" | sed -E 's|^[^:]+:([^@]+)@.*|\1|')
    PG_HOSTPORT=$(echo "$PG_CREDS" | sed -E 's|^[^@]+@||; s|/.*||')
    PG_HOST=$(echo "$PG_HOSTPORT" | cut -d: -f1)
    PG_PORT=$(echo "$PG_HOSTPORT" | cut -d: -f2)
    PG_DB=$(echo "$PG_CREDS" | sed 's|.*/||')

    HAS_ALEMBIC=$(PGPASSWORD="$PG_PASS" psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d "$PG_DB" -tAc \
        "SELECT 1 FROM information_schema.tables WHERE table_name='alembic_version' AND table_schema='public'" 2>/dev/null | tr -d ' ')
    HAS_TABLES=$(PGPASSWORD="$PG_PASS" psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d "$PG_DB" -tAc \
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'" 2>/dev/null | tr -d ' ')

    log_info "Migration state: alembic_version=${HAS_ALEMBIC:-0}, tables=${HAS_TABLES:-0}"

    if [ "${HAS_ALEMBIC:-0}" = "1" ]; then
        log_info "Current revision:"
        "${ALEMBIC_CMD[@]}" current || true
        echo ""
        log_info "Applying upgrades..."
        "${ALEMBIC_CMD[@]}" upgrade head
        check_status "Migrations applied"
        echo ""
        log_info "New revision:"
        "${ALEMBIC_CMD[@]}" current || true
    elif [ "${HAS_TABLES:-0}" -gt "0" ]; then
        log_warn "DB has tables but no alembic_version — stamping as up-to-date"
        log_warn "(This is normal on first update after an install that pre-dates alembic tracking)"
        "${ALEMBIC_CMD[@]}" stamp head
        check_status "Database stamped at head"
    else
        log_info "Fresh database — running full migration"
        "${ALEMBIC_CMD[@]}" upgrade head
        check_status "Migrations applied"
    fi
fi

# ============================================================================
# Step 5: Rebuild frontend
# ============================================================================

log_step "Step 5: Rebuilding React frontend"

if [ -f "$FRONTEND_DIR/package.json" ]; then
    cd "$FRONTEND_DIR"
    npm install --legacy-peer-deps
    npm run build
    check_status "Frontend rebuilt"
else
    log_warn "No $FRONTEND_DIR/package.json, skipping frontend build"
fi

# ============================================================================
# Step 6: Fix permissions
# ============================================================================

log_step "Step 6: Fixing permissions"

chown -R www-data:www-data "$APP_ROOT"
chmod -R 755 "$APP_ROOT"
chmod 640 "$APP_ROOT/.env"
chown www-data:www-data "$APP_ROOT/.env"
log_info "Permissions fixed"

# ============================================================================
# Step 7: Redis cache
# ============================================================================

log_step "Step 7: Redis cache"

if [ "$FLUSH_REDIS" = "yes" ]; then
    if command -v redis-cli &>/dev/null; then
        redis-cli -n 0 FLUSHDB
        check_status "Redis DB 0 flushed"
    else
        log_warn "redis-cli not found, skipping flush"
    fi
else
    log_info "Redis cache preserved (user choice)"
fi

if ! systemctl is-active --quiet redis-server; then
    log_warn "Redis not running, starting..."
    systemctl start redis-server
fi

# ============================================================================
# Step 8: Restart services
# ============================================================================

log_step "Step 8: Restarting services"

systemctl daemon-reload

if [ -f "/etc/systemd/system/$SERVICE_NAME.service" ]; then
    systemctl restart "$SERVICE_NAME"
    check_status "$SERVICE_NAME restarted"
    sleep 3
else
    log_error "Systemd service file not found: /etc/systemd/system/$SERVICE_NAME.service"
    exit 1
fi

if systemctl is-active --quiet nginx; then
    nginx -t && systemctl reload nginx
    check_status "Nginx reloaded"
else
    log_warn "Nginx not running, starting..."
    systemctl start nginx
fi

# ============================================================================
# Step 9: Verify
# ============================================================================

log_step "Step 9: Verifying services"

echo ""
echo "Service status:"
printf "  %-18s %s\n" "postgresql"   "$(systemctl is-active postgresql)"
printf "  %-18s %s\n" "redis-server" "$(systemctl is-active redis-server)"
printf "  %-18s %s\n" "nginx"        "$(systemctl is-active nginx)"
printf "  %-18s %s\n" "$SERVICE_NAME" "$(systemctl is-active $SERVICE_NAME)"
echo ""

if ! systemctl is-active --quiet "$SERVICE_NAME"; then
    log_error "$SERVICE_NAME failed to start. Recent logs:"
    journalctl -u "$SERVICE_NAME" --no-pager -n 30
    exit 1
fi

log_step "Update Complete"
log_info "App root:    $APP_ROOT"
log_info "Backup dir:  $BACKUP_DIR"
log_info "Update log:  $UPDATE_LOG"
echo ""
echo "Watch logs:"
echo "  sudo journalctl -u $SERVICE_NAME -f"
echo "  sudo tail -f $LOG_DIR/backend.log"
echo ""
