# OpenBull Operations Runbook

| Field | Value |
|---|---|
| **Last updated** | 2026-05-11 |
| **Owner** | Platform team |
| **Audience** | Operators running OpenBull in dev or production |
| **Source of truth** | `backend/main.py`, `install/install.sh`, `backend/utils/logging.py`, `backend/services/market_data_cache.py` |

This is the operational playbook for OpenBull — how to diagnose, debug, recover, and tune a running deployment. Pair it with [ARCHITECTURE.md](./ARCHITECTURE.md) for the design background.

---

## 1. Where to look first when something goes wrong

| Symptom | First thing to check |
|---|---|
| Backend won't start | `logs/openbull.log` — startup errors land in the first 50 lines. `migrate_all.py` failure is the most common cause. |
| User can log in but Dashboard is empty | Broker token may have expired overnight. Check `/web/auth/me` response — `broker_status` will indicate. User should log out and re-OAuth. |
| Orders return `Broker-specific module not found` (404) | `VALID_BROKERS` env var doesn't include the user's connected broker, or plugin loader failed at startup. Grep `logs/openbull.log` for `Loaded N broker plugins`. |
| WebSocket clients see "Authentication failed" | API key was rotated or the negative cache (`api_key:invalid:*` in Redis, 5 min TTL) is hot. Flush Redis or wait 5 min. |
| Sandbox orders don't fill | `MarketDataCache` is empty for that symbol. Check `/api/websocket/market-data/{exchange}/{symbol}` — if `null`, the WS feed isn't carrying ticks for that symbol. |
| `/tools/optionchain` shows stale data | Master-contract download didn't run today. Trigger from `/broker/config` (button: "Download Master Contracts"). |
| Sudden 500s on every endpoint | DB connection pool exhausted or PostgreSQL is unreachable. `logs/openbull-error.log` will show `sqlalchemy.exc.OperationalError`. |
| High memory growth in the backend | Probably the `api_logs` queue isn't draining — check `init_api_log_writer` log line and queue depth metric in `/health` (if added; otherwise grep `ApiLogWriter` log lines). |

---

## 2. Log locations

| Stream | Path / Source | Retention |
|---|---|---|
| Console | stdout of `uvicorn` (or `journalctl -u openbull` when systemd-managed) | Whatever the terminal / journald keeps |
| All-levels file | `logs/openbull.log` | `LOG_FILE_BACKUP_COUNT + 1` files × `LOG_FILE_MAX_MB` MB (default 100 MB total) |
| Warnings+ file | `logs/openbull-error.log` | Same rotation |
| DB-backed API logs | `api_logs` table | Bounded to `API_LOG_DB_MAX_ROWS` (default 100,000) |
| DB-backed error logs | `error_logs` table | Bounded to `ERROR_LOG_DB_MAX_ROWS` (default 50,000) |
| In-app log viewer | `/logs` page (auth-gated) | Browses both DB tables with filters |
| Web-server access log | nginx `/var/log/nginx/access.log` and `error.log` | Logrotate default |

**Every record is request-id correlated** via the `X-Request-ID` header (`request_id_var` contextvar). To trace a specific failing request end-to-end:

```bash
grep "abc123def" logs/openbull.log
# then
psql -d openbull -c "SELECT * FROM api_logs WHERE request_id = 'abc123def'"
```

The id is echoed in the response headers, so the user can grab it from browser devtools and quote it on a support ticket.

**Sensitive-data redaction** runs on every record before any handler emits — `apikey`, `password`, `access_token`, `Authorization` headers, `Bearer`/`token` schemes. If you see something in a log that looks like a secret, it slipped past the redactor — file an issue with the line as evidence.

---

## 3. Health endpoint

```bash
curl http://127.0.0.1:8000/health
```

```json
{"status": "ok", "app": "OpenBull", "version": "0.1.0"}
```

This is a liveness probe — it doesn't check downstream dependencies. For deeper checks:

| What to verify | How |
|---|---|
| Database reachable | `psql -d openbull -c "SELECT 1"` |
| Redis reachable | `redis-cli ping` (returns `PONG`) |
| WebSocket proxy listening | `nc -z 127.0.0.1 8765` |
| ZeroMQ bus alive | `python -c "import zmq; ctx=zmq.Context(); s=ctx.socket(zmq.SUB); s.connect('tcp://127.0.0.1:5555'); s.setsockopt(zmq.SUBSCRIBE, b''); s.setsockopt(zmq.RCVTIMEO, 5000); print(s.recv())"` — should print a tick within 5s during market hours. |
| Live tick is flowing | `curl http://127.0.0.1:8000/api/websocket/market-data/NSE_INDEX/NIFTY` — non-null `ltp` during market hours. |
| Broker plugins loaded | First minute of `logs/openbull.log` — `Loaded N broker plugins: [...]`. |

---

## 4. Common failure modes

### 4.1 PostgreSQL connection pool exhausted

**Symptom**: every endpoint starts returning HTTP 500 with `OperationalError`. The 504/timeout depends on uvicorn workers being able to acquire DB connections.

**Diagnose**:
```bash
psql -d openbull -c "SELECT count(*), state FROM pg_stat_activity WHERE datname='openbull' GROUP BY state"
```

If `idle in transaction` is large, a code path is leaking connections.

**Recover**:
1. Restart the backend (`systemctl restart openbull` or `Ctrl+C` + rerun).
2. Bump connection pool size in `backend/database.py` (`pool_size`, `max_overflow`) — only if usage genuinely warrants it.
3. Run `install/perftuning.sh` if Postgres hasn't been tuned for the host.

### 4.2 Redis unavailable

**Symptom**: requests still succeed (cache-aside falls through to DB), but they're 10–100× slower. Log line: `Redis GET failed:` followed by exception. Login latency spikes because every request runs `Argon2.verify` on the API key.

**Diagnose**: `redis-cli ping` returns nothing or times out.

**Recover**:
1. Restart Redis: `systemctl restart redis-server`.
2. Confirm `REDIS_URL` in `.env` matches the running instance.
3. If using a remote Redis (managed / Docker), check network reachability.

Once Redis is back, it'll re-populate organically from cache-misses; no manual warm-up needed.

### 4.3 Broker token revoked mid-session

**Symptom**: orders return broker-specific 401/403 with messages like `"Invalid Token"` or `"Session expired"`.

**Recover**: user must log out via the topbar and re-complete OAuth. There's no in-app refresh-token flow for most brokers (Upstox supports refresh tokens but expires them daily at 3:30 AM IST, hence the `SESSION_EXPIRY_TIME=03:00` default).

**Force-fix at the admin level**:
```sql
DELETE FROM broker_auth WHERE user_id = <id>;
DELETE FROM active_sessions WHERE user_id = <id>;
```

User will be forced to log in fresh next time.

### 4.4 Master-contract download fails

**Symptom**: `/search`, option chain, and any option-services endpoint return `Symbol not found`. The Underlying picker shows zero options.

**Diagnose**:
```bash
psql -d openbull -c "SELECT exchange, COUNT(*) FROM symtoken GROUP BY exchange"
```

Empty result → master download never ran. Partial result (only some exchanges) → broker's master endpoint returned an error mid-download.

**Recover**:
1. Trigger a manual download from the `/broker/config` page (button: Download Master Contracts).
2. Watch `logs/openbull.log` for `master_contract_download` output.
3. If the broker's master endpoint is rate-limiting (Fyers, Upstox both throttle), wait 5 min and retry.

The download is idempotent — every run replaces all rows for that broker, so it's safe to retry.

### 4.5 Sandbox orders stay pending forever

**Symptom**: orders created in sandbox mode show `status: "open"` indefinitely. `MarketDataCache` reads return `null` for the symbol.

**Diagnose**:
- Is the WS proxy running? `nc -z 127.0.0.1 8765`.
- Is the broker adapter publishing? Check `logs/openbull.log` for `[<broker>_adapter]` lines around the time you placed the order.
- Is the symbol on the broker's subscription list? Sandbox engine subscribes via `MarketDataCache`, which only carries symbols that an external WS client (or another sandbox order) has triggered a subscribe for.

**Recover**:
1. Open `/websocket/test` in the UI and subscribe to the affected symbol in `Quote` mode — this forces the broker adapter to subscribe.
2. The 5-second polling fallback (`execution_engine`) will pick up the next matching tick.
3. If the broker WS itself is down, you'll see `WebSocket closed` followed by repeated reconnect attempts.

### 4.6 WebSocket clients keep getting disconnected

**Symptom**: clients reconnect every 30–60 seconds; subscriptions don't survive.

**Diagnose**:
- Check `MAX_WEBSOCKET_CONNECTIONS` env var (default 10 backend-wide; clients beyond that get rejected with `Max connections reached`).
- Check the client's heartbeat. The server pings every 30 s with a 10 s timeout — clients that don't pong are dropped.
- Behind nginx? Confirm the proxy_read_timeout is at least 60 s (`install/install.sh` sets it; check `/etc/nginx/sites-enabled/openbull`).

**Recover**: bump `MAX_WEBSOCKET_CONNECTIONS` if you need >10 concurrent clients, or aggregate at the client side (use one shared WS for the whole tab, like the Strategy Portfolio does).

### 4.7 `api_logs` table is growing too fast

**Symptom**: disk pressure on the DB partition. `\dt+ api_logs` shows the table is at its row cap.

**Recover**: the writer auto-trims to `API_LOG_DB_MAX_ROWS` after each batch — disk shouldn't grow unbounded. If it is:
1. Confirm `API_LOG_DB_MAX_ROWS` is set (default 100,000).
2. Run `VACUUM FULL api_logs` once after a manual trim to reclaim space — autovacuum doesn't shrink the on-disk size, only marks rows as reusable.

For more aggressive trimming, lower the cap; the writer applies it on the next batch.

---

## 5. Deployment

| Mode | Command | Notes |
|---|---|---|
| **Dev** | `uv run uvicorn backend.main:app --reload` + `cd frontend && npm run dev` | Reload on code change. Vite at 5173, FastAPI at 8000, WS at 8765. |
| **First-time prod install** | `sudo ./install/install.sh` | Cloudflare-aware Ubuntu installer. Sets up nginx (A-grade security headers), Postgres, Redis, systemd, certbot, optional swap. Idempotent — safe to re-run. |
| **Update prod** | `sudo ./install/update.sh` | Pull, run `migrate_all.py`, build frontend, reload services. |
| **Performance tune** | `sudo ./install/perftuning.sh` | Postgres + Redis kernel and ulimit tuning. Apply once after install. |

### 5.1 Systemd units

- `openbull.service` — runs `uvicorn backend.main:app` (the WebSocket proxy starts inside the same process).
- nginx (system unit) — terminates TLS, proxies `/auth`, `/web`, `/api`, `/upstox`, `/zerodha`, `/health`, `/ws` to the backend.

```bash
# Common ops
systemctl status openbull         # is it running?
systemctl restart openbull        # restart after env changes
journalctl -u openbull -f         # tail console output
systemctl reload nginx            # after config change
```

### 5.2 Zero-downtime caveats

OpenBull is currently a **single-process backend**. Restarting the unit will drop in-flight WebSocket subscriptions; clients have to re-authenticate. There's no rolling-restart support. For a production setup that can't tolerate this:

1. Run two backend instances behind nginx with sticky sessions.
2. Each instance gets its own ZeroMQ port and broker WS.
3. Promote/demote one at a time during upgrades.

This is documented for completeness — the shipped install assumes a single-process backend is acceptable.

---

## 6. Monitoring

OpenBull doesn't ship a metrics emitter. To monitor in production, set up one of:

### Lightweight: log-based

- `logs/openbull.log` lines like `METHOD PATH -> STATUS in Xms` are structured enough for log-shipping (Loki, Vector, Promtail) and ad-hoc rate / latency dashboards.
- `error_logs` table holds WARNING+ events for the last `ERROR_LOG_DB_MAX_ROWS` records. Query for alerting:
  ```sql
  SELECT COUNT(*) FROM error_logs WHERE created_at > now() - interval '5 min'
  ```

### Recommended: Prometheus exporter (gap — see Action Plan)

Adding `prometheus-fastapi-instrumentator` to `backend/main.py` would expose `/metrics` with HTTP latency histograms, request counts, and exception counters. Not currently wired — listed in the Action Plan below.

### What to alert on

| Signal | Threshold | Why |
|---|---|---|
| HTTP 5xx rate | `> 1% over 5 min` | Backend or downstream broker fault |
| Order placement failures | `> 5 over 10 min` | Broker-side issue or auth expiry |
| `MarketDataCache.is_data_fresh()` returns `False` | sustained `> 60 s` during market hours | Broker WS feed is stale; trade-management gate trips |
| Master-contract row count | `< previous day × 0.9` | Master download partial-success |
| `error_logs` insert rate | `> 50/min` | Bug spike — open the viewer |

---

## 7. Backup and recovery

### What to back up

| Item | How | Why |
|---|---|---|
| `openbull` database | `pg_dump openbull > openbull-$(date +%F).sql` daily | Holds users, strategies, sandbox state, audit trail, API keys (hashed). |
| `.env` | Out-of-band, encrypted | `APP_SECRET_KEY` + `ENCRYPTION_PEPPER` regenerated => every stored API key + broker secret is unreadable. Treat as crown jewels. |
| `logs/` | Optional | Rotating files; useful for post-mortem if you don't ship them off-host. |

Redis is **not** backed up — it's strictly cache. Rebuilds from DB on cold start.

### Restore procedure

1. Provision a fresh Postgres of the same major version.
2. `psql -d openbull -f openbull-YYYY-MM-DD.sql`
3. Drop the original `.env` into the new host, keeping the same `APP_SECRET_KEY` + `ENCRYPTION_PEPPER` so encrypted broker secrets decrypt cleanly.
4. Run `uv run migrate_all.py` — idempotent; brings the restored DB up to current schema.
5. Start the backend; users will be prompted to re-OAuth their broker since `active_sessions` were truncated by the daily 3 AM expiry.

### Disaster recovery RTO/RPO

| Metric | Default | How to improve |
|---|---|---|
| RTO (recovery time) | ~15 min (provision + restore + start) | Pre-warm a hot-standby Postgres replica |
| RPO (recovery point) | ≤ 24 h (daily backup) | Continuous WAL archiving with PITR |

---

## 8. Debugging tips

### 8.1 Enable verbose logging temporarily

```bash
# In .env:
LOG_LEVEL=DEBUG
# Restart the backend. Rotate back to INFO after capture.
```

DEBUG-level logs are noisy — keep this window short, then revert. The sensitive-data redactor still runs, so dropping to DEBUG won't leak secrets.

### 8.2 Reproduce a sandbox failure

Run the end-to-end sandbox harness:

```bash
uv run python sandbox_e2e_test.py
```

It exercises the order → fill → MTM → squareoff lifecycle against a deterministic tick stream. Useful when something feels off in the sandbox engine but no real-broker-side issue is visible.

### 8.3 Inspect a failing API call

1. Get the `X-Request-ID` from response headers (browser devtools → Network → Response Headers).
2. ```sql
   SELECT * FROM api_logs WHERE request_id = '<id>';
   ```
3. The row has the full request body (redacted), response body, latency, mode, and user_id.
4. Tail-grep the file log for the same id:
   ```bash
   grep "<id>" logs/openbull.log
   ```

### 8.4 Inspect broker WS traffic without opening a subscription

The `/websocket/test` page (dev only — `requires_broker`) lets you cherry-pick symbols and watch their cached ticks land in real time. Useful when a specific symbol seems to "not be ticking" — it'll either confirm the cache is dead, or surface that ticks ARE arriving and the consumer is broken.

### 8.5 Check Redis cache state

```bash
redis-cli
> KEYS openbull:*
> GET openbull:api_key:valid:abc...
> HGETALL openbull:symtoken:tok2sym
```

All keys are namespaced under `openbull:`. To invalidate a user's cached broker context after manual DB tweaks:

```bash
redis-cli DEL openbull:broker_ctx:<user_id> openbull:api_ctx:<user_id>
```

---

## 9. Common ops commands cheatsheet

```bash
# Backend
systemctl status openbull              # health
systemctl restart openbull             # restart
journalctl -u openbull -f              # tail console

# Database
psql -d openbull                       # connect
psql -d openbull -c "SELECT COUNT(*) FROM symtoken GROUP BY exchange"
psql -d openbull -c "SELECT COUNT(*) FROM api_logs WHERE created_at > now() - interval '1 hour'"
pg_dump openbull > openbull-$(date +%F).sql

# Redis
redis-cli ping
redis-cli KEYS 'openbull:*' | head
redis-cli DBSIZE
redis-cli FLUSHDB                      # nuclear option; cache will rebuild

# Migrations
uv run migrate_all.py                  # idempotent; safe on every deploy

# Health
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/api/websocket/market-data/NSE_INDEX/NIFTY

# Update
sudo ./install/update.sh               # pull + migrate + rebuild + reload

# Logs
tail -f logs/openbull.log
tail -f logs/openbull-error.log
```

---

## 10. Escalation

When a failure isn't covered above:

1. Capture the `request_id` of a failing call.
2. Grab the matching `api_logs` row + `error_logs` rows for the request id.
3. Tail `logs/openbull-error.log` for the same time window.
4. Open an issue at https://github.com/marketcalls/openbull/issues (or your internal tracker) with:
   - Request id
   - Time window
   - Stack trace
   - Steps to reproduce
   - Whether the user was in live or sandbox mode

---

## See also

- [ARCHITECTURE.md](./ARCHITECTURE.md) — system design, why things are wired the way they are
- [PRODUCT.md](../PRODUCT.md) — what OpenBull does and for whom
- [SERVICES.md](./SERVICES.md) — every service function with file:line locations
- [Top-level README § Production deployment](../../README.md#production-deployment) — install / update / perftuning scripts
