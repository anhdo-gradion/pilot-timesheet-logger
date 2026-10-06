# Agent Context & Skills Guide

## Available Skills & Workflows

### 1. Timesheet Logger
- **Purpose:** Automatically records and summarizes engineering work for the day into `timesheet-entries.json` and logs single-run token consumption into `token-usage.json`.
- **Trigger:** When asked to "log today's work", "log work hôm nay", or "timesheet".
- **Token Optimization (CRITICAL):**
  - **DO NOT** run `cat workflow.md` (instructions and schema are already here).
  - **DO NOT** run verification commands (`ls`, `cat`) after writing files or running scripts.
  - Complete the task in **strictly 2 turns**:

**Turn 1 [Script Data Collection]:**
Run the collector script:
```bash
python3 timesheet-logger/scripts/collect_work.py
```

**Turn 2 [AI Synthesis & Atomic Save]:**
1. Read the compact JSON output from Turn 1. Apply [AI] judgment to group commits by topic, format inline `PRs: #...`, and construct the timesheet payload matching:
   ```json
   [
     {
       "date": "YYYY-MM-DD",
       "start_time": "HH:MM",
       "end_time": "HH:MM",
       "project": "Personal Pilot Timesheet",
       "description": "<Grouped Topics Summary>. PRs: <#list>. Sources: <repo/commits>"
     }
   ]
   ```
2. Save the payload to `timesheet-entries.json` AND run token tracker in **ONE single command**:
   ```bash
   cat << 'EOF' > timesheet-entries.json
   <FORMATTED_JSON>
   EOF
   python3 timesheet-logger/scripts/track_token_usage.py
   ```
3. Report the final timesheet summary directly to the user. Do not call any further tools.
- **Safety Rule:** NEVER call external server APIs without explicit user instruction.
