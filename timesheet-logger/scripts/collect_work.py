#!/usr/bin/env python3
"""Collect date-scoped GitHub and Google Calendar data as compact JSON."""

import argparse
import json
import os
import sys
from datetime import date

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from collect_calendar import collect_today_events, ensure_google_auth
from collect_github import collect_today_github, ensure_github_auth


def today():
    return date.today().isoformat()


def main():
    parser = argparse.ArgumentParser(description="Collect GitHub and Calendar activity for a date.")
    parser.add_argument("--date", default=today(), help="Target date YYYY-MM-DD (default: today)")
    parser.add_argument("--username", help="GitHub username (default: authenticated account)")
    parser.add_argument("--timezone-offset", type=float, default=7, help="Timezone UTC offset in hours (default: +7)")
    parser.add_argument("--mode", choices=("search",), default="search", help="GitHub Search API mode")
    parser.add_argument("--calendar-id", default="primary")
    parser.add_argument("--max-calendar-results", type=int, default=250)
    parser.add_argument("--auth", action="store_true", help="Run Google and GitHub browser authentication if needed")
    args = parser.parse_args()

    try:
        date.fromisoformat(args.date)
    except ValueError:
        parser.error("--date must use YYYY-MM-DD")

    if args.auth:
        ensure_google_auth()
        ensure_github_auth()

    github = collect_today_github(
        username=args.username,
        target_date=args.date,
        mode=args.mode,
        tz_offset_hours=args.timezone_offset,
    )
    calendar = collect_today_events(
        target_date=args.date,
        tz_offset_hours=args.timezone_offset,
        calendar_id=args.calendar_id,
        max_results=args.max_calendar_results,
    )

    result = {
        "date": args.date,
        "timezone": f"UTC{args.timezone_offset:+g}",
        "github_tasks": github.get("tasks", []),
        "github_task_count": github.get("task_count", {}),
        "unlinked_commits": github.get("unlinked_commits", []),
        "events": calendar.get("events", []),
        "sources_available": {
            "github": github.get("available", False),
            "calendar": calendar.get("available", False),
        },
        "source_errors": {
            "github": github.get("error"),
            "calendar": calendar.get("error"),
        },
    }
    print(json.dumps(result, separators=(",", ":"), ensure_ascii=False))
    return 0 if all(result["sources_available"].values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
