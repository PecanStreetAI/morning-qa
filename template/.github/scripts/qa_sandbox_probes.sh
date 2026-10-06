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
# No `set -e`: every probe must report, even after an earlier one fails.

probe() { printf 'PROBE %s=%s\n' "$1" "$2"; }

# 1. A host NOT on the allowlist must be refused.
if curl -sS -m 15 -o /dev/null https://example.org/ 2>/dev/null; then
  probe egress_unlisted open
else
  probe egress_unlisted blocked
fi

# 2. A host ON the allowlist must still work (the checks need it).
code=$(curl -sS -m 15 -o /dev/null -w '%{http_code}' https://registry.npmjs.org/ 2>/dev/null) || true
probe egress_allowed "${code:-none}"

# 3. Secrets the CLI and MCP server hold must be unset in here.
for v in ANTHROPIC_API_KEY MDB_MCP_CONNECTION_STRING GH_TOKEN; do
  if [ -n "${!v+x}" ]; then probe "env_$v" present; else probe "env_$v" absent; fi
done

# 4. ...and must not be readable from any other process's environment.
leaks=$(grep -lsaE '(ANTHROPIC_API_KEY|MDB_MCP_CONNECTION_STRING|GH_TOKEN)=' /proc/[0-9]*/environ 2>/dev/null | wc -l)
probe proc_leaks "$leaks"

# 5. /tmp must stay writable: the report is built there.
if printf 'probe\n' > /tmp/qa-sandbox-probe 2>/dev/null; then
  probe tmp_write ok
else
  probe tmp_write denied
fi

# 6. Settings paths must not be: a command that could write them could
#    widen its own sandbox on the next settings reload.
ws="${GITHUB_WORKSPACE:-$PWD}"
if touch "$ws/.claude/qa-sandbox-probe" 2>/dev/null; then
  probe settings_write allowed
else
  probe settings_write denied
fi
if touch "$HOME/.claude/qa-sandbox-probe" 2>/dev/null; then
  probe user_settings_write allowed
else
  probe user_settings_write denied
fi
