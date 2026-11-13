"""The daily check-in: did what should have gone out today go out? It starts what GitHub missed.

GitHub's schedule ran hours late or not at all on Nov 11-13, 2026, so this runs on the organizer's
computer at about 9:30 AM, 11:30 AM and 1:30 PM Eastern. For each job it reads the job's own record
on GitHub (what was posted or sent), works out what was due by now, and reports it with links.

    python -I -X utf8 run-check/catch_up.py           # report only; changes nothing
    python -I -X utf8 run-check/catch_up.py --auto    # also start missed jobs and send the summary

With --auto, when something due hasn't gone out and no run of that job is queued or running, it
starts the job on GitHub with the same live action its schedule runs (post-due, send-due or
sync-now; the organizer's OK, Nov 13, 2026). Each job still decides for itself what is due and does
each thing only once, so starting one never double-posts. From 1 PM, or whenever something needs the
organizer, it also sends the summary to the "Daily check-in" issue through the "Check-in note"
workflow. That comment comes from GitHub's bot, so GitHub emails it to the owner.

Exit code 0 means everything due went out (or is on its way), 1 means something needs the organizer,
and 2 means it couldn't check.
"""
import argparse
import importlib.util
import json
import pathlib
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

REPO_NAME = "ryantimjohn/spanish-club-automation"
ROOT = pathlib.Path(__file__).resolve().parent.parent
TZ = ZoneInfo("America/New_York")
REPORT_FROM = 13  # Eastern hour from which --auto always sends the day's summary
DISCORD_SERVER = "1567005705888866446"  # the club's Discord server id (not secret), for message links


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---- reading GitHub ----------------------------------------------------------------------

def sh(args, **kw):
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=120, **kw)


_FETCHED = False


def on_github(path, default):
    """A JSON record as it is on GitHub now (the jobs push their records there). The working copy
    is never touched."""
    global _FETCHED
    if not _FETCHED:
        out = sh(["git", "fetch", "-q", "origin", "main"])
        if out.returncode != 0:
            raise RuntimeError(f"git fetch failed: {out.stderr.strip()[:200]}")
        _FETCHED = True
    out = sh(["git", "show", f"origin/main:{path}"])
    return json.loads(out.stdout) if out.returncode == 0 and out.stdout.strip() else default


def runs_today(file, now):
    """Today's (Eastern) runs of a workflow: event, status, conclusion, created_at."""
    start = datetime.combine(now.date(), datetime.min.time(), TZ).astimezone(timezone.utc)
    out = sh(["gh", "run", "list", "-R", REPO_NAME, "-w", file, "-L", "50",
              "--json", "event,status,conclusion,createdAt"])
    if out.returncode != 0:
        raise RuntimeError(f"gh run list failed for {file}: {out.stderr.strip()[:200]}")
    runs = json.loads(out.stdout or "[]")
    return [r for r in runs if datetime.fromisoformat(r["createdAt"].replace("Z", "+00:00")) >= start]


def busy(runs):
    return any(r["status"] != "completed" for r in runs)


def et(stamp):
    """'2026-11-13T09:00' or an ISO time with offset, as Eastern."""
    d = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    return d.replace(tzinfo=TZ) if d.tzinfo is None else d.astimezone(TZ)


def clock(d):
    return d.strftime("%I:%M %p").lstrip("0")


# ---- what each job owes today -------------------------------------------------------------
# Each returns (lines, pending, needs_you): report lines, True if something due hasn't gone out and
# can still go out (so starting the job would help), and lines for things the organizer must handle.

def instagram(now, post):
    manifest = on_github("manifest.json", {"items": [], "approved": {}})
    log = on_github("log/posted.json", {})
    approved = manifest.get("approved", {})
    lines, pending, needs = [], False, []
    for item in manifest.get("items", []):
        at = et(item["post_at"])
        if at.date() != now.date():
            continue
        name = f'"{item.get("what") or item["id"]}"'
        entry = log.get(item["id"])
        if entry:
            st = entry.get("status")
            link = entry.get("permalink", "")
            when = f" at {clock(et(entry['at']))}" if entry.get("at") else ""
            lines.append(f"Instagram: {name} {st}{when} {link}".rstrip())
            if st not in ("posted", "skipped"):
                needs.append(f"Instagram: {name} is marked {st}. Check Instagram and the Instagram log issue.")
        elif approved.get(item["id"]) != item.get("fingerprint"):
            lines.append(f"Instagram: {name} isn't approved, so it won't post.")
            if now >= at:
                needs.append(f"Instagram: {name} was due at {clock(at)} but isn't approved.")
        elif now < at:
            lines.append(f"Instagram: {name} is due at {clock(at)}.")
        elif now < post.ig_deadline(item):
            lines.append(f"Instagram: {name} was due at {clock(at)} and hasn't posted yet.")
            pending = True
        else:
            lines.append(f"Instagram: {name} was due at {clock(at)} and is now too late to post.")
            needs.append(f"Instagram: {name} didn't post, and it's past its last posting time. Post it by hand "
                         "if it still matters.")
    # Facebook: the Friday feed post, from FB_LIVE_FROM.
    fb_log = log.get("facebook", {})
    for name, entry in fb_log.items():
        if isinstance(entry, dict) and entry.get("status") == "posted" and entry.get("at") \
                and et(entry["at"]).date() == now.date():
            lines.append(f"Facebook: {name} posted at {clock(et(entry['at']))} {entry.get('permalink', '')}".rstrip())
    if post.FB_LIVE_FROM is not None:
        due, missed = post.fb_due(manifest.get("items", []), fb_log, now, post.FB_LIVE_FROM, approved)
        for item in due:
            lines.append(f"Facebook: \"{item.get('what') or item['id']}\" hasn't posted yet.")
            pending = True
        for item in missed:
            needs.append(f"Facebook: \"{item.get('what') or item['id']}\" didn't post in time.")
    return lines, pending, needs


def discord(now, weekly):
    posted = on_github("discord/posted.json", {})
    today = now.date().isoformat()
    if now.weekday() != 4:
        return [], False, []
    if weekly.LIVE_FROM is None or now.date() < weekly.LIVE_FROM:
        return ["Discord: not switched on yet."], False, []
    entry = posted.get(today)
    if entry:
        st = entry.get("status")
        link = (f"https://discord.com/channels/{DISCORD_SERVER}/{weekly.CHANNEL_ID}/{entry['message_id']}"
                if entry.get("message_id") else "")
        when = f" at {clock(et(entry['at']))}" if entry.get("at") else ""
        needs = [] if st in ("posted", "skipped") else [f"Discord: today's post is marked {st}. Check #announcements."]
        return [f"Discord: {st}{when} {link}".rstrip()], False, needs
    if weekly.is_due(now, posted, weekly.LIVE_FROM):
        return ["Discord: today's post hasn't gone out yet."], True, []
    return [f"Discord: due at {weekly.POST_HOUR} AM."], False, []


def invites(now, invite):
    sent = on_github("luma-invites/sent.json", {})
    after = (now - timedelta(days=1)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    events = [e.get("event", e) for e in invite.paginate(f"/v1/calendars/events/list?after={after}&sort_direction=asc")]
    names = {e["id"]: f"{e['name']} ({invite.nice(e['start_at'])})" for e in events}
    lines, needs = [], []
    for eid, info in sent.items():
        if info.get("at") and et(info["at"]).date() == now.date():
            what = names.get(eid, eid)
            if info.get("status") == "sent":
                lines.append(f"Luma invites: sent for {what} at {clock(et(info['at']))} "
                             f"({info.get('invited', '?')} invited)")
            else:
                lines.append(f"Luma invites: {what} is marked {info.get('status')}.")
                needs.append(f"Luma invites for {what} were interrupted. Check its guest list on Luma.")
    if invite.LIVE_FROM is None:
        return lines + ["Luma invites: not switched on."], False, needs
    due = [e for e in events if invite.is_due(e, now, sent, invite.LIVE_FROM)]
    if due and now.hour < invite.SEND_HOUR:
        lines.append(f"Luma invites: due at {invite.SEND_HOUR} AM for " + "; ".join(names[e["id"]] for e in due) + ".")
        return lines, False, needs
    for e in due:
        lines.append(f"Luma invites: not sent yet for {names[e['id']]}.")
    if not lines:
        lines.append("Luma invites: nothing due today.")
    return lines, bool(due), needs


def sync_job(label, now, mod, runs, note_off):
    if mod.LIVE_FROM is None or now.date() < mod.LIVE_FROM:
        return [f"{label}: {note_off}"], False, []
    ok = [r for r in runs if r["conclusion"] == "success"]
    if ok:
        first = min(et(r["createdAt"]) for r in ok)
        return [f"{label}: synced at {clock(first)}."], False, []
    failed = [r for r in runs if r["status"] == "completed" and r["conclusion"] not in ("success", "skipped", "cancelled")]
    needs = [f"{label}: today's sync failed. See its log issue."] if failed else []
    return [f"{label}: not synced yet today."], not failed, needs


# ---- starting jobs and sending the note ---------------------------------------------------

def start(file, action):
    out = sh(["gh", "workflow", "run", file, "-R", REPO_NAME, "-f", f"action={action}"])
    return out.returncode == 0, (out.stdout or out.stderr).strip()[:200]


def send_note(text):
    out = sh(["gh", "workflow", "run", "checkin-note.yml", "-R", REPO_NAME, "-f", f"text={text}"])
    return out.returncode == 0, (out.stdout or out.stderr).strip()[:200]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--auto", action="store_true", help="start missed jobs, and send the summary from 1 PM")
    ap.add_argument("--note", action="store_true", help="send the summary now, whatever the time")
    args = ap.parse_args(argv)
    now = datetime.now(TZ)

    post = load_module("ig_post", "post.py")
    weekly = load_module("discord_weekly", "discord/weekly.py")
    invite = load_module("luma_invite", "luma-invites/invite.py")
    eventbrite = load_module("eventbrite_sync", "eventbrite/sync.py")
    meetup = load_module("meetup_sync", "meetup/sync.py")

    jobs = [
        ("Instagram", "instagram.yml", "post-due", lambda r: instagram(now, post)),
        ("Discord", "discord.yml", "post-due", lambda r: discord(now, weekly)),
        ("Luma invites", "luma-invites.yml", "send-due", lambda r: invites(now, invite)),
        ("Eventbrite", "eventbrite.yml", "sync-now",
         lambda r: sync_job("Eventbrite", now, eventbrite, r, "not switched on.")),
        ("Meetup", "meetup.yml", "sync-now",
         lambda r: sync_job("Meetup", now, meetup, r, "off until Meetup approves the API.")),
    ]
    report, needs, started, broken = [], [], [], False
    for label, file, action, check in jobs:
        try:
            runs = runs_today(file, now)
            lines, pending, need = check(runs)
        except Exception as e:  # noqa: BLE001 - report and keep going
            report.append(f"{label}: COULDN'T CHECK ({str(e)[:200]})")
            broken = True
            continue
        report += lines
        needs += need
        if pending and busy(runs):
            report.append(f"{label}: a run is already going, so nothing was started.")
        elif pending and args.auto:
            ok, msg = start(file, action)
            started.append(f"{label} ({action})" if ok else f"{label} (couldn't start: {msg})")
            if not ok:
                needs.append(f"{label}: something is due but the check-in couldn't start the job: {msg}")
        elif pending:
            report.append(f"{label}: would start {action} (run with --auto).")

    head = f"Check-in for {now:%a %b %d} at {clock(now)} Eastern"
    text = "\n".join([head] + [f"- {line}" for line in report]
                     + ([f"Started now because GitHub hadn't run them: {', '.join(started)}. "
                         "Each job comments on its own log issue when it posts or sends."] if started else [])
                     + (["Needs you:"] + [f"- {n}" for n in needs] if needs else []))
    print(text)
    if args.note or (args.auto and (now.hour >= REPORT_FROM or needs or broken)):
        ok, msg = send_note(text)
        print("Summary sent to the Daily check-in issue." if ok else f"Couldn't send the summary: {msg}")
    return 2 if broken else 1 if needs else 0


if __name__ == "__main__":
    sys.exit(main())
