# Operator Desk

A one-page admin dashboard for the operator: what is waiting on you across
Claude Code sessions, project reminders and morning-qa runs. Items are
grouped by session (a Claude Code session, a reminder project, or a QA run),
ordered by priority, and move to a collapsible archive when done.

It is published as a private claude.ai Artifact, not served from this repo.
This file is the source of record.

## Where each section's data comes from

| Section | Source | Refresh |
|---|---|---|
| Claude Code sessions | The viewer's built-in **Claude Code Remote** connector (`list_sessions`), called live from the page as the signed-in owner | **Refresh** button, and on returning to the tab after 10 min |
| Project reminders | The artifact's own database, collection `reminders` | Live |
| QA runs | The artifact's database, collection `qa_items`, written by Claude (`ArtifactData`) or a routine | Live |

There is no supported way for a standalone script to list Claude Code cloud
sessions (the CLI only offers an interactive picker), and the page cannot
fetch GitHub directly, which is why sessions go through the connector and QA
items go through the database.

## Priority

| | Claude Code session | Reminder | QA item |
|---|---|---|---|
| P1 | Blocked, waiting on you | Set by hand | Triage a Critical |
| P2 | Last turn failed, or its summary names an action for you | Overdue or due today | Re-dispatch, verify an `[UNVERIFIED]` Critical |
| P3 | Ready for review | Due within 7 days | Verdict owed, ledger sign-off |
| P4 | Finished but not archived (last 14 days) | Due later | Reminder-grade watch items |

## Retiring items

* **Claude Code**: archiving the session (from the page or claude.ai) retires
  it. **Mark done** hides it until the session has new activity.
* **Reminders and QA items**: **Mark done** moves them to the archive, from
  which **Restore** puts them back.

## Data access

The database rules make everything readable and writable by the artifact's
owner only. A shared viewer sees an "owner only" notice, and connector calls
always run as the person viewing, never as the owner.

## `qa_items` document shape

```json
{
  "sessionId": "212",
  "sessionTitle": "2026-10-07 morning report",
  "sessionUrl": "https://github.com/<owner>/<repo>/issues/212",
  "sessionRef": "#212",
  "sessionLabel": "🔴 Critical · pinned",
  "sessionLabelKind": "crit",
  "sessionMeta": "17/17 checks · 21 min · $3.18",
  "p": 1,
  "kind": "Triage Critical",
  "title": "Check 9: listing-sync zero yield, 3rd day",
  "detail": "Heartbeat green, 0 rows written since 10-04.",
  "openedAt": "2026-10-07T11:52:00Z"
}
```

`sessionLabelKind` is one of `crit`, `inc`, `retro`, `clean`.
