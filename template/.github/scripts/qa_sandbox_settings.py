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
  * network.allowedDomains     — exactly what the checks' own probes need.
  * credentials.envVars deny   — the secrets the CLI and MCP server need are
                                 unset inside every sandboxed command.
  * filesystem.denyWrite       — settings and MCP config paths, so a command
                                 cannot widen its own sandbox or tool surface.
  * filesystem.allowWrite /tmp — the report and scratch files live there.

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

# Hosts every shipped check's Bash probes need (Check 7: pip list --outdated,
# pip-audit, npm outdated/audit, the npm registry fallback probes).  The app
# origin from APP_BASE_URL is added at runtime.  Adding a host here widens
# where an injected command can send data — say why in the commit.
BASE_ALLOWED_DOMAINS = ("registry.npmjs.org", "pypi.org")

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
        os.path.join(workspace, ".claude"),
        os.path.join(workspace, ".mcp.json"),
        os.path.join(workspace, ".mcp.qa.json"),
        os.path.join(home, ".claude"),
    ]
    return {
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
                "allowWrite": ["/tmp"],
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
