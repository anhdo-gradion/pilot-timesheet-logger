#!/usr/bin/env python3
"""
[Script Step] collect_work.py
Deterministic collection of Git commits, GitHub PRs/issues, and Google Calendar events.
Output is ultra-compact JSON to minimize LLM token budget (<50 tokens).
GitHub data collection is delegated to collect_github.py using GitHub Search API.
"""

import os
import sys
import warnings
warnings.filterwarnings("ignore")
import json
import shutil
import subprocess
from datetime import datetime, date

def get_today_str():
    return date.today().strftime("%Y-%m-%d")

def run_cmd(cmd, cwd=None):
    try:
        res = subprocess.run(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False
        )
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except Exception as e:
        return 1, "", str(e)

def collect_git_commits(repo_path=".", target_date=None):
    date_str = target_date or get_today_str()
    # Format: short_hash|time|message
    fmt = "%h|%ad|%s"
    cmd = [
        "git", "log", "--all",
        f"--since={date_str} 00:00:00",
        f"--until={date_str} 23:59:59",
        "--date=format:%H:%M",
        f"--format={fmt}"
    ]
    code, out, _ = run_cmd(cmd, cwd=repo_path)
    if code != 0 or not out:
        return []

    commits = []
    repo_name = os.path.basename(os.path.abspath(repo_path))
    for line in out.splitlines():
        parts = line.split("|", 2)
        if len(parts) >= 3:
            short_h, c_time, msg = parts[0], parts[1], parts[2]
            commits.append({
                "hash": short_h,
                "time": c_time,
                "msg": msg,
                "repo": repo_name
            })
    return commits

def collect_github(target_date=None, username=None):
    """
    Collects GitHub PRs, issues, and remote commits using the split collect_github.py module
    (applying GitHub Search API).
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    try:
        from collect_github import collect_today_github
        res = collect_today_github(username=username, target_date=target_date)
        if isinstance(res, dict) and res.get("available"):
            return res
    except Exception:
        pass

    # Alternative: check if collect_github.py exists in same directory and run as subprocess
    gh_script = os.path.join(script_dir, "collect_github.py")
    if os.path.exists(gh_script):
        cmd = [sys.executable, gh_script]
        if username:
            cmd.append(username)
        if target_date:
            cmd.append(target_date)
        code, out, _ = run_cmd(cmd)
        if code == 0 and out:
            try:
                data = json.loads(out)
                if data.get("available"):
                    return data
            except Exception:
                pass

    return {"available": False, "commits": [], "prs": [], "issues": []}

def collect_calendar(target_date=None):
    """Tries to extract Google Calendar events using collect_calendar.py."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    try:
        from collect_calendar import collect_today_events
        res = collect_today_events(target_date=target_date)
        if isinstance(res, dict) and res.get("available"):
            return res.get("events", [])
    except Exception:
        pass

    cal_script = os.path.join(script_dir, "collect_calendar.py")
    if os.path.exists(cal_script):
        cmd = [sys.executable, cal_script]
        if target_date:
            cmd.append(target_date)
        code, out, _ = run_cmd(cmd)
        if code == 0 and out:
            try:
                data = json.loads(out)
                if data.get("available"):
                    return data.get("events", [])
            except Exception:
                pass
    return []

def main():
    repo_dirs = []
    target_date = get_today_str()
    username = None

    for arg in sys.argv[1:]:
        if os.path.isdir(arg):
            repo_dirs.append(arg)
        elif len(arg) == 10 and arg.count("-") == 2:
            try:
                datetime.strptime(arg, "%Y-%m-%d")
                target_date = arg
            except ValueError:
                pass
        elif not arg.startswith("-"):
            username = arg

    if not repo_dirs:
        repo_dirs = ["."]

    all_commits = []
    for r in repo_dirs:
        all_commits.extend(collect_git_commits(r, target_date=target_date))

    # GitHub Search API collection via split module
    gh_data = collect_github(target_date=target_date, username=username)
    gh_commits = gh_data.get("commits", [])
    prs = gh_data.get("prs", [])

    # Merge remote commits from GitHub Search API (avoid duplicate hashes)
    known_hashes = {c.get("hash") for c in all_commits if c.get("hash")}
    for rc in gh_commits:
        h = rc.get("hash")
        if h and h not in known_hashes:
            all_commits.append(rc)
            known_hashes.add(h)

    events = collect_calendar(target_date=target_date)
    all_commits.sort(key=lambda c: c.get("time", ""))

    first_time = all_commits[0]["time"] if all_commits else "09:00"
    last_time = all_commits[-1]["time"] if all_commits else "17:30"

    structured_summary = {
        "date": target_date,
        "span": [first_time, last_time],
        "commits": all_commits,
        "prs": prs,
        "events": events
    }

    # Print compact JSON without whitespace to conserve model input tokens
    print(json.dumps(structured_summary, separators=(",", ":")))

if __name__ == "__main__":
    main()
