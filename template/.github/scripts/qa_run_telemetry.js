#!/usr/bin/env node
// Append a one-line run-telemetry footer to the morning-QA report.
//
// Usage: node qa_run_telemetry.js <stream-json-log-path> <report-md-path>
//
// Reads the Claude Code CLI `--output-format stream-json` log and appends
// a footer to the report with the run's model, cost, turn count, and
// duration, parsed from:
//   * type:"system", subtype:"init"  -> .model   (first occurrence wins)
//   * type:"result"                  -> .total_cost_usd / .num_turns /
//                                       .duration_ms  (LAST result wins —
//                                       intentional: a resumed/continued
//                                       session emits one result per
//                                       segment and the last is the
//                                       session's final accounting)
//
// This is the drift-observability half of the 2026-07-23 cost fix (the
// other half is the `--model` pin in morning-qa.yml): the footer makes
// model/price drift visible on the daily issue the morning it happens,
// instead of on the monthly invoice.
//
// Fail-soft CONTRACT with the workflow step that invokes this script:
//   * Exit code is ALWAYS 0 on any parse/read problem — a missing footer
//     must never block the report -> artifact -> issue chain. (The only
//     nonzero exits are argv misuse, which a structural workflow-pin
//     test should prevent shipping, and an append failure, which the
//     step's `|| echo` absorbs.)
//   * Distinct footer messages per failure mode, because "unavailable"
//     is also what a genuine timeout day produces and a parser/shape
//     breakage must not be indistinguishable from routine noise
//     (a 2026-07-23 review finding):
//       - no log file            -> "no stream-json log captured"
//       - no result event        -> "no result event ... died mid-flight"
//       - result but no numeric  -> "result event present but no numeric
//         total_cost_usd            total_cost_usd — CLI wire-shape
//                                   change? inspect the artifact"
//   * The stream-json wire shape is produced by @anthropic-ai/claude-code.
//     The workflow installs that CLI at an EXACT version (not `latest`),
//     so the producer side cannot drift overnight — a shape change now
//     arrives as a reviewable version bump. Pin this script's behavior
//     with fixture logs on your side too; both halves still need
//     re-checking when the CLI pin is bumped, because the fixtures do
//     not track the real CLI.
//
// TRUST BOUNDARY (a 2026-07-23 review finding): the log this parses is
// written by the same OS user the QA agent's Bash tool runs as, so a
// prompt-injected agent could in principle append forged events. The
// footer is therefore BEST-EFFORT OBSERVABILITY for catching accidental
// drift (the realistic failure mode, and the one that actually happened);
// the provider Console / invoice remains the integrity source for cost
// auditing. A clean footer is never proof of a clean spend.

"use strict";
const fs = require("fs");

function main() {
  const [logPath, reportPath] = process.argv.slice(2);
  if (!logPath || !reportPath) {
    console.error("usage: qa_run_telemetry.js <stream-json-log> <report-md>");
    process.exit(2); // argv misuse only — never reachable from the pinned workflow step
  }

  // No report (or an empty one): the run died before even the fallback
  // skeleton wrote. Don't create a footer-only report — the post-issue
  // job's no-artifact handling owns that path.
  let reportStat;
  try { reportStat = fs.statSync(reportPath); } catch { reportStat = null; }
  if (!reportStat || reportStat.size === 0) {
    console.log("report absent or empty; skipping telemetry footer");
    return;
  }

  let model = "unknown";
  let sawResult = false;
  let footer = "";
  try {
    const log = fs.readFileSync(logPath, "utf8");
    for (const raw of log.split("\n")) {
      const line = raw.trim();
      if (!line) continue;
      let ev;
      try { ev = JSON.parse(line); } catch { continue; } // tolerate junk lines
      if (ev.type === "system" && ev.subtype === "init" && ev.model && model === "unknown") {
        model = ev.model;
      }
      if (ev.type === "result") {
        sawResult = true;
        if (typeof ev.total_cost_usd === "number") {
          const mins = Math.round((ev.duration_ms || 0) / 60000);
          footer =
            "_Run telemetry: model `" + model + "` · cost $" + ev.total_cost_usd.toFixed(2) +
            " · " + (ev.num_turns ?? "?") + " turns · " + mins + " min_";
        }
      }
    }
  } catch {
    footer = "";
    sawResult = false;
    model = "unknown";
    // fall through to the no-log footer below
    if (!fs.existsSync(logPath)) {
      appendFooter(reportPath, "_Run telemetry: unavailable (no stream-json log captured)_");
      return;
    }
  }

  if (!footer) {
    footer = sawResult
      ? "_Run telemetry: run completed, but the result event carries no numeric total_cost_usd — CLI wire-shape change? inspect the claude-stdout.log artifact_"
      : "_Run telemetry: unavailable (no result event in stream-json log — run likely died mid-flight; see the claude-stdout.log artifact)_";
  }
  appendFooter(reportPath, footer);
}

function appendFooter(reportPath, footer) {
  fs.appendFileSync(reportPath, "\n---\n" + footer + "\n");
  console.log("telemetry footer appended");
}

main();
