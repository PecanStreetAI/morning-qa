#!/usr/bin/env python3
"""Emit the Claude Code `--settings` JSON that sandboxes the QA agent's Bash.

Usage (from the workflow, which passes the JSON INLINE, not as a file — a
settings file the agent's Bash could rewrite would be a way out):

    claude -p ... --settings "$(python3 .github/scripts/qa_sandbox_settings.py)"

Reads APP_BASE_URL, GITHUB_WORKSPACE and HOME from the environment.

Why a sandbox, and why this shape
─────────────────────────────────
The agent runs `Bash` over third-party text (advisories, HTTP bodies, DB
fields): a prompt-injection surface.  Removing secrets from its env (2026-10,
PR #1) left three it cannot work without, and any host on the internet to
send them to.  A job-wide domain allowlist cannot close that: the job must
reach api.anthropic.com, and anyone holding their own API key can stash data
there and read it back later.  So the boundary goes around the agent's
COMMANDS, not the job: Claude Code's built-in sandbox (bubblewrap + a socat
proxy on Linux) wraps every model-issued Bash call, while the CLI process and
the MCP server — not model-controlled — keep their own network access.

  * failIfUnavailable          — a sandbox that cannot start fails the run;
                                 it never silently degrades to unsandboxed.
  * allowUnsandboxedCommands   — false: the model cannot opt a command out.
  * network.strictAllowlist    — unlisted hosts are refused, not prompted.
  * network.allowedDomains     — the app origin only.  No package registry:
                                 npm and PyPI accept uploads (`npm publish`
                                 is a PUT to registry.npmjs.org), so either
                                 one would be a way to send off-box anything
                                 the agent can read.  Check 7's registry
                                 work runs in the pre-compute step instead.
  * credentials.envVars deny   — the secrets the CLI and MCP server need are
                                 unset inside every sandboxed command.
  * filesystem.allowWrite      — ONLY AGENT_DIR (/tmp/qa-agent), re-created
                                 empty and 0700 by qa_sandbox_prepare.sh.
                                 Not all of /tmp: later UNSANDBOXED steps
                                 write fixed /tmp paths (the report-missing
                                 fallback, the telemetry footer), so a
                                 symlink planted there would redirect those
                                 writes anywhere the runner user can write.
                                 The workflow copies the report out with
                                 qa_collect_report.py, which never follows
                                 a link.
  * filesystem.denyWrite       — the whole workspace and ~/.claude.  The
                                 sandbox otherwise lets a command write its
                                 launch directory, i.e. the checkout: the
                                 scripts later steps run unsandboxed
                                 (qa_run_telemetry.js), settings and MCP
                                 config, and .git/hooks (actions/checkout
                                 does not clean them; on a self-hosted
                                 runner they run in the next job).  Also
                                 /tmp/claude and ~/.npm/_logs, which the
                                 CLI's sandbox runtime adds to the writable
                                 set by itself (the workflow points the CLI
                                 at AGENT_DIR/tmp with CLAUDE_CODE_TMPDIR).
  * permissions.deny Read      — /proc/**/environ: the Read tool runs in the
                                 CLI process, outside the sandbox, and could
                                 otherwise read the CLI's own environment
                                 (its API key).  Read deny rules also join
                                 the sandbox's read-deny list.
  * env                        — tool caches redirected into AGENT_DIR, so
                                 credential-free commands that write a cache
                                 (npm, pip, pytest) work with $HOME and the
                                 workspace read-only.  Settings env reaches
                                 the CLI and the MCP server too, which run
                                 unsandboxed: see the residuals in the
                                 framework's docs/design.md.

`WebFetch` and `WebSearch` run inside the CLI process and are NOT covered by
the sandbox's network rules — the workflow disallows them outright.

strictAllowlist and credentials are only honored from user, managed or CLI
(`--settings`) settings; project settings files are ignored for them, which
is why this is passed on the command line.
"""

import json
import os
import sys
from urllib.parse import urlsplit

# Hosts the agent's Bash may reach beyond the app origin (APP_BASE_URL, added
# at runtime).  Empty on purpose: every host here is somewhere an injected
# command can send whatever the agent reads (Mongo rows via the MCP
# included), and a host that accepts uploads — a package registry, a paste
# site, a git host — is an exfiltration channel even with no credential.
# Prefer moving a probe into qa_precompute.py; if a check truly needs a host,
# add it and say in the commit why it cannot store data.
BASE_ALLOWED_DOMAINS = ()

# The ONLY directory the agent's commands may write.  qa_sandbox_prepare.sh
# re-creates it empty (0700) with a tmp/ subdirectory, the workflow runs the
# CLI with CLAUDE_CODE_TMPDIR=<AGENT_DIR>/tmp, the skill writes its report to
# <AGENT_DIR>/qa-report.md, and qa_collect_report.py copies that out.  Tests
# pin the literal in all of those places.
AGENT_DIR = "/tmp/qa-agent"

# Every secret in the agent step's env (tests pin this against the workflow).
# GH_TOKEN is not in that env at all; it is listed so that re-adding it to
# the step would still leave it invisible to the agent's commands.
DENIED_ENV_VARS = ("ANTHROPIC_API_KEY", "MDB_MCP_CONNECTION_STRING", "GH_TOKEN")


def app_host(base_url):
    """Hostname (with an explicit port, if any) of APP_BASE_URL, or None."""
    if not base_url or not base_url.strip():
        return None
    parts = urlsplit(base_url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"APP_BASE_URL is not an http(s) URL: {base_url!r}")
    return f"{parts.hostname}:{parts.port}" if parts.port else parts.hostname


def build_settings(app_base_url, workspace, home):
    domains = list(BASE_ALLOWED_DOMAINS)
    host = app_host(app_base_url)
    if host and host not in domains:
        domains.append(host)
    deny_write = [
        workspace,
        os.path.join(home, ".claude"),
        # The sandbox runtime makes these two writable on its own (its
        # default temp dir, npm's log dir).  qa_sandbox_prepare.sh creates
        # them so the deny can bind: a missing path cannot be made read-only.
        "/tmp/claude",
        os.path.join(home, ".npm", "_logs"),
    ]
    return {
        "env": {
            "npm_config_cache": f"{AGENT_DIR}/npm-cache",
            "XDG_CACHE_HOME": f"{AGENT_DIR}/cache",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTEST_ADDOPTS": "-p no:cacheprovider",
        },
        "permissions": {
            # `*` stops at a `/`: the second rule also covers each thread's
            # copy (/proc/<pid>/task/<tid>/environ) and /proc/self/root/...
            "deny": ["Read(//proc/*/environ)", "Read(//proc/**/environ)"],
        },
        "sandbox": {
            "enabled": True,
            "failIfUnavailable": True,
            "allowUnsandboxedCommands": False,
            "autoAllowBashIfSandboxed": True,
            "network": {
                "allowedDomains": domains,
                "strictAllowlist": True,
            },
            "filesystem": {
                "allowWrite": [AGENT_DIR],
                "denyWrite": deny_write,
            },
            "credentials": {
                "envVars": [{"name": n, "mode": "deny"} for n in DENIED_ENV_VARS],
            },
        }
    }


def main():
    workspace = os.environ.get("GITHUB_WORKSPACE") or os.getcwd()
    home = os.environ.get("HOME") or os.path.expanduser("~")
    try:
        settings = build_settings(os.environ.get("APP_BASE_URL"), workspace, home)
    except ValueError as e:
        print(f"qa_sandbox_settings: {e}", file=sys.stderr)
        return 1
    json.dump(settings, sys.stdout, separators=(",", ":"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
