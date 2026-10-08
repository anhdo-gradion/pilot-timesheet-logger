#!/usr/bin/env python3
"""
[Script Step] collect_work.py
Deterministic collection of GitHub commits, pull requests, PR comments/reviews, and Google Calendar events.
Output is compact JSON containing only worklog-relevant activity and calendar events.
GitHub data collection is delegated to collect_github.py using GitHub Search API.
"""

import os
import sys
import json
import shutil
import subprocess
import argparse
from datetime import datetime, date

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from collect_calendar import collect_today_events, ensure_google_auth
from collect_github import collect_today_github, GitHubClient, get_github_token

def get_today_str():
    return date.today().strftime("%Y-%m-%d")

def main():
    parser = argparse.ArgumentParser(description="Collect work activity for one date.")
    parser.add_argument("--date", default=get_today_str(), help="Target date YYYY-MM-DD (default: today)")
    parser.add_argument("--username", help="GitHub username (default: authenticated account)")
    parser.add_argument("--timezone-offset", type=float, default=7, help="Timezone UTC offset in hours (default: +7)")
    parser.add_argument("--mode", choices=["search"], default="search", help="GitHub Search API mode")
    parser.add_argument("--auth", action="store_true", help="Open Google/GitHub browser authentication when needed")
    args = parser.parse_args()
    try:
        datetime.strptime(args.date, "%Y-%m-%d")
    except ValueError:
        parser.error("--date must use YYYY-MM-DD")
    target_date = args.date
    username = args.username
    if args.auth:
        ensure_authentication()

    gh_data = collect_today_github(username=username, target_date=target_date, mode=args.mode, tz_offset_hours=args.timezone_offset)
    activities = gh_data.get("activities", [])

    calendar_data = collect_today_events(target_date=target_date, tz_offset_hours=args.timezone_offset)
    events = calendar_data.get("events", [])

    structured_summary = {
        "date": target_date,
        "timezone": f"UTC{args.timezone_offset:+g}",
        "activities": activities,
        "events": events,
        "sources_available": {"github": gh_data.get("available", False), "calendar": calendar_data.get("available", False)},
        "source_errors": {"github": gh_data.get("error"), "calendar": calendar_data.get("error")}
    }

    # Print compact JSON without whitespace to conserve model input tokens
    print(json.dumps(structured_summary, separators=(",", ":")))


def ensure_authentication():
    """Authenticate Google and GitHub in their browser-based native OAuth flows."""
    ensure_google_auth()

    existing_token = get_github_token()
    if existing_token:
        profile = GitHubClient(token=existing_token).request("/user")
        if isinstance(profile, dict) and profile.get("login"):
            return
    if not shutil.which("gh"):
        raise RuntimeError("GitHub login is missing. Install GitHub CLI (gh), then rerun with --auth.")
    print("GitHub login required. Complete the browser authentication opened by GitHub CLI.", file=sys.stderr)
    result = subprocess.run(
        ["gh", "auth", "login", "--web", "--hostname", "github.com", "--scopes", "repo,read:user,user:email"],
        check=False
    )
    if result.returncode != 0 or not get_github_token():
        raise RuntimeError("GitHub browser authentication did not complete successfully.")

if __name__ == "__main__":
    main()
