# Daily Worklog Workflow

Return a draft JSON array in chat. Do not save it or submit a timesheet API request.

## Collect

Start token measurement before collection and remember the returned ID:

```bash
python3 timesheet-logger/scripts/track_token_usage.py begin --prompt "Log work for YYYY-MM-DD"
```

Collect the requested date (today if omitted), using UTC+7 / `Asia/Ho_Chi_Minh` by default:

```bash
python3 timesheet-logger/scripts/collect_work.py --date YYYY-MM-DD --timezone-offset 7 --mode search --auth
```

`--username` is optional and defaults to the authenticated GitHub account. Search API is the only GitHub activity source. The collector returns commits, PRs, PR comments/reviews, and Calendar events; commits include `related_prs` resolved by SHA. Standalone issue comments are excluded. `--auth` opens Google OAuth or GitHub CLI web login when needed. Never ask for pasted credentials. Report unavailable sources.

## Synthesize

- Use one entry per PR; never combine PRs, even when related or adjacent in time. Attach commits only to their `related_prs`.
- Describe the code change from the full commit message. Use PR title/body to clarify; mention review or merge only when evidenced. Do not replace commit content with generic “review/merge” wording. Include that PR's number and URL.
- Keep each Calendar event at its actual time. Fill 09:00–12:00 and 13:00–18:00 continuously with evidenced PR tasks and meetings; no overlaps or lunch entry. Split work intervals between PRs when boundaries are unclear, keeping each PR separate. Commit timestamps order activities but are not durations; open-to-done time is not work time. Do not invent task topics.
- Exclude standalone issues and issue-only comments. Keep descriptions concise and verifiable.

## Output

Every work item must have exactly this shape:

```json
{
  "arguments": {
    "date": "2026-10-07",
    "startTime": "15:30",
    "endTime": "17:30",
    "classification": "Gradion Intern Academy 2026",
    "description": "Implement calendar event grouping for PR #12: https://github.com/org/repo/pull/12",
    "task": "#SE",
    "billable": false
  }
}
```

Keep the collector output compact (target under 800 tokens on a normal day). Do not include diffs or raw payloads in the draft.

## Finalize tokens

After preparing the JSON, run:

```bash
python3 timesheet-logger/scripts/track_token_usage.py finalize --run-id <run_id>
```

Pass `--transcript <path>` when automatic discovery misses an agent transcript; repeat for multiple sessions and include the run ID in helper-agent prompts. The tracker counts native per-response usage once, never cumulative totals or text-based estimates. It always writes a run record; missing state/transcript/usage is `status: "unavailable"`, `tokens: null`, with a reason. Report measured/unavailable status and `highest_cost_step` only when measured.
