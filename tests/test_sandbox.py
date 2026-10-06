"""Pins for the QA agent's Bash sandbox and its live canary.

The sandbox is the control that binds `Bash` — the one tool the tier flags
cannot contain.  What these pins can prove offline: the settings are the
ones intended, every secret the agent step holds is denied to its commands,
the workflow passes them inline and disallows the tools the sandbox cannot
cover, and the canary tests exactly what production runs.  What they cannot
prove is that the sandbox STARTS on a real runner — that is the canary's
job (.github/workflows/sandbox-canary.yml, run by hand).
"""

import json
import re

import pytest

from probes import (
    CANARY_WORKFLOW,
    SANDBOX_CANARY_SCRIPT,
    SANDBOX_PREPARE_SCRIPT,
    SANDBOX_PROBES_SCRIPT,
    SANDBOX_SETTINGS_SCRIPT,
    WORKFLOW,
    job,
    load_script_module,
    load_workflow,
    named_step,
    shell_lines,
    step_names,
)
import yaml

sbx = load_script_module(SANDBOX_SETTINGS_SCRIPT, "qa_sandbox_settings")
canary = load_script_module(SANDBOX_CANARY_SCRIPT, "qa_sandbox_canary")

_AGENT_STEP = "Run morning QA skill"
_PREPARE_STEP = "Prepare runner for the agent sandbox"


def _run_qa():
    return job(load_workflow(), "run-qa")


def _agent_run():
    return named_step(_run_qa(), _AGENT_STEP)["run"]


def _quoted_flag(text, flag):
    # The real flag line (indented, starts with the flag) — not a mention
    # of it inside a `# ...` comment.
    m = re.search(rf"^\s+{flag}\s+'([^']+)'", text, re.M)
    assert m, f"{flag} not found as a single-quoted list"
    return {t.strip() for t in m.group(1).split(",") if t.strip()}


def _canary():
    return yaml.safe_load(CANARY_WORKFLOW.read_text(encoding="utf-8"))


def _canary_run():
    return named_step(_canary()["jobs"]["canary"], "Run the probes inside the agent's sandbox")


# ── The settings themselves ─────────────────────────────────────────────────
def test_settings_fail_closed_and_cannot_be_opted_out_of():
    sb = sbx.build_settings("https://app.example.com", "/ws", "/home/runner")["sandbox"]
    assert sb["enabled"] is True
    assert sb["failIfUnavailable"] is True, "a sandbox that cannot start must fail the run"
    assert sb["allowUnsandboxedCommands"] is False, "the model must not be able to opt out"
    assert sb["network"]["strictAllowlist"] is True, "unlisted hosts must be refused, not prompted"


def test_allowlist_is_exactly_the_checks_hosts_plus_the_app():
    sb = sbx.build_settings("https://app.example.com/some/path", "/ws", "/h")["sandbox"]
    assert sb["network"]["allowedDomains"] == ["registry.npmjs.org", "pypi.org", "app.example.com"]
    # No app configured -> no app host; the base list never includes GitHub
    # or Anthropic (the CLI process reaches Anthropic itself, outside the
    # sandbox; GitHub data arrives via the pre-compute bundle).
    bare = sbx.build_settings(None, "/ws", "/h")["sandbox"]["network"]["allowedDomains"]
    assert bare == ["registry.npmjs.org", "pypi.org"]
    for host in bare:
        assert "anthropic" not in host and "github" not in host


def test_app_host_parsing():
    assert sbx.app_host("https://app.example.com") == "app.example.com"
    assert sbx.app_host("https://app.example.com:8443/x") == "app.example.com:8443"
    assert sbx.app_host("") is None and sbx.app_host(None) is None
    for bad in ("app.example.com", "ftp://x.example.com", "https://"):
        with pytest.raises(ValueError):
            sbx.app_host(bad)


def test_settings_and_mcp_paths_are_write_protected_and_tmp_is_writable():
    fs = sbx.build_settings(None, "/ws", "/home/runner")["sandbox"]["filesystem"]
    assert fs["allowWrite"] == ["/tmp"], "the incremental report lives in /tmp"
    for path in ("/ws/.claude", "/ws/.mcp.json", "/ws/.mcp.qa.json", "/home/runner/.claude"):
        assert path in fs["denyWrite"], f"{path} must not be writable from the sandbox"


def test_every_secret_in_the_agent_env_is_denied_to_its_commands():
    env = named_step(_run_qa(), _AGENT_STEP).get("env") or {}
    secrets = {k for k, v in env.items() if "secrets." in str(v)}
    assert secrets, "agent env has no secrets? reader broken"
    denied = {
        e["name"]
        for e in sbx.build_settings(None, "/ws", "/h")["sandbox"]["credentials"]["envVars"]
        if e["mode"] == "deny"
    }
    assert secrets <= denied, f"secrets visible to the agent's Bash: {sorted(secrets - denied)}"


def test_main_emits_compact_json_and_rejects_a_bad_app_url(monkeypatch, capsys):
    monkeypatch.setenv("APP_BASE_URL", "https://app.example.com")
    monkeypatch.setenv("GITHUB_WORKSPACE", "/ws")
    assert sbx.main() == 0
    out = capsys.readouterr().out
    assert "\n" not in out.strip() and json.loads(out)["sandbox"]["enabled"] is True
    monkeypatch.setenv("APP_BASE_URL", "not a url")
    assert sbx.main() == 1
    assert capsys.readouterr().out == "", "nothing on stdout means nothing to pass as --settings"


# ── Workflow wiring ─────────────────────────────────────────────────────────
def test_runner_is_prepared_before_the_agent_and_preparation_fails_hard():
    names = step_names(_run_qa())
    assert names.index(_PREPARE_STEP) < names.index(_AGENT_STEP)
    step = named_step(_run_qa(), _PREPARE_STEP)
    assert "qa_sandbox_prepare.sh" in step["run"]
    assert not step.get("continue-on-error"), "no sandbox must mean no agent"
    text = SANDBOX_PREPARE_SCRIPT.read_text(encoding="utf-8")
    assert "set -euo pipefail" in text
    for needle in ("bubblewrap", "socat", "apparmor_restrict_unprivileged_userns", ".claude"):
        assert needle in text


def test_agent_gets_the_settings_inline_and_never_runs_without_them():
    run = _agent_run()
    assert 'SANDBOX_SETTINGS="$(python3 .github/scripts/qa_sandbox_settings.py)"' in run
    assert '--settings "$SANDBOX_SETTINGS"' in run, (
        "settings must be passed inline — a settings FILE is something the "
        "agent's Bash could rewrite"
    )
    # The claude call is inside the success branch of the settings build.
    lines = shell_lines(run)
    if_idx = next(i for i, ln in enumerate(lines) if "if SANDBOX_SETTINGS=" in ln)
    claude_idx = next(i for i, ln in enumerate(lines) if 'claude -p "/morning-qa"' in ln)
    else_idx = next(i for i, ln in enumerate(lines) if ln.strip() == "else" and i > if_idx)
    assert if_idx < claude_idx < else_idx


def test_skill_no_longer_relies_on_webfetch_or_github_access():
    from probes import SKILL
    skill = SKILL.read_text(encoding="utf-8")
    assert "via WebFetch" not in skill
    assert "gh issue list \\" not in skill, "the agent has no GitHub token to run it with"
    assert "Every Bash command runs in a sandbox" in skill


# ── The canary tests what production runs ───────────────────────────────────
def _pin(text, pattern):
    m = re.search(pattern, text)
    assert m, pattern
    return m.group(1)


def test_canary_uses_productions_cli_model_and_tool_flags():
    prod = WORKFLOW.read_text(encoding="utf-8")
    can = CANARY_WORKFLOW.read_text(encoding="utf-8")
    cli = r"npm install -g @anthropic-ai/claude-code@(\d\S*)"
    assert _pin(can, cli) == _pin(prod, cli), "canary must test the CLI version production runs"
    model = r"--model\s+(claude-[\w.-]+)\s*\\\n"
    assert _pin(can, model) == _pin(prod, model)
    assert _quoted_flag(can, "--disallowed-tools") == _quoted_flag(prod, "--disallowed-tools")
    assert "--strict-mcp-config" in can


def test_canary_runs_productions_scripts_and_judges_the_result():
    wf = _canary()
    triggers = wf.get("on", wf.get(True))  # PyYAML reads a bare `on:` key as True
    assert set(triggers) == {"workflow_dispatch"}, "manual only — it spends API credit"
    assert wf["permissions"] == {"contents": "read"}
    steps = wf["jobs"]["canary"]["steps"]
    runs = "\n".join(s.get("run", "") for s in steps)
    for script in ("qa_sandbox_prepare.sh", "qa_sandbox_settings.py",
                   "qa_sandbox_probes.sh", "qa_sandbox_canary.py"):
        assert f"template/.github/scripts/{script}" in runs, f"canary skips {script}"
    env = _canary_run()["env"]
    # Every denied var is present (as a dummy, or the real API key) so the
    # probes can prove it is hidden — not merely absent from the start.
    for entry in sbx.build_settings(None, "/ws", "/h")["sandbox"]["credentials"]["envVars"]:
        assert entry["name"] in env, f"canary cannot prove {entry['name']} is hidden"
    for name, value in env.items():
        if name != "ANTHROPIC_API_KEY":
            assert "secrets." not in str(value), f"canary must not load real secret {name}"


def test_probe_script_reports_every_property_the_judge_expects():
    text = "\n".join(
        ln for ln in SANDBOX_PROBES_SCRIPT.read_text(encoding="utf-8").splitlines()
        if not ln.lstrip().startswith("#")
    )
    loop = re.search(r"for v in ([A-Z_ ]+); do", text)
    assert loop
    reported = set(re.findall(r"probe ([a-z_]+) ", text))
    reported |= {f"env_{v}" for v in loop.group(1).split()}
    assert set(canary.EXPECTED) == reported, (
        f"probe/judge drift: only judged {sorted(set(canary.EXPECTED) - reported)}, "
        f"only probed {sorted(reported - set(canary.EXPECTED))}"
    )


# ── The canary's judge ──────────────────────────────────────────────────────
_GOOD = {
    "egress_unlisted": "blocked", "egress_allowed": "200",
    "env_ANTHROPIC_API_KEY": "absent",  # pragma: allowlist secret
    "env_MDB_MCP_CONNECTION_STRING": "absent",
    "env_GH_TOKEN": "absent", "proc_leaks": "0", "tmp_write": "ok",
    "settings_write": "denied", "user_settings_write": "denied",
}


def _log(probes, tools=("Bash", "Read"), as_list=False):
    text = "".join(f"PROBE {k}={v}\n" for k, v in probes.items())
    content = [{"type": "text", "text": text}] if as_list else text
    events = [
        {"type": "system", "subtype": "init", "tools": list(tools)},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash",
            "input": {"command": "bash template/.github/scripts/qa_sandbox_probes.sh"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": content}]}},
    ]
    return [json.dumps(e) for e in events]


def _ok(lines):
    return all(ok for ok, _ in canary.judge(*canary.parse(lines)))


@pytest.mark.parametrize("as_list", [False, True])
def test_judge_passes_a_contained_run(as_list):
    assert _ok(_log(_GOOD, as_list=as_list))


@pytest.mark.parametrize("name,bad", [
    ("egress_unlisted", "open"), ("egress_allowed", "none"),
    ("env_ANTHROPIC_API_KEY", "present"), ("proc_leaks", "2"),
    ("tmp_write", "denied"), ("settings_write", "allowed"),
])
def test_judge_fails_each_broken_property(name, bad):
    assert not _ok(_log({**_GOOD, name: bad}))


def test_judge_fails_a_skipped_probe_and_a_missing_init():
    partial = dict(_GOOD)
    partial.pop("proc_leaks")
    assert not _ok(_log(partial))
    assert not _ok(_log(_GOOD)[1:]), "no init event must not pass"


def test_judge_fails_when_webfetch_is_offered():
    assert not _ok(_log(_GOOD, tools=("Bash", "WebFetch")))


def test_judge_ignores_markers_outside_tool_results():
    """The probe commands' own text names both outcomes; only RESULTS count."""
    lines = _log({})
    lines.insert(1, json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "".join(f"PROBE {k}={v}\n" for k, v in _GOOD.items())}]}}))
    assert not _ok(lines)


def test_judge_main_exit_codes(tmp_path):
    good = tmp_path / "good.log"
    good.write_text("\n".join(_log(_GOOD)), encoding="utf-8")
    assert canary.main(["x", str(good)]) == 0
    assert canary.main(["x", str(tmp_path / "missing.log")]) == 1
