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
import argparse
import uuid
from datetime import datetime, date, timezone

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
    pending_tools = {}
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

        if t == "response_item" and p.get("type") in ("custom_tool_call", "function_call"):
            tid = p.get("turn_id", current_turn_id or "default")
            pending_tools.setdefault(tid, []).append(p.get("input", ""))

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
            turns.setdefault(tid, []).append({"usage": p.get("usage", {}), "tools": pending_tools.pop(tid, [])})

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
    total_cache = sum(r.get("usage", {}).get("cached_input_tokens", 0) + r.get("usage", {}).get("cache_write_input_tokens", 0) for r in target_records)
    step_usage = {}
    for record in target_records:
        step = classify_codex_step(record.get("tools", []))
        bucket = step_usage.setdefault(step, {"input": 0, "output": 0, "cache": 0, "total": 0})
        usage = record.get("usage", {})
        bucket["input"] += usage.get("input_tokens", 0)
        bucket["output"] += usage.get("output_tokens", 0)
        bucket["cache"] += usage.get("cached_input_tokens", 0) + usage.get("cache_write_input_tokens", 0)
        bucket["total"] += usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
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
        "total_tokens": total_tokens,
        "step_usage": step_usage,
        "step_attribution": "tool_call_adjacent"
    }

def classify_codex_step(tool_inputs):
    text = " ".join(value if isinstance(value, str) else json.dumps(value) for value in tool_inputs).lower()
    if "collect_work.py" in text or "collect_github.py" in text or "collect_calendar.py" in text:
        return "collect_sources"
    if "track_token_usage.py" in text and "begin" in text:
        return "start_measurement"
    if "track_token_usage.py" in text and "finalize" in text:
        return "finalize_measurement"
    return "worklog_synthesis"

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
    step_usage = {}
    for item in target_items:
        inp = item.get("input_tokens", 0)
        out = item.get("output_tokens", 0)
        cache = item.get("cache_read_tokens", 0)
        if not isinstance(inp, int) or not isinstance(out, int):
            continue
        step = classify_codex_step([json.dumps(item, ensure_ascii=False)])
        bucket = step_usage.setdefault(step, {"input": 0, "output": 0, "cache": 0, "total": 0})
        bucket["input"] += inp
        bucket["output"] += out
        bucket["cache"] += cache if isinstance(cache, int) else 0
        bucket["total"] += inp + out

    return {
        "agent": "antigravity",
        "session_id": session_id,
        "turn_id": session_id,
        "scope": "single_run" if single_run else "full_session",
        "prompt": prompt,
        "input_tokens": total_input,
        "output_tokens": total_output,
        "cache_tokens": total_cache,
        "total_tokens": total_tokens,
        "step_usage": step_usage,
        "step_attribution": "transcript_marker"
    }

def parse_claude(items, file_path, single_run):
    session_id = os.path.splitext(os.path.basename(file_path))[0]
    target_items = items
    prompt = "Full session"

    if single_run:
        last_user_idx = -1
        for i, it in enumerate(items):
            if it.get("type") == "user" or it.get("role") == "user":
                msg_content = it.get("message", {}).get("content", "") or it.get("content", "")
                if isinstance(msg_content, list):
                    text_parts = [block.get("text", "") for block in msg_content if isinstance(block, dict) and block.get("type") == "text"]
                    prompt_text = " ".join(text_parts).strip()
                else:
                    prompt_text = str(msg_content).strip()
                # Claude transcripts also encode tool results as user messages; only a human text prompt starts a run.
                if prompt_text:
                    last_user_idx = i
                    prompt = prompt_text[:120]
        if last_user_idx != -1:
            target_items = items[last_user_idx:]

    total_input = 0
    total_output = 0
    total_cache = 0
    step_usage = {}

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
            content = it.get("message", {}).get("content", [])
            tool_inputs = []
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tool_inputs.append(json.dumps({"name": block.get("name"), "input": block.get("input", {})}, ensure_ascii=False))
            step = classify_codex_step(tool_inputs)
            bucket = step_usage.setdefault(step, {"input": 0, "output": 0, "cache": 0, "total": 0})
            bucket["input"] += inp
            bucket["output"] += out
            bucket["cache"] += cache_r + cache_c
            bucket["total"] += inp + out

    return {
        "agent": "claude",
        "session_id": session_id,
        "turn_id": session_id,
        "scope": "single_run" if single_run else "full_session",
        "prompt": prompt,
        "input_tokens": total_input,
        "output_tokens": total_output,
        "cache_tokens": total_cache,
        "total_tokens": total_input + total_output,
        "step_usage": step_usage,
        "step_attribution": "tool_call_adjacent"
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
        if new_record.get("run_id") and r.get("run_id") == new_record["run_id"]:
            match_index = idx
            break
        if r.get("session_id") == new_record["session_id"] and r.get("turn_id") == new_record.get("turn_id") and r.get("scope") == new_record["scope"]:
            match_index = idx
            break

    if match_index != -1:
        records[match_index] = new_record
    else:
        records.append(new_record)

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)

def run_state_path(run_id):
    return os.path.join(".timesheet-token-runs", f"{run_id}.json")

def begin_run(run_id=None, prompt=""):
    """Mark the beginning of a worklog request; usage is collected when finalized."""
    run_id = run_id or str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    state = {
        "run_id": run_id,
        "started_at": started_at.isoformat(),
        "prompt": prompt[:240],
        "transcripts_before": find_session_transcripts()
    }
    path = run_state_path(run_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as state_file:
        json.dump(state, state_file, indent=2, ensure_ascii=False)
    return state

def finalize_run(run_id, output_path=DEFAULT_JSON_PATH, transcript_paths=None, ended_at=None):
    """Aggregate the latest prompt-to-response turn from every active agent transcript."""
    state_path = run_state_path(run_id)
    with open(state_path, "r", encoding="utf-8") as state_file:
        state = json.load(state_file)
    started = datetime.fromisoformat(state["started_at"])
    ended = ended_at or datetime.now(timezone.utc)
    explicit_paths = list(transcript_paths or [])
    candidates = explicit_paths or find_session_transcripts()
    before = set(state.get("transcripts_before", []))
    active_paths = []
    for path in candidates:
        if not os.path.isfile(path):
            continue
        # Explicit paths are authoritative. Otherwise include files created or updated during this run.
        if explicit_paths or path not in before or datetime.fromtimestamp(os.path.getmtime(path), timezone.utc) >= started:
            active_paths.append(path)

    components = []
    seen = set()
    for path in active_paths:
        stats = detect_and_parse_transcript(path, single_run=True)
        if not stats:
            continue
        identity = (stats.get("agent"), stats.get("session_id"))
        if identity in seen:
            continue
        seen.add(identity)
        components.append({
            "agent": stats["agent"],
            "session_id": stats["session_id"],
            "turn_id": stats.get("turn_id"),
            "input": stats["input_tokens"],
            "output": stats["output_tokens"],
            "cache": stats["cache_tokens"],
            "total": stats["total_tokens"],
            "steps": stats.get("step_usage", {}),
            "step_attribution": stats.get("step_attribution", "unavailable"),
            "transcript_path": os.path.abspath(path)
        })

    total_input = sum(item["input"] for item in components)
    total_output = sum(item["output"] for item in components)
    total_cache = sum(item["cache"] for item in components)
    step_usage = {}
    for component in components:
        for step, metrics in component["steps"].items():
            bucket = step_usage.setdefault(step, {"input": 0, "output": 0, "cache": 0, "total": 0})
            for metric in bucket:
                bucket[metric] += metrics.get(metric, 0)
    highest_cost_step = max(step_usage, key=lambda step: step_usage[step]["total"]) if step_usage else None
    agents = sorted(set(item["agent"] for item in components))
    record = {
        "date": ended.astimezone().strftime("%Y-%m-%d"),
        "timestamp": ended.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
        "scope": "worklog_run",
        "run_id": run_id,
        "started_at": state["started_at"],
        "completed_at": ended.isoformat(),
        "trigger_prompt": state.get("prompt", ""),
        "agents": agents,
        "sessions": components,
        "step_usage": step_usage,
        "highest_cost_step": highest_cost_step,
        "tokens": {
            "input": total_input,
            "output": total_output,
            "cache": total_cache,
            "total": total_input + total_output
        },
        "status": "measured" if components else "transcript_unavailable"
    }
    update_json_records(output_path, record)
    os.remove(state_path)
    return record

def main():
    parser = argparse.ArgumentParser(description="Track token usage for an end-to-end worklog run.")
    subparsers = parser.add_subparsers(dest="command")
    begin = subparsers.add_parser("begin", help="Start a worklog token measurement")
    begin.add_argument("--run-id")
    begin.add_argument("--prompt", default="")
    finalize = subparsers.add_parser("finalize", help="Aggregate active agent transcripts for a run")
    finalize.add_argument("--run-id", required=True)
    finalize.add_argument("--output", default=DEFAULT_JSON_PATH)
    finalize.add_argument("--transcript", action="append", dest="transcripts", help="Explicit transcript path; repeat for agent sessions")
    args, legacy = parser.parse_known_args()

    if args.command == "begin":
        state = begin_run(args.run_id, args.prompt)
        print(json.dumps({"run_id": state["run_id"], "started_at": state["started_at"]}))
        return
    if args.command == "finalize":
        record = finalize_run(args.run_id, args.output, args.transcripts)
        print(json.dumps({"run_id": record["run_id"], "status": record["status"], "agents": record["agents"], "tokens": record["tokens"], "highest_cost_step": record["highest_cost_step"], "step_usage": record["step_usage"]}, ensure_ascii=False))
        return

    # Backward compatible one-shot mode: --single-run/--full-session [output.json] [transcript.jsonl]
    single_run = "--full-session" not in legacy
    positional = [arg for arg in legacy if not arg.startswith("--")]
    target_json = next((arg for arg in positional if arg.endswith(".json")), DEFAULT_JSON_PATH)
    specific_file = next((arg for arg in positional if arg.endswith(".jsonl")), None)
    files_to_check = [specific_file] if specific_file else find_session_transcripts()
    if not files_to_check:
        print("No transcript .jsonl files found in current workspace or standard directories.")
        return
    files_to_check.sort(key=lambda path: os.path.getmtime(path), reverse=True)
    latest_file = files_to_check[0]
    stats = detect_and_parse_transcript(latest_file, single_run=single_run)
    if not stats:
        print(f"Could not parse token metrics from {latest_file}")
        return
    now = datetime.now()
    record = {
        "date": date.today().strftime("%Y-%m-%d"), "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
        "scope": stats["scope"], "agent": stats["agent"], "session_id": stats["session_id"],
        "turn_id": stats.get("turn_id"), "trigger_prompt": stats["prompt"],
        "tokens": {"input": stats["input_tokens"], "output": stats["output_tokens"], "cache": stats["cache_tokens"], "total": stats["total_tokens"]},
        "transcript_path": latest_file
    }
    update_json_records(target_json, record)
    print(f"Logged [{stats['scope']}] token usage in [{target_json}]: {stats['total_tokens']:,} tokens ({stats['agent']}).")

if __name__ == "__main__":
    main()
