# Porting policy

This repository is a **clean-room extraction** of a QA observer that runs in
production inside a private codebase. The framework here — the graduated-autonomy
ladder, the check-spec format, the precompute bundle, the calibration ledger —
was lifted out of that instance at a fixed baseline (2026-08-27) and sanitized
for public release.

How the two codebases relate:

* **Improvements flow one way: private → public, manually.** When the production
  instance learns something worth generalizing (a new invariant, a closed
  failure class, a better rubric), a human ports it here through a sanitization
  checklist. There is no automated sync in either direction, and there never
  will be — an automated sync is exactly the kind of unattended write path this
  framework exists to distrust.
* **The private instance and this framework diverge by design.** The production
  instance runs 17 checks against one specific product; this repo ships 3 worked
  examples and a template. Bug fixes and design changes land here when they are
  general, not because the private instance changed.
* **`scripts/denylist_scan.sh` is the sanitization backstop.** Every port runs
  it before publishing. If you find something in this repo that looks like it
  escaped sanitization, please report it privately rather than opening a public
  issue with the details — [SECURITY.md](SECURITY.md) has the channel.
* **Contributions are welcome — and reviewed as untrusted input.** This is a
  framework whose output an operator is asked to trust every morning, so PRs get
  the same skeptical read this framework applies to its own probes: check specs
  are prompts, workflow changes are supply chain, and anything touching the
  severity or labeling path gets pinned by a test before it merges.

If you adopt the template and build something on it, you are not expected to
track this repo. Copy `template/`, make it yours, and treat future releases
here as a source of ideas, not upstream.
