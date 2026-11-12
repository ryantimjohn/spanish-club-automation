"""Checks that each of the repo's GitHub jobs ran when it should, and says which didn't.

GitHub's schedule can run late or skip runs, so this runs on the organizer's computer (not on
GitHub), once a day. It reads the repo's run history (the repo is public) through the `gh` command
and prints one line per job for the given Eastern date.

    python -I -X utf8 run-check/check_runs.py                    # today, so far
    python -I -X utf8 run-check/check_runs.py --date 2026-11-11  # a past day
    python -I -X utf8 run-check/check_runs.py --schedule         # just the expected times

It changes nothing: it never starts or re-runs a job. Exit code 0 means every job ran in its
window, 1 means at least one didn't, and 2 means it couldn't read GitHub.
"""
import argparse
import json
import pathlib
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

REPO = "ryantimjohn/spanish-club-automation"
TZ = ZoneInfo("America/New_York")
WORKFLOWS = pathlib.Path(__file__).resolve().parent.parent / ".github" / "workflows"
DAILY = frozenset(range(7))  # Monday is 0


@dataclass(frozen=True)
class Job:
    name: str
    file: str
    days: frozenset   # Eastern weekdays it must run, Monday = 0
    start: time       # Eastern: a run before this does nothing useful (the script checks the hour)
    deadline: time    # Eastern: a run must have started by now to count as on time
    does: str


# Each job's useful window, from the scripts' own time checks. Keep in step with the workflows.
JOBS = [
    Job("Instagram", "instagram.yml", DAILY, time(8), time(12),
        "posts that day's Instagram posts (and the Friday Facebook post). Runs go on until about 7 PM, and a "
        "post can still go out up to 10 hours late, but never after the night it lists starts. This window is the "
        "early warning"),
    Job("Luma invites", "luma-invites.yml", DAILY, time(10), time(12),
        "sends Luma invites for events 6 days out; sends only at or after 10 AM"),
    Job("Meetup", "meetup.yml", DAILY, time(6), time(12),
        "copies Luma events to Meetup (off until Meetup approves the API)"),
    Job("Eventbrite", "eventbrite.yml", DAILY, time(6), time(12),
        "copies Luma events to Eventbrite"),
    Job("Discord", "discord.yml", frozenset({4}), time(9), time(12),
        "posts next week's events in Discord on Fridays; posts only at or after 9 AM"),
]


# ---- the workflows' cron lines -------------------------------------------------------------

def crons(path):
    """The cron strings in a workflow file's schedule."""
    return re.findall(r'^\s*-\s*cron:\s*["\']([^"\']+)["\']', path.read_text(encoding="utf-8"), re.M)


def expand(field, lo, hi):
    """The numbers a cron field allows: *, N, a-b, a,b (and */n)."""
    out = set()
    for part in field.split(","):
        step = 1
        if "/" in part:
            part, step = part.split("/")
            step = int(step)
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            a, b = map(int, part.split("-"))
        else:
            a = b = int(part)
        out.update(range(a, b + 1, step))
    return out


def slots(cron, day):
    """The times (Eastern) a cron line fires on an Eastern date. Cron is in UTC, so the Eastern
    day covers parts of two UTC days."""
    minute, hour, dom, month, dow = cron.split()
    mins, hours = expand(minute, 0, 59), expand(hour, 0, 23)
    doms, months = expand(dom, 1, 31), expand(month, 1, 12)
    dows = {d % 7 for d in expand(dow, 0, 7)}  # cron: Sunday is 0 (or 7)
    start = datetime.combine(day, time(0), TZ).astimezone(timezone.utc)
    out = []
    for offset in range(-1, 2):
        d = (start + timedelta(days=offset)).date()
        if d.day not in doms or d.month not in months or (d.isoweekday() % 7) not in dows:
            continue
        for h in sorted(hours):
            for m in sorted(mins):
                at = datetime(d.year, d.month, d.day, h, m, tzinfo=timezone.utc).astimezone(TZ)
                if at.date() == day:
                    out.append(at)
    return sorted(out)


def expected(job, day):
    """Every time (Eastern) the job's workflow is scheduled on that day."""
    return sorted(t for c in crons(WORKFLOWS / job.file) for t in slots(c, day))


# ---- judging a day -----------------------------------------------------------------------

def eastern(stamp):
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(TZ)


def judge(job, runs, day, now):
    """(status, detail) for one job on one Eastern day, from GitHub's run list.
    status: OK, MISSED, LATE, FAILED, RUNNING, WAITING (window not over yet), or OFF (not a run day)."""
    if day.weekday() not in job.days:
        return "OFF", "not a run day"
    start = datetime.combine(day, job.start, TZ)
    deadline = datetime.combine(day, job.deadline, TZ)
    mine = [r for r in runs if eastern(r["created_at"]).date() == day]
    sched = sorted((r for r in mine if r["event"] == "schedule"), key=lambda r: r["created_at"])
    by_hand = len(mine) - len(sched)
    in_window = [r for r in sched if start <= eastern(r["created_at"]) <= deadline]
    good = [r for r in in_window if r.get("conclusion") == "success"]
    failed = [r for r in sched if r.get("status") == "completed" and r.get("conclusion") not in ("success", "skipped")]
    times = ", ".join(clock(eastern(r["created_at"])) for r in sched) or "none"
    extra = f"; {by_hand} started by hand" if by_hand else ""
    fails = f"; {len(failed)} failed: {', '.join(r['html_url'] for r in failed)}" if failed else ""
    detail = f"ran on schedule at: {times}{extra}{fails}"

    if good:
        status = "OK"
    elif any(r.get("status") != "completed" for r in in_window):
        status = "RUNNING"
    elif in_window:
        status = "FAILED"
    elif now < deadline:
        status = "WAITING"
    elif any(r.get("conclusion") == "success" and eastern(r["created_at"]) > deadline for r in sched):
        status = "LATE"
    else:
        status = "MISSED"
    if status == "OK" and failed:
        status = "OK, with failures"
    return status, detail


# ---- GitHub ------------------------------------------------------------------------------

def gh_runs(job, day):
    """The workflow's runs created around that Eastern day, via `gh api` (no key is printed)."""
    span = f"{day - timedelta(days=1)}..{day + timedelta(days=1)}"
    out = subprocess.run(
        ["gh", "api", f"repos/{REPO}/actions/workflows/{job.file}/runs?created={span}&per_page=100",
         "--jq", "[.workflow_runs[] | {event, status, conclusion, created_at, html_url}]"],
        capture_output=True, text=True, encoding="utf-8", timeout=60)
    if out.returncode != 0:
        raise RuntimeError(f"gh api failed for {job.file}: {out.stderr.strip()[:200]}")
    return json.loads(out.stdout or "[]")


def clock(t):
    return t.strftime("%I:%M %p").lstrip("0")


def latest_day(now):
    """Today once every job's deadline has passed, otherwise yesterday."""
    return now.date() if now.time() >= max(j.deadline for j in JOBS) else now.date() - timedelta(days=1)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--date", help="Eastern date to check, YYYY-MM-DD (default: today)")
    ap.add_argument("--latest", action="store_true",
                    help="the latest day whose windows are all over: today after noon, else yesterday")
    ap.add_argument("--schedule", action="store_true", help="only print the expected times")
    args = ap.parse_args(argv)
    now = datetime.now(TZ)
    day = date.fromisoformat(args.date) if args.date else latest_day(now) if args.latest else now.date()

    print(f"GitHub jobs for {day:%a %b %d, %Y} (Eastern), checked {now:%a %b %d} at {clock(now)}")
    if args.schedule:
        for job in JOBS:
            when = ", ".join(clock(t) for t in expected(job, day)) or "no runs that day"
            print(f"- {job.name}: {when}. Must start between {clock(datetime.combine(day, job.start))} and "
                  f"{clock(datetime.combine(day, job.deadline))}. It {job.does}.")
        return 0

    problems = 0
    for job in JOBS:
        try:
            status, detail = judge(job, gh_runs(job, day), day, now)
        except Exception as e:  # noqa: BLE001 - report and keep going
            print(f"- {job.name}: COULDN'T CHECK ({e})")
            problems = max(problems, 2)
            continue
        n = len(expected(job, day))
        print(f"- {job.name}: {status} (scheduled {n}x; {detail})")
        if status in ("MISSED", "LATE", "FAILED", "OK, with failures"):
            problems = max(problems, 1)
    return problems


if __name__ == "__main__":
    sys.exit(main())
