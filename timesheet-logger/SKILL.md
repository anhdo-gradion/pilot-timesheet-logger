---
name: timesheet-logger
description: Draft daily worklogs from GitHub and Google Calendar, visualize them for approval, then upload only after approval.
---

# Timesheet Logger

Run `run_worklog.py --draft-only` with `--work-period morning` for morning requests, `afternoon` for afternoon requests, and `full` only for full-day or unspecified requests. Partial-day drafts include only GitHub activity within that period. Limit each PR/task to four hours per date; leave excess time unassigned unless another evidenced task can take it. Allow outbound network for GitHub and Google API collection when prompted. A successful `gh auth status` with collector connection errors indicates a network issue; do not prompt for another login. Visualize the entries, then wait for approval before pushing. Do not inspect user source code or upload before approval. See [workflow.md](workflow.md).
