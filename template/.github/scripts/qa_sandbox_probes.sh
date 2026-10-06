#!/usr/bin/env bash
#
# Containment probes for the QA agent's Bash sandbox.  Run BY THE AGENT,
# inside the sandbox, by the sandbox canary workflow; judged afterwards by
# qa_sandbox_canary.py from the run's stream-json log.
#
# Each probe prints exactly one `PROBE <name>=<value>` line and never prints
# a secret's value — only whether a variable is set (the `${!v+x}` form
# expands to `x` or nothing, never to the value).
#
# A write probe that SUCCEEDS leaves its file behind; that only happens on a
# broken sandbox, on the canary's throwaway runner.
#
# No `set -e`: every probe must report, even after an earlier one fails.

probe() { printf 'PROBE %s=%s\n' "$1" "$2"; }

# can_write <probe-name> <command...>: "allowed" if the command succeeds.
can_write() {
  local name=$1
  shift
  if "$@" 2>/dev/null; then probe "$name" allowed; else probe "$name" denied; fi
}

open_for_append() { : >> "$1"; }

# can_reach <probe-name> <curl args...>: "open" if curl completes a request.
can_reach() {
  local name=$1
  shift
  if curl -sS -m 15 -o /dev/null "$@" 2>/dev/null; then probe "$name" open; else probe "$name" blocked; fi
}

# metadata_answer <curl args...>: true if the cloud metadata service itself
# answered.  curl can exit 0 on the proxy's own deny page, so only a real
# metadata answer counts: AWS IMDSv2's 401 (it wants a token) or a body
# listing `ami-id`, or Azure's instance document (`"compute"`).
IMDS=169.254.169.254  # denylist-ok: link-local cloud metadata address, probed to prove it is unreachable
metadata_answer() {
  local out code
  out=$(curl -sS -m 5 -w '\n%{http_code}' "$@" "http://$IMDS/latest/meta-data/" 2>/dev/null) || true
  code=${out##*$'\n'}
  [ "$code" = 401 ] && return 0
  printf '%s' "$out" | grep -q 'ami-id' && return 0
  out=$(curl -sS -m 5 -H 'Metadata: true' "$@" \
    "http://$IMDS/metadata/instance?api-version=2021-02-01" 2>/dev/null) || true
  printf '%s' "$out" | grep -q '"compute"'
}

# ── Network ──────────────────────────────────────────────────────────────────
# 1. A host NOT on the allowlist must be refused...
can_reach egress_unlisted https://example.org/

# 2. ...also when the command skips the sandbox's proxy and dials directly.
can_reach egress_noproxy --noproxy '*' https://example.org/

# 3. The package registries accept uploads (`npm publish` is a PUT), so they
#    are off the allowlist too.
can_reach egress_registry https://registry.npmjs.org/

# 4. The app host IS on the allowlist and must still work.
code=none
if [ -n "${APP_BASE_URL:-}" ]; then
  code=$(curl -sS -m 15 -o /dev/null -w '%{http_code}' "$APP_BASE_URL" 2>/dev/null) || true
fi
probe egress_allowed "${code:-none}"

# 5. The cloud metadata service (instance credentials) must be unreachable,
#    through the proxy and directly.
if metadata_answer; then probe imds_proxy open; else probe imds_proxy blocked; fi
if metadata_answer --noproxy '*'; then probe imds_direct open; else probe imds_direct blocked; fi

# ── Secrets ──────────────────────────────────────────────────────────────────
# 6. Secrets the CLI and MCP server hold must be unset in here.
for v in ANTHROPIC_API_KEY MDB_MCP_CONNECTION_STRING GH_TOKEN; do
  if [ -n "${!v+x}" ]; then probe "env_$v" present; else probe "env_$v" absent; fi
done

# 7. ...and must not be readable from any other process's environment.
leaks=$(grep -lsaE '(ANTHROPIC_API_KEY|MDB_MCP_CONNECTION_STRING|GH_TOKEN)=' /proc/[0-9]*/environ 2>/dev/null | wc -l)
probe proc_leaks "$leaks"

# ── Filesystem ───────────────────────────────────────────────────────────────
ws="${GITHUB_WORKSPACE:-$PWD}"
here=$(cd "$(dirname "$0")" && pwd)

# 8. The agent's own directory is writable: the report is built there.
if printf 'probe\n' > /tmp/qa-agent/qa-sandbox-probe 2>/dev/null; then
  probe agent_dir_write ok
else
  probe agent_dir_write denied
fi

# 9. The rest of /tmp is not: later unsandboxed steps write fixed /tmp paths,
#    and a planted symlink would redirect those writes.
#    Fresh names ($$), so a file left by an earlier run cannot make a
#    failed-because-it-exists command read as "denied".
can_write tmp_write touch "/tmp/qa-sandbox-probe.$$"
can_write tmp_symlink ln -s "$HOME" "/tmp/qa-sandbox-symlink-probe.$$"

# 10. Nor is the checkout: not its root, not a script a later step runs
#     unsandboxed (`: >>` opens it for append and writes nothing), not
#     .git/hooks, not the settings directory.
can_write workspace_write touch "$ws/qa-sandbox-probe.$$"
if [ -f "$here/qa_run_telemetry.js" ]; then
  can_write workspace_script_write open_for_append "$here/qa_run_telemetry.js"
else
  probe workspace_script_write missing
fi
can_write git_hooks_write touch "$ws/.git/hooks/qa-sandbox-probe.$$"
can_write settings_write touch "$ws/.claude/qa-sandbox-probe"
can_write user_settings_write touch "$HOME/.claude/qa-sandbox-probe"

# 11. Nor the paths the sandbox runtime opens on its own.
can_write claude_tmp_write touch /tmp/claude/qa-sandbox-probe
can_write npm_logs_write touch "$HOME/.npm/_logs/qa-sandbox-probe"

# 12. Check 0 still works under the Read deny on /proc/*/environ.
if df -P -k / /tmp >/dev/null 2>&1; then probe df ok; else probe df failed; fi
