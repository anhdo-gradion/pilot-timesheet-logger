# Worklog

1. **Start token tracking and run the script to collect and build.** Do not inspect source code, README, credentials, or tokens.

   ```bash
   python3 timesheet-logger/scripts/track_token_usage.py begin --prompt "<user_prompt>"
   python3 timesheet-logger/scripts/run_worklog.py --date YYYY-MM-DD --work-period <period> --draft-only
   python3 timesheet-logger/scripts/track_token_usage.py finalize --run-id <run_id>
   ```

   Omit `--date` for today. Use `morning` for 09:00–12:00, `afternoon` for 13:00–18:00, and `full` only when the user asks for a full day or does not specify a period. A partial-day draft includes only PRs with commit, review, or comment activity inside that period; do not pull later activity into an earlier draft. Cap each PR at four hours total for that date. Leave excess time unassigned if no other evidenced task can take it.

   The collector makes outbound HTTPS requests to `api.github.com`, `www.googleapis.com`, and `oauth2.googleapis.com`. Allow network access for the draft command when the execution environment asks. If `gh auth status` succeeds but the collector reports a connection error, treat it as a network failure and retry after enabling access; do not ask the user to repeat GitHub login for that error. Token tracking finalizes immediately after drafting (measuring prompt-to-draft only).

2. **Show the result.** Visualize `timesheet-entries.json` as a calendar.

3. **Wait for explicit approval in chat.** Approval is only for uploading this exact draft.

4. **After approval, push it.**

   ```bash
   python3 timesheet-logger/scripts/push_worklog.py submit --input timesheet-entries.json --confirm
   ```

Never upload before approval. Do not check or read user source code during this flow.

If GitHub login fails because `gh` is missing, install GitHub CLI once (`brew install gh` on macOS) and rerun. The script then opens browser login automatically. Never ask for a GitHub token in chat.
