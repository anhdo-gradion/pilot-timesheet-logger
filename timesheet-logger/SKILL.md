---
name: timesheet-logger
description: Automates daily work logging by collecting Git commits, GitHub PRs, and Calendar events, then summarizing them into structured timesheet payloads.
---

# Personal Pilot Timesheet Logger Skill

## Overview

This skill automates the extraction and summarization of daily engineering work into structured timesheet log entries following the Gradion Timesheet format.

- **Trigger:** Run when asked to "log today's work", "review today's log", or as an end-of-day automation.
- **Deliverables:** Generates structured JSON payloads matching the timesheet submission schema, ready for manual confirmation or API dispatch.

## Principles

1. **Deterministic [Script] vs Judgment [AI] Split:**
   - Raw data retrieval (Git history, GitHub PRs, Calendar) is handled 100% deterministically by local scripts.
   - LLM is only invoked for high-level judgment: grouping related tasks into topics, synthesizing multi-task summaries, and selecting time blocks.
2. **Minimal Token Budget:**
   - Raw logs (full diffs, tree objects, verbose JSON) are never passed to the AI.
   - The script compacts the daily footprint into < 300 tokens before prompting the LLM.
3. **Safety First:**
   - Mutations (writing to timesheet API) require user review of the finalized payload. No silent remote calls.

