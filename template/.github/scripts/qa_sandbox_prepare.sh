#!/usr/bin/env bash
#
# Prepare a GitHub-hosted Ubuntu runner for Claude Code's Bash sandbox.
# Used by morning-qa.yml before the agent step (and by the repo's sandbox
# canary workflow, so the canary tests exactly what production runs).
#
#   usage: qa_sandbox_prepare.sh <workspace-dir>
#
# * bubblewrap + socat: the Linux sandbox's isolation and network proxy.
# * Ubuntu 24.04 restricts unprivileged user namespaces through AppArmor
#   (kernel.apparmor_restrict_unprivileged_userns=1), which stops bubblewrap
#   from creating its namespaces.  Lifting it is acceptable on an ephemeral
#   hosted runner; on a long-lived self-hosted runner, prefer an AppArmor
#   profile for /usr/bin/bwrap instead.
# * The settings' denyWrite paths must EXIST to be protected (a missing path
#   cannot be bind-mounted read-only), so the protected directories are
#   created up front.  /tmp/claude and ~/.npm/_logs are ones the CLI's sandbox
#   runtime adds to the writable set by itself; /tmp/claude is re-created
#   empty so nothing a previous run left there survives.  On a shared
#   self-hosted box where another user runs Claude Code, that user owns
#   /tmp/claude and this fails hard — run the QA job on a box of its own.
# * /tmp/qa-agent is the ONLY directory the agent's commands may write (see
#   qa_sandbox_settings.py's AGENT_DIR).  It is removed and re-created empty,
#   0700, every run: on a self-hosted runner /tmp persists, and yesterday's
#   report or a planted symlink must not be there today.  Its tmp/ is the
#   CLI's temp dir (the workflow sets CLAUDE_CODE_TMPDIR to it).  `mkdir`
#   without -p fails if something re-appears between the two commands.
#
# Fails hard: a sandbox that cannot start must fail the run, never degrade
# (the CLI's failIfUnavailable is the second line of that defense).

set -euo pipefail

WORKSPACE="${1:?usage: qa_sandbox_prepare.sh <workspace-dir>}"

sudo apt-get update -qq
sudo apt-get install -y -qq bubblewrap socat

if [ -e /proc/sys/kernel/apparmor_restrict_unprivileged_userns ]; then
  sudo sysctl -q -w kernel.apparmor_restrict_unprivileged_userns=0
fi

mkdir -p "$WORKSPACE/.claude" "$HOME/.claude" "$HOME/.npm/_logs"
rm -rf /tmp/claude
mkdir -m 700 /tmp/claude

AGENT_DIR=/tmp/qa-agent
rm -rf "$AGENT_DIR"
mkdir -m 700 "$AGENT_DIR" "$AGENT_DIR/tmp"

# Prove the sandbox primitive works here, before any model runs.
bwrap --ro-bind / / --dev /dev --unshare-pid --proc /proc true
echo "sandbox prerequisites ready (bwrap: $(bwrap --version), socat: $(socat -V | sed -n 2p))"
