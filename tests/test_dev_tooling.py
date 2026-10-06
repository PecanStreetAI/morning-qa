"""Pins for this repo's own dev/CI tooling (not the template's).

requirements-dev.txt is the ONE place the tooling versions live: CI installs
from it, a contributor installs from it, and Dependabot reads it.  Before it
existed the pins were inline `pip install` arguments in ci.yml — invisible to
Dependabot and easy to fork between the two jobs.  These pins keep it that way,
and keep ShellCheck covering every tracked shell script.
"""

import re

import yaml

from probes import CI_WORKFLOW, DEPENDABOT_CONFIG, REQUIREMENTS_DEV

_EXACT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*==[0-9][0-9A-Za-z.+-]*$")


def _requirements():
    lines = REQUIREMENTS_DEV.read_text(encoding="utf-8").splitlines()
    return [ln.split("#", 1)[0].strip() for ln in lines if ln.split("#", 1)[0].strip()]


def _ci_steps():
    wf = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    return {
        job: [s for s in spec.get("steps") or [] if s.get("run")]
        for job, spec in wf["jobs"].items()
    }


def test_dev_requirements_are_exact_pins():
    reqs = _requirements()
    assert reqs, "requirements-dev.txt is empty"
    loose = [r for r in reqs if not _EXACT.match(r)]
    assert not loose, f"not exact `name==version` pins: {loose}"
    names = {r.split("==")[0].lower() for r in reqs}
    for tool in ("pytest", "pyyaml", "detect-secrets", "shellcheck-py"):
        assert tool in names, f"{tool} missing from requirements-dev.txt"


def test_ci_installs_only_from_the_requirements_file():
    """No inline `pip install <pkg>` in ci.yml: a pin there is one Dependabot
    cannot see and the other job does not share."""
    for job, steps in _ci_steps().items():
        for step in steps:
            for line in step["run"].splitlines():
                if re.search(r"\bpip install\b", line):
                    assert "-r requirements-dev.txt" in line, (
                        f"{job}: inline pip install bypasses requirements-dev.txt: {line.strip()}"
                    )


def test_shellcheck_runs_over_every_tracked_shell_script():
    runs = [s["run"] for s in _ci_steps()["tests"]]
    sc = [r for r in runs if "shellcheck" in r]
    assert sc, "the tests job no longer runs ShellCheck"
    assert any("git ls-files" in r and "'*.sh'" in r for r in sc), (
        "ShellCheck must enumerate scripts with `git ls-files '*.sh'` so a new "
        "script is linted without anyone updating a list"
    )


def test_dependabot_watches_the_dev_requirements():
    cfg = yaml.safe_load(DEPENDABOT_CONFIG.read_text(encoding="utf-8"))
    pip = [u for u in cfg["updates"] if u["package-ecosystem"] == "pip"]
    assert pip and any(u.get("directory") == "/" for u in pip), (
        "Dependabot has no pip entry for requirements-dev.txt"
    )
