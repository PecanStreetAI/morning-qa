#!/usr/bin/env bash
# denylist_scan.sh — CI gate: private-origin references must not (re)enter
# this tree.
#
# This repo is a clean-room extraction from a private production system
# (see PORTING.md). Ports are sanitized on the PRIVATE side before they
# arrive here; this scan is the public-side backstop that keeps a raw
# paste from slipping in via a later commit or PR.
#
# Design note — nothing here may disclose what it hunts: a public denylist
# that enumerated the private system's actual names, IPs, or schema terms
# would itself leak them. So this script checks structural patterns
# (IP literals, emails, credentialed URIs, cloud-key shapes) plus a short
# list of private terms stored only as length+SHA-256 pairs. The fully
# named term list lives with the private repo's extraction checklist and
# is applied there, before anything is ported.
#
# Implementation notes (learned the hard way, 2026-08-27):
#   * No `set -e`/`pipefail` here — a filter stage like `grep -v` exits 1
#     on empty input, which under -e/pipefail kills the script silently
#     BEFORE it can report "clean". Every stage is || true'd instead and
#     the script manages its own exit status.
#   * Findings are captured into variables, not piped into a reporter
#     function — a pipeline stage runs in a subshell, where FAIL=1 can
#     never reach the parent.
#   * grep's --exclude options come BEFORE the path operand: BSD grep
#     stops option parsing at the first operand, so trailing options
#     would be read as filenames on macOS.
#
# Usage: scripts/denylist_scan.sh [tree-root]   (default: repo root)
# Exit 0 = clean; exit 1 = findings printed as file:line.

set -u

ROOT="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$ROOT" || exit 1

FAIL=0

report() { # $1 = rule name, $2 = hits (possibly empty)
  if [ -n "$2" ]; then
    FAIL=1
    printf '✗ %s\n%s\n\n' "$1" "$2"
  fi
}

# Everything except .git and this script itself.
G() { grep -RInE \
        --exclude-dir=.git \
        --exclude-dir=node_modules \
        --exclude-dir=__pycache__ \
        --exclude='denylist_scan.sh' \
        "$@" . 2>/dev/null || true; }

# 1. Hashed private terms — the origin project's name, its runner/instance
#    vocabulary, and one infrastructure address. Stored as (length, SHA-256)
#    pairs so this public script cannot itself disclose what it hunts (an
#    early draft named the terms in cleartext — in the one file whose job
#    was to prevent exactly that). Matching slides a window of each term's
#    length over every lowercased line and compares digests. Honest limit:
#    a short dictionary word's hash is brute-forceable — this protects
#    against reading, not against a determined investigator (who has
#    easier avenues anyway).
hits=$(python3 - <<'PY'
import hashlib, os, sys

TERMS = [  # (length, sha256 of the lowercased term, generic label)
    (5,  "a1e634d900bf403fe72ab7bd3a20ca6f5130e76038585a94b21194960484e924", "origin project name"),  # pragma: allowlist secret
    (9,  "a92442cce5dde9754d417877025fa68ace676b06ae85d1e86e2d665c6afd1bf0", "origin runner vocabulary"),  # pragma: allowlist secret
    (4,  "6a817d3951ee779003131d4d3420e8468f0121f8bfdec430b53ad5f1af7d518b", "origin instance vocabulary"),  # pragma: allowlist secret
    (13, "563bbe176c89e045b33b689b0ac2377fd71f3709ab310b361edd62328726ce77", "origin infra address"),  # pragma: allowlist secret
]
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".pytest_cache"}
SELF = "denylist_scan.sh"

for root, dirs, files in os.walk("."):
    dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
    for name in files:
        if name == SELF:
            continue
        path = os.path.join(root, name)
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                for lineno, line in enumerate(fh, 1):
                    low = line.lower()
                    for tlen, digest, label in TERMS:
                        for i in range(len(low) - tlen + 1):
                            if hashlib.sha256(
                                low[i : i + tlen].encode()
                            ).hexdigest() == digest:
                                print(f"{path}:{lineno}: {label}")
                                break
        except OSError:
            continue
PY
)
report "hashed private terms" "$hits"

# 2. IPv4 literals. Loopback, unspecified, broadcast, and the RFC 5737
#    documentation ranges are allowed; anything else is suspect (version
#    strings rarely have four dot-separated parts — annotate a legitimate
#    match with `# denylist-ok: <reason>` on the same line).
hits=$(G '\b([0-9]{1,3}\.){3}[0-9]{1,3}\b' \
  | grep -vE '127\.0\.0\.1|0\.0\.0\.0|255\.255\.255\.255|192\.0\.2\.|198\.51\.100\.|203\.0\.113\.' \
  | grep -v 'denylist-ok' || true)
report "IPv4 literal outside allowed ranges" "$hits"

# 3. Email addresses (example.com and the GitHub/Anthropic bot addresses
#    are allowed).
hits=$(G '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' \
  | grep -vE '@example\.com|noreply@anthropic\.com|users\.noreply\.github\.com|@users\.noreply' \
  | grep -v 'denylist-ok' || true)
report "email address" "$hits"

# 4. Cloud credential shapes (never valid in any file here, docs included).
hits=$(G 'AKIA[0-9A-Z]{16}')
report "AWS access key id shape" "$hits"
hits=$(G 'mongodb(\+srv)?://[^ "'"'"']*@')
report "credentialed MongoDB URI" "$hits"

# (Rule 5, private-infra vocabulary, folded into the hashed terms above.)

if [ "$FAIL" -ne 0 ]; then
  echo "denylist scan FAILED — remove or sanitize the findings above."
  echo "(A legitimate structural match can be annotated '# denylist-ok: <reason>' on the same line.)"
  exit 1
fi
echo "denylist scan clean."
