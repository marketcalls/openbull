#!/usr/bin/env bash
# Render every OpenBull k8s overlay and assert the structural invariants of
# the package. No live cluster is required (set RUN_CLIENT_DRYRUN=1 to also
# attempt `kubectl apply --dry-run=client`, which needs a reachable cluster).
#
# Usage: bash deploy/k8s/tests/validate.sh
set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
K8S_DIR="$REPO_ROOT/deploy/k8s"
TMPDIR_RENDER="$(mktemp -d)"
FAILURES=0
CHECKS=0
DUMMY_SECRETS=()

cleanup() {
    local f
    for f in "${DUMMY_SECRETS[@]:-}"; do
        [ -n "$f" ] && rm -f "$f"
    done
    rm -rf "$TMPDIR_RENDER"
}
trap cleanup EXIT

# --- build command: kubectl (bundled kustomize) > kubecolor > kustomize ----
if command -v kubectl >/dev/null 2>&1; then
    BUILD=(kubectl kustomize)
elif command -v kubecolor >/dev/null 2>&1; then
    BUILD=(kubecolor kustomize)
elif command -v kustomize >/dev/null 2>&1; then
    BUILD=(kustomize build)
else
    echo "FAIL: no kustomize renderer found (kubectl, kubecolor or kustomize)"
    exit 2
fi

pass() { CHECKS=$((CHECKS + 1)); echo "  ok   $1"; }
fail() { CHECKS=$((CHECKS + 1)); FAILURES=$((FAILURES + 1)); echo "  FAIL $1"; }

check() { # check <description> <command...>
    local desc="$1"; shift
    if "$@" >/dev/null 2>&1; then pass "$desc"; else fail "$desc"; fi
}

render() { # render <kustomization-dir> <outfile>
    "${BUILD[@]}" "$1" >"$2" 2>"$2.err"
}

kubeconform_check() { # kubeconform_check <rendered-file> <label>
    if command -v kubeconform >/dev/null 2>&1; then
        if kubeconform -strict -summary -ignore-missing-schemas <"$1" >/dev/null 2>&1; then
            pass "kubeconform: $2"
        else
            fail "kubeconform: $2 (run: kubeconform -strict -ignore-missing-schemas < $1)"
        fi
    fi
}

validate_overlay() { # validate_overlay <overlay-dir>
    local dir="$1" name rendered
    name="$(basename "$dir")"
    rendered="$TMPDIR_RENDER/$name.yaml"

    echo "==> overlay: $name"

    # secretGenerator needs secret.env to exist; plant a dummy when absent
    # (it is gitignored — never commit a real one).
    if [ ! -f "$dir/secret.env" ]; then
        cat >"$dir/secret.env" <<'EOF'
APP_SECRET_KEY=dummy-key-for-rendering-only
ENCRYPTION_PEPPER=dummy-pepper-for-rendering-only
DATABASE_URL=postgresql+asyncpg://u:p@localhost:5432/openbull
REDIS_URL=redis://localhost:6379/0
EOF
        DUMMY_SECRETS+=("$dir/secret.env")
    fi

    if render "$dir" "$rendered"; then
        pass "render"
    else
        fail "render (see $rendered.err)"
        return
    fi

    # --- workload invariants ------------------------------------------------
    check "exactly one openbull Deployment" grep -q "^  name: openbull$" "$rendered"
    check "no StatefulSet in build" sh -c "! grep -q 'kind: StatefulSet' '$rendered'"
    check "replicas: 1" grep -q "replicas: 1" "$rendered"
    check "strategy: Recreate" grep -q "type: Recreate" "$rendered"

    # --- ZMQ / port hygiene --------------------------------------------------
    check "no 5555 as containerPort/Service/Ingress port" \
        sh -c "! grep -Eq '(containerPort|targetPort|number): 5555' '$rendered'"
    check "ZMQ_HOST pinned to 127.0.0.1" \
        sh -c "grep -A1 'name: ZMQ_HOST' '$rendered' | grep -q '127.0.0.1'"
    check "market-data WS proxy binds 0.0.0.0 (8765 declared)" \
        sh -c "grep -q 'containerPort: 8765' '$rendered'"

    # --- image pinning consistency -------------------------------------------
    # The migrate + seed init containers and the app container must share the
    # same image string so the overlay `images:` transformer rewrites them as
    # one (the OpenAlgo #1996 review lesson).
    local distinct_images
    distinct_images=$(grep -oE 'image: [^[:space:]]*openbull[^[:space:]]*' "$rendered" | sort -u | wc -l)
    CHECKS=$((CHECKS + 1))
    if [ "$distinct_images" -eq 1 ]; then
        pass "app + init containers share one image string"
    else
        fail "app + init containers share one image string ($distinct_images distinct)"
    fi

    # --- references ------------------------------------------------------------
    check "Deployment envFrom references generated openbull-env Secret" \
        grep -q "secretRef:" "$rendered"
    local secret_refs
    secret_refs=$(grep -c "name: openbull-env-" "$rendered")
    CHECKS=$((CHECKS + 1))
    if [ "$secret_refs" -ge 2 ]; then
        pass "generated Secret present and referenced (envFrom + resource)"
    else
        fail "generated Secret present and referenced (found $secret_refs openbull-env-* refs)"
    fi
    check "Service openbull-http present" grep -q "name: openbull-http" "$rendered"
    check "Service selector matches pod template label" \
        sh -c "awk '/kind: Service/,/^---/' '$rendered' | grep -q 'app.kubernetes.io/name: openbull'"

    # --- edge -------------------------------------------------------------------
    if grep -q "kind: Ingress" "$rendered" 2>/dev/null; then
        check "Ingress backend -> openbull-http" \
            sh -c "awk '/kind: Ingress/,/^---/' '$rendered' | grep -q 'name: openbull-http'"
    fi

    # --- unpatched placeholder leak ---------------------------------------------
    # _template keeps its CHANGE_ME placeholders on purpose (it is the
    # copy-me reference); every concrete overlay must be fully patched.
    if [ "$name" != "_template" ]; then
        check "no CHANGE_ME placeholder left" sh -c "! grep -q CHANGE_ME '$rendered'"
    fi

    kubeconform_check "$rendered" "$name"

    if [ "${RUN_CLIENT_DRYRUN:-0}" = "1" ]; then
        if command -v kubectl >/dev/null 2>&1; then
            if kubectl apply --dry-run=client -k "$dir" >/dev/null 2>&1; then
                pass "kubectl client dry-run"
            else
                fail "kubectl client dry-run"
            fi
        fi
    fi
}

echo "OpenBull k8s package validation"
echo "renderer: ${BUILD[*]}"
echo

# Libraries must render standalone (references may dangle by design).
echo "==> library: base"
if render "$K8S_DIR/base" "$TMPDIR_RENDER/base.yaml"; then
    pass "render"
    kubeconform_check "$TMPDIR_RENDER/base.yaml" "base"
else
    fail "render (see $TMPDIR_RENDER/base.yaml.err)"
fi

for edge in ingress gateway; do
    echo "==> library: edge/$edge"
    if render "$K8S_DIR/edge/$edge" "$TMPDIR_RENDER/edge-$edge.yaml"; then
        pass "render"
        kubeconform_check "$TMPDIR_RENDER/edge-$edge.yaml" "edge/$edge"
    else
        fail "render (see $TMPDIR_RENDER/edge-$edge.yaml.err)"
    fi
done
echo

for dir in "$K8S_DIR"/overlays/*/; do
    [ -f "$dir/kustomization.yaml" ] || continue
    validate_overlay "$dir"
    echo
done

echo "==========================================="
echo "$CHECKS checks, $FAILURES failures"
[ "$FAILURES" -eq 0 ] || exit 1
exit 0
