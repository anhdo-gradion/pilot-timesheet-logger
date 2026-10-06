# Assignment 1 — Personal Pilot Timesheet Logger

**For:** Intern (individual assignment)
**Supervisor reviews this document too.** Use it to check progress and to run the Friday reviews.

**Duration:** Week 1 to Week 4 (build in Week 1, then use every day)
**Workspace:** the `gradion-workspace` repo (ask your Supervisor for the clone URL/location if you do not have it yet)
**Real stakeholder:** Track Leader (Tin)

---

## Teaching Intent

The timesheet logger is only the exercise. These are the skills this assignment really trains, and what your Supervisor looks for beyond "does it run":

- **First-principles thinking.** There is no template for the [Script] vs [AI] split (3.5). For each step you must decide what that step really needs: a fixed rule, or real judgment. Breaking a problem down instead of sending everything to the AI is a Gradion first principle. It applies to all your work, not only to this assignment.
- **Continuous improvement.** You repeat the loop log → review what broke → improve → log again (3.5) every day for a month, and your Supervisor can see every step. Your skill at the end of Week 4 must be clearly better than the version you wrote in Week 1.
- **Data-driven culture.** 3.6 exists so you can prove "I made it better" with your own tokens/day numbers instead of an opinion. At Gradion, every decision and every demo must show evidence. This assignment is your first practice of that habit.

When your Supervisor grades this work, what you learned and what you changed count at least as much as whether the tool logged every single day.

---

## 1. Background

You must record your daily work during the internship. A manual log is easy to forget and easy to fake. This assignment asks you to build a small tool that logs your work for you, from real sources: your git commits and your Google Calendar events.

This is your first Gradion Workspace project. Treat it like a real product, not a school exercise.

## 2. Objective

Build a personal pilot timesheet logger, packaged as a **Skill (`SKILL.md`) with a `workflow.md`** that Claude can read and run every day. The tool must:

- Read your git commit history, and your pull requests (opened, reviewed, merged).
- Read your Google Calendar events (meetings, blocks).
- Combine both into multiple daily log entries, grouped by topic and time block.
- Split the work correctly between **script** (deterministic — the same input always gives the same output) and **AI** (judgment). See 3.5. This split is the core design skill this assignment tests, not just "does it log."

## 3. Requirements

### 3.1 Data sources

- **Git history:** Pull commits you authored, across the repos you work in, for the current day.
- **Pull requests:** Pull PRs you opened, reviewed, or that merged today (title, repo, status — opened/reviewed/merged, link). Use the GitHub or GitLab API or CLI (`gh` for GitHub, `glab` for GitLab), whichever this workspace's repos use.
- **Google Calendar:** Pull your events for the current day (title, start time, end time, attendees optional).

### 3.2 Output

- Multiple entries per day, one per time block (matching your calendar blocks — e.g. a morning meeting block, an afternoon work block), not one giant entry for the whole day.
- Each entry groups its content **by topic, not by raw event**: if a block covers several small tasks, combine them into one short topic list (e.g. "CSV import, deals board totals, forecast, and frontend contract fixes"). Do not list every small action on its own line.
- Each entry lists its **PRs together, inline**, tagged clearly (e.g. `PRs: #1182, #1174, #1170`) — one PR reference list per entry, not scattered across the description.
- Each entry must show at least: date, start time, end time, grouped topic summary, PR list, and the source data (repo, commit messages, calendar event title) so anyone can check where the entry came from.
- Use the same fields as the "Edit entry" panel of a real timesheet: `Date`, `Start time`, `End time`, and a short `Description`. Put the grouped topics in `Description`, and the `PRs:` list at the end of it. See the reference screenshot in Section 9.
- If a day has no calendar events, still write one entry for that day, built from your commits and PRs.
- Store entries somewhere you can review later (a file, a database, a doc — your choice, but pick one and be consistent).

### 3.3 Automation

- A human-triggered run is fine (for example, you ask Claude to "log today's work" once a day). A schedule (cron, or a calendar-based trigger) is a bonus, not a requirement.
- If a run fails and you run it again, that still counts as one day. Do not create a second entry for the same time block.
- Connect to git, your PR host (GitHub/GitLab), and Google Calendar through MCP, Skills, or a CLI. You can be the trigger, but the tool must pull and assemble the data. Re-typing or copying the data by hand is not acceptable.

### 3.4 Git discipline

Applies to every task from now on. The logger reads your commits and PRs directly, so poor git habits produce a poor log.

- One task = one small branch = one focused PR. No bundling unrelated changes.
- PR has a clear title, a real description (what/why/how tested), and no direct commits to `main`.
- Commit messages state what changed and why — no "wip" / "update".

### 3.5 Skill / workflow packaging (required shape of the deliverable)

The output of this assignment is not "a script" and not "a chat prompt." It is a **Skill folder** that Claude Code can invoke on its own every day:

```
your-skill-folder/
├── SKILL.md        # what the skill is, when Claude should use it, how to invoke it
└── workflow.md      # the actual daily procedure — step by step
```

- `SKILL.md` describes the skill so Claude picks it up correctly (name, one-line description, trigger — e.g. "run once per day" or "run when asked to log today's work").
- `workflow.md` is the daily procedure. Write it as an ordered list of steps, each step tagged with who does it:
  - **[Script]** steps: fixed, repeatable, no judgment involved (pull git commits, pull calendar events, format them into a draft entry, write to the log file). These must be real scripts (shell/Python/Node/Go — your choice), not the AI re-deriving the same output from scratch each day.
  - **[AI]** steps: anything that needs judgment (deciding whether an ambiguous calendar event counts as work, writing a one-line human-readable summary of the day, flagging a day that looks incomplete). Keep this list short — the fewer judgment calls, the fewer tokens and the more consistent the output.

**Principle: deterministic work is a script; only real decisions go to the AI.** Before you write `workflow.md`, list every step you plan to include and mark each one [Script] or [AI]. If a step is marked [AI] but has one obviously correct answer every time, it's actually a [Script] step — move it.

**Token budget:** the [AI] step must receive the smallest possible input: the structured output of your script, for example a short list of commits and events. Never send the full `git log` output, the full calendar JSON, or logs from earlier days. State your target token budget for one day in `workflow.md` (a rough number is fine), and explain briefly how you kept the [AI] input that small.

**Why this matters:** your Supervisor reads these entries for weeks. A skill that is fast, cheap, and produces a clean, readable entry is easy to judge. If your skill sends a large prompt every day only to reformat data that a script could format, that is a design mistake, even when the output looks correct. Explain any such case in your Friday review.

**Note — the first days will not be perfect, and that is expected.** Your first entries may miss edge cases, classify an event wrongly, or need a manual fix. This is normal, not a failure. Each day, write down in a short log what went wrong. Use that log to improve `workflow.md`: make the [Script] steps stricter, reduce what the [AI] step has to decide, or add a rule that removes a judgment call. The expected pattern is **log → review what broke → improve the skill → log again**. Each week the skill should be more accurate and cheaper, with fewer or smaller [AI] steps. Bring this improvement log to your Friday reviews. Steady improvement raises your score; a skill that never changed does not.

### 3.6 Measurement — prove the optimization, don't just claim it

3.5 asks you to keep the [AI] step cheap. This section asks you to measure it, so you report a number instead of an opinion.

- Capture your Claude Code session id and the session's JSONL transcript from `~/.claude` (each session is logged locally as a `.jsonl` file — find the ones that correspond to your skill's daily runs).
- From those JSONL files, pull the token usage for each run (input/output/cache tokens are all in the transcript's usage records).
- Add these up into a **daily total: tokens used per day** running the skill. If you ran the skill more than once that day, add all runs together. Keep this next to your log entries (a simple CSV or table is enough), including the session id for each run.
- In your Week 4 final presentation, include a short stats section. Show tokens/day over time in a table or chart. Then pick one high-token day and one lower-token day, and explain what you changed in `workflow.md` between them — for example a narrower [AI] step, a smaller script summary, or a judgment call you removed.
- This is your evidence for the "log → review → improve" claim in 3.5. A chart that goes down, with each drop labelled with the change that caused it, is stronger evidence than any written description.

## 4. Constraints

- Must log real work starting **Tue, Week 1**. Every working day after that, not a one-time report.
- Group research days (Assignment 2 or 3) are working days too — they must be logged like any other day.
- Must use git (for the tool's own code) and Google Calendar (as a data source).

## 5. Timeline

| When | Milestone |
| --- | --- |
| Tue 6 Oct | Kickoff. Start logging from today, even with a rough version. |
| Fri 9 Oct | Tool finished and demoed live. |
| Fri 30 Oct | Final check by Track Leader: logged every working day, tokens/day stats and what you optimized (3.6). |

## 6. Definition of Done

- [ ] Deliverable is a Skill folder with `SKILL.md` + `workflow.md` that Claude can run once a day (scheduled or human-triggered, either is fine).
- [ ] `workflow.md` marks every step **[Script]** or **[AI]**, and a real script performs every [Script] step — the AI does not reproduce the same output.
- [ ] Tool pulls commits, pull requests, and calendar events automatically, no manual copy-paste.
- [ ] Entries match the shape in 3.2: one entry per time block, content grouped by topic, one inline `PRs:` list, and the source data kept for checking.
- [ ] Git discipline from 3.4 followed: one task per branch, one focused PR with a real description, no direct commits to `main`.
- [ ] A log entry exists for every working day from Tue, Week 1 onward. Weekends, public holidays, and approved leave are not gaps; any other missing day must be explained in the improvement log.
- [ ] Code lives in git, with a commit history that shows real incremental work.
- [ ] You can explain, in the Friday review, how it works end to end, and justify why each [AI] step needed judgment rather than a fixed rule.
- [ ] `workflow.md` states a target token budget for one day and explains how the [AI] input was kept small.
- [ ] You capture daily token usage from your `~/.claude` session JSONL files and track it over time, and the Week 4 presentation shows a tokens/day stat tied to specific `workflow.md` changes.

## 7. Out of Scope

- A polished UI. A plain file or simple list view is fine.
- Logging teammates' work. This is a personal tool.
- Perfect classification of work type (meeting vs coding vs research). A rough label is enough — but the topic grouping required in 3.2 still applies.
- Using the [AI] step for work a script can do with a fixed rule, for example parsing a fixed JSON shape or adding up numbers again. That is a design failure, not a feature.

## 8. Review Points

- **Fri, Week 1:** Live demo. Tool must be working today.
- **Fri 30 Oct (Week 4):** Final check. Track Leader confirms the log has run every day without gaps, and reviews your tokens/day statistics and optimization story.

## 9. References

- Gradion Workspace Skills / MCP — see also Assignment 2, which researches the MCP standard in depth.
- Google Calendar API docs (for the calendar connector you build or reuse).
- Reference screenshot of the timesheet "Edit entry" panel — **[Track Leader: add the file path or link here before kickoff]**.
