"""Checks that each of the repo's GitHub jobs ran when it should, and says which didn't.

GitHub's schedule can run late or skip runs, so the Cloudflare timer (cloudflare/timer) starts the
jobs on time, and GitHub's own schedule is a backup. This runs on the organizer's computer (not on
GitHub), by hand. It reads the repo's run history (the repo is public) through the `gh` command and
prints one line per job for the given Eastern date. A job is on time if the timer or GitHub's
schedule started it inside its window. It also flags any timer start that didn't happen. The
timer's times are read from cloudflare/timer/src/index.js, so they never need copying here.

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
ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
TIMER_JS = ROOT / "cloudflare" / "timer" / "src" / "index.js"
TIMER_FROM = date(2026, 11, 16)  # the timer's current times are live from this day (it started Nov 13 with fewer)
TIMER_GRACE = timedelta(minutes=10)  # a "Run workflow" start this soon after a timer time is the timer's
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
        "posts that day's Instagram posts (and the Friday Facebook post). Runs go on until about 11:30 PM, "
        "since a night's Story posts at 9 PM the evening before. A post can still go out up to 10 hours late (a "
        "night's Story until its night starts), but never after the night it lists starts. This window is the "
        "early warning"),
    Job("Luma invites", "luma-invites.yml", DAILY, time(10), time(12),
        "sends Luma invites for events 6 days out; sends on the first run at or after 10 AM, and runs go on hourly "
        "until about 6 PM"),
    Job("Meetup", "meetup.yml", DAILY, time(6), time(12),
        "copies Luma events to Meetup (off until Meetup approves the API); the first scheduled run that works each "
        "day syncs, and later ones that day do nothing"),
    Job("Eventbrite", "eventbrite.yml", DAILY, time(6), time(12),
        "copies Luma events to Eventbrite; the first scheduled run that works each day syncs, and later ones that "
        "day do nothing"),
    Job("Discord", "discord.yml", frozenset({4}), time(9), time(12),
        "posts next week's events in Discord on Fridays; posts on the first run at or after 9 AM, and runs go on "
        "hourly until about 6 PM"),
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


# ---- the Cloudflare timer's times ----------------------------------------------------------

def timer_schedule():
    """The timer's SCHEDULE from cloudflare/timer/src/index.js: workflow file -> (Eastern weekdays with
    Monday 0, [times]). The JavaScript counts Sunday as 0."""
    js = TIMER_JS.read_text(encoding="utf-8")
    consts = {name: [int(x) for x in re.findall(r"\d+", body)]
              for name, body in re.findall(r"const (\w+) = \[([\d,\s]*)\]", js)}
    out = {}
    for file, days, times in re.findall(
            r'\{\s*workflow:\s*"([^"]+)"[^}]*?days:\s*(\w+|\[[^\]]*\])[^}]*?times:\s*\[([^\]]*)\]', js):
        js_days = [int(x) for x in re.findall(r"\d+", days)] if days.startswith("[") else consts[days]
        out[file] = (frozenset((d - 1) % 7 for d in js_days),
                     [time.fromisoformat(t) for t in re.findall(r'"(\d\d:\d\d)"', times)])
    return out


def timer_slots(job, day):
    """The times (Eastern) the Cloudflare timer starts the job on that day."""
    if day < TIMER_FROM:
        return []
    days, times = timer_schedule().get(job.file, (frozenset(), []))
    return [datetime.combine(day, t, TZ) for t in times] if day.weekday() in days else []


# ---- judging a day -----------------------------------------------------------------------

def eastern(stamp):
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(TZ)


def judge(job, runs, day, now):
    """(status, detail) for one job on one Eastern day, from GitHub's run list.
    status: OK, MISSED, LATE, FAILED, RUNNING, WAITING (window not over yet), or OFF (not a run day).
    OK can add ", with failures"; OK, RUNNING and WAITING add ", timer missed N" when a timer start
    that was due by now didn't happen. Runs the timer or GitHub's schedule started count; runs
    started by hand or by the check-in are only noted."""
    if day.weekday() not in job.days:
        return "OFF", "not a run day"
    start = datetime.combine(day, job.start, TZ)
    deadline = datetime.combine(day, job.deadline, TZ)
    mine = sorted((r for r in runs if eastern(r["created_at"]).date() == day), key=lambda r: r["created_at"])
    sched = [r for r in mine if r["event"] == "schedule"]
    # The timer's starts are "Run workflow" starts within a few minutes after one of its times.
    tslots, timer, hit = timer_slots(job, day), [], set()
    for r in mine:
        t = eastern(r["created_at"])
        s = next((s for s in tslots if s <= t < s + TIMER_GRACE), None)
        if r["event"] == "workflow_dispatch" and s is not None:
            timer.append(r)
            hit.add(s)
    missed = [s for s in tslots if s not in hit and s + TIMER_GRACE <= now]
    counted = sorted(sched + timer, key=lambda r: r["created_at"])
    by_hand = len(mine) - len(counted)
    in_window = [r for r in counted if start <= eastern(r["created_at"]) <= deadline]
    good = [r for r in in_window if r.get("conclusion") == "success"]
    failed = [r for r in counted if r.get("status") == "completed" and r.get("conclusion") not in ("success", "skipped")]

    def times(rs):
        return ", ".join(clock(eastern(r["created_at"])) for r in rs) or "none"

    parts = []
    if tslots:
        parts.append(f"timer started it at: {times(timer)}")
        if missed:
            parts.append(f"timer missed {', '.join(clock(s) for s in missed)}")
    parts.append(f"GitHub's schedule ran at: {times(sched)}")
    if by_hand:
        parts.append(f"{by_hand} started by hand or by the check-in")
    if failed:
        parts.append(f"{len(failed)} failed: {', '.join(r['html_url'] for r in failed)}")
    detail = "; ".join(parts)

    if good:
        status = "OK"
    elif any(r.get("status") != "completed" for r in in_window):
        status = "RUNNING"
    elif in_window:
        status = "FAILED"
    elif now < deadline:
        status = "WAITING"
    elif any(r.get("conclusion") == "success" and eastern(r["created_at"]) > deadline for r in counted):
        status = "LATE"
    else:
        status = "MISSED"
    if status == "OK" and failed:
        status = "OK, with failures"
    if missed and status.split(",")[0] in ("OK", "RUNNING", "WAITING"):
        status += f", timer missed {len(missed)}"
    return status, detail


def needs_look(status):
    """True when a job's status means something went wrong."""
    return status not in ("OK", "RUNNING", "WAITING", "OFF")


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
            timer = ", ".join(clock(t) for t in timer_slots(job, day)) or "no starts that day"
            github = ", ".join(clock(t) for t in expected(job, day)) or "no runs that day"
            print(f"- {job.name}: the timer starts it at {timer}. GitHub's own schedule (a backup) runs it at "
                  f"{github}. Must start between {clock(datetime.combine(day, job.start))} and "
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
        n, t = len(expected(job, day)), len(timer_slots(job, day))
        print(f"- {job.name}: {status} (timer {t}x, GitHub's schedule {n}x; {detail})")
        if needs_look(status):
            problems = max(problems, 1)
    return problems


if __name__ == "__main__":
    sys.exit(main())
