"""probes.py — shared readers + executable contracts for the lockstep suites.

Why this module exists
──────────────────────
The suites in this directory share their subjects: the same workflow file, the
same severity-label script, the same skill/spec/catalog documents.  A reader
copied into each suite is a reader that drifts by accident, and a contract
stated twice is a contract that can be weakened in one place while the other
still reads green (the production system this framework was extracted from
learned that on 2026-08-21, when a new pin file quietly re-stated a sibling
suite's contract with looser clauses).  So every reader and every executable
contract lives HERE, once, and the suites import it.

Design rules (inherited from the production probe idiom):

1.  **A reader that cannot find its subject FAILS, never returns a vacuous
    nothing.**  A renamed step or a dropped block must be a red test that says
    what is missing — not a ``KeyError`` from whichever assertion looked
    first, or (worse) a later assertion that passes against an empty value.
2.  **Executable contracts run the REAL artifact.**  ``classify`` runs the
    checked-in ``qa_severity_label.sh``; ``run_label_block`` executes the
    workflow's actual label-glue bash.  A pin that only greps the source
    proves agreement with the source, not correctness (a 2026-08-04 review
    lesson) — and this classifier shipped three bugs while it lived as
    inline, untestable workflow bash.
3.  **No ``test_`` prefix, nothing runs at import.**  pytest collects nothing
    here, and a malformed subject becomes one red test, not a collection
    error for the whole run.
"""
from __future__ import annotations

import importlib.util
import re
import shlex
import shutil
import subprocess
from pathlib import Path, PurePosixPath

import yaml

# ── Paths (the suites' shared subjects) ──────────────────────────────────────
REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "template"

WORKFLOW = TEMPLATE / ".github/workflows/morning-qa.yml"
RETRO_WORKFLOW = TEMPLATE / ".github/workflows/qa-ledger-retro.yml"
SEV_SCRIPT = TEMPLATE / ".github/scripts/qa_severity_label.sh"
PRECOMPUTE_SCRIPT = TEMPLATE / ".github/scripts/qa_precompute.py"
TELEMETRY_SCRIPT = TEMPLATE / ".github/scripts/qa_run_telemetry.js"
LEDGER_SCRIPT = TEMPLATE / ".github/scripts/qa_ledger_retro.py"
MCP_CONFIG = TEMPLATE / ".mcp.qa.json"

SKILL = TEMPLATE / ".claude/skills/morning-qa/SKILL.md"
CHECKS_DIR = TEMPLATE / ".claude/skills/morning-qa/checks"

CATALOG = REPO / "docs/check_catalog.md"
PRECOMPUTE_DOC = REPO / "docs/precompute.md"
DESIGN_DOC = REPO / "docs/design.md"
LEDGER_DOC = REPO / "docs/calibration_ledger.md"

# bash is guaranteed on every POSIX CI image; falling back to the bare name
# keeps the executable pins from silently vanishing on an unusual PATH.  If
# bash really is absent they error loudly, which is correct.
BASH = shutil.which("bash") or "bash"

# The `<!-- qa-max-severity: value -->` marker, bare-word values only: the
# report template's `{none|info|warning|critical}` placeholder is a shape, not
# a value, and `[a-z]+` deliberately does not match it.
MARKER_VALUE_RE = re.compile(r"<!--\s*qa-max-severity:\s*([a-z]+)\s*-->")


# ── Workflow readers ─────────────────────────────────────────────────────────
def load_workflow(path=WORKFLOW):
    """The parsed workflow — a mapping, or a red test naming the file."""
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    assert isinstance(doc, dict), f"{Path(path).name} did not parse as a YAML mapping"
    return doc


def job(doc, name):
    jobs = doc.get("jobs") or {}
    assert name in jobs, f"job {name!r} missing — renamed? found {sorted(jobs)}"
    return jobs[name]


def named_step(job_map, name):
    steps = {s.get("name"): s for s in job_map.get("steps") or []}
    assert name in steps, (
        f"step {name!r} missing — renamed? update the pin in lockstep. "
        f"found {sorted(k for k in steps if k)}"
    )
    return steps[name]


def step_names(job_map):
    return [s.get("name") for s in job_map.get("steps") or []]


# ── Shell-text readers ───────────────────────────────────────────────────────
# Trailing backslashes on a line.  A line continues only on an ODD count —
# `foo \\` ends in an escaped literal backslash and is complete.
_TRAILING_BACKSLASHES = re.compile(r"\\+$")


def code(run):
    r"""Shell text with comment lines dropped and `\`-continuations joined.

    Walked line by line, because the ORDER of the two operations matters in
    both directions: a COMMENT line's trailing `\` does NOT continue it (POSIX
    ends a comment at the newline), while a CODE line continued onto a line
    that starts with `#` DOES absorb it (after a continuation the `#` is an
    argument).  The workflow's run: blocks carry long explanatory comments
    that mention the very flags and package names the pins look for — a naive
    substring search over the raw text matches prose instead of the flag,
    which is exactly how a pin passes against its own explanatory comment.
    """
    out, buf = [], None
    for raw in (run or "").splitlines():
        if buf is None:
            if raw.strip().startswith("#"):
                continue  # a comment line: its trailing `\` is inert
            cur = raw
        else:
            cur = buf + " " + raw.lstrip()
        m = _TRAILING_BACKSLASHES.search(cur)
        if m and len(m.group(0)) % 2 == 1:
            buf = cur[:-1]  # drop the continuation slash, keep accumulating
        else:
            out.append(cur)
            buf = None
    if buf is not None:
        out.append(buf)  # text ended mid-continuation — keep what we have
    return "\n".join(out)


def shell_lines(run):
    """Non-comment lines of a run: block, continuations joined — a list,
    because every caller iterates."""
    return code(run).splitlines()


def flat(text):
    """Whitespace-collapsed text.  Every multi-word doc pin matches against
    this, never raw bytes: the prose hard-wraps at ~72 cols, so a raw-text pin
    breaks on a pure re-wrap that changes nothing."""
    return " ".join(text.split())


# ── Doc readers ──────────────────────────────────────────────────────────────
def catalog_section(n):
    """The catalog's `## Check N` section, up to the next H2."""
    text = CATALOG.read_text(encoding="utf-8")
    anchor = f"## Check {n} —"
    assert anchor in text, f"check catalog has no section for Check {n}"
    start = text.index(anchor)
    end = text.find("\n## ", start + 1)
    return text[start : end if end != -1 else len(text)]


def spec_path(n):
    hits = sorted(CHECKS_DIR.glob(f"{n:02d}-*.md"))
    assert len(hits) == 1, f"expected one checks/{n:02d}-*.md spec, found {hits}"
    return hits[0]


def severity_rows(text):
    """Normalized table rows that carry a severity glyph.  Indent-tolerant:
    spec tables live inside numbered lists and are indented."""
    rows = [
        re.sub(r"\s+", " ", line).strip()
        for line in text.splitlines()
        if line.lstrip().startswith("| ")
        and any(g in line for g in ("🔴", "🟡", "🟢", "✅"))
    ]
    assert rows, "no severity rows found — table moved or reformatted?"
    return rows


# ── Script-module loader ─────────────────────────────────────────────────────
_MODULES = {}


def load_script_module(path, name):
    """Import a checked-in script (no package) as a module, cached per run.
    The scripts under template/.github/scripts/ are deliberately import-safe:
    their only import-time I/O is reading os.environ."""
    if name not in _MODULES:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _MODULES[name] = mod
    return _MODULES[name]


# ── Executable contract 1: the severity classifier ───────────────────────────
def classify(tmp_path, body):
    """(is_critical, is_incomplete) from the REAL qa_severity_label.sh.
    body=None => the report file is never created (the unreadable-input path).

    The script's contract is asserted here once for every caller: it ALWAYS
    exits 0 (a non-zero exit would fail the post-issue step and take down the
    daily report over a labeling decision) and prints exactly the two lines.
    """
    report = tmp_path / "qa-report.md"
    if body is not None:
        report.write_text(body, encoding="utf-8")
    proc = subprocess.run(
        [BASH, str(SEV_SCRIPT), str(report)],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, (
        "classifier must ALWAYS exit 0 — fail-soft is load-bearing.\n"
        f"stderr: {proc.stderr}"
    )
    parsed = dict(
        ln.split("=", 1) for ln in proc.stdout.strip().splitlines() if "=" in ln
    )
    assert set(parsed) == {"is_critical", "is_incomplete"}, (
        f"classifier stdout contract broken; got {proc.stdout!r}"
    )
    return parsed["is_critical"], parsed["is_incomplete"]


# ── Executable contract 2: the workflow's label-glue bash ────────────────────
def run_label_block(tmp_path, body, classifier=True):
    """Execute the workflow's REAL label-arg logic against a report body.

    The classifier pins prove the script classifies correctly; this proves the
    workflow does the right thing WITH that answer — the glue is where the
    `--label` vs `--add-label` and remove-on-downgrade paths live, and none of
    it is otherwise executable.  Slices the run block between two anchors; if
    a future edit moves them, the assert names the drift instead of silently
    testing nothing.

    classifier=False runs the block from a directory where the classifier's
    repo-relative path does not resolve — simulating a missing or broken
    classifier, the path where the two flags must fail in OPPOSITE directions.
    Returns (CREATE_LABEL_ARGS, EDIT_LABEL_ARGS).
    """
    run = named_step(job(load_workflow(), "post-issue"), "Post / update daily QA Issue")["run"]
    start_anchor = 'CREATE_LABEL_ARGS="--label qa-agent-daily"'
    end_anchor = 'if [ -n "$EXISTING" ]'
    assert start_anchor in run and end_anchor in run, (
        "post-issue label-arg block anchors moved — re-point this contract"
    )
    block = run[run.index(start_anchor):run.index(end_anchor)]

    report = tmp_path / "qa-report.md"
    report.write_text(body, encoding="utf-8")
    script = tmp_path / "block.sh"
    script.write_text(
        "set -eo pipefail\n"  # GitHub Actions' default shell flags
        + block.replace("/tmp/qa-report.md", str(report))
        + '\necho "CREATE:$CREATE_LABEL_ARGS"\necho "EDIT:$EDIT_LABEL_ARGS"\n',
        encoding="utf-8",
    )
    # cwd matters: the block invokes the classifier by repo-relative path,
    # exactly as the job does from its checkout root (= template/ here).
    proc = subprocess.run(
        [BASH, str(script)], capture_output=True, text=True, timeout=30,
        cwd=str(TEMPLATE if classifier else tmp_path),
    )
    assert proc.returncode == 0, (
        f"label block aborted (set -e); the daily issue would not post.\n{proc.stderr}"
    )
    out = dict(
        ln.split(":", 1) for ln in proc.stdout.splitlines()
        if ln.startswith(("CREATE:", "EDIT:"))
    )
    return out["CREATE"], out["EDIT"]


# ── Executable contract 3: the global-npm-install pin detector ───────────────
# A PURE classifier over a run-block string, driven BOTH ways by parametrized
# calibration tests: every known way to defeat the exact-version pin must red,
# and every equivalent correct rewrite must not.  Structural pins pass on main
# by construction, so without those two halves nothing proves the guard can
# fail — and a later "cleanup" of the parsing could hollow it out silently.

_NPM_SUBCOMMANDS = {"install", "i", "add"}
_GLOBAL_FLAGS = {"-g", "--global", "--location=global"}
# npm flags whose value is a SEPARATE token; that value is not a package spec.
# Not npm's full option table — just the forms plausible in CI.  Unknown
# value-taking flags therefore fail CLOSED (a loud red naming this set as the
# one-line fix) rather than open.
_VALUE_FLAGS = {"--registry", "--prefix", "--loglevel", "--userconfig",
                "--cache", "--location", "--workspace", "-w"}
# Exact semver only.  Rejects `latest`/`next` (dist-tags), `^`/`~`/`x` ranges,
# and a bare name (no `@` at all); accepts prerelease + build metadata, which
# are still exact.
_EXACT_SEMVER = re.compile(
    r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.\-]+)?(?:\+[0-9A-Za-z.\-]+)?$"
)


def _command_segments(run_block):
    """Shell command segments of a run: block — comments dropped,
    continuations joined, then split on the operators that separate one
    command from the next, so a SECOND command sharing a line
    (`… && npm install -g b@latest`) is classified too."""
    return [seg for seg in re.split(r"&&|\|\||[;|&\n]", code(run_block)) if seg.strip()]


def _global_install_specs(segment):
    """Package specs a single segment installs globally, else None (None =
    "not a global npm install": `npm ci`, `which`, a local install — local
    installs are lockfile territory, not this pin's)."""
    try:
        tokens = shlex.split(segment, comments=True)
    except ValueError:
        return None  # unbalanced quotes — not a command we can reason about
    # Anchor on the first token whose BASENAME is npm, so a path-qualified npm
    # and `sudo npm` both resolve.
    idx = next((i for i, t in enumerate(tokens) if PurePosixPath(t).name == "npm"), None)
    if idx is None:
        return None
    rest = tokens[idx + 1:]
    if not (_NPM_SUBCOMMANDS & set(rest)):
        return None
    is_global = bool(_GLOBAL_FLAGS & set(rest)) or any(
        a == "--location" and b == "global" for a, b in zip(rest, rest[1:])
    )
    if not is_global:
        return None
    specs, skip_next = [], False
    for tok in rest:
        if skip_next:
            skip_next = False
            continue
        if tok in _VALUE_FLAGS:
            skip_next = True
            continue
        if tok.startswith("-") or tok in _NPM_SUBCOMMANDS:
            continue
        specs.append(tok)
    return specs


def split_spec(spec):
    """'@scope/name@1.2.3' -> ('@scope/name', '1.2.3').  Splits on the LAST
    '@' so a scoped package's leading '@' isn't mistaken for the version
    delimiter; version None = a bare name (the unpinned case)."""
    at = spec.rfind("@")
    if at <= 0:
        return spec, None
    return spec[:at], spec[at + 1:]


def unpinned_defect(run_block):
    """Reason string if this run block installs anything globally without an
    exact version, else None.  Pure — the calibration tests drive it with
    synthetic blocks, which is what makes its failure paths reachable."""
    for segment in _command_segments(run_block):
        for spec in _global_install_specs(segment) or ():
            pkg, version = split_spec(spec)
            if version is None:
                return (
                    f"`{pkg}` installed with no version — a bare name resolves the "
                    "`latest` dist-tag on every daily run.  Pin an exact version. "
                    f"(If {spec!r} is a flag's VALUE rather than a package, add that "
                    "flag to _VALUE_FLAGS in tests/probes.py.)"
                )
            if not _EXACT_SEMVER.match(version):
                return (
                    f"`{pkg}@{version}` — {version!r} is a dist-tag or range, which "
                    "re-resolves daily into a secret-bearing environment.  Pin an "
                    "exact version and bump it deliberately."
                )
    return None


def all_run_steps(doc):
    """(job_name, step) for every step with a run:, across ALL jobs — a guard
    whose docstring promises exhaustive enumeration must not quietly
    enumerate a subset (a name-keyed dict collapses unnamed steps)."""
    return [
        (job_name, s)
        for job_name, j in (doc.get("jobs") or {}).items()
        for s in j.get("steps") or []
        if s.get("run")
    ]
