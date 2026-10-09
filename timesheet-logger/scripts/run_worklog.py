#!/usr/bin/env python3
"""Run collection, deterministic scheduling, preview, confirmation, and upload."""

import argparse
import json
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parents[1]

sys.path.insert(0, str(SCRIPT_DIR))
from collect_calendar import collect_today_events, ensure_google_auth  # noqa: E402
from collect_github import collect_today_github, ensure_github_auth  # noqa: E402
from build_worklog import build_entries  # noqa: E402


def local_today():
    return datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date().isoformat()


def save_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(
        description="Collect GitHub and Calendar activity, build a worklog, ask for terminal confirmation, then upload."
    )
    parser.add_argument("--date", default=local_today(), help="Target date YYYY-MM-DD; default is today in Asia/Ho_Chi_Minh")
    parser.add_argument("--work-period", choices=("full", "morning", "afternoon"), default="full")
    parser.add_argument("--timezone-offset", type=float, default=7)
    parser.add_argument("--github-output", default="github-data.json")
    parser.add_argument("--calendar-output", default="calendar-data.json")
    parser.add_argument("--worklog-output", default="timesheet-entries.json")
    parser.add_argument(
        "--draft-only",
        action="store_true",
        help="Collect and build the draft, then stop without asking in the terminal or uploading",
    )
    args = parser.parse_args()

    try:
        date.fromisoformat(args.date)
        print(f"Collecting GitHub activity for {args.date}…", file=sys.stderr)
        ensure_github_auth()
        github_data = collect_today_github(
            target_date=args.date,
            mode="search",
            tz_offset_hours=args.timezone_offset,
        )
        save_json(args.github_output, github_data)
        if not github_data.get("available"):
            raise RuntimeError(f"GitHub collection failed: {github_data.get('error', 'unknown error')}")

        print(f"Collecting Google Calendar events for {args.date}…", file=sys.stderr)
        ensure_google_auth()
        calendar_data = collect_today_events(
            target_date=args.date,
            tz_offset_hours=args.timezone_offset,
        )
        save_json(args.calendar_output, calendar_data)
        if not calendar_data.get("available"):
            raise RuntimeError(f"Calendar collection failed: {calendar_data.get('error', 'unknown error')}")

        entries = build_entries(args.date, github_data, calendar_data, args.work_period)
        save_json(args.worklog_output, entries)
        pr_count = len({
            item["arguments"]["description"].split(" — ", 1)[0]
            for item in entries
            if item["arguments"]["description"].startswith("PR #")
        })
        print(json.dumps(entries, ensure_ascii=False, indent=2))
        print(
            f"\nDraft saved to {args.worklog_output}: {pr_count} unique PRs, {len(entries)} worklog blocks.",
            file=sys.stderr,
        )

        if args.draft_only:
            print("Draft-only mode: no upload was attempted.", file=sys.stderr)
            return 0

        if not sys.stdin.isatty():
            print("No upload: run this command in an interactive terminal to confirm the draft.", file=sys.stderr)
            return 2
        try:
            confirmation = input("Type CONFIRM to upload this exact draft; anything else cancels: ").strip()
        except EOFError:
            confirmation = ""
        if confirmation != "CONFIRM":
            print("Upload cancelled. The draft remains saved locally.", file=sys.stderr)
            return 0

        upload_script = SCRIPT_DIR / "push_worklog.py"
        return subprocess.run(
            [sys.executable, str(upload_script), "submit", "--input", str(Path(args.worklog_output).resolve()), "--confirm"],
            cwd=ROOT_DIR,
            check=False,
        ).returncode
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
