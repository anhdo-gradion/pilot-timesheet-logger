#!/usr/bin/env python3
"""Measure native token usage for one worklog run across supported agent logs."""

import argparse
import glob
import json
import os
import sys
import uuid
from datetime import datetime, timezone


DEFAULT_JSON_PATH = "token-usage.json"
STATE_DIR = ".timesheet-token-runs"
METRICS = ("input", "cached_input", "uncached_input", "output", "thinking", "total")


def scan_files_safely(base_dir):
    """List JSON/JSONL files below a supported agent data directory."""
    matches = []
    if not os.path.exists(base_dir):
        return matches
    try:
        for root, _dirs, files in os.walk(base_dir, onerror=lambda _error: None):
            if "chunks" in root or "messages" in root:
                continue
            for filename in files:
                if filename == "transcript_full.jsonl":
                    continue
                if filename.endswith((".jsonl", ".json")):
                    matches.append(os.path.join(root, filename))
    except OSError:
        pass
    return matches


def find_session_transcripts():
    """Find possible local transcript paths; run markers select the actual sessions."""
    home = os.path.expanduser("~")
    paths = []
    cwd = os.getcwd()
    paths.extend(glob.glob(os.path.join(cwd, "rollout-*.jsonl")))
    paths.extend(glob.glob(os.path.join(cwd, "*transcript*.jsonl")))

    roots = (
        os.path.join(home, ".codex", "sessions"),
        os.path.join(home, "Library", "Application Support", "Codex", "sessions"),
        os.path.join(home, ".claude"),
        os.path.join(home, ".gemini", "antigravity", "brain"),
        os.path.join(home, ".gemini", "antigravity-cli", "brain"),
    )
    for root in roots:
        paths.extend(scan_files_safely(root))

    brain_dir = os.environ.get("ANTIGRAVITY_BRAIN_DIR")
    if brain_dir:
        paths.extend(scan_files_safely(brain_dir))
    return sorted(set(paths))


def transcript_snapshot():
    """Capture metadata so finalization only inspects files touched during this run."""
    snapshot = {}
    for path in find_session_transcripts():
        try:
            stat = os.stat(path)
            snapshot[path] = {"mtime_ns": stat.st_mtime_ns, "size": stat.st_size}
        except OSError:
            continue
    return snapshot


def read_json_lines(path):
    items = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as transcript:
            for line in transcript:
                line = line.strip()
                if not line:
                    continue
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError as error:
        print(f"Could not read transcript {path}: {error}", file=sys.stderr)
    return items


def detect_format(items):
    for item in items:
        if item.get("type") == "token_usage_record":
            return "codex"
        if item.get("event") == "step_update" or isinstance(item.get("step_update"), dict):
            return "antigravity"
    if any(item.get("type") in ("USER_INPUT", "PLANNER_RESPONSE") for item in items):
        return "antigravity"
    if any(item.get("type") in ("user", "assistant") or item.get("role") in ("user", "assistant") for item in items):
        return "claude"
    return "unknown"


def serialized(item):
    return json.dumps(item, ensure_ascii=False, separators=(",", ":"))


def has_marker(item, run_id):
    return run_id in serialized(item)


def empty_metrics():
    return {metric: 0 for metric in METRICS}


def normalize_metrics(input_tokens, cached_tokens, output_tokens, thinking_tokens=0):
    input_tokens = max(int(input_tokens or 0), 0)
    cached_tokens = max(min(int(cached_tokens or 0), input_tokens), 0)
    output_tokens = max(int(output_tokens or 0), 0)
    thinking_tokens = max(int(thinking_tokens or 0), 0)
    return {
        "input": input_tokens,
        "cached_input": cached_tokens,
        "uncached_input": input_tokens - cached_tokens,
        "output": output_tokens,
        "thinking": thinking_tokens,
        "total": input_tokens + output_tokens + thinking_tokens,
    }


def classify_step(tool_inputs):
    text = " ".join(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False) for value in tool_inputs).lower()
    if any(name in text for name in ("collect_work.py", "collect_github.py", "collect_calendar.py")):
        return "collect_sources"
    if "track_token_usage.py" in text and "begin" in text:
        return "start_measurement"
    if "track_token_usage.py" in text and "finalize" in text:
        return "finalize_measurement"
    return "worklog_synthesis"


def add_response(responses, response_id, step, metrics):
    responses.append({"response_id": str(response_id), "step": step, "tokens": metrics})


def sum_response_metrics(responses):
    total = empty_metrics()
    steps = {}
    for response in responses:
        metrics = response["tokens"]
        for metric in METRICS:
            total[metric] += metrics.get(metric, 0)
        bucket = steps.setdefault(response["step"], empty_metrics())
        for metric in METRICS:
            bucket[metric] += metrics.get(metric, 0)
    return total, steps


def base_component(agent, session_id, run_id, path):
    return {
        "agent": agent,
        "agent_id": None,
        "root_turn_ids": [],
        "session_id": session_id,
        "turn_ids": [],
        "run_id": run_id,
        "status": "unavailable",
        "reason": None,
        "attribution": "unavailable",
        "responses": [],
        "tokens": None,
        "step_usage": {},
        "transcript_path": os.path.abspath(path),
    }


def parse_codex(items, path, run_id):
    session_id = os.path.splitext(os.path.basename(path))[0]
    current_turn_id = None
    marker_turns = set()
    end_marker_indexes = []
    for index, item in enumerate(items):
        payload = item.get("payload", {})
        if not isinstance(payload, dict):
            payload = {}
        if item.get("type") == "session_meta":
            session_id = payload.get("session_id", session_id)
        if item.get("type") == "turn_context" and payload.get("turn_id"):
            current_turn_id = payload["turn_id"]
        turn_id = payload.get("turn_id") or current_turn_id
        if has_marker(item, run_id) and turn_id:
            marker_turns.add(str(turn_id))
            if item.get("type") == "response_item" and payload.get("type") in ("custom_tool_call", "function_call"):
                call_text = serialized(payload).lower()
                if "track_token_usage.py" in call_text and "finalize" in call_text:
                    end_marker_indexes.append(index)

    component = base_component("codex", session_id, run_id, path)
    if not marker_turns:
        component["reason"] = "run_marker_not_found"
        return component

    end_index = len(items) - 1
    if end_marker_indexes:
        finalizer_index = min(end_marker_indexes)
        end_index = finalizer_index
        for index in range(finalizer_index + 1, len(items)):
            item = items[index]
            payload = item.get("payload", {})
            if item.get("type") == "token_usage_record" and str(payload.get("turn_id", "")) in marker_turns:
                end_index = index
                break
            if item.get("type") == "response_item" and payload.get("type") == "custom_tool_call_output":
                break

    pending_tools = {}
    seen_responses = set()
    current_turn_id = None
    for index, item in enumerate(items[:end_index + 1]):
        payload = item.get("payload", {})
        if not isinstance(payload, dict):
            continue
        if item.get("type") == "turn_context" and payload.get("turn_id"):
            current_turn_id = str(payload["turn_id"])
        if item.get("type") == "response_item" and payload.get("type") in ("custom_tool_call", "function_call"):
            turn_id = str(payload.get("turn_id") or current_turn_id or "")
            if turn_id in marker_turns:
                pending_tools.setdefault(turn_id, []).append(payload.get("input", ""))
        if item.get("type") != "token_usage_record":
            continue
        turn_id = str(payload.get("turn_id") or current_turn_id or "")
        if turn_id not in marker_turns:
            continue
        response_id = payload.get("response_id") or f"{turn_id}:{index}"
        if response_id in seen_responses:
            continue
        usage = payload.get("usage", {})
        if not isinstance(usage, dict) or not isinstance(usage.get("input_tokens"), int) or not isinstance(usage.get("output_tokens"), int):
            continue
        seen_responses.add(response_id)
        cached = usage.get("cached_input_tokens", 0) + usage.get("cache_write_input_tokens", 0)
        metrics = normalize_metrics(usage["input_tokens"], cached, usage["output_tokens"])
        tools = pending_tools.pop(turn_id, [])
        add_response(component["responses"], response_id, classify_step(tools), metrics)
        if turn_id not in component["turn_ids"]:
            component["turn_ids"].append(turn_id)
        # Preserve Codex root-turn metadata without treating it as a subagent identifier.
        root_turn_id = payload.get("root_turn_id")
        if root_turn_id and str(root_turn_id) not in component["root_turn_ids"]:
            component["root_turn_ids"].append(str(root_turn_id))
        agent_id = payload.get("subagent_id") or payload.get("agent_id")
        if agent_id:
            component["agent_id"] = str(agent_id)

    if component["responses"]:
        component["status"] = "measured"
        component["attribution"] = "tool_call_adjacent"
        component["tokens"], component["step_usage"] = sum_response_metrics(component["responses"])
    else:
        component["reason"] = "native_usage_not_found_for_run"
    return component


def claude_text(item):
    message = item.get("message", {})
    content = message.get("content", item.get("content", ""))
    if isinstance(content, str):
        return content
    parts = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "tool_result":
                    continue
    return " ".join(parts)


def claude_tool_inputs(item):
    message = item.get("message", {})
    content = message.get("content", item.get("content", []))
    inputs = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                inputs.append({"name": block.get("name"), "input": block.get("input", {})})
    return inputs


def parse_claude(items, path, run_id):
    session_id = None
    marker_indexes = [index for index, item in enumerate(items) if has_marker(item, run_id)]
    component = base_component("claude", "unknown", run_id, path)
    if not marker_indexes:
        component["reason"] = "run_marker_not_found"
        return component

    for item in items:
        if item.get("sessionId") or item.get("session_id"):
            session_id = item.get("sessionId") or item.get("session_id")
            break
    if not session_id:
        session_id = os.path.splitext(os.path.basename(path))[0]
    component["session_id"] = str(session_id)

    first_marker = marker_indexes[0]
    start_index = first_marker
    for index in range(first_marker, -1, -1):
        item = items[index]
        if item.get("type") == "user" or item.get("role") == "user":
            if claude_text(item).strip():
                start_index = index
                break

    finalizer_indexes = []
    for index in marker_indexes:
        item = items[index]
        text = serialized(item).lower()
        if "track_token_usage.py" in text and "finalize" in text:
            finalizer_indexes.append(index)
    # Stop at the finalizer request, whose assistant usage is already in its message.
    # This excludes the visible final answer emitted after the tool returns.
    end_index = min(finalizer_indexes) if finalizer_indexes else len(items) - 1
    if not finalizer_indexes:
        for index in range(first_marker + 1, len(items)):
            item = items[index]
            if (item.get("type") == "user" or item.get("role") == "user") and claude_text(item).strip():
                end_index = index - 1
                break

    seen_responses = set()
    for index, item in enumerate(items[start_index:end_index + 1], start=start_index):
        if item.get("type") not in ("assistant", None) and item.get("role") != "assistant":
            continue
        message = item.get("message", {})
        usage = item.get("usage") or message.get("usage")
        if not isinstance(usage, dict):
            continue
        input_tokens = usage.get("input_tokens", usage.get("prompt_tokens"))
        output_tokens = usage.get("output_tokens", usage.get("completion_tokens"))
        if not isinstance(input_tokens, int) or not isinstance(output_tokens, int):
            continue
        cache_read = usage.get("cache_read_input_tokens", 0)
        cache_write = usage.get("cache_creation_input_tokens", 0)
        if not isinstance(cache_read, int):
            cache_read = 0
        if not isinstance(cache_write, int):
            cache_write = 0
        # Anthropic reports uncached input separately from cache read/write input.
        metrics = normalize_metrics(input_tokens + cache_read + cache_write, cache_read + cache_write, output_tokens)
        response_id = message.get("id") or item.get("requestId") or item.get("uuid") or f"{session_id}:{index}"
        if response_id in seen_responses:
            continue
        seen_responses.add(response_id)
        add_response(component["responses"], response_id, classify_step(claude_tool_inputs(item)), metrics)
        turn_id = item.get("turn_id") or item.get("parentUuid")
        if turn_id and str(turn_id) not in component["turn_ids"]:
            component["turn_ids"].append(str(turn_id))

    if component["responses"]:
        component["status"] = "measured"
        component["attribution"] = "tool_call_adjacent"
        component["tokens"], component["step_usage"] = sum_response_metrics(component["responses"])
    else:
        component["reason"] = "native_usage_not_found_for_run"
    return component


def antigravity_step(item):
    value = item.get("step_update")
    if not isinstance(value, dict):
        value = item.get("data", {}).get("step_update", {}) if isinstance(item.get("data"), dict) else {}
    return value if isinstance(value, dict) else {}


def parse_antigravity(items, path, run_id):
    marker_indexes = [index for index, item in enumerate(items) if has_marker(item, run_id)]
    if not marker_indexes:
        component = base_component("antigravity", "unknown", run_id, path)
        component["reason"] = "run_marker_not_found"
        return [component]

    markers_by_conversation = {}
    for index in marker_indexes:
        event = antigravity_step(items[index])
        conversation_id = event.get("conversation_id") or items[index].get("conversation_id")
        if conversation_id:
            markers_by_conversation.setdefault(str(conversation_id), []).append(index)
    if not markers_by_conversation:
        # Some transcript versions store the run marker in a user/tool event without a
        # conversation ID. Use the file only when it contains exactly one native session.
        conversations = set()
        for item in items:
            event = antigravity_step(item)
            conversation_id = event.get("conversation_id") or item.get("conversation_id")
            if conversation_id:
                conversations.add(str(conversation_id))
        if len(conversations) == 1:
            markers_by_conversation[next(iter(conversations))] = marker_indexes
        else:
            session_hint = os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(path)))) or "unknown"
            markers_by_conversation[session_hint] = marker_indexes

    components = []
    session_hint = os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(path)))) or "unknown"
    for conversation_id, conversation_markers in markers_by_conversation.items():
        component = base_component("antigravity", conversation_id, run_id, path)
        start_index = min(conversation_markers)
        for index in range(start_index, -1, -1):
            event = antigravity_step(items[index])
            item_conversation = event.get("conversation_id") or items[index].get("conversation_id") or session_hint
            if items[index].get("type") == "USER_INPUT" and (not item_conversation or str(item_conversation) == conversation_id):
                start_index = index
                break
        end_index = len(items) - 1
        for index in conversation_markers:
            text = serialized(items[index]).lower()
            if "track_token_usage.py" in text and "finalize" in text:
                end_index = index
                finalizer_step = antigravity_step(items[index]).get("step_index")
                if finalizer_step is not None:
                    for next_index in range(index + 1, len(items)):
                        event = antigravity_step(items[next_index])
                        if str(event.get("step_index")) == str(finalizer_step) and str(event.get("state", "")).upper() in ("DONE", "COMPLETED"):
                            end_index = next_index
                            break
                break
        else:
            latest_marker = max(conversation_markers)
            for index in range(latest_marker + 1, len(items)):
                event = antigravity_step(items[index])
                item_conversation = event.get("conversation_id") or items[index].get("conversation_id") or session_hint
                if items[index].get("type") == "USER_INPUT" and (not item_conversation or str(item_conversation) == conversation_id):
                    end_index = index - 1
                    break

        # Headless streaming can emit several updates for one step. Keep its final native usage once.
        final_steps = {}
        for item in items[start_index:end_index + 1]:
            event = antigravity_step(item)
            item_conversation = event.get("conversation_id") or item.get("conversation_id") or session_hint
            if str(item_conversation) != conversation_id:
                continue
            usage = event.get("usage")
            step_index = event.get("step_index") if event.get("step_index") is not None else item.get("step_index")
            if not isinstance(usage, dict):
                if isinstance(item.get("input_tokens"), int) or isinstance(item.get("output_tokens"), int):
                    usage = {
                        "input_tokens": item.get("input_tokens", 0),
                        "output_tokens": item.get("output_tokens", 0),
                        "cache_read_tokens": item.get("cache_read_tokens", 0),
                        "cache_write_tokens": item.get("cache_write_tokens", 0),
                        "thinking_tokens": item.get("thinking_tokens", 0),
                        "total_tokens": item.get("input_tokens", 0) + item.get("output_tokens", 0),
                    }
                else:
                    continue
            if step_index is None:
                continue
            state = str(event.get("state") or item.get("status") or "").upper()
            if state not in ("DONE", "COMPLETED"):
                continue
            final_steps[(conversation_id, str(step_index))] = (event if event else item, usage)

        for (step_conversation, step_index), (event, usage) in sorted(final_steps.items()):
            input_tokens = usage.get("input_tokens")
            output_tokens = usage.get("output_tokens")
            if not isinstance(input_tokens, int) or not isinstance(output_tokens, int):
                continue
            cache_read = usage.get("cache_read_tokens", 0)
            cache_write = usage.get("cache_write_tokens", 0)
            thinking = usage.get("thinking_tokens", 0)
            metrics = normalize_metrics(input_tokens, cache_read + cache_write, output_tokens, thinking)
            native_total = usage.get("total_tokens")
            if isinstance(native_total, int) and native_total >= 0:
                metrics["total"] = native_total
            response_id = f"{step_conversation}:{step_index}"
            step_name = (
                event.get("tool_name")
                or (event.get("tool_calls", [{}])[0].get("name") if event.get("tool_calls") else None)
                or event.get("step_type")
                or event.get("type")
                or "unknown"
            )
            stage = classify_step([step_name, event.get("text", "") or event.get("content", "")])
            if stage == "worklog_synthesis":
                stage = f"antigravity:{step_name}"
            add_response(component["responses"], response_id, stage, metrics)
        if component["responses"]:
            component["status"] = "measured"
            component["attribution"] = "native_step"
            component["tokens"], component["step_usage"] = sum_response_metrics(component["responses"])
        else:
            component["reason"] = "native_step_usage_not_found_for_run"
        components.append(component)
    return components


def parse_transcript(path, run_id):
    items = read_json_lines(path)
    if not items:
        return None
    transcript_format = detect_format(items)
    if transcript_format == "codex":
        return [parse_codex(items, path, run_id)]
    if transcript_format == "claude":
        return [parse_claude(items, path, run_id)]
    if transcript_format == "antigravity":
        return parse_antigravity(items, path, run_id)
    return None


def state_path(run_id):
    normalized_id = str(uuid.UUID(run_id))
    return os.path.join(STATE_DIR, f"{normalized_id}.json")


def begin_run(run_id=None, prompt=""):
    run_id = run_id or str(uuid.uuid4())
    state = {
        "run_id": run_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "prompt": prompt[:240],
        "transcripts_before": transcript_snapshot(),
    }
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(state_path(run_id), "w", encoding="utf-8") as state_file:
        json.dump(state, state_file, indent=2, ensure_ascii=False)
    return state


def changed_transcripts(snapshot):
    changed = []
    for path in find_session_transcripts():
        try:
            stat = os.stat(path)
        except OSError:
            continue
        before = snapshot.get(path)
        if before is None or before.get("size") != stat.st_size or before.get("mtime_ns") != stat.st_mtime_ns:
            changed.append(path)
    return changed


def update_json_records(json_path, record):
    records = []
    if os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as source:
                loaded = json.load(source)
            if isinstance(loaded, list):
                records = loaded
        except (OSError, json.JSONDecodeError):
            records = []

    match_index = next((index for index, old in enumerate(records) if record.get("run_id") and old.get("run_id") == record["run_id"]), None)
    if match_index is None:
        records.append(record)
    else:
        records[match_index] = record
    with open(json_path, "w", encoding="utf-8") as target:
        json.dump(records, target, indent=2, ensure_ascii=False)


def finalize_run(run_id, output_path=DEFAULT_JSON_PATH, transcript_paths=None):
    run_state_path = state_path(run_id)
    try:
        with open(run_state_path, "r", encoding="utf-8") as state_file:
            state = json.load(state_file)
        state_missing = False
    except FileNotFoundError:
        # A missed/failed `begin` should not silently prevent any usage record.
        state = {"run_id": run_id, "started_at": None, "prompt": "", "transcripts_before": {}}
        state_missing = True
    explicit_paths = list(dict.fromkeys(transcript_paths or []))
    paths = explicit_paths or changed_transcripts(state.get("transcripts_before", {}))

    components = []
    identities = set()
    for path in paths:
        parsed_components = parse_transcript(path, run_id)
        if not parsed_components:
            continue
        for component in parsed_components:
            if component.get("reason") == "run_marker_not_found":
                continue
            identity = (component.get("agent"), component.get("session_id"), tuple(component.get("turn_ids", [])))
            if identity in identities:
                continue
            identities.add(identity)
            components.append(component)

    if not components:
        reason = "run_state_missing" if state_missing else ("no_changed_transcript_found" if not paths else "run_marker_not_found")
        component = base_component("unknown", "unknown", run_id, "unavailable")
        component["reason"] = reason
        components.append(component)

    measured_totals = empty_metrics()
    for component in components:
        if component["status"] != "measured":
            continue
        for metric in METRICS:
            measured_totals[metric] += component["tokens"].get(metric, 0)
    measured_count = sum(component["status"] == "measured" for component in components)
    valid_agents = sorted({component["agent"] for component in components if component.get("agent") and component.get("agent") != "unknown"})
    primary_agent = ", ".join(valid_agents) if valid_agents else (components[0].get("agent", "unknown") if components else "unknown")
    ended = datetime.now(timezone.utc)
    record = {
        "date": ended.astimezone().strftime("%Y-%m-%d"),
        "timestamp": ended.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
        "agent": primary_agent,
        "trigger_prompt": state.get("prompt", ""),
        "run_id": run_id,
        "tokens": {
            "input": measured_totals["input"],
            "output": measured_totals["output"],
            "cache": measured_totals["cached_input"],
            "total": measured_totals["total"],
        } if measured_count else None,
    }
    if measured_count:
        update_json_records(output_path, record)
    if not state_missing:
        os.remove(run_state_path)
    return record


def main():
    parser = argparse.ArgumentParser(description="Measure native token usage for one worklog run.")
    commands = parser.add_subparsers(dest="command")
    begin = commands.add_parser("begin", help="Start a token measurement run")
    begin.add_argument("--run-id")
    begin.add_argument("--prompt", default="")
    finalize = commands.add_parser("finalize", help="Finalize token measurement for a run")
    finalize.add_argument("--run-id", required=True)
    finalize.add_argument("--output", default=DEFAULT_JSON_PATH)
    finalize.add_argument("--transcript", action="append", default=[], help="Path to an agent transcript; repeat for each agent")
    args = parser.parse_args()

    if args.command is None:
        parser.print_usage(sys.stderr)
        print(
            "Missing command. Start with `begin --prompt \"...\"`, then finish with `finalize --run-id <run_id>`.",
            file=sys.stderr,
        )
        return 2

    if args.command == "begin":
        state = begin_run(args.run_id, args.prompt)
        print(json.dumps({"run_id": state["run_id"], "started_at": state["started_at"]}, ensure_ascii=False))
        return
    record = finalize_run(args.run_id, args.output, args.transcript)
    print(json.dumps(record, ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
