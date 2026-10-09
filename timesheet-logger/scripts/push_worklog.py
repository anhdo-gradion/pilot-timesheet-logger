#!/usr/bin/env python3
"""Discover Timesheet MCP tools and submit confirmed worklog JSON via Gradion proxy."""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date


BASE_URL = os.environ.get("GRADION_BASE_URL", "https://workspace.gradion.com").rstrip("/")
SKILL_VERSION = os.environ.get("GRADION_SKILL_VERSION", "1.9.0")
TOKEN_ENV = "GRADION_API_TOKEN"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENV_FILE = os.path.join(REPO_ROOT, ".env")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
REQUIRED_FIELDS = {"date", "startTime", "endTime", "description"}
OPTIONAL_FIELDS = {"classification", "classificationId", "task", "billable"}


class APIError(Exception):
    pass


def load_local_env():
    """Load only the Gradion token from the ignored, owner-only workspace .env."""
    if not os.path.exists(ENV_FILE):
        return
    permissions = os.stat(ENV_FILE).st_mode & 0o777
    if permissions & 0o077:
        raise APIError(".env permissions are too broad; restrict the file to its owner (chmod 600 .env).")
    with open(ENV_FILE, "r", encoding="utf-8") as source:
        for line in source:
            key, separator, value = line.strip().partition("=")
            if separator and key.strip() == TOKEN_ENV and value.strip():
                token = value.strip().strip("\"'")
                os.environ.setdefault(TOKEN_ENV, token)


def request_json(method, path, payload=None):
    load_local_env()
    token = os.environ.get(TOKEN_ENV)
    if not token:
        raise APIError(f"Set {TOKEN_ENV} in .env or the process environment; do not paste it in chat.")
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Gradion-Skill-Bundle-Version": SKILL_VERSION,
        "Accept": "application/json",
    }
    body = None
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(BASE_URL + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        # Do not echo request headers or credentials in errors.
        detail = error.read(2048).decode("utf-8", errors="replace")
        detail = detail.replace(token, "<redacted>")
        raise APIError(f"Workspace API returned HTTP {error.code}: {detail}") from None
    except (urllib.error.URLError, TimeoutError) as error:
        raise APIError(f"Workspace API request failed: {error}") from None
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        raise APIError("Workspace API returned a non-JSON response.") from None


def discover_catalog():
    result = request_json("GET", "/api/me/apps/mcp")
    apps = result.get("apps", []) if isinstance(result, dict) else []
    timesheet = [app for app in apps if app.get("slug") == "timesheet"]
    if not timesheet:
        raise APIError("No Timesheet app is available to this token. Check its app scope or installation.")
    return timesheet


def print_catalog(_args):
    for app in discover_catalog():
        print(json.dumps({
            "slug": app.get("slug"),
            "name": app.get("name"),
            "tools": app.get("tools", []),
        }, ensure_ascii=False, indent=2))


def tool_call(name, arguments):
    result = request_json(
        "POST",
        f"/api/me/apps/timesheet/tools/{urllib.parse.quote(name, safe='')}/call",
        {"arguments": arguments},
    )
    if isinstance(result, dict) and result.get("isError"):
        messages = [item.get("text", "") for item in result.get("content", []) if isinstance(item, dict)]
        raise APIError(f"Timesheet tool {name} failed: {' '.join(messages) or 'unspecified tool error'}")
    return result


def hhmm(value, field):
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string in HH:MM format")
    match = TIME_RE.fullmatch(value)
    if not match:
        raise ValueError(f"{field} must use 24-hour HH:MM format")
    return int(match.group(1)) * 60 + int(match.group(2))


def validate_entries(data):
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list) or not data:
        raise ValueError("Input must be a non-empty JSON array of worklog objects.")

    entries = []
    for index, item in enumerate(data, 1):
        if not isinstance(item, dict) or not isinstance(item.get("arguments"), dict):
            raise ValueError(f"Entry {index} must have an 'arguments' object.")
        args = item["arguments"]
        missing = REQUIRED_FIELDS - args.keys()
        unknown = args.keys() - REQUIRED_FIELDS - OPTIONAL_FIELDS
        if missing or unknown:
            raise ValueError(f"Entry {index}: missing={sorted(missing)}, unsupported={sorted(unknown)}")
        if not isinstance(args["date"], str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args["date"]):
            raise ValueError(f"Entry {index}: date must be YYYY-MM-DD.")
        date.fromisoformat(args["date"])
        start = hhmm(args["startTime"], "startTime")
        end = hhmm(args["endTime"], "endTime")
        if end <= start:
            raise ValueError(f"Entry {index}: endTime must be later than startTime.")
        if not isinstance(args["description"], str) or not args["description"].strip() or len(args["description"]) > 3000:
            raise ValueError(f"Entry {index}: description must contain 1–3000 characters.")
        if ("classification" in args) == ("classificationId" in args):
            raise ValueError(f"Entry {index}: provide exactly one of classification or classificationId.")
        if "task" in args and (not isinstance(args["task"], str) or len(args["task"]) > 150):
            raise ValueError(f"Entry {index}: task must be a string of at most 150 characters.")
        if "billable" in args and not isinstance(args["billable"], bool):
            raise ValueError(f"Entry {index}: billable must be boolean.")
        entries.append({"arguments": args, "start": start, "end": end})

    entries.sort(key=lambda row: (row["arguments"]["date"], row["start"]))
    for previous, current in zip(entries, entries[1:]):
        if previous["arguments"]["date"] == current["arguments"]["date"] and current["start"] < previous["end"]:
            raise ValueError(f"Draft entries overlap on {current['arguments']['date']}.")
    return entries


def read_input(path):
    if path == "-":
        return validate_entries(json.load(sys.stdin))
    with open(path, "r", encoding="utf-8") as source:
        return validate_entries(json.load(source))


def print_preview(entries):
    print(json.dumps([{"arguments": item["arguments"]} for item in entries], ensure_ascii=False, indent=2))


def submit(args):
    if not args.confirm:
        raise APIError("No write was made. Get explicit user approval, then rerun with --confirm.")
    entries = read_input(args.input)
    print_preview(entries)
    tool_name = args.tool or "log_time"

    successes = []
    for entry in entries:
        try:
            result = tool_call(tool_name, entry["arguments"])
        except APIError as error:
            print(json.dumps({"submitted": successes, "failed_entry": entry["arguments"], "error": str(error)}, ensure_ascii=False, indent=2))
            raise APIError(f"Stopped after {len(successes)} successful entries. Do not retry the full batch; inspect the result and submit only the remaining entries.") from None
        successes.append({"arguments": entry["arguments"], "result": result})
    print(json.dumps({"submitted": successes}, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Discover Timesheet MCP tools or submit user-confirmed worklogs.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("discover", help="Show the Timesheet tool catalog and input schemas").set_defaults(func=print_catalog)
    submit_parser = subparsers.add_parser("submit", help="Submit JSON only after explicit human approval")
    submit_parser.add_argument("--input", required=True, help="JSON file, or '-' to read JSON from stdin")
    submit_parser.add_argument("--tool", help="Tool name from discover (default: log_time)")
    submit_parser.add_argument("--confirm", action="store_true", help="Required after user confirms the exact draft")
    submit_parser.set_defaults(func=submit)
    args = parser.parse_args()
    try:
        args.func(args)
    except (APIError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
