# Deploying OpenBull for free

Two pieces:

- **A. Always-on app** — one free VM running the whole stack via Docker Compose.
- **B. Daily accumulating export** — keeps the Databricks archive growing.

> Databricks itself is **not free** (the SQL warehouse bills per query, but
> auto-stops when idle, so it's cheap). Everything below is $0.

---

## A. App on a free always-on VM (recommended: Oracle Cloud Always Free)

Why a VM and not Render/Railway/Koyeb free tiers: OpenBull runs background
daemons + a WebSocket depth feed that must stay alive. Idle-sleep free tiers
kill those. Oracle's **Always Free** ARM VM (up to 4 vCPU / 24 GB) is genuinely
always-on. Any small VM works (a 1 vCPU / 1 GB box is enough to start).

### 1. Provision
- Create an **Always Free** VM (Ubuntu 22.04, ARM `VM.Standard.A1.Flex` or AMD `E2.1.Micro`).
- Open ports **80** (and **443** later) in the VCG security list + `ufw`.

### Quick start (one script)
On a fresh Ubuntu VM you can skip steps 2–4 and run the provisioner, which
installs Docker, clones your fork, generates secrets, and brings the stack up:
```bash
REPO_URL=https://github.com/cshah911/openbull.git BRANCH=deploy/cloud-and-smc \
  bash <(curl -fsSL https://raw.githubusercontent.com/cshah911/openbull/deploy/cloud-and-smc/deploy/provision.sh)
```
It pauses once for you to edit `.env.prod` (DOMAIN, Databricks). Prefer manual?
Follow the steps below.

### 2. Install Docker
```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER && newgrp docker
```

### 3. Clone + configure
```bash
git clone <your-repo-url> openbull && cd openbull
cp .env.prod.example .env.prod
# edit .env.prod — generate secrets:
python3 -c "import secrets; print(secrets.token_hex(32))"   # APP_SECRET_KEY
python3 -c "import secrets; print(secrets.token_hex(32))"   # ENCRYPTION_PEPPER
# set POSTGRES_PASSWORD + matching DATABASE_URL, FRONTEND_URL/CORS_ORIGINS to
# your domain or http://<VM_PUBLIC_IP>, and the DATABRICKS_* values.
```

### 4. Launch
```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
```
- Frontend: `http://<VM_PUBLIC_IP>/`  →  first visit, create your user account.
- Backend is internal; nginx proxies `/api /auth /web /upstox /zerodha /ws/ /health`.
- Logs: `docker compose -f docker-compose.prod.yml logs -f backend`
- Update: `git pull && docker compose -f docker-compose.prod.yml up -d --build`

### 5. HTTPS (built in via Caddy)
The stack includes a **Caddy** service that auto-fetches a Let's Encrypt cert.
Point a DNS A-record at the VM's public IP, then set in `.env.prod`:
```
DOMAIN=openbull.example.com
```
Re-run `docker compose ... up -d` and Caddy serves HTTPS on 443 (HTTP→HTTPS
redirect) with automatic renewal. Leave `DOMAIN=:80` for plain HTTP while
testing. Cookies are session-based, so use HTTPS before real use.

> The entrypoint refuses to start if `APP_SECRET_KEY`, `ENCRYPTION_PEPPER`,
> `DATABASE_URL`, or `REDIS_URL` are unset or still `change_me...` placeholders.

### Security checklist
- Secrets only in `.env.prod` (git-ignored) — never commit them.
- Firewall: expose only 80/443. Postgres/Redis stay on the internal compose network.
- Upstox is sandbox; still treat the token as real. It refreshes daily via the app.

---

## B. Daily export — two ways

The export reads the **Upstox token from the app DB** (decrypted with
`ENCRYPTION_PEPPER`) — it can't mint a token, so a fresh one must already be in
the DB (the running backend refreshes it daily after you log in / re-auth).

### B1. On the VM (simplest — same box, same DB, token already present)
```bash
crontab -e
# 16:00 IST Mon–Fri: run inside the backend container
30 10 * * 1-5 cd /home/ubuntu/openbull && docker compose -f docker-compose.prod.yml --env-file .env.prod exec -T backend uv run python analysis_export.py --option-expiries 3 --no-scans --no-depth >> /home/ubuntu/export.log 2>&1
```

### B2. GitHub Actions (`.github/workflows/daily-export.yml`)
Use this only if your Postgres is **reachable from GitHub runners** (a managed
DB like Neon/Supabase, or your VM's Postgres exposed over TLS). Add these repo
secrets (Settings → Secrets and variables → Actions):

`DATABASE_URL`, `ENCRYPTION_PEPPER`, `APP_SECRET_KEY`, `DATABRICKS_HOST`,
`DATABRICKS_HTTP_PATH`, `DATABRICKS_TOKEN`, `DATABRICKS_CATALOG`,
`DATABRICKS_SCHEMA`, `DATABRICKS_STAGE_VOLUME`.

It runs weekdays at 16:00 IST (and on-demand via "Run workflow"). If the DB has
no fresh token it logs `No active Upstox session` and leaves Databricks untouched.

---

## Retrain after exports accumulate
Once more expiries are archived, refresh the SMC model (options, intraday,
long-only are the defaults):
```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod exec -T backend uv run python smc_train.py
```
The backend hot-reloads the model file (mtime cache) — no restart needed.
