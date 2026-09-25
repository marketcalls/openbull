#!/usr/bin/env python3
"""Guard: Kubernetes deployment secret inputs must never enter the Docker build context.

The production Dockerfile stage does ``COPY . .``, so the root ``.dockerignore``
is the only filter between a repo checkout and a locally built image. Git-ignoring
``deploy/k8s/overlays/<name>/secret.env`` keeps credentials out of git but NOT
out of the image: dockerignore patterns match slash-separated relative paths where
``*`` does not cross ``/``, so a plain root-level ``*.env`` never matches nested
overlay files. These checks pin the recursive exclusions that fix that.

Runs with stdlib only (no pytest — this repo has no test framework dependency):

    python3 deploy/k8s/tests/test_dockerignore.py

The pattern-semantics checks run everywhere; the build-context check is a
synthetic-marker build against a real Docker daemon and is skipped when no
daemon answers.
"""

from __future__ import annotations

import fnmatch
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCKERIGNORE = REPO_ROOT / ".dockerignore"

# Files that must NEVER ship in the image (nested under any overlay).
SECRET_INPUTS = [
    "deploy/k8s/overlays/example/secret.env",
    "deploy/k8s/overlays/dev/secret.env",
    "deploy/k8s/overlays/_template/secret.env",
    "deploy/k8s/overlays/example/secret.yaml",
    "deploy/k8s/overlays/example/kubeseal-input.secret.env",
    "secret.env",
    "deploy/secret.yaml",
]

# Templates and samples that MUST stay in the context ("keep usable templates").
MUST_KEEP = [
    ".env.example",
    "deploy/k8s/overlays/_template/secret.env.example",
    "deploy/k8s/overlays/dev/secret.env.example",
]

RESULTS: list[tuple[str, str]] = []  # (status, description)


def report(ok: bool, skip: bool, desc: str, detail: str = "") -> None:
    status = "SKIP" if (skip and not ok) else ("ok" if ok else "FAIL")
    RESULTS.append((status, desc))
    print(f"  {status:4} {desc}" + (f"\n       {detail}" if detail and status == "FAIL" else ""))


def _load_patterns() -> list[str]:
    patterns = []
    for line in DOCKERIGNORE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            patterns.append(line)
    return patterns


def _segment_match(pattern: str, path: str) -> bool:
    """Dockerignore segment matching: '**' crosses separators, '*' does not."""
    pseg = pattern.strip("/").split("/")
    fseg = path.split("/")

    def match(pi: int, fi: int) -> bool:
        if pi == len(pseg):
            return fi == len(fseg)
        if pseg[pi] == "**":
            return any(match(pi + 1, k) for k in range(fi, len(fseg) + 1))
        if fi == len(fseg) or not fnmatch.fnmatchcase(fseg[fi], pseg[pi]):
            return False
        return match(pi + 1, fi + 1)

    return match(0, 0)


def _is_excluded(patterns: list[str], path: str) -> bool:
    """Mirror moby patternmatcher basics: last matching pattern wins."""
    excluded = False
    for pat in patterns:
        negate = pat.startswith("!")
        if _segment_match(pat[1:] if negate else pat, path):
            excluded = not negate
    return excluded


def check_dockerignore_patterns_exclude_nested_secret_inputs() -> None:
    """The recursive exclusions must cover every nested secret input pattern."""
    patterns = _load_patterns()
    leaked = [p for p in SECRET_INPUTS if not _is_excluded(patterns, p)]
    report(
        not leaked,
        False,
        "nested secret inputs excluded from the Docker build context",
        "would enter the context: " + ", ".join(leaked),
    )


def check_dockerignore_patterns_keep_usable_templates() -> None:
    """Exclusions must not swallow templates and sample files."""
    patterns = _load_patterns()
    swallowed = [p for p in MUST_KEEP if _is_excluded(patterns, p)]
    report(
        not swallowed,
        False,
        "templates stay usable in the Docker build context",
        "wrongly excluded: " + ", ".join(swallowed),
    )


def _docker_daemon_available() -> bool:
    """True only when the CLI exists AND a daemon answers (`docker info`).

    Binary presence alone is not enough: environments that ship the docker
    CLI without a daemon would otherwise false-fail with "Cannot connect to
    the Docker daemon".
    """
    if shutil.which("docker") is None:
        return False
    try:
        return (
            subprocess.run(
                ["docker", "info"],
                capture_output=True,
                timeout=30,
            ).returncode
            == 0
        )
    except (OSError, subprocess.TimeoutExpired):
        return False


def check_docker_build_context_excludes_secret_inputs(daemon: bool) -> None:
    """Synthetic-marker check against the real Docker context.

    Plants marker files matching every secret input pattern inside a fake
    overlay, builds a one-step image that copies the context, and fails the
    build if any marker survived .dockerignore (or if the template vanished).
    """
    if not daemon:
        report(False, True, "real-build marker check (docker daemon not reachable)")
        return

    marker_dir = REPO_ROOT / "deploy" / "k8s" / "overlays" / "_dockerignore_marker"
    markers = ["secret.env", "secret.yaml", "marker.secret.env"]
    dockerfile = (
        "FROM busybox:latest\n"
        "COPY . /ctx\n"
        "RUN for f in "
        + " ".join(f"ctx/deploy/k8s/overlays/_dockerignore_marker/{m}" for m in markers)
        + '; do [ ! -e "$f" ] || { echo "LEAKED: $f"; exit 1; }; done '
        "&& [ -e ctx/deploy/k8s/overlays/_template/secret.env.example ] "
        "&& [ -e ctx/deploy/k8s/overlays/dev/secret.env.example ] "
        "|| { echo 'MISSING: secret template'; exit 1; }\n"
    )
    tag = "openbull-dockerignore-check:ci"
    ok = False
    detail = ""
    try:
        marker_dir.mkdir(parents=True, exist_ok=True)
        for m in markers:
            (marker_dir / m).write_text("MARKER-NOT-A-REAL-SECRET\n")
        result = subprocess.run(
            ["docker", "build", "-t", tag, "-f", "-", "."],
            input=dockerfile,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=900,
        )
        leaked = [
            line for line in result.stdout.splitlines() if "LEAKED" in line or "MISSING" in line
        ]
        ok = result.returncode == 0
        if not ok:
            detail = "\n".join(leaked or (result.stdout[-2000:] + result.stderr[-2000:]))
    finally:
        shutil.rmtree(marker_dir, ignore_errors=True)
        subprocess.run(
            ["docker", "rmi", "-f", tag],
            capture_output=True,
            timeout=120,
        )
    report(ok, False, "real-build marker check (secret inputs stay out of the image)", detail)


def main() -> int:
    print("Docker build-context secret guard")
    check_dockerignore_patterns_exclude_nested_secret_inputs()
    check_dockerignore_patterns_keep_usable_templates()
    check_docker_build_context_excludes_secret_inputs(_docker_daemon_available())

    failures = sum(1 for status, _ in RESULTS if status == "FAIL")
    skips = sum(1 for status, _ in RESULTS if status == "SKIP")
    print("===========================================")
    print(f"{len(RESULTS)} checks, {failures} failures, {skips} skipped")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
