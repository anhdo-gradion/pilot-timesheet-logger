#!/usr/bin/env python3
"""
[Script Step] collect_github.py
Comprehensive GitHub user activity crawler and deterministic collector.
Combines:
  1. Activity Events API (GET /users/{username}/events) - Best for past 30 days (captures commits, PRs, issues, comments, reviews, tags).
  2. GitHub Search API (GET /search/issues & GET /search/commits) - Best for older history (>30 days).
  3. Automatic mode selection and compact JSON formatting for timesheet logging and Calendar App synchronization.
"""

import os
import sys
import json
import time
import shutil
import argparse
import urllib.request
import urllib.error
import urllib.parse
import subprocess
from datetime import datetime, date, timezone, timedelta
from typing import Dict, List, Any, Optional

API_VERSION = "2022-11-28"

def run_cmd(cmd: List[str]) -> tuple[int, str, str]:
    """Runs a subprocess command safely."""
    try:
        res = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False
        )
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except Exception as e:
        return 1, "", str(e)

def get_github_token() -> Optional[str]:
    """Retrieves GitHub token from GITHUB_TOKEN env var, .env file, or gh CLI."""
    token = os.environ.get("GITHUB_TOKEN")
    if token and token.strip():
        return token.strip()

    # Try reading from root .env or current dir .env
    for candidate_env in [
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".env"),
        os.path.join(os.getcwd(), ".env"),
        ".env"
    ]:
        if os.path.exists(candidate_env):
            try:
                with open(candidate_env, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("GITHUB_TOKEN=") or (line.startswith("GITHUB_TOKEN ") and "=" in line):
                            _, val = line.split("=", 1)
                            token_val = val.strip().strip("'\"")
                            if token_val:
                                return token_val
            except Exception:
                pass

    if shutil.which("gh"):
        code, out, _ = run_cmd(["gh", "auth", "token"])
        if code == 0 and out.strip():
            return out.strip()
    return None

class GitHubClient:
    """Lightweight REST client for GitHub API using standard urllib."""
    def __init__(self, token: Optional[str] = None):
        self.token = token or get_github_token()
        self.base_url = "https://api.github.com"
        self.network_logs: List[Dict[str, Any]] = []

    def _get_headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "GitHub-Activity-Crawler-SyncHub",
            "X-GitHub-Api-Version": API_VERSION,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def request(self, endpoint: str, params: Optional[Dict[str, Any]] = None, description: str = "") -> Any:
        url = f"{self.base_url}{endpoint}"
        if params:
            encoded_params = urllib.parse.urlencode(params)
            url = f"{url}?{encoded_params}"

        req = urllib.request.Request(url, headers=self._get_headers())
        start_time = time.time()
        call_time_str = datetime.now().strftime("%H:%M:%S.%f")[:-3]

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                status_code = resp.status
                duration_ms = round((time.time() - start_time) * 1000, 2)
                raw_body = resp.read().decode("utf-8")
                parsed_json = json.loads(raw_body)

                log_entry = {
                    "id": len(self.network_logs) + 1,
                    "time": call_time_str,
                    "method": "GET",
                    "endpoint": endpoint,
                    "full_url": url,
                    "description": description or f"GET {endpoint}",
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                    "rate_limit_remaining": resp.headers.get("x-ratelimit-remaining"),
                    "rate_limit_limit": resp.headers.get("x-ratelimit-limit"),
                    "error": False
                }
                self.network_logs.append(log_entry)
                return parsed_json

        except urllib.error.HTTPError as e:
            # Fallback to gh api if available and token unauthorized
            if shutil.which("gh") and not self.token:
                gh_cmd = ["gh", "api", endpoint]
                if params:
                    for k, v in params.items():
                        gh_cmd.extend(["-F", f"{k}={v}"])
                code, out, _ = run_cmd(gh_cmd)
                if code == 0 and out:
                    try:
                        return json.loads(out)
                    except Exception:
                        pass

            duration_ms = round((time.time() - start_time) * 1000, 2)
            try:
                error_body = e.read().decode("utf-8")
                error_json = json.loads(error_body)
                msg = error_json.get("message", error_body)
            except Exception:
                msg = str(e)
                error_json = {"raw_error": msg}

            log_entry = {
                "id": len(self.network_logs) + 1,
                "time": call_time_str,
                "method": "GET",
                "endpoint": endpoint,
                "full_url": url,
                "description": description or f"GET {endpoint} (HTTP {e.code})",
                "status_code": e.code,
                "duration_ms": duration_ms,
                "rate_limit_remaining": e.headers.get("x-ratelimit-remaining") if hasattr(e, "headers") else None,
                "raw_response": error_json,
                "error": True,
                "error_message": msg
            }
            self.network_logs.append(log_entry)
            if e.code == 404:
                return [] if "/events" in endpoint else {}
            return {}

        except Exception as e:
            return {}

def get_github_username(client: Optional[GitHubClient] = None) -> Optional[str]:
    """Auto-detects the authenticated GitHub username."""
    user = os.environ.get("GITHUB_USER") or os.environ.get("GITHUB_ACTOR")
    if user and user.strip():
        return user.strip()
    if client and client.token:
        try:
            user_data = client.request("/user")
            if isinstance(user_data, dict) and user_data.get("login"):
                return user_data["login"]
        except Exception:
            pass
    if shutil.which("gh"):
        code, out, _ = run_cmd(["gh", "api", "user", "--jq", ".login"])
        if code == 0 and out.strip():
            return out.strip()
    code, out, _ = run_cmd(["git", "config", "github.user"])
    if code == 0 and out.strip():
        return out.strip()
    return "anhdo-gradion"

def parse_target_date(date_val: Optional[Any]) -> date:
    """Normalizes input to a date object."""
    if isinstance(date_val, date):
        return date_val
    if isinstance(date_val, datetime):
        return date_val.date()
    if isinstance(date_val, str) and date_val.strip():
        try:
            return datetime.strptime(date_val.strip(), "%Y-%m-%d").date()
        except ValueError:
            pass
    return date.today()

def parse_iso_datetime(dt_str: Optional[str]) -> datetime:
    """Parse ISO 8601 string to timezone-aware datetime."""
    if not dt_str:
        return datetime.now(timezone.utc)
    s = dt_str
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return datetime.now(timezone.utc)

def format_time_hh_mm(dt_str: Optional[str], tz_offset_hours: int = 7) -> str:
    """Extracts HH:MM in target timezone (default UTC+7) from ISO datetime string."""
    if not dt_str:
        return "12:00"
    try:
        dt = parse_iso_datetime(dt_str)
        target_tz = timezone(timedelta(hours=tz_offset_hours))
        local_dt = dt.astimezone(target_tz)
        return local_dt.strftime("%H:%M")
    except Exception:
        if "T" in str(dt_str):
            return str(dt_str).split("T")[1][:5]
        return "12:00"

# -------------------------------------------------------------
# Method 1: Activity Events API (GET /users/{username}/events)
# -------------------------------------------------------------
def crawl_user_events_by_date(client: GitHubClient, username: str, target_date: date, tz_offset_hours: int = 7) -> Dict[str, Any]:
    """
    Crawls user events timeline via Activity API.
    Fetches up to 300 recent events (3 pages x 100), filters for target_date in given timezone.
    """
    all_events = []
    target_tz = timezone(timedelta(hours=tz_offset_hours))

    for page in range(1, 4):
        try:
            events = client.request(f"/users/{username}/events", {"per_page": 100, "page": page}, description=f"Activity Events API p.{page}")
            if not events or not isinstance(events, list):
                break
            all_events.extend(events)

            # Check date of last event in page
            last_event_time = parse_iso_datetime(events[-1].get("created_at"))
            last_event_local_date = last_event_time.astimezone(target_tz).date()
            if last_event_local_date < target_date:
                break
        except Exception:
            break

    filtered_events = []
    for ev in all_events:
        created_at_dt = parse_iso_datetime(ev.get("created_at"))
        local_date = created_at_dt.astimezone(target_tz).date()
        if local_date == target_date:
            filtered_events.append(ev)

    categorized: Dict[str, List[Any]] = {
        "commits": [],
        "pull_requests": [],
        "issues": [],
        "issue_comments": [],
        "pr_reviews": [],
        "pr_review_comments": [],
        "branch_and_tag_creations": [],
        "forks": [],
        "stars": [],
        "other": []
    }

    for ev in filtered_events:
        ev_type = ev.get("type")
        repo_name = ev.get("repo", {}).get("name", "unknown")
        created_at = ev.get("created_at")
        time_hh_mm = format_time_hh_mm(created_at, tz_offset_hours)
        payload = ev.get("payload", {})

        if ev_type == "PushEvent":
            commits = payload.get("commits") or []
            branch = payload.get("ref", "").replace("refs/heads/", "")
            head_sha = payload.get("head", "")

            if commits:
                for c in commits:
                    categorized["commits"].append({
                        "repo": repo_name,
                        "branch": branch,
                        "sha": c.get("sha", "")[:7],
                        "message": c.get("message", "").strip(),
                        "url": f"https://github.com/{repo_name}/commit/{c.get('sha')}",
                        "created_at": created_at,
                        "time": time_hh_mm
                    })
            elif head_sha:
                commit_msg = f"Push to branch {branch}"
                categorized["commits"].append({
                    "repo": repo_name,
                    "branch": branch,
                    "sha": head_sha[:7],
                    "message": commit_msg,
                    "url": f"https://github.com/{repo_name}/commit/{head_sha}",
                    "created_at": created_at,
                    "time": time_hh_mm
                })

        elif ev_type == "PullRequestEvent":
            pr = payload.get("pull_request", {})
            action = payload.get("action", "")
            categorized["pull_requests"].append({
                "action": action,
                "repo": repo_name,
                "number": pr.get("number"),
                "title": pr.get("title", f"Pull Request #{pr.get('number')}"),
                "url": pr.get("html_url", f"https://github.com/{repo_name}/pull/{pr.get('number')}"),
                "merged": pr.get("merged", False),
                "state": "merged" if pr.get("merged") else pr.get("state", "open"),
                "created_at": created_at,
                "time": time_hh_mm
            })

        elif ev_type == "IssuesEvent":
            issue = payload.get("issue", {})
            action = payload.get("action", "")
            categorized["issues"].append({
                "action": action,
                "repo": repo_name,
                "number": issue.get("number"),
                "title": issue.get("title", f"Issue #{issue.get('number')}"),
                "url": issue.get("html_url", f"https://github.com/{repo_name}/issues/{issue.get('number')}"),
                "state": issue.get("state", "open"),
                "created_at": created_at,
                "time": time_hh_mm
            })

        elif ev_type == "IssueCommentEvent":
            issue = payload.get("issue", {})
            comment = payload.get("comment", {})
            is_pr = "pull_request" in issue
            categorized["issue_comments"].append({
                "type": "pull_request" if is_pr else "issue",
                "repo": repo_name,
                "number": issue.get("number"),
                "title": issue.get("title", f"#{issue.get('number')}"),
                "comment_body": comment.get("body", "")[:200],
                "url": comment.get("html_url", ""),
                "created_at": created_at,
                "time": time_hh_mm
            })

        elif ev_type == "PullRequestReviewEvent":
            review = payload.get("review", {})
            pr = payload.get("pull_request", {})
            categorized["pr_reviews"].append({
                "repo": repo_name,
                "pr_number": pr.get("number"),
                "pr_title": pr.get("title", ""),
                "state": review.get("state", ""),
                "url": review.get("html_url", ""),
                "created_at": created_at,
                "time": time_hh_mm
            })

        elif ev_type == "PullRequestReviewCommentEvent":
            comment = payload.get("comment", {})
            pr = payload.get("pull_request", {})
            categorized["pr_review_comments"].append({
                "repo": repo_name,
                "pr_number": pr.get("number"),
                "pr_title": pr.get("title", ""),
                "comment_body": comment.get("body", "")[:200],
                "url": comment.get("html_url", ""),
                "created_at": created_at,
                "time": time_hh_mm
            })

        elif ev_type == "CreateEvent":
            ref_type = payload.get("ref_type")
            ref = payload.get("ref")
            categorized["branch_and_tag_creations"].append({
                "repo": repo_name,
                "type": ref_type,
                "ref": ref,
                "created_at": created_at,
                "time": time_hh_mm
            })

        elif ev_type == "ForkEvent":
            forkee = payload.get("forkee", {})
            categorized["forks"].append({
                "from_repo": repo_name,
                "fork_url": forkee.get("html_url", ""),
                "created_at": created_at,
                "time": time_hh_mm
            })

        elif ev_type == "WatchEvent":
            categorized["stars"].append({
                "repo": repo_name,
                "created_at": created_at,
                "time": time_hh_mm
            })

        else:
            categorized["other"].append({
                "type": ev_type,
                "repo": repo_name,
                "created_at": created_at,
                "time": time_hh_mm
            })

    total_results = (
        len(categorized["commits"]) +
        len(categorized["pull_requests"]) +
        len(categorized["issues"]) +
        len(categorized["issue_comments"]) +
        len(categorized["pr_reviews"]) +
        len(categorized["pr_review_comments"]) +
        len(categorized["branch_and_tag_creations"])
    )

    return {
        "method": "events_api",
        "username": username,
        "date": target_date.isoformat(),
        "timezone_offset_hours": tz_offset_hours,
        "total_events": len(filtered_events),
        "total_results": total_results,
        "categorized": categorized,
        "raw_events_count": len(all_events)
    }

# -------------------------------------------------------------
# Method 2: Search API (/search/issues & /search/commits)
# -------------------------------------------------------------
def crawl_user_activity_via_search(client: GitHubClient, username: str, target_date: date) -> Dict[str, Any]:
    """
    Crawls user activity on target_date via GitHub Search API.
    Compatible with dates older than 30 days.
    """
    date_str = target_date.isoformat()
    categorized: Dict[str, List[Any]] = {
        "prs_created": [],
        "prs_updated": [],
        "issues_created": [],
        "prs_reviewed": [],
        "items_commented": [],
        "commits": []
    }
    seen_prs = set()

    # 1. Search PRs created
    try:
        q_prs = f"author:{username} type:pr created:{date_str}"
        res_prs = client.request("/search/issues", {"q": q_prs, "per_page": 100}, description=f"Search PRs created {date_str}")
        for item in res_prs.get("items", []):
            num = item.get("number")
            repo = item.get("repository_url", "").replace("https://api.github.com/repos/", "")
            seen_prs.add((repo, num))
            is_merged = bool(item.get("pull_request", {}).get("merged_at"))
            state = "merged" if is_merged else item.get("state", "open")
            categorized["prs_created"].append({
                "number": num,
                "title": item.get("title", ""),
                "state": state,
                "repo": repo,
                "url": item.get("html_url", ""),
                "created_at": item.get("created_at"),
                "time": format_time_hh_mm(item.get("created_at"))
            })
    except Exception:
        pass

    # 2. Search PRs updated
    try:
        q_prs_up = f"author:{username} type:pr updated:{date_str}"
        res_prs_up = client.request("/search/issues", {"q": q_prs_up, "per_page": 100}, description=f"Search PRs updated {date_str}")
        for item in res_prs_up.get("items", []):
            num = item.get("number")
            repo = item.get("repository_url", "").replace("https://api.github.com/repos/", "")
            if (repo, num) not in seen_prs:
                seen_prs.add((repo, num))
                is_merged = bool(item.get("pull_request", {}).get("merged_at"))
                state = "merged" if is_merged else item.get("state", "open")
                categorized["prs_updated"].append({
                    "number": num,
                    "title": item.get("title", ""),
                    "state": state,
                    "repo": repo,
                    "url": item.get("html_url", ""),
                    "updated_at": item.get("updated_at"),
                    "created_at": item.get("updated_at"),
                    "time": format_time_hh_mm(item.get("updated_at"))
                })
    except Exception:
        pass

    # 3. Search Issues created
    try:
        q_issues = f"author:{username} type:issue created:{date_str}"
        res_issues = client.request("/search/issues", {"q": q_issues, "per_page": 100}, description=f"Search Issues created {date_str}")
        for item in res_issues.get("items", []):
            repo = item.get("repository_url", "").replace("https://api.github.com/repos/", "")
            categorized["issues_created"].append({
                "number": item.get("number"),
                "title": item.get("title", ""),
                "state": item.get("state", "open"),
                "repo": repo,
                "url": item.get("html_url", ""),
                "created_at": item.get("created_at"),
                "time": format_time_hh_mm(item.get("created_at"))
            })
    except Exception:
        pass

    # 4. Search PRs reviewed
    try:
        q_review = f"reviewed-by:{username} type:pr updated:{date_str}"
        res_reviews = client.request("/search/issues", {"q": q_review, "per_page": 50}, description=f"Search PRs reviewed {date_str}")
        for item in res_reviews.get("items", []):
            repo = item.get("repository_url", "").replace("https://api.github.com/repos/", "")
            categorized["prs_reviewed"].append({
                "number": item.get("number"),
                "title": item.get("title", ""),
                "state": item.get("state", "open"),
                "repo": repo,
                "url": item.get("html_url", ""),
                "updated_at": item.get("updated_at"),
                "created_at": item.get("updated_at"),
                "time": format_time_hh_mm(item.get("updated_at"))
            })
    except Exception:
        pass

    # 5. Search Issues/PRs commented
    try:
        q_comment = f"commenter:{username} updated:{date_str}"
        res_comments = client.request("/search/issues", {"q": q_comment, "per_page": 50}, description=f"Search Comments {date_str}")
        for item in res_comments.get("items", []):
            is_pr = "pull_request" in item
            repo = item.get("repository_url", "").replace("https://api.github.com/repos/", "")
            categorized["items_commented"].append({
                "type": "pull_request" if is_pr else "issue",
                "number": item.get("number"),
                "title": item.get("title", ""),
                "url": item.get("html_url", ""),
                "repo": repo,
                "updated_at": item.get("updated_at"),
                "created_at": item.get("updated_at"),
                "time": format_time_hh_mm(item.get("updated_at"))
            })
    except Exception:
        pass

    # 6. Search Commits
    try:
        q_commits = f"author:{username} author-date:{date_str}"
        res_commits = client.request("/search/commits", {"q": q_commits, "per_page": 100}, description=f"Search Commits {date_str}")
        for item in res_commits.get("items", []):
            commit_data = item.get("commit", {})
            repo_info = item.get("repository", {})
            repo_full_name = repo_info.get("name") or repo_info.get("full_name") or item.get("url", "").split("/commits/")[0].replace("https://api.github.com/repos/", "")
            author_date = commit_data.get("author", {}).get("date")
            time_hh_mm = format_time_hh_mm(author_date)
            msg = commit_data.get("message", "").split("\n")[0].strip()

            categorized["commits"].append({
                "sha": item.get("sha", "")[:7],
                "message": msg,
                "repo": repo_full_name,
                "url": item.get("html_url", ""),
                "author_date": author_date,
                "created_at": author_date,
                "time": time_hh_mm
            })
    except Exception:
        pass

    total_items = (
        len(categorized["prs_created"]) +
        len(categorized["prs_updated"]) +
        len(categorized["issues_created"]) +
        len(categorized["prs_reviewed"]) +
        len(categorized["items_commented"]) +
        len(categorized["commits"])
    )

    return {
        "method": "search_api",
        "username": username,
        "date": date_str,
        "total_results": total_items,
        "categorized": categorized
    }

# -------------------------------------------------------------
# Unified Activity Crawl (Auto Dispatch)
# -------------------------------------------------------------
def crawl_user_activity(
    client: GitHubClient,
    username: str,
    target_date: date,
    mode: str = "auto",
    tz_offset_hours: int = 7
) -> Dict[str, Any]:
    """
    Crawls activity using either 'events' API or 'search' API.
    If mode == 'auto', uses 'events' API for the past 30 days, falling back to 'search' API if 0 events found.
    """
    today = date.today()
    days_ago = (today - target_date).days

    if mode == "events":
        return crawl_user_events_by_date(client, username, target_date, tz_offset_hours)
    elif mode == "search":
        return crawl_user_activity_via_search(client, username, target_date)
    else:  # auto
        if 0 <= days_ago <= 30:
            res = crawl_user_events_by_date(client, username, target_date, tz_offset_hours)
            if res.get("total_results", 0) > 0 or days_ago <= 2:
                return res
            # Fallback to search if events returned nothing for older days
            search_res = crawl_user_activity_via_search(client, username, target_date)
            if search_res.get("total_results", 0) > 0:
                return search_res
            return res
        else:
            return crawl_user_activity_via_search(client, username, target_date)

# -------------------------------------------------------------
# Compact Collector for Timesheet & Worklogs
# -------------------------------------------------------------
def collect_today_github(
    username: Optional[str] = None,
    target_date: Optional[Any] = None,
    token: Optional[str] = None,
    mode: str = "auto"
) -> Dict[str, Any]:
    """
    Main entry point for timesheet logging & calendar sync.
    Returns normalized compact data:
    - 'commits': [{'hash', 'time', 'msg', 'repo'}]
    - 'prs': [{'num', 'title', 'state'}]
    - 'issues': [{'num', 'title', 'state'}]
    - 'categorized': raw category dict
    """
    client = GitHubClient(token=token)
    resolved_user = username or get_github_username(client)
    resolved_date = parse_target_date(target_date)

    if not resolved_user:
        return {
            "available": False,
            "error": "Could not determine GitHub username. Set GITHUB_USER or login via gh.",
            "commits": [],
            "prs": [],
            "issues": []
        }

    crawl_res = crawl_user_activity(client, resolved_user, resolved_date, mode=mode, tz_offset_hours=7)
    cat = crawl_res.get("categorized", {})

    # Build compact commits list
    compact_commits = []
    for c in cat.get("commits", []):
        compact_commits.append({
            "hash": c.get("sha", "")[:7],
            "time": c.get("time", "12:00"),
            "msg": c.get("message", ""),
            "repo": c.get("repo", "")
        })

    # Build compact PRs list (combining created, updated, and reviewed)
    compact_prs = []
    seen_pr_nums = set()

    for pr in cat.get("pull_requests", []) + cat.get("prs_created", []) + cat.get("prs_updated", []):
        p_num = pr.get("number")
        if p_num and p_num not in seen_pr_nums:
            seen_pr_nums.add(p_num)
            compact_prs.append({
                "num": p_num,
                "title": pr.get("title", ""),
                "state": pr.get("state", "open")
            })

    for pr in cat.get("pr_reviews", []) + cat.get("prs_reviewed", []):
        p_num = pr.get("pr_number") or pr.get("number")
        if p_num and p_num not in seen_pr_nums:
            seen_pr_nums.add(p_num)
            compact_prs.append({
                "num": p_num,
                "title": f"[Review] {pr.get('pr_title') or pr.get('title', '')}",
                "state": pr.get("state", "open")
            })

    # Build compact issues list
    compact_issues = []
    for iss in cat.get("issues", []) + cat.get("issues_created", []):
        compact_issues.append({
            "num": iss.get("number"),
            "title": iss.get("title", ""),
            "state": iss.get("state", "open")
        })

    return {
        "available": True,
        "username": resolved_user,
        "date": resolved_date.isoformat(),
        "commits": compact_commits,
        "prs": compact_prs,
        "issues": compact_issues,
        "categorized": cat,
        "method": crawl_res.get("method", mode)
    }

# -------------------------------------------------------------
# Markdown Report Generator
# -------------------------------------------------------------
def generate_markdown_report(data: Dict[str, Any]) -> str:
    """Creates clean markdown summary report from crawled data."""
    username = data.get("username", "user")
    date_str = data.get("date", "")
    method = data.get("method", "auto")
    cat = data.get("categorized", {})

    lines = [
        f"# 📊 Báo Cáo Hoạt Động GitHub: @{username}",
        f"- **Ngày:** `{date_str}`",
        f"- **Phương thức:** `{method}`",
        f"- **Thời gian:** `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`",
        "",
        "---"
    ]

    commits = cat.get("commits", [])
    prs = cat.get("pull_requests", []) or cat.get("prs_created", [])
    issues = cat.get("issues", []) or cat.get("issues_created", [])
    comments = cat.get("issue_comments", []) or cat.get("items_commented", [])
    reviews = cat.get("pr_reviews", []) or cat.get("prs_reviewed", [])

    lines.append("## 📈 Thống Kê Tổng Quan")
    lines.append("| Hoạt động | Số lượng |")
    lines.append("| :--- | :--- |")
    lines.append(f"| 🔨 Commits | **{len(commits)}** |")
    lines.append(f"| 🔀 Pull Requests | **{len(prs)}** |")
    lines.append(f"| 🐛 Issues | **{len(issues)}** |")
    lines.append(f"| 💬 Comments | **{len(comments)}** |")
    lines.append(f"| 👀 Reviews | **{len(reviews)}** |")
    lines.append("")

    if commits:
        lines.append("## 🔨 Commits")
        for c in commits:
            lines.append(f"- [`{c.get('sha', '')[:7]}`]({c.get('url', '#')}) in **{c.get('repo')}**: {c.get('message')}")
        lines.append("")

    if prs:
        lines.append("## 🔀 Pull Requests")
        for p in prs:
            lines.append(f"- [#{p.get('number')}]({p.get('url', '#')}) {p.get('title')} (**{p.get('repo')}**)")
        lines.append("")

    if issues:
        lines.append("## 🐛 Issues")
        for iss in issues:
            lines.append(f"- [#{iss.get('number')}]({iss.get('url', '#')}) {iss.get('title')} (**{iss.get('repo')}**)")
        lines.append("")

    return "\n".join(lines)

def main():
    parser = argparse.ArgumentParser(description="Deterministic GitHub user activity collector and crawler.")
    parser.add_argument("username", nargs="?", default=None, help="GitHub username")
    parser.add_argument("date", nargs="?", default=None, help="Target date YYYY-MM-DD")
    parser.add_argument("--mode", choices=["auto", "events", "search"], default="auto", help="Crawl mode")
    parser.add_argument("--token", default=None, help="GitHub Token")
    parser.add_argument("--tz", type=int, default=7, help="Timezone offset hours (default: 7)")
    parser.add_argument("--detailed", action="store_true", help="Print full JSON output")
    parser.add_argument("--output-dir", default=None, help="Export JSON & Markdown to directory")

    args = parser.parse_args()
    client = GitHubClient(token=args.token)
    resolved_user = args.username or get_github_username(client)
    resolved_date = parse_target_date(args.date)

    crawl_res = crawl_user_activity(client, resolved_user, resolved_date, mode=args.mode, tz_offset_hours=args.tz)

    if args.output_dir:
        os.makedirs(args.output_dir, exist_ok=True)
        json_path = os.path.join(args.output_dir, f"github_activity_{resolved_user}_{resolved_date.isoformat()}.json")
        md_path = os.path.join(args.output_dir, f"github_activity_{resolved_user}_{resolved_date.isoformat()}.md")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(crawl_res, f, ensure_ascii=False, indent=2)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(generate_markdown_report(crawl_res))
        print(f"[✓] Saved JSON to {json_path}")
        print(f"[✓] Saved Markdown report to {md_path}")
        return

    if args.detailed:
        print(json.dumps(crawl_res, indent=2, ensure_ascii=False))
    else:
        # Timesheet logger compact format
        compact_res = collect_today_github(username=resolved_user, target_date=resolved_date, token=args.token, mode=args.mode)
        output_payload = {
            "available": compact_res.get("available", False),
            "username": compact_res.get("username", ""),
            "date": compact_res.get("date", ""),
            "commits": compact_res.get("commits", []),
            "prs": compact_res.get("prs", [])
        }
        if compact_res.get("issues"):
            output_payload["issues"] = compact_res.get("issues")
        print(json.dumps(output_payload, separators=(",", ":"), ensure_ascii=False))

if __name__ == "__main__":
    main()
