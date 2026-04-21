#!/bin/bash
set -e

# ============================================================================
# OpenBull - Performance Tuning Script
# Optimizes Nginx, PostgreSQL, Redis, Systemd, and kernel settings
# for 200+ simultaneous users.
#
# Usage: sudo bash perftuning.sh
# ============================================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_step()  { echo -e "\n${CYAN}=== $1 ===${NC}\n"; }

check_status() {
    if [ $? -eq 0 ]; then
        echo "  [OK] $1"
    else
        echo "  [FAIL] $1"
    fi
}

# Fixed paths (must match install.sh)
APP_ROOT="/var/www/openbull"
SERVICE_NAME="openbull"
SERVICE_FILE="/etc/systemd/system/$SERVICE_NAME.service"
NGINX_CONF="/etc/nginx/sites-available/openbull"

# ============================================================================
# Pre-flight
# ============================================================================

if [ "$EUID" -ne 0 ]; then
    log_error "Please run as root (sudo bash perftuning.sh)"
    exit 1
fi

if [ ! -f "$NGINX_CONF" ]; then
    log_error "Nginx config not found at $NGINX_CONF"
    log_error "Run install.sh first."
    exit 1
fi

# Detect RAM
TOTAL_RAM_KB=$(grep MemTotal /proc/meminfo | awk '{print $2}')
TOTAL_RAM_GB=$((TOTAL_RAM_KB / 1024 / 1024))
[ $TOTAL_RAM_GB -lt 1 ] && TOTAL_RAM_GB=1
log_info "Detected RAM: ${TOTAL_RAM_GB}GB"

log_step "OpenBull Performance Tuning"
echo "  Target:  200+ simultaneous users"
echo "  RAM:     ${TOTAL_RAM_GB}GB"
echo ""

TIMESTAMP=$(date +%Y%m%d%H%M%S)

# ============================================================================
# Step 1: Nginx Optimization
# ============================================================================

log_step "Step 1: Nginx"

cp "$NGINX_CONF" "${NGINX_CONF}.bak.$TIMESTAMP"
check_status "Nginx site config backed up"

# install.sh already writes the upstream with keepalive 32 and WS timeouts.
# Add a per-IP connection cap if not present.
if ! grep -q "limit_conn_zone.*openbull_conn" /etc/nginx/conf.d/openbull-rate-limit.conf 2>/dev/null; then
    cat > /etc/nginx/conf.d/openbull-rate-limit.conf <<'RLEOF'
limit_req_zone  $binary_remote_addr zone=openbull_login:10m rate=5r/m;
limit_req_zone  $binary_remote_addr zone=openbull_api:10m   rate=30r/s;
limit_conn_zone $binary_remote_addr zone=openbull_conn:10m;
RLEOF
    check_status "Rate-limit/connection zones added"
fi

if ! grep -q "limit_conn openbull_conn" "$NGINX_CONF"; then
    sed -i '/listen 443 ssl/a\    limit_conn openbull_conn 50;' "$NGINX_CONF"
    check_status "Per-IP connection limit set to 50"
fi

# nginx.conf main tuning
NGINX_MAIN="/etc/nginx/nginx.conf"
if [ -f "$NGINX_MAIN" ]; then
    cp "$NGINX_MAIN" "${NGINX_MAIN}.bak.$TIMESTAMP"
    sed -i 's/^worker_processes .*/worker_processes auto;/' "$NGINX_MAIN"
    if ! grep -q "^worker_rlimit_nofile" "$NGINX_MAIN"; then
        sed -i '/^worker_processes/a worker_rlimit_nofile 65535;' "$NGINX_MAIN"
    fi
    sed -i 's/worker_connections [0-9]*;/worker_connections 4096;/' "$NGINX_MAIN"
    if ! grep -q "multi_accept on" "$NGINX_MAIN"; then
        sed -i '/worker_connections/a\    multi_accept on;' "$NGINX_MAIN"
    fi
    check_status "nginx.conf worker settings tuned"
fi

nginx -t 2>/dev/null
check_status "Nginx config test passed"
systemctl reload nginx
check_status "Nginx reloaded"

# ============================================================================
# Step 2: PostgreSQL Optimization
# ============================================================================

log_step "Step 2: PostgreSQL"

PG_CONF=$(find /etc/postgresql -name "postgresql.conf" 2>/dev/null | head -1)
if [ -z "$PG_CONF" ]; then
    log_warn "PostgreSQL config not found, skipping"
else
    cp "$PG_CONF" "${PG_CONF}.bak.$TIMESTAMP"

    if [ "$TOTAL_RAM_GB" -ge 16 ]; then
        SHARED_BUFFERS="2GB"
        EFFECTIVE_CACHE="8GB"
        WORK_MEM="32MB"
        MAINT_WORK_MEM="512MB"
    elif [ "$TOTAL_RAM_GB" -ge 8 ]; then
        SHARED_BUFFERS="1GB"
        EFFECTIVE_CACHE="4GB"
        WORK_MEM="16MB"
        MAINT_WORK_MEM="256MB"
    elif [ "$TOTAL_RAM_GB" -ge 4 ]; then
        SHARED_BUFFERS="512MB"
        EFFECTIVE_CACHE="2GB"
        WORK_MEM="8MB"
        MAINT_WORK_MEM="128MB"
    else
        SHARED_BUFFERS="256MB"
        EFFECTIVE_CACHE="1GB"
        WORK_MEM="4MB"
        MAINT_WORK_MEM="64MB"
    fi

    sed -i "s/^#\?max_connections = .*/max_connections = 150/"                                   "$PG_CONF"
    sed -i "s/^#\?shared_buffers = .*/shared_buffers = $SHARED_BUFFERS/"                         "$PG_CONF"
    sed -i "s/^#\?effective_cache_size = .*/effective_cache_size = $EFFECTIVE_CACHE/"            "$PG_CONF"
    sed -i "s/^#\?work_mem = .*/work_mem = $WORK_MEM/"                                           "$PG_CONF"
    sed -i "s/^#\?maintenance_work_mem = .*/maintenance_work_mem = $MAINT_WORK_MEM/"             "$PG_CONF"
    sed -i "s/^#\?wal_buffers = .*/wal_buffers = 64MB/"                                          "$PG_CONF"
    sed -i "s/^#\?checkpoint_completion_target = .*/checkpoint_completion_target = 0.9/"        "$PG_CONF"
    sed -i "s/^#\?random_page_cost = .*/random_page_cost = 1.1/"                                 "$PG_CONF"
    sed -i "s/^#\?effective_io_concurrency = .*/effective_io_concurrency = 200/"                 "$PG_CONF"
    sed -i "s/^#\?idle_in_transaction_session_timeout = .*/idle_in_transaction_session_timeout = 30000/" "$PG_CONF"
    sed -i "s/^#\?statement_timeout = .*/statement_timeout = 60000/"                             "$PG_CONF"
    sed -i "s/^#\?log_min_duration_statement = .*/log_min_duration_statement = 1000/"            "$PG_CONF"

    echo "  [OK] max_connections       = 150"
    echo "  [OK] shared_buffers        = $SHARED_BUFFERS"
    echo "  [OK] effective_cache_size  = $EFFECTIVE_CACHE"
    echo "  [OK] work_mem              = $WORK_MEM"
    echo "  [OK] maintenance_work_mem  = $MAINT_WORK_MEM"
    echo "  [OK] wal_buffers           = 64MB"
    echo "  [OK] statement_timeout     = 60s"
    echo "  [OK] random_page_cost      = 1.1 (SSD-optimized)"

    systemctl restart postgresql
    check_status "PostgreSQL restarted with optimized settings"
fi

# ============================================================================
# Step 3: Redis Optimization
# ============================================================================

log_step "Step 3: Redis"

REDIS_CONF="/etc/redis/redis.conf"
if [ -f "$REDIS_CONF" ]; then
    cp "$REDIS_CONF" "${REDIS_CONF}.bak.$TIMESTAMP"

    if [ "$TOTAL_RAM_GB" -ge 16 ]; then
        REDIS_MAXMEM="512mb"
    elif [ "$TOTAL_RAM_GB" -ge 8 ]; then
        REDIS_MAXMEM="256mb"
    else
        REDIS_MAXMEM="128mb"
    fi

    sed -i "s/^#\? *maxmemory .*/maxmemory $REDIS_MAXMEM/" "$REDIS_CONF"
    grep -q "^maxmemory " "$REDIS_CONF" || echo "maxmemory $REDIS_MAXMEM" >> "$REDIS_CONF"

    sed -i "s/^#\? *maxmemory-policy .*/maxmemory-policy allkeys-lru/" "$REDIS_CONF"
    grep -q "^maxmemory-policy" "$REDIS_CONF" || echo "maxmemory-policy allkeys-lru" >> "$REDIS_CONF"

    sed -i "s/^#\? *tcp-keepalive .*/tcp-keepalive 60/" "$REDIS_CONF"

    echo "  [OK] maxmemory        = $REDIS_MAXMEM"
    echo "  [OK] maxmemory-policy = allkeys-lru"
    echo "  [OK] tcp-keepalive    = 60"

    systemctl restart redis-server 2>/dev/null || systemctl restart redis 2>/dev/null
    check_status "Redis restarted with optimized settings"
else
    log_warn "Redis config not found, skipping"
fi

# ============================================================================
# Step 4: System Kernel Tuning
# ============================================================================

log_step "Step 4: Kernel tuning"

SYSCTL_CONF="/etc/sysctl.d/99-openbull-perf.conf"
cat > "$SYSCTL_CONF" <<'EOF'
# OpenBull Performance Tuning
fs.file-max = 65536

# TCP
net.core.somaxconn = 4096
net.core.netdev_max_backlog = 4096
net.ipv4.tcp_max_syn_backlog = 4096

# TCP keepalive
net.ipv4.tcp_keepalive_time = 60
net.ipv4.tcp_keepalive_intvl = 10
net.ipv4.tcp_keepalive_probes = 6

net.ipv4.tcp_fin_timeout = 15
net.ipv4.tcp_tw_reuse = 1

net.ipv4.ip_local_port_range = 1024 65535

net.core.rmem_max = 16777216
net.core.wmem_max = 16777216

# VM
vm.swappiness = 10
vm.overcommit_memory = 1
EOF

sysctl -p "$SYSCTL_CONF" > /dev/null 2>&1
check_status "Kernel parameters applied"

LIMITS_CONF="/etc/security/limits.d/99-openbull.conf"
cat > "$LIMITS_CONF" <<'EOF'
www-data soft nofile 65536
www-data hard nofile 65536
www-data soft nproc 4096
www-data hard nproc 4096
EOF
check_status "File descriptor limits raised (www-data)"

# ============================================================================
# Step 5: Systemd service tuning
# ============================================================================

log_step "Step 5: Systemd service tuning"

if [ -f "$SERVICE_FILE" ]; then
    cp "$SERVICE_FILE" "${SERVICE_FILE}.bak.$TIMESTAMP"

    if ! grep -q "^LimitNOFILE=" "$SERVICE_FILE"; then
        sed -i '/^\[Service\]/a LimitNOFILE=65536' "$SERVICE_FILE"
        check_status "Added LimitNOFILE=65536"
    fi
    if ! grep -q "^LimitNPROC=" "$SERVICE_FILE"; then
        sed -i '/^\[Service\]/a LimitNPROC=4096' "$SERVICE_FILE"
        check_status "Added LimitNPROC=4096"
    fi

    systemctl daemon-reload
    check_status "Systemd daemon reloaded"
else
    log_warn "Service file not found: $SERVICE_FILE"
fi

# ============================================================================
# Step 6: Restart services
# ============================================================================

log_step "Step 6: Restarting services"

if systemctl is-enabled --quiet "$SERVICE_NAME" 2>/dev/null; then
    systemctl restart "$SERVICE_NAME"
    check_status "$SERVICE_NAME restarted"
fi

# ============================================================================
# Step 7: Verify
# ============================================================================

log_step "Step 7: Verification"

sleep 3

for svc in nginx postgresql redis-server "$SERVICE_NAME"; do
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        echo "  [OK]   $svc is running"
    elif [ "$svc" = "redis-server" ] && systemctl is-active --quiet "redis" 2>/dev/null; then
        echo "  [OK]   redis is running"
    else
        echo "  [WARN] $svc is not running"
    fi
done

SOCKET_FILE="/run/openbull/openbull.sock"
if [ -S "$SOCKET_FILE" ]; then
    if curl -s --max-time 5 --unix-socket "$SOCKET_FILE" http://localhost/api/health 2>/dev/null | grep -qiE "ok|healthy|status"; then
        echo "  [OK]   Backend health check passed (via socket)"
    else
        echo "  [WARN] Backend health check inconclusive (endpoint may differ)"
    fi
fi

# ============================================================================
# Summary
# ============================================================================

log_step "Applied settings"

echo "  Nginx:        upstream keepalive 32, per-IP cap 50, worker_conn 4096, multi_accept on"
echo "  PostgreSQL:   max_conn=150, shared_buffers=${SHARED_BUFFERS:-unchanged},"
echo "                effective_cache_size=${EFFECTIVE_CACHE:-unchanged}, work_mem=${WORK_MEM:-unchanged}"
echo "  Redis:        maxmemory=${REDIS_MAXMEM:-unchanged}, policy=allkeys-lru, tcp-keepalive=60"
echo "  Kernel:       somaxconn=4096, tcp_keepalive=60, swappiness=10, file-max=65536"
echo "  Systemd:      LimitNOFILE=65536, LimitNPROC=4096 on $SERVICE_NAME"
echo ""

log_step "Performance Tuning Complete"
echo ""
echo "  Monitor with:"
echo "    sudo journalctl -u $SERVICE_NAME -f"
echo "    sudo systemctl status $SERVICE_NAME"
echo ""
