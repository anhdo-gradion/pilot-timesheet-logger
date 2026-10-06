#!/usr/bin/env python3
"""
[Script Step] collect_work.py
Deterministic collection of Git commits and GitHub PRs for today.
Output is ultra-compact JSON to minimize LLM token budget (<50 tokens).
"""

import os
import sys
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

def collect_git_commits(repo_path="."):
    today_str = get_today_str()
    # Format: short_hash|time|message
    fmt = "%h|%ad|%s"
    cmd = [
        "git", "log", "--all",
        f"--since={today_str} 00:00:00",
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

def collect_github_prs():
    today_str = get_today_str()
    prs = []
    gh_path = shutil.which("gh")
    if not gh_path:
        return []

    cmd = [
        "gh", "search", "prs",
        "--author=@me",
        f"--updated=>={today_str}",
        "--json", "number,title,state"
    ]
    code, out, _ = run_cmd(cmd)
    if code == 0 and out:
        try:
            data = json.loads(out)
            for item in data:
                prs.append({
                    "num": item.get("number"),
                    "title": item.get("title"),
                    "state": item.get("state")
                })
            return prs
        except Exception:
            pass

    cmd_repo = [
        "gh", "pr", "list",
        "--state", "all",
        "--limit", "10",
        "--json", "number,title,state"
    ]
    code_r, out_r, _ = run_cmd(cmd_repo)
    if code_r == 0 and out_r:
        try:
            data = json.loads(out_r)
            for item in data:
                prs.append({
                    "num": item.get("number"),
                    "title": item.get("title"),
                    "state": item.get("state")
                })
            return prs
        except Exception:
            pass

    return prs

def main():
    repo_dirs = [d for d in sys.argv[1:] if os.path.isdir(d)]
    if not repo_dirs:
        repo_dirs = ["."]

    all_commits = []
    for r in repo_dirs:
        all_commits.extend(collect_git_commits(r))

    prs = collect_github_prs()
    all_commits.sort(key=lambda c: c.get("time", ""))

    first_time = all_commits[0]["time"] if all_commits else "09:00"
    last_time = all_commits[-1]["time"] if all_commits else "17:30"

    structured_summary = {
        "date": get_today_str(),
        "span": [first_time, last_time],
        "commits": all_commits,
        "prs": prs
    }

    # Print compact JSON without whitespace to conserve model input tokens
    print(json.dumps(structured_summary, separators=(",", ":")))

if __name__ == "__main__":
    main()
