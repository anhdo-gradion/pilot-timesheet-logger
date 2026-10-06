#!/usr/bin/env python3
"""
[Script Step] track_token_usage.py
Automatically tracks token usage for a SINGLE execution of the timesheet logging skill
(from the user's prompt triggering the run to completion) across Codex, Claude Code, and Antigravity.
Outputs structured metrics to token-usage.json.
"""

import os
import sys
import json
import glob
from datetime import datetime, date

DEFAULT_JSON_PATH = "token-usage.json"

def scan_files_safely(base_dir, pattern="*.jsonl"):
    """Recursively scans directory for files matching pattern, silently handling permission errors."""
    matches = []
    if not os.path.exists(base_dir):
        return matches
    try:
        for root, dirs, files in os.walk(base_dir, onerror=lambda e: None):
            for f in files:
                if f.endswith(".jsonl"):
                    matches.append(os.path.join(root, f))
    except Exception:
        pass
    return matches

def find_session_transcripts():
    """
    Finds transcript .jsonl files across supported agent systems:
    1. Local workspace (rollout-*.jsonl, transcript.jsonl)
    2. Codex CLI / Desktop (~/.codex/sessions/**/rollout-*.jsonl)
    3. Claude Code (~/.claude/**/*.jsonl)
    4. Antigravity conversation logs
    """
    home = os.path.expanduser("~")
    candidates = []

    # 1. Local workspace files (e.g. copied rollout logs or local runs)
    cwd = os.getcwd()
    for f in glob.glob(os.path.join(cwd, "rollout-*.jsonl")):
        candidates.append(f)
    for f in glob.glob(os.path.join(cwd, "*transcript*.jsonl")):
        candidates.append(f)

    # 2. Codex CLI & Desktop sessions (~/.codex/sessions/YYYY-MM/DD/rollout-*.jsonl)
    codex_paths = [
        os.path.join(home, ".codex", "sessions"),
        os.path.join(home, ".codex"),
        os.path.join(home, "Library", "Application Support", "Codex", "sessions")
    ]
    for c_dir in codex_paths:
        for f in scan_files_safely(c_dir):
            if "rollout-" in os.path.basename(f) and f not in candidates:
                candidates.append(f)

    # 3. Claude Code sessions (~/.claude/**/*.jsonl)
    claude_base = os.path.join(home, ".claude")
    for f in scan_files_safely(claude_base):
        if f not in candidates:
            candidates.append(f)

    # 4. Antigravity conversation logs
    env_brain = os.environ.get("ANTIGRAVITY_BRAIN_DIR")
    if env_brain:
        agy_log = os.path.join(env_brain, ".system_generated", "logs", "transcript.jsonl")
        if os.path.exists(agy_log) and agy_log not in candidates:
            candidates.append(agy_log)
    else:
        # Search ~/.gemini/antigravity/brain
        agy_base = os.path.join(home, ".gemini", "antigravity", "brain")
        for f in scan_files_safely(agy_base):
            if f.endswith("transcript.jsonl") and f not in candidates:
                candidates.append(f)

    return candidates

def detect_and_parse_transcript(file_path, single_run=True):
    """
    Detects format (Codex, Claude Code, Antigravity) and computes token consumption.
    If single_run=True, isolates tokens for the latest user turn/request.
    If single_run=False, calculates tokens for the entire session.
    """
    if not os.path.exists(file_path):
        return None

    items = []
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        items.append(json.loads(line))
                    except Exception:
                        continue
    except Exception as e:
        print(f"Error reading {file_path}: {e}", file=sys.stderr)
        return None

    if not items:
        return None

    # Detect format
    format_type = "claude"
    for item in items[:30]:
        t = item.get("type")
        if t in ("token_usage_record", "session_meta", "turn_context"):
            format_type = "codex"
            break
        if t in ("USER_INPUT", "PLANNER_RESPONSE"):
            format_type = "antigravity"
            break
        if t in ("user", "assistant") and ("message" in item or "usage" in item):
            format_type = "claude"
            break

    # Parse based on detected format
    if format_type == "codex":
        return parse_codex(items, file_path, single_run)
    elif format_type == "antigravity":
        return parse_antigravity(items, file_path, single_run)
    else:
        return parse_claude(items, file_path, single_run)

def parse_codex(items, file_path, single_run):
    turns = {}
    current_turn_id = None
    user_prompts = {}
    session_id = os.path.splitext(os.path.basename(file_path))[0]

    for item in items:
        t = item.get("type")
        p = item.get("payload", {})
        if not isinstance(p, dict):
            continue

        if t == "session_meta" and p.get("session_id"):
            session_id = p.get("session_id")

        if t == "turn_context" and p.get("turn_id"):
            current_turn_id = p.get("turn_id")

        if t == "response_item" and p.get("role") == "user":
            content = p.get("content", [])
            text = ""
            if isinstance(content, list):
                for c in content:
                    txt = c.get("text", "") if isinstance(c, dict) else str(c)
                    if not txt.startswith(("# AGENTS.md", "<environment_context>", "<external_codex_apps", "<app-context>", "<codex_apps", "<multi_agent")):
                        text += txt
            elif isinstance(content, str):
                text = content
            text = text.strip()
            if text:
                tid = current_turn_id or f"turn_{len(user_prompts)}"
                user_prompts[tid] = text

        if t == "token_usage_record":
            tid = p.get("turn_id", current_turn_id or "default")
            if tid not in turns:
                turns[tid] = []
            turns[tid].append(p)

    if not turns:
        return None

    latest_turn_id = list(turns.keys())[-1]
    if single_run:
        target_records = turns[latest_turn_id]
        prompt = user_prompts.get(latest_turn_id, "Run timesheet workflow")
        turn_scope_id = latest_turn_id
    else:
        target_records = [r for r_list in turns.values() for r in r_list]
        prompt = "Full session"
        turn_scope_id = session_id

    total_input = sum(r.get("usage", {}).get("input_tokens", 0) for r in target_records)
    total_output = sum(r.get("usage", {}).get("output_tokens", 0) for r in target_records)
    total_cache = sum(r.get("usage", {}).get("cached_input_tokens", 0) for r in target_records)
    total_tokens = total_input + total_output

    return {
        "agent": "codex",
        "session_id": session_id,
        "turn_id": turn_scope_id,
        "scope": "single_run" if single_run else "full_session",
        "prompt": prompt,
        "input_tokens": total_input,
        "output_tokens": total_output,
        "cache_tokens": total_cache,
        "total_tokens": total_tokens
    }

def parse_antigravity(items, file_path, single_run):
    session_id = os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(file_path))))
    target_items = items
    prompt = "Full session"

    if single_run:
        last_user_idx = -1
        for i, it in enumerate(items):
            if it.get("type") == "USER_INPUT":
                last_user_idx = i
                prompt = str(it.get("content", ""))[:120].strip()
        if last_user_idx != -1:
            target_items = items[last_user_idx:]

    total_input = sum(it.get("input_tokens", 0) for it in target_items if isinstance(it.get("input_tokens"), int))
    total_output = sum(it.get("output_tokens", 0) for it in target_items if isinstance(it.get("output_tokens"), int))
    total_cache = sum(it.get("cache_read_tokens", 0) for it in target_items if isinstance(it.get("cache_read_tokens"), int))
    total_tokens = total_input + total_output

    return {
        "agent": "antigravity",
        "session_id": session_id,
        "turn_id": session_id,
        "scope": "single_run" if single_run else "full_session",
        "prompt": prompt,
        "input_tokens": total_input,
        "output_tokens": total_output,
        "cache_tokens": total_cache,
        "total_tokens": total_tokens
    }

def parse_claude(items, file_path, single_run):
    session_id = os.path.splitext(os.path.basename(file_path))[0]
    target_items = items
    prompt = "Full session"

    if single_run:
        last_user_idx = -1
        for i, it in enumerate(items):
            if it.get("type") == "user" or it.get("role") == "user":
                last_user_idx = i
                msg_content = it.get("message", {}).get("content", "") or it.get("content", "")
                prompt = str(msg_content)[:120].strip()
        if last_user_idx != -1:
            target_items = items[last_user_idx:]

    total_input = 0
    total_output = 0
    total_cache = 0

    for it in target_items:
        usage = it.get("usage") or it.get("message", {}).get("usage")
        if isinstance(usage, dict):
            inp = usage.get("input_tokens", 0) or usage.get("prompt_tokens", 0)
            out = usage.get("output_tokens", 0) or usage.get("completion_tokens", 0)
            cache_r = usage.get("cache_read_input_tokens", 0) or usage.get("prompt_tokens_details", {}).get("cached_tokens", 0)
            cache_c = usage.get("cache_creation_input_tokens", 0)

            total_input += inp
            total_output += out
            total_cache += (cache_r + cache_c)

    return {
        "agent": "claude",
        "session_id": session_id,
        "turn_id": session_id,
        "scope": "single_run" if single_run else "full_session",
        "prompt": prompt,
        "input_tokens": total_input,
        "output_tokens": total_output,
        "cache_tokens": total_cache,
        "total_tokens": total_input + total_output
    }

def update_json_records(json_path, new_record):
    """Appends or updates run metrics in token-usage.json."""
    records = []
    if os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    loaded = json.loads(content)
                    if isinstance(loaded, list):
                        records = loaded
        except Exception:
            records = []

    # Check if record for same turn/session already exists; if so, update it
    match_index = -1
    for idx, r in enumerate(records):
        if r.get("session_id") == new_record["session_id"] and r.get("turn_id") == new_record.get("turn_id") and r.get("scope") == new_record["scope"]:
            match_index = idx
            break

    if match_index != -1:
        records[match_index] = new_record
    else:
        records.append(new_record)

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)

def main():
    args = sys.argv[1:]
    single_run = True
    target_json = DEFAULT_JSON_PATH
    specific_file = None

    for arg in args:
        if arg == "--full-session":
            single_run = False
        elif arg == "--single-run":
            single_run = True
        elif arg.endswith(".json"):
            target_json = arg
        elif arg.endswith(".jsonl"):
            specific_file = arg

    if specific_file:
        files_to_check = [specific_file]
    else:
        files_to_check = find_session_transcripts()

    if not files_to_check:
        print("No transcript .jsonl files found in current workspace or standard directories.")
        return

    # Sort files by modification time (most recent first)
    files_to_check.sort(key=lambda x: os.path.getmtime(x), reverse=True)
    latest_file = files_to_check[0]

    stats = detect_and_parse_transcript(latest_file, single_run=single_run)
    if not stats:
        print(f"Could not parse token metrics from {latest_file}")
        return

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    today_str = date.today().strftime("%Y-%m-%d")

    record = {
        "date": today_str,
        "timestamp": now_str,
        "scope": stats["scope"],
        "agent": stats["agent"],
        "session_id": stats["session_id"],
        "turn_id": stats.get("turn_id"),
        "trigger_prompt": stats["prompt"],
        "tokens": {
            "input": stats["input_tokens"],
            "output": stats["output_tokens"],
            "cache": stats["cache_tokens"],
            "total": stats["total_tokens"]
        },
        "transcript_path": latest_file
    }

    update_json_records(target_json, record)

    print(f"Logged [{stats['scope']}] token usage in [{target_json}]:")
    print(f" - Agent:         {stats['agent']}")
    print(f" - Prompt:        {stats['prompt'][:60]}...")
    print(f" - Session ID:    {stats['session_id']}")
    print(f" - Input Tokens:  {stats['input_tokens']:,}")
    print(f" - Output Tokens: {stats['output_tokens']:,}")
    print(f" - Cache Tokens:  {stats['cache_tokens']:,}")
    print(f" - Total Tokens:  {stats['total_tokens']:,}")

if __name__ == "__main__":
    main()
