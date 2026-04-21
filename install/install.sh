#!/bin/bash
set -e

# ============================================================================
# OpenBull - Automated Installation Script
# FastAPI + React 19 + PostgreSQL + Redis + Nginx + Systemd + UFW
# Supports apex domains and subdomains (e.g. bull.marketcalls.in)
# Only user input required: domain name
# GitHub: https://github.com/marketcalls/openbull
# ============================================================================

# Colors
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

# Configuration (fixed)
REPO_URL="https://github.com/marketcalls/openbull.git"
APP_ROOT="/var/www/openbull"
BACKEND_DIR="$APP_ROOT"
FRONTEND_DIR="$APP_ROOT/frontend"
LOG_DIR="/var/log/openbull"
SERVICE_NAME="openbull"
NODE_VERSION="20"

# Database (matches .env.example default)
DB_HOST="localhost"
DB_PORT="5432"
DB_NAME="openbull"
DB_USER="postgres"
DB_PASSWORD="123456"

# Banner
echo -e "${BLUE}"
echo "  ██████╗ ██████╗ ███████╗███╗   ██╗██████╗ ██╗   ██╗██╗     ██╗     "
echo " ██╔═══██╗██╔══██╗██╔════╝████╗  ██║██╔══██╗██║   ██║██║     ██║     "
echo " ██║   ██║██████╔╝█████╗  ██╔██╗ ██║██████╔╝██║   ██║██║     ██║     "
echo " ██║   ██║██╔═══╝ ██╔══╝  ██║╚██╗██║██╔══██╗██║   ██║██║     ██║     "
echo " ╚██████╔╝██║     ███████╗██║ ╚████║██████╔╝╚██████╔╝███████╗███████╗"
echo "  ╚═════╝ ╚═╝     ╚══════╝╚═╝  ╚═══╝╚═════╝  ╚═════╝ ╚══════╝╚══════╝"
echo -e "${NC}"
echo -e "${GREEN}FastAPI + React 19 Options Trading Platform${NC}"
echo ""

# ============================================================================
# Helpers
# ============================================================================

generate_secret() {
    openssl rand -hex 32
}

validate_domain() {
    if [[ "$1" =~ ^([a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$ ]]; then
        return 0
    fi
    return 1
}

install_package() {
    if ! dpkg -l "$1" &>/dev/null; then
        apt-get install -y "$1"
    else
        log_info "$1 already installed"
    fi
}

wait_for_dpkg_lock() {
    local max_wait=300
    local waited=0
    while fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 || \
          fuser /var/lib/dpkg/lock >/dev/null 2>&1 || \
          fuser /var/lib/apt/lists/lock >/dev/null 2>&1; do
        [ $waited -ge $max_wait ] && { log_error "Timeout waiting for apt lock"; exit 1; }
        [ $waited -eq 0 ] && log_warn "Waiting for apt lock..."
        printf "."
        sleep 5
        waited=$((waited + 5))
    done
    [ $waited -gt 0 ] && echo ""
}

# ============================================================================
# Pre-flight Checks
# ============================================================================

if [ "$EUID" -ne 0 ]; then
    log_error "Please run as root (sudo bash install.sh)"
    exit 1
fi

if ! grep -qi "ubuntu\|debian" /etc/os-release 2>/dev/null; then
    log_warn "This script is designed for Ubuntu/Debian. Proceed with caution."
fi

# Logs dir
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
INSTALL_LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$INSTALL_LOG_DIR"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
INSTALL_LOG="$INSTALL_LOG_DIR/install_${TIMESTAMP}.log"
exec > >(tee -a "$INSTALL_LOG") 2>&1

# Detect re-install
IS_REINSTALL=false
[ -f "$APP_ROOT/.env" ] && IS_REINSTALL=true

# ============================================================================
# Gather Configuration (domain is the ONLY input)
# ============================================================================

while true; do
    read -rp "Enter your domain (e.g., bull.marketcalls.in): " DOMAIN
    if validate_domain "$DOMAIN"; then
        break
    fi
    log_error "Invalid domain format. Please try again."
done

# Derive admin email from domain (apex portion). Used for Let's Encrypt.
DOMAIN_PARTS=$(echo "$DOMAIN" | tr '.' '\n' | wc -l)
if [ "$DOMAIN_PARTS" -eq 2 ]; then
    ADMIN_EMAIL="admin@$DOMAIN"
    IS_SUBDOMAIN=false
else
    APEX_DOMAIN=$(echo "$DOMAIN" | awk -F. '{n=NF; print $(n-1)"."$n}')
    ADMIN_EMAIL="admin@$APEX_DOMAIN"
    IS_SUBDOMAIN=true
fi

log_info "Domain:       $DOMAIN"
log_info "SSL email:    $ADMIN_EMAIL (auto-derived)"
log_info "Install path: $APP_ROOT"
[ "$IS_REINSTALL" = true ] && log_warn "Re-install detected (existing .env will be backed up)"
echo ""

# ============================================================================
# Step 1: Timezone (auto-set to IST for Indian markets)
# ============================================================================

log_step "Step 1: Timezone"
CUR_TZ=$(timedatectl 2>/dev/null | grep "Time zone" | awk '{print $3}')
if [ "$CUR_TZ" != "Asia/Kolkata" ]; then
    log_info "Setting timezone to Asia/Kolkata..."
    timedatectl set-timezone Asia/Kolkata 2>/dev/null || log_warn "Could not set timezone"
else
    log_info "Timezone already IST"
fi

# ============================================================================
# Step 2: System Packages
# ============================================================================

log_step "Step 2: Installing system packages"

wait_for_dpkg_lock
apt-get update -y

for pkg in git curl wget build-essential python3 python3-dev python3-venv python3-pip \
           libpq-dev libffi-dev libssl-dev openssl pkg-config; do
    install_package "$pkg"
done

# Node.js 20.x
if ! command -v node &>/dev/null || [[ $(node -v | cut -d. -f1 | tr -d 'v') -lt $NODE_VERSION ]]; then
    log_info "Installing Node.js $NODE_VERSION.x..."
    curl -fsSL "https://deb.nodesource.com/setup_${NODE_VERSION}.x" | bash -
    apt-get install -y nodejs
fi
log_info "Node.js: $(node -v)"

# PostgreSQL
if ! command -v psql &>/dev/null; then
    log_info "Installing PostgreSQL..."
    apt-get install -y postgresql postgresql-contrib
fi
systemctl enable postgresql >/dev/null 2>&1 || true
systemctl start postgresql

# Redis
if ! command -v redis-server &>/dev/null; then
    log_info "Installing Redis..."
    apt-get install -y redis-server
fi
systemctl enable redis-server >/dev/null 2>&1 || true
systemctl start redis-server

# Nginx
if ! command -v nginx &>/dev/null; then
    log_info "Installing Nginx..."
    apt-get install -y nginx
fi
systemctl enable nginx >/dev/null 2>&1 || true

# Certbot
if ! command -v certbot &>/dev/null; then
    log_info "Installing Certbot..."
    apt-get install -y certbot python3-certbot-nginx
fi

# UFW firewall
if ! command -v ufw &>/dev/null; then
    apt-get install -y ufw
fi

# uv (Python package manager)
if ! command -v uv &>/dev/null; then
    log_info "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    if [ -f "/root/.local/bin/uv" ]; then
        ln -sf /root/.local/bin/uv /usr/local/bin/uv
    elif [ -f "$HOME/.local/bin/uv" ]; then
        ln -sf "$HOME/.local/bin/uv" /usr/local/bin/uv
    fi
fi
log_info "uv: $(uv --version)"

# ============================================================================
# Step 3: Firewall (22, 80, 443)
# ============================================================================

log_step "Step 3: Configuring firewall"

ufw default deny incoming  >/dev/null
ufw default allow outgoing >/dev/null
ufw allow 22/tcp           >/dev/null
ufw allow 80/tcp           >/dev/null
ufw allow 443/tcp          >/dev/null
ufw --force enable         >/dev/null
log_info "UFW enabled: 22, 80, 443 allowed"

# ============================================================================
# Step 4: Clone / Update Repository
# ============================================================================

log_step "Step 4: Cloning OpenBull repository"

if [ -d "$APP_ROOT/.git" ]; then
    log_info "Repository exists, pulling latest..."
    cd "$APP_ROOT"
    git config --global --add safe.directory "$APP_ROOT" 2>/dev/null || true
    git fetch origin
    git pull origin main 2>/dev/null || git pull origin master 2>/dev/null || true
else
    if [ -d "$APP_ROOT" ]; then
        BACKUP_ROOT="${APP_ROOT}_backup_${TIMESTAMP}"
        log_warn "$APP_ROOT exists but isn't a git repo — backing up to $BACKUP_ROOT"
        mv "$APP_ROOT" "$BACKUP_ROOT"
    fi
    mkdir -p "$(dirname "$APP_ROOT")"
    git clone "$REPO_URL" "$APP_ROOT"
fi

# ============================================================================
# Step 5: PostgreSQL Setup (matches .env.example: postgres/123456/openbull)
# ============================================================================

log_step "Step 5: Configuring PostgreSQL"

# Set password for postgres superuser to match DATABASE_URL sample
sudo -u postgres psql -c "ALTER USER $DB_USER WITH PASSWORD '$DB_PASSWORD';" >/dev/null
log_info "Postgres user '$DB_USER' password set"

# Create database if missing
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -q 1; then
    sudo -u postgres psql -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;" >/dev/null
    log_info "Database '$DB_NAME' created"
else
    log_info "Database '$DB_NAME' already exists"
fi

# Allow local password-based auth for postgres user (asyncpg uses TCP 127.0.0.1)
PG_HBA=$(find /etc/postgresql -name "pg_hba.conf" 2>/dev/null | head -1)
if [ -n "$PG_HBA" ]; then
    if ! grep -qE "^host\s+all\s+all\s+127.0.0.1/32\s+md5" "$PG_HBA"; then
        echo "host    all    all    127.0.0.1/32    md5" >> "$PG_HBA"
        systemctl reload postgresql
        log_info "Added md5 auth for 127.0.0.1 to pg_hba.conf"
    fi
fi

# ============================================================================
# Step 6: Environment Configuration (.env)
# ============================================================================

log_step "Step 6: Writing .env file"

# Preserve existing secrets on re-install to avoid invalidating sessions/tokens
APP_SECRET_KEY=""
ENCRYPTION_PEPPER=""
if [ "$IS_REINSTALL" = true ] && [ -f "$APP_ROOT/.env" ]; then
    cp "$APP_ROOT/.env" "$APP_ROOT/.env.backup.$TIMESTAMP"
    APP_SECRET_KEY=$(grep -E '^APP_SECRET_KEY\s*=' "$APP_ROOT/.env" | head -1 | sed 's/^[^=]*=[ \t]*"\?//; s/"\?[ \t]*$//')
    ENCRYPTION_PEPPER=$(grep -E '^ENCRYPTION_PEPPER\s*=' "$APP_ROOT/.env" | head -1 | sed 's/^[^=]*=[ \t]*"\?//; s/"\?[ \t]*$//')
    log_info "Existing .env backed up; preserving secrets"
fi

[ -z "$APP_SECRET_KEY" ]   && APP_SECRET_KEY=$(generate_secret)
[ -z "$ENCRYPTION_PEPPER" ] && ENCRYPTION_PEPPER=$(generate_secret)

DATABASE_URL="postgresql+asyncpg://$DB_USER:$DB_PASSWORD@$DB_HOST:$DB_PORT/$DB_NAME"

cat > "$APP_ROOT/.env" <<ENVEOF
# ============================================================
# OpenBull Configuration - $DOMAIN
# Generated by install.sh on $(date)
# ============================================================

# ---- Core Secrets ----
APP_SECRET_KEY = "$APP_SECRET_KEY"
ENCRYPTION_PEPPER = "$ENCRYPTION_PEPPER"

# ---- Database ----
DATABASE_URL = "$DATABASE_URL"

# ---- Server ----
BACKEND_HOST = "127.0.0.1"
BACKEND_PORT = 8000
FRONTEND_URL = "https://$DOMAIN"
FLASK_DEBUG = "false"

# ---- CORS ----
CORS_ORIGINS = "https://$DOMAIN"

# ---- Brokers ----
VALID_BROKERS = "upstox,zerodha"

# ---- Logging ----
LOG_LEVEL = "INFO"

# ---- Rate Limits ----
LOGIN_RATE_LIMIT_MIN = "5 per minute"
LOGIN_RATE_LIMIT_HOUR = "25 per hour"
API_RATE_LIMIT = "50 per second"
ORDER_RATE_LIMIT = "10 per second"

# ---- Session ----
SESSION_EXPIRY_TIME = "03:00"

# ---- WebSocket Proxy ----
WEBSOCKET_HOST = "127.0.0.1"
WEBSOCKET_PORT = 8765
WEBSOCKET_URL = "wss://$DOMAIN/ws"
ZMQ_HOST = "127.0.0.1"
ZMQ_PORT = 5555

# ---- Redis ----
REDIS_URL = "redis://127.0.0.1:6379/0"

# ---- WebSocket Connection Pooling ----
MAX_SYMBOLS_PER_WEBSOCKET = 1000
MAX_WEBSOCKET_CONNECTIONS = 3
ENABLE_CONNECTION_POOLING = "true"
ENVEOF

chmod 640 "$APP_ROOT/.env"
log_info ".env written to $APP_ROOT/.env"

# Sync the alembic.ini to use the same DB (sync driver)
SYNC_DB_URL="postgresql://$DB_USER:$DB_PASSWORD@$DB_HOST:$DB_PORT/$DB_NAME"
sed -i "s|^sqlalchemy.url = .*|sqlalchemy.url = $SYNC_DB_URL|" "$APP_ROOT/alembic.ini"
log_info "alembic.ini updated with DB URL"

# Frontend production env
cat > "$FRONTEND_DIR/.env.production" <<FEOF
VITE_API_URL=https://$DOMAIN/api
VITE_WS_URL=wss://$DOMAIN/ws
FEOF

# ============================================================================
# Step 7: Backend Dependencies (uv sync)
# ============================================================================

log_step "Step 7: Installing backend dependencies (uv sync)"

cd "$APP_ROOT"
uv sync
log_info "Python environment ready at $APP_ROOT/.venv"

# ============================================================================
# Step 8: Database Migrations (Alembic)
# ============================================================================

log_step "Step 8: Running database migrations"

cd "$APP_ROOT"
if [ -x "$APP_ROOT/.venv/bin/alembic" ]; then
    "$APP_ROOT/.venv/bin/alembic" upgrade head || \
        log_warn "Alembic reported issues (tables may be auto-created at app startup)"
else
    log_warn "Alembic not installed; tables will be auto-created at app startup"
fi

# ============================================================================
# Step 9: Frontend Build
# ============================================================================

log_step "Step 9: Building React frontend"

cd "$FRONTEND_DIR"
npm install --legacy-peer-deps
npm run build
log_info "Frontend built to $FRONTEND_DIR/dist"

# ============================================================================
# Step 10: Directories & Permissions
# ============================================================================

log_step "Step 10: Setting permissions"

mkdir -p "$LOG_DIR"
mkdir -p "$APP_ROOT/tmp"
chown -R www-data:www-data "$APP_ROOT"
chown -R www-data:www-data "$LOG_DIR"
chmod -R 755 "$APP_ROOT"
chmod 640 "$APP_ROOT/.env"
chown www-data:www-data "$APP_ROOT/.env"

# Logrotate for systemd log files
cat > /etc/logrotate.d/openbull <<LREOF
$LOG_DIR/*.log {
    size 100M
    rotate 5
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
LREOF
log_info "Permissions set, logrotate configured"

# ============================================================================
# Step 11: Systemd Service
# ============================================================================

log_step "Step 11: Creating systemd service"

CPU_CORES=$(nproc --all 2>/dev/null || echo 2)
WORKERS=$((CPU_CORES))
[ $WORKERS -gt 4 ] && WORKERS=4
[ $WORKERS -lt 1 ] && WORKERS=1
log_info "Uvicorn workers: $WORKERS (on $CPU_CORES CPU cores)"

systemctl stop "$SERVICE_NAME" 2>/dev/null || true

cat > /etc/systemd/system/${SERVICE_NAME}.service <<SVCEOF
[Unit]
Description=OpenBull Backend (FastAPI / Uvicorn)
After=network.target postgresql.service redis-server.service
Requires=postgresql.service

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=$APP_ROOT
Environment="PATH=$APP_ROOT/.venv/bin:/usr/local/bin:/usr/bin:/bin"
Environment="PYTHONUNBUFFERED=1"

RuntimeDirectory=openbull
RuntimeDirectoryMode=0755

ExecStart=$APP_ROOT/.venv/bin/uvicorn backend.main:app \\
    --uds /run/openbull/openbull.sock \\
    --workers $WORKERS \\
    --proxy-headers \\
    --forwarded-allow-ips=127.0.0.1 \\
    --log-level info

Restart=always
RestartSec=5
TimeoutSec=300

StandardOutput=append:$LOG_DIR/backend.log
StandardError=append:$LOG_DIR/backend.error.log

NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME" >/dev/null 2>&1

# ============================================================================
# Step 12: Nginx - Initial HTTP for Certbot
# ============================================================================

log_step "Step 12: Configuring Nginx (initial HTTP)"

NGINX_CONF="/etc/nginx/sites-available/openbull"
NGINX_LINK="/etc/nginx/sites-enabled/openbull"

# Back up existing site config if any
[ -f "$NGINX_CONF" ] && cp "$NGINX_CONF" "${NGINX_CONF}.bak.$TIMESTAMP"

cat > "$NGINX_CONF" <<NGEOF
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;
    root /var/www/html;

    location / {
        try_files \$uri \$uri/ =404;
    }
}
NGEOF

rm -f /etc/nginx/sites-enabled/default
ln -sf "$NGINX_CONF" "$NGINX_LINK"

nginx -t
systemctl reload nginx 2>/dev/null || systemctl start nginx

# ============================================================================
# Step 13: Start Backend (needs socket before final nginx config)
# ============================================================================

log_step "Step 13: Starting backend service"

systemctl start "$SERVICE_NAME"
sleep 3
if systemctl is-active --quiet "$SERVICE_NAME"; then
    log_info "$SERVICE_NAME is running"
else
    log_error "$SERVICE_NAME failed to start. Recent logs:"
    journalctl -u "$SERVICE_NAME" --no-pager -n 30
fi

# ============================================================================
# Step 14: SSL Certificate
# ============================================================================

log_step "Step 14: Obtaining SSL certificate"

if certbot certificates 2>/dev/null | grep -q "Domains: .*\b$DOMAIN\b"; then
    log_info "SSL certificate already exists for $DOMAIN"
else
    log_info "Requesting Let's Encrypt certificate for $DOMAIN..."
    if [ "$IS_SUBDOMAIN" = true ]; then
        certbot --nginx -d "$DOMAIN" \
            --non-interactive --agree-tos --email "$ADMIN_EMAIL" --redirect
    else
        certbot --nginx -d "$DOMAIN" -d "www.$DOMAIN" \
            --non-interactive --agree-tos --email "$ADMIN_EMAIL" --redirect 2>/dev/null || \
        certbot --nginx -d "$DOMAIN" \
            --non-interactive --agree-tos --email "$ADMIN_EMAIL" --redirect
    fi
fi

# Auto-renewal cron
echo '0 3 * * * root certbot renew --quiet --deploy-hook "systemctl reload nginx"' > /etc/cron.d/openbull-certbot-renewal
chmod 644 /etc/cron.d/openbull-certbot-renewal

if [ ! -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]; then
    log_error "SSL certificate not obtained. Check DNS for $DOMAIN, then run certbot manually."
else
    log_info "SSL certificate obtained"
fi

# ============================================================================
# Step 15: Final Nginx Configuration (SSL + proxy + SPA)
# ============================================================================

log_step "Step 15: Writing final Nginx configuration"

cat > "$NGINX_CONF" <<NGEOF
# OpenBull - $DOMAIN

upstream openbull_backend {
    server unix:/run/openbull/openbull.sock;
    keepalive 32;
}

server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;

    location = /ws  { return 301 https://\$host\$request_uri; }
    location /ws/   { return 301 https://\$host\$request_uri; }
    location /      { return 301 https://\$host\$request_uri; }
}

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name $DOMAIN;

    ssl_certificate     /etc/letsencrypt/live/$DOMAIN/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$DOMAIN/privkey.pem;

    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers on;
    ssl_ciphers EECDH+AESGCM:EDH+AESGCM;
    ssl_ecdh_curve secp384r1;
    ssl_session_timeout 10m;
    ssl_session_cache shared:SSL:10m;
    ssl_session_tickets off;
    ssl_stapling on;
    ssl_stapling_verify on;

    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;
    add_header X-Frame-Options SAMEORIGIN always;
    add_header X-Content-Type-Options nosniff always;
    add_header Referrer-Policy strict-origin-when-cross-origin always;
    server_tokens off;

    gzip on;
    gzip_vary on;
    gzip_min_length 1024;
    gzip_comp_level 6;
    gzip_types text/plain text/css text/xml application/json application/javascript application/xml image/svg+xml;

    client_max_body_size 100M;
    client_body_timeout 300s;
    keepalive_timeout 65s;

    # Frontend (built React SPA)
    root $FRONTEND_DIR/dist;
    index index.html;

    # FastAPI backend routes
    location /api {
        proxy_pass http://openbull_backend;
        proxy_http_version 1.1;
        proxy_read_timeout 300s;
        proxy_connect_timeout 300s;
        proxy_send_timeout 300s;
        proxy_buffer_size 128k;
        proxy_buffers 4 256k;
        proxy_busy_buffers_size 256k;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
    }

    location /docs         { proxy_pass http://openbull_backend; include /etc/nginx/proxy_params; }
    location /redoc        { proxy_pass http://openbull_backend; include /etc/nginx/proxy_params; }
    location /openapi.json { proxy_pass http://openbull_backend; include /etc/nginx/proxy_params; }

    # WebSocket proxy (backend runs on 8765)
    location = /ws {
        proxy_pass http://127.0.0.1:8765;
        proxy_http_version 1.1;
        proxy_read_timeout 86400s;
        proxy_send_timeout 86400s;
        proxy_buffering off;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    location /ws/ {
        proxy_pass http://127.0.0.1:8765/;
        proxy_http_version 1.1;
        proxy_read_timeout 86400s;
        proxy_send_timeout 86400s;
        proxy_buffering off;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    # SPA fallback for React Router
    location / {
        try_files \$uri \$uri/ /index.html;
    }

    # Deny hidden/sensitive files
    location ~ /\.          { deny all; return 404; }
    location ~ \.(env|git|ini|log|sh|sql|conf|bak)$ { deny all; return 404; }
}
NGEOF

# Make sure proxy_params exists
if [ ! -f /etc/nginx/proxy_params ]; then
    cat > /etc/nginx/proxy_params <<'PPEOF'
proxy_set_header Host $http_host;
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
PPEOF
fi

nginx -t
systemctl reload nginx
log_info "Final Nginx configuration applied"

# ============================================================================
# Done
# ============================================================================

log_step "Installation Complete"

echo -e "${GREEN}"
echo "  OpenBull is now running at:"
echo ""
echo "    https://$DOMAIN"
echo ""
echo "  Configuration:"
echo "    App root:    $APP_ROOT"
echo "    .env file:   $APP_ROOT/.env"
echo "    Database:    $DB_NAME (user: $DB_USER @ $DB_HOST:$DB_PORT)"
echo "    Redis:       redis://127.0.0.1:6379/0"
echo "    Service:     $SERVICE_NAME"
echo "    Log dir:     $LOG_DIR"
echo "    Install log: $INSTALL_LOG"
echo ""
echo "  Useful commands:"
echo "    sudo systemctl status $SERVICE_NAME"
echo "    sudo systemctl restart $SERVICE_NAME"
echo "    sudo journalctl -u $SERVICE_NAME -f"
echo "    sudo tail -f $LOG_DIR/backend.log"
echo ""
echo "  Update to latest code:"
echo "    sudo bash $APP_ROOT/install/update.sh"
echo ""
echo "  Run performance tuning:"
echo "    sudo bash $APP_ROOT/install/perftuning.sh"
echo -e "${NC}"
