#!/usr/bin/env python3
"""
[Script Step] collect_github.py
Date-scoped GitHub Search API collector for commits, PRs, comments, and reviews.
"""

import os
import sys
import json
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
    """Retrieves GitHub token from the environment or the GitHub CLI credential store."""
    token = os.environ.get("GITHUB_TOKEN")
    if token and token.strip():
        return token.strip()

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

    def _get_headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "Gradion-Timesheet-Logger",
            "X-GitHub-Api-Version": API_VERSION,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def request(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self.base_url}{endpoint}"
        if params:
            encoded_params = urllib.parse.urlencode(params)
            url = f"{url}?{encoded_params}"

        req = urllib.request.Request(url, headers=self._get_headers())
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw_body = resp.read().decode("utf-8")
                return json.loads(raw_body)

        except urllib.error.HTTPError as e:
            try:
                error_body = e.read().decode("utf-8", errors="replace")
                error_data = json.loads(error_body)
            except json.JSONDecodeError:
                error_data = {}
            if e.code == 404:
                return [] if "/events" in endpoint else {}
            return {"error": error_data.get("message", f"GitHub API returned HTTP {e.code}")}

        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise RuntimeError(f"GitHub API request failed for {endpoint}: {e}") from None
        except json.JSONDecodeError:
            raise RuntimeError(f"GitHub API returned invalid JSON for {endpoint}.") from None

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
    return None

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
            events = client.request(f"/users/{username}/events", {"per_page": 100, "page": page})
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
        "pr_comments": [],
        "pr_reviews": [],
        "pr_review_comments": []
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
                        "sha": c.get("sha", ""),
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
                    "sha": head_sha,
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
                "body": (pr.get("body") or "").strip()[:240],
                "url": pr.get("html_url", f"https://github.com/{repo_name}/pull/{pr.get('number')}"),
                "merged": pr.get("merged", False),
                "state": "merged" if pr.get("merged") else pr.get("state", "open"),
                "created_at": created_at,
                "time": time_hh_mm
            })

        elif ev_type == "IssueCommentEvent":
            issue = payload.get("issue", {})
            comment = payload.get("comment", {})
            is_pr = "pull_request" in issue
            if is_pr:
                categorized["pr_comments"].append({
                    "type": "pull_request",
                    "repo": repo_name,
                    "number": issue.get("number"),
                    "title": issue.get("title", f"PR #{issue.get('number')}"),
                    "comment_body": comment.get("body", "")[:100],
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

    total_results = (
        len(categorized["commits"]) +
        len(categorized["pull_requests"]) +
        len(categorized["pr_comments"]) +
        len(categorized["pr_reviews"]) +
        len(categorized["pr_review_comments"])
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
def crawl_user_activity_via_search(client: GitHubClient, username: str, target_date: date, tz_offset_hours: int = 7) -> Dict[str, Any]:
    """
    Crawls user activity on target_date via GitHub Search API.
    Compatible with dates older than 30 days.
    """
    date_str = target_date.isoformat()
    categorized: Dict[str, List[Any]] = {
        "prs_created": [],
        "prs_updated": [],
        "prs_reviewed": [],
        "pr_comments": [],
        "commits": []
    }
    seen_prs = set()
    collection_errors = []

    def search_all(endpoint, query, per_page=100):
        items = []
        for page in range(1, 11):
            response = client.request(endpoint, {"q": query, "per_page": per_page, "page": page})
            if not isinstance(response, dict) or response.get("error"):
                raise RuntimeError(response.get("error", "GitHub Search API returned an invalid response."))
            page_items = response.get("items", [])
            items.extend(page_items)
            total = int(response.get("total_count", len(items)))
            if len(items) >= total or len(page_items) < per_page:
                break
        if len(items) < int(response.get("total_count", len(items))):
            raise RuntimeError(f"GitHub Search API result exceeds the retrievable 1,000-item limit for: {query}")
        return items

    # 1. Search PRs created
    try:
        q_prs = f"author:{username} type:pr created:{date_str}"
        for item in search_all("/search/issues", q_prs):
            num = item.get("number")
            repo = item.get("repository_url", "").replace("https://api.github.com/repos/", "")
            seen_prs.add((repo, num))
            is_merged = bool(item.get("pull_request", {}).get("merged_at"))
            state = "merged" if is_merged else item.get("state", "open")
            categorized["prs_created"].append({
                "number": num,
                "title": item.get("title", ""),
                "body": (item.get("body") or "").strip()[:240],
                "state": state,
                "repo": repo,
                "url": item.get("html_url", ""),
                "created_at": item.get("created_at"),
                "time": format_time_hh_mm(item.get("created_at"), tz_offset_hours)
            })
    except Exception as exc:
        collection_errors.append(f"prs_created: {exc}")

    # 2. Search PRs updated
    try:
        q_prs_up = f"author:{username} type:pr updated:{date_str}"
        for item in search_all("/search/issues", q_prs_up):
            num = item.get("number")
            repo = item.get("repository_url", "").replace("https://api.github.com/repos/", "")
            if (repo, num) not in seen_prs:
                seen_prs.add((repo, num))
                is_merged = bool(item.get("pull_request", {}).get("merged_at"))
                state = "merged" if is_merged else item.get("state", "open")
                categorized["prs_updated"].append({
                    "number": num,
                    "title": item.get("title", ""),
                    "body": (item.get("body") or "").strip()[:240],
                    "state": state,
                    "repo": repo,
                    "url": item.get("html_url", ""),
                    "updated_at": item.get("updated_at"),
                    "created_at": item.get("updated_at"),
                    "time": format_time_hh_mm(item.get("updated_at"), tz_offset_hours)
                })
    except Exception as exc:
        collection_errors.append(f"prs_updated: {exc}")

    # Search PRs reviewed
    try:
        q_review = f"reviewed-by:{username} type:pr updated:{date_str}"
        for item in search_all("/search/issues", q_review, per_page=50):
            repo = item.get("repository_url", "").replace("https://api.github.com/repos/", "")
            categorized["prs_reviewed"].append({
                "number": item.get("number"),
                "title": item.get("title", ""),
                "state": item.get("state", "open"),
                "repo": repo,
                "url": item.get("html_url", ""),
                "updated_at": item.get("updated_at"),
                "created_at": item.get("updated_at"),
                "time": format_time_hh_mm(item.get("updated_at"), tz_offset_hours)
            })
    except Exception as exc:
        collection_errors.append(f"prs_reviewed: {exc}")

    # Search comments on PRs only; standalone issue comments are not worklog tasks.
    try:
        q_comment = f"commenter:{username} updated:{date_str}"
        for item in search_all("/search/issues", q_comment, per_page=50):
            is_pr = "pull_request" in item
            repo = item.get("repository_url", "").replace("https://api.github.com/repos/", "")
            if is_pr:
                categorized["pr_comments"].append({
                    "type": "pull_request",
                    "number": item.get("number"),
                    "title": item.get("title", ""),
                    "url": item.get("html_url", ""),
                    "repo": repo,
                    "updated_at": item.get("updated_at"),
                    "created_at": item.get("updated_at"),
                    "time": format_time_hh_mm(item.get("updated_at"), tz_offset_hours)
                })
    except Exception as exc:
        collection_errors.append(f"pr_comments: {exc}")

    # 6. Search Commits
    try:
        q_commits = f"author:{username} author-date:{date_str}"
        for item in search_all("/search/commits", q_commits):
            commit_data = item.get("commit", {})
            repo_info = item.get("repository", {})
            repo_full_name = repo_info.get("full_name") or repo_info.get("name") or item.get("url", "").split("/commits/")[0].replace("https://api.github.com/repos/", "")
            author_date = commit_data.get("author", {}).get("date")
            time_hh_mm = format_time_hh_mm(author_date, tz_offset_hours)
            msg = commit_data.get("message", "").strip()

            categorized["commits"].append({
                "sha": item.get("sha", ""),
                "message": msg,
                "repo": repo_full_name,
                "url": item.get("html_url", ""),
                "author_date": author_date,
                "created_at": author_date,
                "time": time_hh_mm
            })
    except Exception as exc:
        collection_errors.append(f"commits: {exc}")

    total_items = (
        len(categorized["prs_created"]) +
        len(categorized["prs_updated"]) +
        len(categorized["prs_reviewed"]) +
        len(categorized["pr_comments"]) +
        len(categorized["commits"])
    )

    return {
        "method": "search_api",
        "username": username,
        "date": date_str,
        "total_results": total_items,
        "categorized": categorized,
        "collection_errors": collection_errors,
    }

# -------------------------------------------------------------
# Unified Activity Crawl (Search API)
# -------------------------------------------------------------
def crawl_user_activity(
    client: GitHubClient,
    username: str,
    target_date: date,
    mode: str = "search",
    tz_offset_hours: int = 7
) -> Dict[str, Any]:
    """
    Crawls date-scoped work activity using only GitHub Search API.

    `mode` remains accepted for compatibility but does not enable the Activity Events API.
    """
    return crawl_user_activity_via_search(client, username, target_date, tz_offset_hours)

# Compact daily data for the worklog model. Raw category payloads never leave this function.
def collect_today_github(username=None, target_date=None, token=None, mode="search", tz_offset_hours=7):
    client = GitHubClient(token=token)
    username = username or get_github_username(client)
    day = parse_target_date(target_date)
    if not username:
        return {
            "available": False,
            "error": "GitHub username unavailable.",
            "tasks": [],
            "task_count": {"pull_requests": 0, "total": 0},
        }

    result = crawl_user_activity(client, username, day, mode=mode, tz_offset_hours=tz_offset_hours)
    if result.get("collection_errors"):
        return {
            "available": False,
            "error": "; ".join(result["collection_errors"]),
            "date": day.isoformat(),
            "tasks": [],
            "task_count": {"pull_requests": 0, "total": 0},
        }
    categories = result.get("categorized", {})
    tasks = {}

    def ensure_task(kind, repo, item):
        number = item.get("number") or item.get("pr_number")
        if not number:
            return None
        key = (kind, repo, str(number))
        task = tasks.setdefault(key, {
            "kind": kind,
            "repo": repo,
            "number": number,
            "title": (item.get("title") or item.get("pr_title") or "").strip()[:160],
            "url": item.get("url") or f"https://github.com/{repo}/{'pull' if kind == 'PR' else 'issues'}/{number}",
            "time": item.get("time", "12:00"),
            "commits": [],
            "activity": [],
            "has_direct_work": False,
        })
        if not task["title"]:
            task["title"] = (item.get("title") or item.get("pr_title") or "").strip()[:160]
        if item.get("body") and not task.get("body"):
            task["body"] = item["body"][:240]
        task["time"] = min(task["time"], item.get("time", task["time"]))
        return task

    pr_categories = {
        "prs_created": "opened", "prs_updated": "updated", "prs_reviewed": "reviewed",
        "pr_comments": "commented", "pr_reviews": "reviewed", "pr_review_comments": "reviewed",
    }
    for category, action in pr_categories.items():
        for item in categories.get(category, []):
            repo = item.get("repo", "")
            task = ensure_task("PR", repo, item)
            if task:
                if action in {"opened", "updated"}:
                    task["has_direct_work"] = True
                if action in {"reviewed", "commented"}:
                    continue
                task["activity"].append({
                    "kind": action,
                    "time": item.get("time", "12:00"),
                    "detail": (item.get("comment_body") or item.get("state") or "")[:160],
                })

    associated_pr_cache = {}
    unlinked_commits = []
    for commit in categories.get("commits", []):
        repo = commit.get("repo", "")
        sha = commit.get("sha", "")
        related_prs = []
        if repo and sha:
            cache_key = (repo, sha)
            if cache_key not in associated_pr_cache:
                linked = client.request(f"/repos/{repo}/commits/{sha}/pulls")
                associated_pr_cache[cache_key] = []
                if isinstance(linked, list):
                    associated_pr_cache[cache_key] = [
                        {
                            "number": pr.get("number"),
                            "title": pr.get("title", ""),
                            "url": pr.get("html_url", f"https://github.com/{repo}/pull/{pr.get('number')}"),
                            "body": (pr.get("body") or "").strip()[:240],
                        }
                        for pr in linked
                        if isinstance(pr, dict) and pr.get("number")
                    ]
            related_prs = associated_pr_cache[cache_key]
        message = (commit.get("message") or "").strip()
        commit_entry = {
            "sha": sha[:7], "time": commit.get("time", "12:00"),
            "message": message[:320], "url": commit.get("url", ""),
        }
        if related_prs:
            for pr in related_prs:
                task = ensure_task("PR", repo, pr)
                if task:
                    task["has_direct_work"] = True
                    if all(existing["sha"] != commit_entry["sha"] for existing in task["commits"]):
                        task["commits"].append(commit_entry)
        else:
            unlinked_commits.append({"repo": repo, **commit_entry})

    activity_errors = []

    def list_all(endpoint):
        rows = []
        for page in range(1, 11):
            response = client.request(endpoint, {"per_page": 100, "page": page})
            if not isinstance(response, list):
                raise RuntimeError(response.get("error", f"GitHub returned invalid data for {endpoint}"))
            rows.extend(response)
            if len(response) < 100:
                break
        return rows

    local_tz = timezone(timedelta(hours=tz_offset_hours))
    for task in tasks.values():
        if task["kind"] != "PR" or not task["repo"]:
            continue
        base = f"/repos/{task['repo']}/pulls/{task['number']}"
        for endpoint, activity_kind, timestamp_field in (
            (f"{base}/reviews", "code_review", "submitted_at"),
            (f"{base}/comments", "code_review_comment", "created_at"),
            (f"/repos/{task['repo']}/issues/{task['number']}/comments", "pr_comment", "created_at"),
        ):
            try:
                records = list_all(endpoint)
            except Exception as exc:
                activity_errors.append(f"PR #{task['number']} {activity_kind}: {exc}")
                continue
            for record in records:
                actor = record.get("user", {}).get("login", "")
                timestamp = record.get(timestamp_field)
                if actor.casefold() != username.casefold() or not timestamp:
                    continue
                if parse_iso_datetime(timestamp).astimezone(local_tz).date() != day:
                    continue
                detail = (record.get("body") or record.get("state") or "").strip().replace("\n", " ")[:180]
                task["activity"].append({
                    "kind": activity_kind,
                    "time": format_time_hh_mm(timestamp, tz_offset_hours),
                    "detail": detail,
                })
                activity_time = format_time_hh_mm(timestamp, tz_offset_hours)
                task["time"] = min(task["time"], activity_time) if task["has_direct_work"] else min(
                    (item["time"] for item in task["activity"] if item["kind"] in {"code_review", "code_review_comment", "pr_comment"}),
                    default=activity_time,
                )

    if activity_errors:
        return {
            "available": False,
            "error": "; ".join(activity_errors),
            "date": day.isoformat(),
            "tasks": [],
            "task_count": {"pull_requests": 0, "total": 0},
        }

    tasks = {
        key: task for key, task in tasks.items()
        if task["has_direct_work"] or any(
            item["kind"] in {"code_review", "code_review_comment", "pr_comment"}
            for item in task["activity"]
        )
    }
    for task in tasks.values():
        task.pop("has_direct_work", None)

    ordered_tasks = sorted(tasks.values(), key=lambda item: (item["time"], item["repo"], item["number"]))
    for task in ordered_tasks:
        task["commits"].sort(key=lambda item: item["time"])
        task["activity"].sort(key=lambda item: item["time"])
    return {
        "available": True,
        "date": day.isoformat(),
        "tasks": ordered_tasks,
        "task_count": {
            "pull_requests": sum(task["kind"] == "PR" for task in ordered_tasks),
            "total": len(ordered_tasks),
        },
        "unlinked_commits": unlinked_commits,
    }

def main():
    parser = argparse.ArgumentParser(description="Collect compact GitHub activity for worklog drafting.")
    parser.add_argument("--username")
    parser.add_argument("--date")
    parser.add_argument("--mode", choices=["search"], default="search", help="GitHub Search API mode")
    parser.add_argument("--timezone-offset", type=float, default=7)
    parser.add_argument("--auth", action="store_true", help="Open GitHub CLI browser login when needed")
    args = parser.parse_args()
    if args.auth:
        ensure_github_auth()
    print(json.dumps(collect_today_github(args.username, args.date, None, args.mode, args.timezone_offset), separators=(",", ":"), ensure_ascii=False))


def ensure_github_auth():
    token = get_github_token()
    if token:
        profile = GitHubClient(token=token).request("/user")
        if isinstance(profile, dict) and profile.get("login"):
            return
    if not shutil.which("gh"):
        raise RuntimeError(
            "GitHub CLI (gh) is not installed. Install it (for example: brew install gh), "
            "then rerun run_worklog.py; it will open GitHub browser login automatically."
        )
    print("GitHub login required. Complete the browser authentication opened by GitHub CLI.", file=sys.stderr)
    result = subprocess.run(
        ["gh", "auth", "login", "--web", "--hostname", "github.com", "--scopes", "repo,read:user,user:email"],
        check=False,
    )
    if result.returncode != 0 or not get_github_token():
        raise RuntimeError("GitHub browser authentication did not complete successfully.")

if __name__ == "__main__":
    main()
