# Daily Timesheet Logging Workflow

This workflow guides the end-of-day timesheet compilation. Each step is tagged as either **[Script]** (deterministic execution) or **[AI]** (judgment and summarization).

---

## Token Optimization & Budget Strategy (Section 3.5 & 3.6)

- **Target Token Budget per Run:**
  - Script output payload: **< 50 tokens** (compact single-line JSON, pre-filtered commits and PRs).
  - Agent Turn count: **Strictly 2 turns** (no intermediate inspection commands or repetitive file reads).
  - Target input tokens: **~50k tokens** (down from >135k tokens in unoptimized runs, saving >60% tokens).
- **Optimization Rules:**
  1. **Zero Unnecessary File Reads:** Agent instructions are embedded directly in `AGENTS.md`. Agents must NOT execute `cat workflow.md` during execution.
  2. **Deterministic Pre-filtering [Script]:** `collect_work.py` strips all raw git diffs, author redundancies, and verbose headers, returning only essential identifiers (`hash`, `time`, `msg`, `repo`, `prs`).
  3. **Atomic Execution:** File writing and token logging are chained in a single bash command in Turn 2.
  4. **Zero Verification Calls:** No post-save inspection (`cat timesheet-entries.json` or `ls -l`), which waste an entire model context turn (~27k input tokens each).

---

## Daily Steps

### Turn 1 — [Script] Data Collection
Execute the deterministic collector script:
```bash
python3 timesheet-logger/scripts/collect_work.py
```
- Fetches all commits authored today across active repos (`git log --since=midnight`).
- Queries `gh search prs` or `gh pr list` for PRs opened, merged, or reviewed today.
- Outputs a minimal, compact JSON structure (< 50 tokens).

### Turn 2 — [AI] Synthesis & [Script] Atomic Persistence
The model receives the compact JSON from Turn 1 and applies human-like judgment:
1. **[AI] Grouping & Synthesis:**
   - **Time-block Partitioning:** Maps time spans into realistic working blocks (e.g. 09:00 - 12:00, 13:30 - 17:30).
   - **Topic Grouping:** Synthesizes scattered commit messages into coherent engineering topics (e.g., "Refactored timesheet collector and packaged skill deliverable").
   - **Inline PR Tagging:** Formats related PR references inline at the end (e.g., `PRs: #1, #2`).
   - **Source Attribution:** Appends verified sources (`Sources: repo:intern-academy-attachments, commits: 68bd854`).

2. **[Script] Atomic Persistence & Token Tracking:**
   Write the payload directly to `timesheet-entries.json` and immediately run token tracking in a **single command**:
   ```bash
   cat << 'EOF' > timesheet-entries.json
   [
     {
       "date": "YYYY-MM-DD",
       "start_time": "HH:MM",
       "end_time": "HH:MM",
       "project": "Personal Pilot Timesheet",
       "description": "<Grouped Topics Summary>. PRs: <#list>. Sources: <repo/commits>"
     }
   ]
   EOF
   python3 timesheet-logger/scripts/track_token_usage.py
   ```

3. **User Confirmation:**
   Output the concise final timesheet summary directly to the user. **No further tool calls or inspections.**

---

## Output Payload Schema

```json
{
  "date": "YYYY-MM-DD",
  "start_time": "HH:MM",
  "end_time": "HH:MM",
  "project": "Personal Pilot Timesheet",
  "description": "<Grouped Topics Summary>. PRs: <#list>. Sources: <repos/commits>"
}
```
