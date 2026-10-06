"""GitHub Actions pin hygiene across every workflow in the repo.

Each `uses:` is pinned by full commit SHA (a tag can be rewritten under you;
a SHA cannot — the unattended-install reasoning in docs/design.md).  Pins
that never move, though, age silently past security fixes: before
.github/dependabot.yml existed, the retro workflow sat a major version behind
its siblings (checkout v4 vs v5, setup-python v5 vs v6) with nothing to flag
it.  These pins keep three things true:

* every `uses:` is a 40-hex SHA with the `# vN` comment Dependabot rewrites;
* one action resolves to ONE SHA everywhere — CI and both template
  workflows — so a partial bump (one tree updated, the other forgotten) reds;
* Dependabot actually watches every directory holding a workflow.
"""

import re

import yaml

from probes import ALL_WORKFLOWS, DEPENDABOT_CONFIG, REPO, action_pins

_SHA = re.compile(r"^[0-9a-f]{40}$")
_VERSION_COMMENT = re.compile(r"^#\s*v\d+(\.\d+){0,2}\b")


def test_every_action_is_sha_pinned_with_a_version_comment():
    bad = []
    for wf in ALL_WORKFLOWS:
        pins = action_pins(wf)
        assert pins, f"no `uses:` lines found in {wf.name} — reader broken?"
        for n, action, ref, comment in pins:
            if action.startswith("./"):
                continue  # local action: versioned with the repo itself
            if not _SHA.match(ref):
                bad.append(f"{wf.relative_to(REPO)}:{n}: {action}@{ref} is not a full SHA")
            elif not _VERSION_COMMENT.match(comment):
                bad.append(f"{wf.relative_to(REPO)}:{n}: {action} lacks a `# vN` comment")
    assert not bad, "\n".join(bad)


def test_each_action_is_pinned_to_one_sha_across_all_workflows():
    seen = {}
    for wf in ALL_WORKFLOWS:
        for n, action, ref, _ in action_pins(wf):
            seen.setdefault(action, set()).add(f"{ref} ({wf.name}:{n})")
    drift = {
        a: sorted(v) for a, v in seen.items()
        if len({entry.split()[0] for entry in v}) > 1
    }
    assert not drift, (
        "the same action is pinned to different SHAs — a partial bump? "
        f"{drift}"
    )


def test_dependabot_watches_every_workflow_directory_in_one_group():
    cfg = yaml.safe_load(DEPENDABOT_CONFIG.read_text(encoding="utf-8"))
    assert cfg["version"] == 2
    gha = [u for u in cfg["updates"] if u["package-ecosystem"] == "github-actions"]
    assert len(gha) == 1, "one github-actions entry, so one grouped PR spans both trees"
    entry = gha[0]
    dirs = set(entry.get("directories") or [entry.get("directory")])
    # "/" scans .github/workflows/; any other directory is scanned AS-IS
    # (dependabot-core's github_actions file fetcher), so a template entry
    # must name the workflows directory itself.
    expected = {"/" if wf.parent == REPO / ".github/workflows"
                else "/" + str(wf.parent.relative_to(REPO))
                for wf in ALL_WORKFLOWS}
    assert dirs == expected, f"dependabot directories {sorted(dirs)} != {sorted(expected)}"
    groups = entry.get("groups") or {}
    assert any("*" in (g.get("patterns") or []) for g in groups.values()), (
        "without a catch-all group, Dependabot opens one PR per directory and "
        "the one-SHA-per-action pin reds on each"
    )
