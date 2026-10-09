# Timesheet Logger

Drafts daily worklogs from Google Calendar and GitHub activity.

- Agent entry point: [AGENTS.md](AGENTS.md)
- Skill: [timesheet-logger/SKILL.md](timesheet-logger/SKILL.md)
- Canonical collection, scheduling, output, and token instructions: [timesheet-logger/workflow.md](timesheet-logger/workflow.md)

Run the workflow for a requested date. It uses GitHub Search API and Google Calendar, then previews JSON in chat. Submission to Timesheet requires explicit confirmation; see the workflow for safe authentication and overlap checks.
