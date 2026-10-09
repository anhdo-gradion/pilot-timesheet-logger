#!/usr/bin/env python3
"""Build a deterministic, stacked worklog from GitHub and Calendar JSON."""

import argparse
import json
import re
import sys
from pathlib import Path
from datetime import date


CLASSIFICATION = "Gradion Intern Academy 2026"
TASK_TAG = "#SE"
WORK_PERIODS = {
    "full": ((9 * 60, 12 * 60), (13 * 60, 18 * 60)),
    "morning": ((9 * 60, 12 * 60),),
    "afternoon": ((13 * 60, 18 * 60),),
}


def minutes(value, field):
    try:
        hour, minute = value.split(":", 1)
        result = int(hour) * 60 + int(minute)
    except (AttributeError, ValueError):
        raise ValueError(f"{field} must use HH:MM format") from None
    if not (0 <= result < 24 * 60):
        raise ValueError(f"{field} is outside the valid day")
    return result


def clock(value):
    return f"{value // 60:02d}:{value % 60:02d}"


def read_json(path):
    with open(path, "r", encoding="utf-8") as source:
        return json.load(source)


def unique_prs(github_data, work_windows):
    if not github_data.get("available", False):
        raise ValueError(f"GitHub collection unavailable: {github_data.get('error', 'unknown error')}")
    all_tasks = {}
    for item in github_data.get("tasks", []):
        if item.get("kind") != "PR" or not item.get("number"):
            continue
        key = (item.get("repo", ""), str(item["number"]))
        all_tasks.setdefault(key, item)
    expected = github_data.get("task_count", {}).get("pull_requests")
    if expected is not None and expected != len(all_tasks):
        raise ValueError(f"GitHub PR count mismatch: task_count says {expected}, tasks contain {len(all_tasks)} unique PRs")

    def in_period(value):
        try:
            point = minutes(value, "GitHub activity time")
        except ValueError:
            return False
        return any(start <= point < end for start, end in work_windows)

    selected = []
    for item in all_tasks.values():
        commits = [commit for commit in item.get("commits", []) if in_period(commit.get("time"))]
        activity = [event for event in item.get("activity", []) if in_period(event.get("time"))]
        detailed_timestamps_exist = bool(item.get("commits") or item.get("activity"))
        if not commits and not activity:
            if detailed_timestamps_exist or not in_period(item.get("time")):
                continue
        selected.append({**item, "commits": commits, "activity": activity})

    return sorted(selected, key=lambda item: (first_activity(item), item.get("repo", ""), int(item["number"])))


def first_activity(task):
    # Prefer a real commit timestamp when the PR has commits. Review-only PRs
    # use their review/comment timestamp instead.
    commits = [commit.get("time") for commit in task.get("commits", []) if commit.get("time")]
    activity_times = [activity.get("time") for activity in task.get("activity", []) if activity.get("time")]
    times = commits or activity_times or ([task["time"]] if task.get("time") else [])
    valid = []
    for value in times:
        try:
            valid.append(minutes(value, "GitHub activity time"))
        except ValueError:
            continue
    return min(valid, default=23 * 60 + 59)


def description_for_pr(task, part=None):
    number = task["number"]
    title = " ".join((task.get("title") or "PR work").split())
    url = task.get("url") or f"https://github.com/{task.get('repo', '')}/pull/{number}"
    subjects = []
    for commit in task.get("commits", []):
        subject = (commit.get("message") or "").splitlines()[0].strip()
        is_generic_merge = re.match(
            r"^(merge pull request|merge branch|merge remote-tracking branch)\b",
            subject,
            flags=re.IGNORECASE,
        )
        if subject and not is_generic_merge and subject not in subjects:
            subjects.append(subject)
    activity_text = []
    labels = {
        "code_review": "reviewed code",
        "code_review_comment": "left an inline code review comment",
        "pr_comment": "commented on the PR",
    }
    for activity in task.get("activity", []):
        kind = labels.get(activity.get("kind"))
        if not kind:
            continue
        detail = " ".join((activity.get("detail") or "").split())
        value = f"{kind}: {detail}" if detail else kind
        if value not in activity_text:
            activity_text.append(value)
    parts = [f"PR #{number} — {title}."]
    if subjects:
        parts.append("Changes: " + "; ".join(subjects[:4]) + ".")
    else:
        body = " ".join((task.get("body") or "").split())
        if body:
            body = re.sub(r"https?://\S+", "", body).strip()
            if body:
                parts.append("Summary: " + body[:420].rstrip(" .") + ".")
    if activity_text:
        parts.append("Code review: " + "; ".join(activity_text[:4]) + ".")
    if part:
        parts.append(f"Work block {part}.")
    parts.append(url)
    return " ".join(parts)[:3000]


def calendar_entries(calendar_data, date_value, work_windows):
    if not calendar_data.get("available", False):
        raise ValueError(f"Calendar collection unavailable: {calendar_data.get('error', 'unknown error')}")
    entries = []
    occupied = []
    for event in calendar_data.get("events", []):
        if event.get("all_day"):
            continue
        start = minutes(event.get("start"), "Calendar event start")
        end = minutes(event.get("end"), "Calendar event end")
        if end <= start:
            raise ValueError(f"Calendar event has invalid time range: {event.get('title', '(untitled)')}")
        window = next(((left, right) for left, right in work_windows if left <= start and end <= right), None)
        if window is None:
            if any(start < right and end > left for left, right in work_windows):
                raise ValueError(
                    f"Calendar event overlaps work hours but crosses a work boundary: "
                    f"{event.get('title', '(untitled)')} {clock(start)}-{clock(end)}"
                )
            continue
        title = " ".join((event.get("title") or "(Untitled event)").split())
        desc = " ".join((event.get("desc") or "").split())
        description = f"Meeting / Discussion: {title}"
        if desc:
            description += f" — {desc}"
        description += ". Sources: Google Calendar"
        entries.append({"start": start, "end": end, "description": description})
        occupied.append((start, end, title))
    occupied.sort()
    for previous, current in zip(occupied, occupied[1:]):
        if current[0] < previous[1]:
            raise ValueError(f"Calendar events overlap: {previous[2]} and {current[2]}")
    return entries, occupied


def open_gaps(occupied, work_windows):
    gaps = []
    for window_start, window_end in work_windows:
        cursor = window_start
        for start, end, _title in occupied:
            if start < window_start or end > window_end:
                continue
            if start > cursor:
                gaps.append([cursor, start])
            cursor = max(cursor, end)
        if cursor < window_end:
            gaps.append([cursor, window_end])
    return gaps


def allocate_prs(tasks, gaps):
    """Assign each free work minute to the closest Git activity timestamp.

    Calendar intervals stay fixed. Git timestamps act as sorted anchors; the
    midpoint between adjacent anchors divides ownership of the remaining
    workday, so blocks follow actual commit/review timing rather than an
    arbitrary equal split starting at 09:00.
    """
    if not tasks:
        return []

    gap_axes = []
    axis_cursor = 0
    for start, end in gaps:
        gap_axes.append((start, end, axis_cursor, axis_cursor + end - start))
        axis_cursor += end - start
    total_minutes = axis_cursor
    if total_minutes < len(tasks):
        raise ValueError("There are fewer free work minutes than PRs; cannot give every PR a valid work block.")

    def to_axis(value):
        """Project an event time onto free time, skipping lunch and Calendar."""
        elapsed = 0
        candidates = []
        for start, end in gaps:
            if start <= value <= end:
                return elapsed + value - start
            candidates.append((abs(value - start), elapsed))
            candidates.append((abs(value - end), elapsed + end - start))
            elapsed += end - start
        return min(candidates)[1]

    anchored = sorted(
        ((to_axis(first_activity(task)), task) for task in tasks),
        key=lambda pair: (pair[0], pair[1].get("repo", ""), int(pair[1]["number"])),
    )

    # A one-minute minimum keeps every unique PR represented, including when
    # multiple Git events share the same minute. Clamp the anchors to leave
    # enough room for the remaining PRs.
    positions = []
    for index, (position, _task) in enumerate(anchored):
        low = positions[-1] + 1 if positions else 0
        high = total_minutes - (len(anchored) - index)
        position = max(low, min(position, high))
        positions.append(position)

    boundaries = [0]
    boundaries.extend((left + right) // 2 for left, right in zip(positions, positions[1:]))
    boundaries.append(total_minutes)

    blocks = []
    for index, (_position, task) in enumerate(anchored):
        owned_start, owned_end = boundaries[index], boundaries[index + 1]
        task_blocks = []
        for gap_start, gap_end, gap_axis_start, gap_axis_end in gap_axes:
            segment_start = max(owned_start, gap_axis_start)
            segment_end = min(owned_end, gap_axis_end)
            if segment_start >= segment_end:
                continue
            wall_start = gap_start + segment_start - gap_axis_start
            wall_end = gap_start + segment_end - gap_axis_start
            task_blocks.append((wall_start, wall_end))

        task_blocks = [(start, end) for start, end in task_blocks if end > start]
        if not task_blocks:
            raise ValueError(f"Scheduler could not assign a work block to PR #{task['number']}.")
        for part, (start, end) in enumerate(task_blocks, start=1):
            blocks.append({
                "start": start,
                "end": end,
                "description": description_for_pr(
                    task,
                    f"{part}/{len(task_blocks)}" if len(task_blocks) > 1 else None,
                ),
            })
    return blocks


def build_entries(date_value, github_data, calendar_data, work_period="full"):
    work_windows = WORK_PERIODS[work_period]
    tasks = unique_prs(github_data, work_windows)
    meetings, occupied = calendar_entries(calendar_data, date_value, work_windows)
    gaps = open_gaps(occupied, work_windows)
    work = allocate_prs(tasks, gaps)
    rows = meetings + work
    rows.sort(key=lambda item: (item["start"], item["end"], item["description"]))
    return [
        {"arguments": {
            "date": date_value,
            "startTime": clock(item["start"]),
            "endTime": clock(item["end"]),
            "classification": CLASSIFICATION,
            "description": item["description"],
            "task": TASK_TAG,
            "billable": False,
        }}
        for item in rows
    ]


def main():
    parser = argparse.ArgumentParser(description="Deterministically schedule PR work around Google Calendar events.")
    parser.add_argument("--date", required=True, help="Target date YYYY-MM-DD")
    parser.add_argument("--github", default="github-data.json", help="GitHub collector JSON")
    parser.add_argument("--calendar", default="calendar-data.json", help="Calendar collector JSON")
    parser.add_argument("--output", default="timesheet-entries.json", help="Output JSON path, or - for stdout")
    parser.add_argument(
        "--work-period",
        choices=tuple(WORK_PERIODS),
        default="full",
        help="Schedule full day, morning (09:00-12:00), or afternoon (13:00-18:00); default: full",
    )
    args = parser.parse_args()
    try:
        date_value = args.date
        try:
            date.fromisoformat(date_value)
        except ValueError:
            raise ValueError("--date must use YYYY-MM-DD") from None
        entries = build_entries(date_value, read_json(args.github), read_json(args.calendar), args.work_period)
        rendered = json.dumps(entries, ensure_ascii=False, indent=2) + "\n"
        if args.output == "-":
            sys.stdout.write(rendered)
        else:
            Path(args.output).write_text(rendered, encoding="utf-8")
        print(f"Built {len(entries)} entries from {len({(x['arguments']['description'].split(' — ', 1)[0]) for x in entries if x['arguments']['description'].startswith('PR #')})} unique PRs.", file=sys.stderr)
        if not any(item["arguments"]["description"].startswith("PR #") for item in entries):
            print("No PR work was collected; free schedule gaps were left empty.", file=sys.stderr)
    except (OSError, json.JSONDecodeError, ValueError, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
