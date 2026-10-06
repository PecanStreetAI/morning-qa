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
import os
import re
import subprocess
import sys

import pytest

from probes import (
    BASH,
    CANARY_WORKFLOW,
    TEMPLATE,
    catalog_section,
    code,
    flat,
    spec_path,
    COLLECT_REPORT_SCRIPT,
    SKILL,
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
collect = load_script_module(COLLECT_REPORT_SCRIPT, "qa_collect_report")

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


def test_allowlist_is_exactly_the_app():
    """No package registry: npm and PyPI accept uploads (`npm publish` is a
    PUT), so either would carry anything the agent reads off the runner.
    Check 7's registry probes run in the pre-compute step.  GitHub and
    Anthropic are never listed (the CLI process reaches Anthropic itself,
    outside the sandbox; GitHub data arrives via the pre-compute bundle)."""
    sb = sbx.build_settings("https://app.example.com/some/path", "/ws", "/h")["sandbox"]
    assert sb["network"]["allowedDomains"] == ["app.example.com"]
    assert sbx.BASE_ALLOWED_DOMAINS == ()
    bare = sbx.build_settings(None, "/ws", "/h")["sandbox"]["network"]["allowedDomains"]
    assert bare == []


def test_app_host_parsing():
    assert sbx.app_host("https://app.example.com") == "app.example.com"
    assert sbx.app_host("https://app.example.com:8443/x") == "app.example.com:8443"
    assert sbx.app_host("") is None and sbx.app_host(None) is None
    for bad in ("app.example.com", "ftp://x.example.com", "https://"):
        with pytest.raises(ValueError):
            sbx.app_host(bad)


def test_only_the_agent_dir_is_writable():
    """Not all of /tmp: later unsandboxed steps write fixed /tmp paths, and a
    symlink planted there redirects those writes anywhere the runner user
    can write."""
    fs = sbx.build_settings(None, "/ws", "/home/runner")["sandbox"]["filesystem"]
    assert sbx.AGENT_DIR == "/tmp/qa-agent"
    assert fs["allowWrite"] == ["/tmp/qa-agent"]


def test_workspace_home_settings_and_runtime_paths_are_write_protected():
    """The sandbox lets a command write its launch directory (the checkout)
    unless denied: the scripts later steps run unsandboxed, .git/hooks, the
    settings and MCP config.  /tmp/claude and ~/.npm/_logs are added to the
    writable set by the sandbox runtime itself."""
    fs = sbx.build_settings(None, "/ws", "/home/runner")["sandbox"]["filesystem"]
    for path in ("/ws", "/home/runner/.claude", "/tmp/claude", "/home/runner/.npm/_logs"):
        assert path in fs["denyWrite"], f"{path} must not be writable from the sandbox"


def test_read_tool_cannot_read_process_environments():
    """The Read tool runs in the CLI process, outside the sandbox: without
    this deny it can read the CLI's own environment (its API key)."""
    perms = sbx.build_settings(None, "/ws", "/h")["permissions"]
    # `*` stops at a `/`, so the second rule is what covers each thread's
    # copy (/proc/<pid>/task/<tid>/environ) and /proc/self/root/proc/...
    assert perms["deny"] == ["Read(//proc/*/environ)", "Read(//proc/**/environ)"]


def test_tool_caches_are_redirected_into_the_agent_dir():
    env = sbx.build_settings(None, "/ws", "/h")["env"]
    assert env["npm_config_cache"].startswith("/tmp/qa-agent/")
    assert env["XDG_CACHE_HOME"].startswith("/tmp/qa-agent/")
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert env["PYTEST_ADDOPTS"] == "-p no:cacheprovider"


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


def test_prepare_recreates_the_agent_dir_and_the_runtime_paths():
    """The agent dir starts empty and private every run (a self-hosted
    runner's /tmp persists); /tmp/claude and ~/.npm/_logs must exist for
    their denyWrite to bind."""
    lines = shell_lines(SANDBOX_PREPARE_SCRIPT.read_text(encoding="utf-8"))
    code = "\n".join(ln for ln in lines if not ln.lstrip().startswith("#"))
    assert "AGENT_DIR=/tmp/qa-agent" in code
    assert 'rm -rf "$AGENT_DIR"' in code
    assert 'mkdir -m 700 "$AGENT_DIR" "$AGENT_DIR/tmp"' in code
    assert code.index('rm -rf "$AGENT_DIR"') < code.index('mkdir -m 700 "$AGENT_DIR"')
    assert "rm -rf /tmp/claude" in code and "mkdir -m 700 /tmp/claude" in code
    assert '"$HOME/.npm/_logs"' in code


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


def test_cli_temp_dir_is_inside_the_agent_dir_in_production_and_canary():
    prod = WORKFLOW.read_text(encoding="utf-8")
    can = CANARY_WORKFLOW.read_text(encoding="utf-8")
    assert 'CLAUDE_CODE_TMPDIR=/tmp/qa-agent/tmp claude -p "/morning-qa"' in prod
    assert "CLAUDE_CODE_TMPDIR=/tmp/qa-agent/tmp claude -p" in can


def test_skill_writes_its_report_inside_the_agent_dir():
    skill = SKILL.read_text(encoding="utf-8")
    assert "/tmp/qa-agent/qa-report.md" in skill
    assert not re.search(r"(?<![\w-])/tmp/qa-report\.md", skill), (
        "the skill still names /tmp/qa-report.md, which its sandbox cannot write"
    )
    assert collect.SRC == "/tmp/qa-agent/qa-report.md"
    assert collect.DST == "/tmp/qa-report.md"


def test_skill_no_longer_relies_on_webfetch_or_github_access():
    skill = SKILL.read_text(encoding="utf-8")
    assert "via WebFetch" not in skill
    assert "gh issue list \\" not in skill, "the agent has no GitHub token to run it with"
    assert "Every Bash command runs in a sandbox" in skill


_COLLECT_STEP = "Collect the agent's report"
_TELEMETRY_STEP = "Append run telemetry to report"


def _checkout_with(steps):
    hits = [st for st in steps if "actions/checkout@" in str(st.get("uses", ""))]
    assert len(hits) == 1, "expected exactly one checkout step"
    return hits[0].get("with") or {}


def test_run_qa_and_canary_checkouts_keep_no_token():
    """Otherwise checkout leaves the GITHUB_TOKEN in .git/config, readable by
    the agent's Read tool and Bash."""
    assert _checkout_with(_run_qa()["steps"]).get("persist-credentials") is False
    assert _checkout_with(_canary()["jobs"]["canary"]["steps"]).get("persist-credentials") is False
    for st in _run_qa()["steps"]:
        for verb in ("git push", "git fetch", "git pull", "git clone", "git ls-remote"):
            assert verb not in code(st.get("run", "")), (
                f"{st.get('name')}: `{verb}` needs the credentials the checkout no longer keeps"
            )


def test_stale_agent_dir_is_cleaned_before_anything_can_collect_it():
    clean = named_step(_run_qa(), "Clean stale workspace artifacts")["run"]
    assert "/tmp/qa-agent" in clean.split()


def test_agent_step_collects_the_report_before_the_missing_report_fallback():
    lines = shell_lines(_agent_run())
    claude_idx = next(i for i, ln in enumerate(lines) if 'claude -p "/morning-qa"' in ln)
    fi_idx = next(i for i, ln in enumerate(lines) if ln.strip() == "fi" and i > claude_idx)
    collect_idx = next(i for i, ln in enumerate(lines)
                       if ln.strip() == "python3 .github/scripts/qa_collect_report.py")
    fallback_idx = next(i for i, ln in enumerate(lines) if "[ ! -s /tmp/qa-report.md ]" in ln)
    assert fi_idx < collect_idx < fallback_idx, (
        "collect must run after the claude if/else (whether or not the agent "
        "started) and before the report-missing fallback"
    )


def test_a_killed_run_still_has_its_partial_report_collected():
    names = step_names(_run_qa())
    assert names.index(_AGENT_STEP) < names.index(_COLLECT_STEP) < names.index(_TELEMETRY_STEP)
    step = named_step(_run_qa(), _COLLECT_STEP)
    assert step.get("if") == "always()"
    assert code(step["run"]).strip() == "python3 .github/scripts/qa_collect_report.py"


def test_telemetry_never_creates_a_footer_only_report(tmp_path):
    """Run the telemetry step's real bash with no report on disk: it must not
    create one (appending would — and a marker-less footer posts unlabeled)."""
    run = named_step(_run_qa(), _TELEMETRY_STEP)["run"]
    report = tmp_path / "qa-report.md"
    script = tmp_path / "step.sh"
    script.write_text(
        "set -eo pipefail\n"
        + run.replace("/tmp/qa-report.md", str(report))
             .replace("/tmp/claude-stdout.log", str(tmp_path / "log")),
        encoding="utf-8",
    )
    proc = subprocess.run([BASH, str(script)], capture_output=True, text=True, timeout=30,
                          cwd=str(TEMPLATE))
    assert proc.returncode == 0, proc.stderr
    assert not report.exists(), "telemetry created a report out of nothing"
    # The step guards it too, not just the script (which may change).
    assert "if [ -s /tmp/qa-report.md ]; then" in code(run)


def test_check_7_registry_probes_are_pre_compute_only():
    spec7 = flat(spec_path(7).read_text(encoding="utf-8"))
    assert "Pre-compute ONLY for the registry probes" in spec7
    assert "do NOT run those commands" in spec7
    skill = flat(SKILL.read_text(encoding="utf-8"))
    assert "Package registries (`registry.npmjs.org`, `pypi.org`) are **not** reachable" in skill
    assert "Pre-compute only:" in flat(catalog_section(7))


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


def test_exact_tool_set_is_the_same_in_production_canary_and_judge():
    """`--tools` is an allowlist of built-ins: a tool a future CLI adds stays
    off.  The canary's first run (2026-10-06) showed the default set includes
    Monitor and Workflow, which run shell commands by their own path."""
    prod = _quoted_flag(WORKFLOW.read_text(encoding="utf-8"), "--tools")
    can = _quoted_flag(CANARY_WORKFLOW.read_text(encoding="utf-8"), "--tools")
    assert prod == can == set(canary.ALLOWED_TOOLS), (prod, can, canary.ALLOWED_TOOLS)
    for tool in ("Monitor", "Workflow", "Task", "WebFetch", "WebSearch", "Write", "Edit"):
        assert tool not in prod, f"{tool} must not be in the agent's tool set"
    assert "Bash" in prod and "ToolSearch" in prod, (
        "Bash runs the checks; ToolSearch loads the deferred mongo-ro verbs"
    )


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
    reported = set(re.findall(r"(?:probe|can_write|can_reach) ([a-z_]+) ", text))
    reported |= {f"env_{v}" for v in loop.group(1).split()}
    assert set(canary.EXPECTED) == reported, (
        f"probe/judge drift: only judged {sorted(set(canary.EXPECTED) - reported)}, "
        f"only probed {sorted(reported - set(canary.EXPECTED))}"
    )


# ── The canary's judge ──────────────────────────────────────────────────────
_GOOD = {
    "egress_unlisted": "blocked", "egress_noproxy": "blocked",
    "egress_registry": "blocked", "egress_allowed": "200",
    "imds_proxy": "blocked", "imds_direct": "blocked",
    "env_ANTHROPIC_API_KEY": "absent",  # pragma: allowlist secret
    "env_MDB_MCP_CONNECTION_STRING": "absent",
    "env_GH_TOKEN": "absent", "proc_leaks": "0",
    "agent_dir_write": "ok", "tmp_write": "denied", "tmp_symlink": "denied",
    "workspace_write": "denied", "workspace_script_write": "denied",
    "git_hooks_write": "denied", "settings_write": "denied",
    "user_settings_write": "denied", "claude_tmp_write": "denied",
    "npm_logs_write": "denied", "df": "ok",
}
_DENIED_READ = ("/proc/4242/environ", True, "Permission to use Read has been denied.")
_DENIED_TASK_READ = ("/proc/4242/task/4242/environ", True, "Permission to use Read has been denied.")
_CONTROL_READ = ("/etc/os-release", False, 'NAME="Ubuntu"')
_READS = (_DENIED_READ, _DENIED_TASK_READ, _CONTROL_READ)


def _log(probes, tools=("Bash", "Read"), as_list=False, reads=_READS):
    text = "".join(f"PROBE {k}={v}\n" for k, v in probes.items())
    content = [{"type": "text", "text": text}] if as_list else text
    events = [
        {"type": "system", "subtype": "init", "tools": list(tools)},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Bash",
            "input": {"command": "bash template/.github/scripts/qa_sandbox_probes.sh"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": content}]}},
    ]
    for n, (path, is_error, body) in enumerate(reads):
        rid = f"r{n}"
        events.append({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": rid, "name": "Read", "input": {"file_path": path}}]}})
        result = {"type": "tool_result", "tool_use_id": rid, "content": body}
        if is_error:
            result["is_error"] = True
        events.append({"type": "user", "message": {"content": [result]}})
    return [json.dumps(e) for e in events]


def _ok(lines):
    return all(ok for ok, _ in canary.judge(*canary.parse(lines)))


@pytest.mark.parametrize("as_list", [False, True])
def test_judge_passes_a_contained_run(as_list):
    assert _ok(_log(_GOOD, as_list=as_list))


@pytest.mark.parametrize("name,bad", [
    ("egress_unlisted", "open"), ("egress_noproxy", "open"), ("egress_registry", "open"),
    ("egress_allowed", "none"), ("imds_proxy", "open"), ("imds_direct", "open"),
    ("env_ANTHROPIC_API_KEY", "present"), ("proc_leaks", "2"),
    ("agent_dir_write", "denied"), ("tmp_write", "allowed"), ("tmp_symlink", "allowed"),
    ("workspace_write", "allowed"), ("workspace_script_write", "allowed"),
    ("workspace_script_write", "missing"), ("git_hooks_write", "allowed"),
    ("settings_write", "allowed"), ("claude_tmp_write", "allowed"),
    ("npm_logs_write", "allowed"), ("df", "failed"),
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


def test_judge_fails_any_builtin_beyond_the_exact_set_but_allows_mcp_tools():
    assert not _ok(_log(_GOOD, tools=("Bash", "Read", "Monitor")))
    assert not _ok(_log(_GOOD, tools=("Bash", "Workflow")))
    assert _ok(_log(_GOOD, tools=("Bash", "Read", "ToolSearch", "Skill", "mcp__mongo-ro__count")))


def test_judge_ignores_markers_outside_tool_results():
    """The probe commands' own text names both outcomes; only RESULTS count."""
    lines = _log({})
    lines.insert(1, json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "".join(f"PROBE {k}={v}\n" for k, v in _GOOD.items())}]}}))
    assert not _ok(lines)


def test_judge_takes_probe_markers_only_from_bash_results():
    """A file the Read tool returns must not be able to forge a probe."""
    forged = "".join(f"PROBE {k}={v}\n" for k, v in _GOOD.items())
    assert not _ok(_log({}, reads=(_DENIED_READ, _DENIED_TASK_READ,
                                   ("/etc/os-release", False, forged))))


_LEAK = "QA_CANARY_DUMMY_KEY=canary-dummy-key-not-a-secret"  # pragma: allowlist secret


@pytest.mark.parametrize("reads,why", [
    ((("/proc/4242/environ", False, _LEAK), _DENIED_TASK_READ, _CONTROL_READ),
     "the dummy key was read"),
    ((_DENIED_READ, ("/proc/4242/task/4242/environ", False, _LEAK), _CONTROL_READ),
     "the dummy key was read through the per-thread copy"),
    ((("/proc/4242/environ", True, "error, but " + _LEAK), _DENIED_TASK_READ, _CONTROL_READ),
     "the dummy key is in the result"),
    ((("/proc/4242/environ", False, ""), _DENIED_TASK_READ, _CONTROL_READ),
     "the Read was not an error"),
    ((_DENIED_TASK_READ, _CONTROL_READ), "the /proc/<pid>/environ Read was never attempted"),
    ((_DENIED_READ, _CONTROL_READ), "the per-thread Read was never attempted"),
    ((_DENIED_READ, _DENIED_TASK_READ), "no control Read"),
    ((_DENIED_READ, _DENIED_TASK_READ, ("/etc/os-release", True, "denied")),
     "Read is off altogether"),
])
def test_judge_fails_a_read_tool_that_reaches_a_process_environment(reads, why):
    assert not _ok(_log(_GOOD, reads=reads)), why


def test_judge_ignores_a_read_verdict_printed_by_bash():
    """Bash cannot vouch for the Read tool: only Read results count."""
    probes = {**_GOOD, "read_tool_key": "denied"}
    assert not _ok(_log(probes, reads=(_CONTROL_READ,)))


def test_a_symlinked_read_is_reported_but_never_fails_the_run():
    """Read following a link the agent could plant in /tmp/qa-agent is a
    documented residual: the canary reports it as INFO."""
    link = canary.READ_VIA_LINK
    followed = _READS + ((link, False, _LEAK),)
    assert _ok(_log(_GOOD, reads=followed))
    assert "FOLLOWED" in canary.symlink_read_info(canary.parse(_log(_GOOD, reads=followed))[2])
    refused = _READS + ((link, True, "denied"),)
    assert canary.symlink_read_info(canary.parse(_log(_GOOD, reads=refused))[2]).endswith("refused")
    assert "not attempted" in canary.symlink_read_info([])


def test_judge_main_exit_codes(tmp_path):
    good = tmp_path / "good.log"
    good.write_text("\n".join(_log(_GOOD)), encoding="utf-8")
    assert canary.main(["x", str(good)]) == 0
    assert canary.main(["x", str(tmp_path / "missing.log")]) == 1


def test_canary_reads_a_dummy_environ_never_its_own():
    """The canary log is uploaded: if the Read deny broke, reading the CLI's
    own /proc/self/environ would put the real API key in it."""
    run = _canary_run()["run"]
    assert "--allowed-tools 'Bash,Read'" in run, "without Read pre-approved the deny proves nothing"
    assert "env -i QA_CANARY_DUMMY_KEY=" in run and canary.DUMMY_MARKER in run
    assert "/proc/${DUMMY_PID}/environ" in run and canary.READ_CONTROL in run
    assert "/proc/${DUMMY_PID}/task/${DUMMY_PID}/environ" in run
    assert f'ln -s "/proc/${{DUMMY_PID}}/environ" {canary.READ_VIA_LINK}' in run
    assert "/proc/self" not in run
    prompt = re.search(r'claude -p "([^"]*)"', run).group(1)
    assert canary.DUMMY_MARKER not in prompt, "the marker must reach the log only through a Read"


# ── Collecting the report out of the agent dir ──────────────────────────────
def test_collect_copies_a_regular_report(tmp_path):
    src, dst = tmp_path / "src.md", tmp_path / "dst.md"
    src.write_text("<!-- qa-max-severity: none -->\n# report\n", encoding="utf-8")
    assert collect.collect(str(src), str(dst)).startswith("copied")
    assert dst.read_text(encoding="utf-8") == src.read_text(encoding="utf-8")
    assert not dst.is_symlink()


def test_collect_refuses_a_symlinked_source(tmp_path):
    secret = tmp_path / "secret"
    secret.write_text("not for the issue", encoding="utf-8")
    src, dst = tmp_path / "src.md", tmp_path / "dst.md"
    src.symlink_to(secret)
    assert collect.collect(str(src), str(dst)).startswith("refused")
    assert not dst.exists()


def test_collect_refuses_a_fifo_without_hanging(tmp_path):
    src, dst = tmp_path / "src.md", tmp_path / "dst.md"
    os.mkfifo(src)
    assert collect.collect(str(src), str(dst)).startswith("refused")
    assert not dst.exists()


def test_collect_replaces_a_planted_destination_link_without_following_it(tmp_path):
    victim = tmp_path / "victim"
    victim.write_text("untouched", encoding="utf-8")
    src, dst = tmp_path / "src.md", tmp_path / "dst.md"
    src.write_text("# report\n", encoding="utf-8")
    dst.symlink_to(victim)
    assert collect.collect(str(src), str(dst)).startswith("copied")
    assert victim.read_text(encoding="utf-8") == "untouched"
    assert not dst.is_symlink() and dst.read_text(encoding="utf-8") == "# report\n"


def test_collect_is_idempotent_and_keeps_an_existing_report(tmp_path):
    src, dst = tmp_path / "src.md", tmp_path / "dst.md"
    src.write_text("# later\n", encoding="utf-8")
    dst.write_text("# first\n", encoding="utf-8")
    assert collect.collect(str(src), str(dst)).startswith("kept")
    assert dst.read_text(encoding="utf-8") == "# first\n"


def test_collect_caps_the_size_at_a_line_boundary(tmp_path):
    src, dst = tmp_path / "src.md", tmp_path / "dst.md"
    src.write_bytes(b"x" * 99 + b"\n" + b"Q" * 200)
    assert "truncated" in collect.collect(str(src), str(dst), cap=150)
    out = dst.read_bytes()
    assert out.startswith(b"x" * 99 + b"\n") and b"Q" not in out
    assert collect.MAX_BYTES == 512 * 1024


@pytest.mark.parametrize("setup", ["missing", "empty"])
def test_collect_leaves_no_destination_when_there_is_no_report(tmp_path, setup):
    src, dst = tmp_path / "src.md", tmp_path / "dst.md"
    if setup == "empty":
        src.write_text("", encoding="utf-8")
    assert collect.collect(str(src), str(dst)).startswith("missing")
    assert not dst.exists(), "an empty dst would skip the report-missing fallback"


def test_collect_main_always_exits_zero(tmp_path):
    assert collect.main(["x", str(tmp_path / "nope"), str(tmp_path / "dst")]) == 0
    assert collect.main(["x", "too", "many", "args"]) == 0
    proc = subprocess.run([sys.executable, str(COLLECT_REPORT_SCRIPT), str(tmp_path / "nope"),
                           str(tmp_path / "dst")], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0 and "missing" in proc.stdout
