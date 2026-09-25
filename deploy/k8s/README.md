# OpenBull on Kubernetes (Kustomize)

First-class Kubernetes deployment for OpenBull: base manifests + Kustomize
overlays, one instance per overlay, with the application architecture
(single-process backend, in-process WebSocket proxy, loopback ZeroMQ)
preserved exactly as the Ubuntu/VM installer (`install/install.sh`) runs it.

```
deploy/k8s/
├── base/            library: Deployment (app + nginx sidecar), Service,
│                    ConfigMaps (no Secret — overlays generate their own)
├── edge/
│   ├── ingress/     Ingress API variant (default; k3s Traefik works as-is)
│   └── gateway/     Gateway API variant (Standard channel; opt-in)
├── addons/
│   ├── postgres/    dev-only throwaway PostgreSQL (used by overlays/dev)
│   └── redis/       dev-only throwaway Redis
├── overlays/
│   ├── _template/   copy-me reference for a new instance
│   ├── example/     worked production overlay (external DB, Ingress)
│   └── dev/         local-cluster overlay with the addons, no edge
└── tests/           validation scripts (see "Verification" below)
```

## The model

> **One OpenBull instance = one single-replica pod (app container + nginx
> sidecar), one `Secret`, one `Service` (`openbull-http:80`), and one edge
> route (Ingress or HTTPRoute) on its own hostname.**

OpenBull is architecturally single-user and single-process: one uvicorn
worker runs the FastAPI app, the market-data WebSocket proxy (8765), the
loopback ZeroMQ bus (5555), the broker streaming adapters, the sandbox
engine and the IST schedulers — all with module-level globals. PostgreSQL
holds every durable thing; Redis is a cache. So, unlike a classic 12-factor
service:

- **`replicas: 1`, always.** A second replica would open a second broker
  feed, double-fill sandbox orders and run duplicate schedulers. Scaling
  out needs an app redesign (Redis pub/sub fan-out), not a number.
- **No PVC at all.** The only runtime writes are `logs/` and `tmp/`
  (master-contract downloads) — both are `emptyDir`s, wiped per pod and
  regenerated on demand.
- **A `Deployment` with `strategy: Recreate`**, not a StatefulSet and not
  RollingUpdate: with one replica, a rolling update briefly runs two pods —
  enough to double broker logins. Recreate guarantees pod-at-a-time; every
  deploy is a short outage by design.

Inside the pod:

```
                ┌────────────────────── pod: openbull ──────────────────────┐
edge (host+TLS) │  nginx sidecar :8080                                     │
      │         │   ├─ /(auth|web|api|upstox|zerodha|fyers|dhan|angel|docs│
      ▼         │   │  |redoc|openapi.json|health|webhook) → 127.0.0.1:8000│
Service         │   ├─ /ws/strategy/*  → 127.0.0.1:8000   (strategy WS)    │
openbull-http   │   ├─ /ws, /ws/*      → 127.0.0.1:8765   (market-data WS) │
     :80        │   └─ /*              → SPA (frontend/dist via webroot)   │
                │  app container: uvicorn --workers 1 (:8000)              │
                │                + in-process WS proxy (:8765)             │
                │                + ZMQ PUB/SUB (127.0.0.1:5555, never out) │
                │  init: migrate_all.py · init: seed SPA into webroot      │
                └──────────────────────────────────────────────────────────┘
```

This is the Kubernetes port of the VM install's nginx site — the sidecar
proxies over pod-loopback exactly like nginx proxied to the unix socket,
so ports 8000/8765 are never exposed as Services and 5555 never leaves the
pod. The edge (Ingress or Gateway) only routes host + TLS; all path routing
lives in `base/nginx-conf.yaml`, one place, controller-agnostic.

### Invariants — do not violate

| Rule | Why |
| --- | --- |
| `replicas: 1`, never more | double broker feeds, double sandbox fills, duplicate schedulers; two pods would also race the 8765/5555 loopback binds |
| Never split the app / WS proxy / ZMQ into sidecars or Pods | they are literally one process; the ZMQ bus is hardcoded loopback |
| Never expose 5555 as a container port or Service | leaks the raw tick feed; ZMQ stays loopback |
| `strategy: Recreate` | a rolling update of a 1-replica app runs two pods concurrently |
| One uvicorn worker | the WS proxy binds :8765 inside the app process; extra workers die on the bind |
| Unique `APP_SECRET_KEY` / `ENCRYPTION_PEPPER` per instance | shared values destroy session and encryption isolation |
| `COOKIE_SECURE=true` behind HTTPS | otherwise the JWT cookie is rejected/leaks |
| No pod restarts around ~03:00 IST | session-expiry / token-refresh window |
| Postgres reachable before first boot | startup runs migrations synchronously; the migrate init container waits (with backoff) until it is |

## Prerequisites

- **kustomize >= 5.3** (the `labels:` field the base uses needs it). Check
  what your kubectl bundles: `kubectl version --client` (1.27–1.29 ship
  older kustomize and reject the base). If older, install standalone
  kustomize and apply with
  `kustomize build deploy/k8s/overlays/<name> | kubectl apply -f -`.
- **The OpenBull image.** No registry image is published — build it
  yourself from the repo root and push it to your own registry:

  ```bash
  docker build -t your-registry.example.com/openbull:v0.1.0 .
  docker push your-registry.example.com/openbull:v0.1.0
  # then pin it in the overlay kustomization:
  #   images:
  #   - name: openbull
  #     newName: your-registry.example.com/openbull
  #     newTag: v0.1.0
  ```

  The base references the unqualified `openbull:latest` (locally built /
  `kind load` / `k3s ctr images import`) with
  `imagePullPolicy: IfNotPresent`.
- **PostgreSQL 15+ and Redis 7+**, externally managed (see below). The dev
  overlay ships throwaway in-cluster addons for local clusters.
- **An ingress controller** for the default edge variant
  (`ingressClassName: traefik` — patch for ingress-nginx etc.), or a
  **Gateway API controller** for the opt-in variant.
- Optional: cert-manager for TLS.

## Quickstart (production-shaped)

```bash
# 1. Namespace (the manifests set namespace: openbull but never create it)
kubectl create namespace openbull

# 2. Image
docker build -t your-registry.example.com/openbull:v0.1.0 .
docker push your-registry.example.com/openbull:v0.1.0

# 3. Instance directory (copy the template to a NEW directory)
cp -r deploy/k8s/overlays/_template deploy/k8s/overlays/prod

# 4. Secrets
cd deploy/k8s/overlays/prod
cp ../_template/secret.env.example secret.env
python3 -c "import secrets; print(secrets.token_hex(32))"   # APP_SECRET_KEY
python3 -c "import secrets; print(secrets.token_hex(32))"   # ENCRYPTION_PEPPER
# fill in DATABASE_URL / REDIS_URL pointing at your managed instances

# 5. Point patches at your domain (patches/config.yaml,
#    patches/edge-host-ingress.yaml) and pin the image in kustomization.yaml

# 6. Apply and watch
kubectl apply -k deploy/k8s/overlays/prod
kubectl -n openbull get pods,svc,ingress -w
```

DNS: point the instance hostname at your cluster edge (node IP /
LoadBalancer). TLS: uncomment the cert-manager annotation in the edge and
create an issuer, or supply the `openbull-tls` Secret yourself.

## Configuration

Non-sensitive values live in the `openbull-config` ConfigMap (patched per
overlay); credentials live in the `openbull-env` Secret generated from
`secret.env`. The Deployment pins infrastructure invariants
(`WEBSOCKET_HOST=0.0.0.0`, `ZMQ_HOST=127.0.0.1`, `TZ=Asia/Kolkata`, one
worker) as explicit env vars that win over envFrom — do not override them.

| Key | Where | Notes |
| --- | --- | --- |
| `APP_SECRET_KEY` | secret.env | JWT signing. Rotating logs everyone out. |
| `ENCRYPTION_PEPPER` | secret.env | Pepper + Fernet key for stored broker credentials and API keys. **Rotating makes them undecryptable** — generate once, back it up. |
| `DATABASE_URL` | secret.env | `postgresql+asyncpg://user:pass@host:5432/openbull`. Migrations convert to psycopg automatically. |
| `REDIS_URL` | secret.env | `redis://host:6379/0` (cache/rate-limit/locks; auth via `redis://:pass@...`). |
| `FRONTEND_URL` | ConfigMap | Public origin; OAuth redirect base. |
| `CORS_ORIGINS` | ConfigMap | Same origin in production. |
| `WEBSOCKET_URL` | ConfigMap | Public market-data WS, `wss://host/ws`. The SPA fetches it from `/api/websocket/config` and connects verbatim — wrong value = broken streaming. |
| `VALID_BROKERS` | ConfigMap | `upstox,zerodha,fyers,dhan,angel` subset for this instance. |
| `COOKIE_SECURE` | ConfigMap | `true` in the base (HTTPS); the dev overlay sets `false`. |
| `LOG_TO_FILE` | ConfigMap | `false` in the base — stdout/stderr only, collected by kubelet. `true` writes rotating files to the `logs` emptyDir (dies with the pod). |

Broker credentials are **not** env vars: OpenBull stores them in PostgreSQL
(encrypted with the pepper), configured through the web UI / OAuth flow
after first login (`/setup`).

## Database migrations

`migrate_all.py` runs as an init container **before** the app starts:
`create_all` → in-place idempotent ALTERs → `alembic upgrade head`. It is
idempotent, safe on every deploy, and needs only the `openbull-env` Secret
(it reads `DATABASE_URL` from the environment). Consequences:

- **Upgrade order is automatic**: new image → pod recreated → migrations
  run → app starts. `strategy: Recreate` guarantees no app instance is
  serving while migrations run.
- The app *also* self-migrates (`create_all` + ALTERs in its lifespan), so
  an emergency `kubectl rollout undo` never leaves the app unable to boot
  against a newer schema; alembic downgrades are still manual
  (`alembic downgrade` with the old image).
- If Postgres is briefly unavailable at deploy time, the init container
  fails and retries on the standard backoff — that is the intended
  wait-for-database behavior; the app itself has no retry loop.

## PostgreSQL and Redis

Production instances use **externally managed** services: RDS/Cloud SQL/
self-managed VMs for PostgreSQL, ElastiVal/managed or self-managed Redis.
The k8s package deliberately deploys no stateful infrastructure — point
`DATABASE_URL`/`REDIS_URL` at your instances and ensure the cluster can
reach them (security groups, VPC peering, TLS as required).

Redis is a soft dependency (cache, rate-limit storage, strategy locks):
if it is unreachable the app still boots and serves, degraded. PostgreSQL
is hard-required at boot.

`overlays/dev` exists for local clusters (k3s/kind): it pulls in
`addons/postgres` + `addons/redis` (single replica, fixed trivial
credentials, no backups — development only) and skips the edge:

```bash
kubectl create namespace openbull
cd deploy/k8s/overlays/dev && cp secret.env.example secret.env  # generate the two secrets
kubectl apply -k deploy/k8s/overlays/dev
kubectl -n openbull port-forward svc/openbull-http 8080:80
# open http://localhost:8080
```

## Secrets

### Plain Secret (default)

The overlay's `secretGenerator` builds the Secret from `secret.env` and
appends a content hash to its name. Consequences:

- `secret.env` is excluded from both git (`deploy/k8s/.gitignore`) and
  Docker image builds (recursive `**/secret.env` in the root
  `.dockerignore`, pinned by `deploy/k8s/tests/test_dockerignore.py`) —
  building the application image from a checkout with live secrets does
  not leak them into the image.
- Any change to `secret.env` creates a new Secret and **rolls the pod on
  the next apply** — apply secret changes off-hours, never inside the
  ~03:00 IST window.
- The old hashed Secret is **not pruned** by `kubectl apply -k`; rotated
  Secrets accumulate in the namespace. After the pod is up on the new
  Secret, delete the previous one:
  `kubectl -n openbull get secrets --sort-by=.metadata.creationTimestamp`,
  then `kubectl -n openbull delete secret <second-newest-openbull-env-hash>`.
- `kubectl kustomize` output contains the base64 secret values. Never paste
  build output into issues or PRs.

### SealedSecret (git-friendly, optional)

Install [Bitnami Sealed Secrets](https://github.com/bitnami-labs/sealed-secrets)
in the cluster, then seal per instance:

```bash
kubeseal --scope namespace-wide \
  -n openbull \
  < <(kubectl create secret generic openbull-env \
      --from-env-file=secret.env --dry-run=client -o json) \
  > sealed-secret.yaml
```

Commit `sealed-secret.yaml`, delete the overlay's `secretGenerator` block,
and add `sealed-secret.yaml` to the overlay resources instead. Two hard
rules:

- **Seal with `--scope namespace-wide` (or `cluster-wide`).** The default
  strict scope embeds the Secret name — and a `namePrefix` renames the
  resource, so a strict-scope blob would fail to decrypt.
- SealedSecrets carry no content hash: secret changes need a manual
  `kubectl -n openbull rollout restart deploy/openbull`.

ExternalSecrets support is deliberately deferred to a follow-up.

## Ingress (default edge)

`edge/ingress` ships a per-instance Ingress with a single `/` rule to
`openbull-http:80` — the in-pod nginx does all path routing, so no
controller-specific path config is needed. WebSocket-specific notes:

- **Traefik (k3s default):** nothing to configure; long-lived WS streams
  work out of the box.
- **ingress-nginx:** add per-route idle timeouts so market-data streams
  are not cut at the controller default (60–75s), e.g.
  `nginx.ingress.kubernetes.io/proxy-read-timeout: "86400"` and
  `proxy-send-timeout`. (The in-pod nginx already allows 24h.)
- TLS via cert-manager: uncomment the `cert-manager.io/cluster-issuer`
  annotation through an overlay patch.

## Gateway API (opt-in edge)

Swap the edge in the overlay kustomization:

```yaml
resources:
- ../../base
- ../../edge/gateway            # instead of ../../edge/ingress

patches:
- path: patches/edge-host-gateway.yaml   # instead of patches/edge-host-ingress.yaml
```

- Standard channel only (`Gateway` + `HTTPRoute`); WebSocket upgrades are
  Standard since Gateway API v1.2 — both `/ws` and `/ws/strategy/*` work
  over the same rule.
- **No GatewayClass is shipped** (cluster-scoped, controller-owned): a
  `namePrefix` would mint one per instance. `kubectl get gatewayclass`
  lists what your cluster provides; patch `gatewayClassName` if yours is
  not `traefik`.
- `edge/gateway/kustomizeconfig.yaml` carries the `nameReference` rules
  that make a `namePrefix` rewrite `HTTPRoute.backendRefs`/`parentRefs` —
  kustomize's built-in table covers neither. Verify a prefixed build:
  `kubectl kustomize deploy/k8s/overlays/<name> | grep -A2 'backendRefs:'`
  (names must carry the prefix).
- The Gateway's TLS `certificateRefs` name is **never** rewritten (its
  Secret is operator-provided, not a build resource) — the overlay's
  `edge-host-gateway.yaml` patch sets it explicitly.
- k3s note: the bundled Traefik ships the Gateway provider disabled —
  enable it with a `HelmChartConfig` patch
  (`providers.kubernetesGateway.enabled: true`, production-ready since
  Traefik v3.1) plus the Standard-channel CRDs, or run a standalone
  controller (Envoy Gateway / NGINX Gateway Fabric / Cilium).
- One overlay = one Gateway = one LoadBalancer. To share a cluster-level
  Gateway across instances, point the HTTPRoute `parentRef` at it (with
  its namespace) and drop the per-overlay Gateway with a
  `$patch: delete` patch.

## Upgrades / maintenance

1. Build and push the new image tag.
2. Update the overlay's `images:` transformer (it rewrites the app
   container **and** the migrate/seed init containers together).
3. `kubectl apply -k deploy/k8s/overlays/<name>` — Recreate terminates
   the old pod, the migrate init container runs, the new pod starts.
4. Verify: `kubectl -n openbull rollout status deploy/openbull`, then
   `kubectl -n openbull logs deploy/openbull -c openbull | grep -i migrate`.

Rollback: `kubectl -n openbull rollout undo deploy/openbull` (or re-pin the
old tag and re-apply). Alembic downgrades are manual; the app's startup
self-migration tolerates the newer schema well enough to boot, but run
upgrades outside market hours anyway — every deploy is a short outage and
drops in-flight WS subscriptions. Never restart inside the ~03:00 IST
token-refresh window. ConfigMap changes do not auto-roll the pod:
`kubectl -n openbull rollout restart deploy/openbull`.

## Egress / SEBI static-IP

Broker API egress (and the SEBI static-IP mandate) constrains **egress**,
which no Ingress/Gateway controls. For bare-metal clusters, pin egress to
a whitelisted node:

```bash
kubectl label node <node> openbull/egress=whitelisted
# optionally: kubectl taint node <node> openbull/egress=whitelisted:NoSchedule
```

then enable `patches/node-egress.yaml` (copy it from
`../_template/patches/` first) and whitelist that node's IP at every
broker this instance trades with. OpenBull keeps no PVCs, so the pod
follows the label freely. Managed-cluster alternatives (NAT gateway with
a reserved egress IP, Cilium Egress Gateway / Istio / Calico) are out of
scope here — the manifests hardcode no topology.

## Multiple instances

OpenBull is single-user per instance: run N instances as N overlays, one
namespace each (each with its own Postgres database, secrets and hostname).
If you co-locate instances in one namespace, give each overlay a unique
`namePrefix:` (references are rewritten automatically, including the
Gateway API ones via `edge/gateway/kustomizeconfig.yaml`) and a unique
`app.kubernetes.io/instance` label (the base sets
`includeSelectors: true`, so each Service selects only its own pod).

## Verification (no cluster required)

```bash
bash deploy/k8s/tests/validate.sh           # renders + asserts every overlay
python3 deploy/k8s/tests/test_dockerignore.py   # secret-leak guard (real
                                                 # Docker build when a daemon
                                                 # is reachable, else skips)
```

`validate.sh` asserts per overlay: renders cleanly (kubeconform
schema-check when installed), exactly one `openbull` Deployment with
`replicas: 1` and `strategy: Recreate`, no StatefulSet, no 5555 exposed as
a port, `ZMQ_HOST=127.0.0.1`, app + init containers share one image string
(the `images:` transformer pinning contract), the generated Secret is
present and referenced, Service selector/labels line up, edge backends
point at `openbull-http`, and no `CHANGE_ME` placeholder survives in a
concrete overlay.

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| Pod stuck `Init:CrashLoopBackOff` | migrate init container cannot reach PostgreSQL — check `DATABASE_URL` in secret.env and cluster egress to the DB; `kubectl -n openbull logs <pod> -c migrate` |
| Pod `Pending` | nodeSelector from `node-egress.yaml` on an unlabeled node |
| `CrashLoopBackOff` right after init | missing `APP_SECRET_KEY`/`ENCRYPTION_PEPPER` (the app refuses to boot) — both must be in secret.env |
| UI loads, no streaming | `WEBSOCKET_URL` wrong in the ConfigMap (must be the public `wss://host/ws`) or edge idle timeout cutting `/ws` — see the Ingress notes |
| Strategy runs page never updates | `/ws/strategy/*` must reach the API port; it is routed by the in-pod nginx — do not add your own `/ws` rewrite at the edge |
| OAuth callback 404s to the SPA | the broker prefix is missing from the nginx API regex in `base/nginx-conf.yaml` (all five ship covered — append new routers there) |
| 503 from the edge | readiness failing — check both containers: `kubectl -n openbull get pod -o jsonpath='{.status.containerStatuses[*].ready}'`, `kubectl -n openbull describe pod <pod>` |
| Login loops after enabling HTTPS | `COOKIE_SECURE` must be `true` when serving `https://` |
| Secrets piling up in the namespace | expected: rotated hashed Secrets are not pruned — see "Secrets" |
| Check the running config | `kubectl -n openbull exec deploy/openbull -c openbull -- env | grep -E 'DATABASE_URL|WEBSOCKET|ZMQ'` |

Useful commands:

```bash
kubectl -n openbull get pods,svc,ingress,gateway,httproute
kubectl -n openbull describe pod -l app.kubernetes.io/name=openbull
kubectl -n openbull logs deploy/openbull -c openbull      # app
kubectl -n openbull logs deploy/openbull -c nginx         # edge router
kubectl -n openbull logs <pod> -c migrate                 # migrations
kubectl -n openbull get events --sort-by=.lastTimestamp
```

## Known divergences from install/install.sh

The in-pod nginx routing table fixes three latent gaps in the VM
installer's site config (documented here, intentionally not patched in
`install/`):

1. The VM regex misses the `fyers`, `dhan` and `angel` OAuth callbacks —
   they fall through to the SPA and 404. The k8s config routes all five.
2. The VM `location /ws/` blanket rule sends `/ws/strategy/{id}` (a
   FastAPI WebSocket) to the path-agnostic market-data proxy on 8765.
   The k8s config routes `/ws/strategy/` to the API port.
3. `/health` and `/webhook/strategy/*` (external strategy webhooks) are
   routed to the app instead of the SPA fallback.
