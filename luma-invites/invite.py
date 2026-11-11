#!/usr/bin/env python3
"""Spanish Club for Socialists: send Luma invites on schedule.

Every day at about 10 AM Eastern (see .github/workflows/luma-invites.yml) this script:
1. removes every calendar contact tagged "removed-from-group" (people who left the Google Group);
2. invites everyone on the Luma calendar to each event that starts 6 days from today.
It skips anyone already on that event's guest list (invited, registered, declined and so on).
Only the Python standard library is used.

Commands:
    python luma-invites/invite.py run                  Dry run: show what would happen. Changes nothing.
    python luma-invites/invite.py run --as-of 2026-11-17T10:05
                                                       Dry run, pretending it is that Eastern time.
    python luma-invites/invite.py run --live           Remove and send for real (GitHub Actions does this).
    python luma-invites/invite.py sample EVENT_ID EMAIL
                                                       Invite one address to one event, to see the email.
                                                       Run it on the organizer's PC, never on GitHub.

Safety rules built in:
- Nothing is sent until LIVE_FROM is set, and only for events whose send day is on or after it.
- An event is marked "sending" in sent.json (and pushed) before its invites go out, so a crash
  can't cause a second send. A run that finds "sending" reports it instead of resending.
- If Luma's contact list looks wrong (under MIN_CONTACTS people), nothing is sent.
- Reports go to the "Luma invite log" issue, which GitHub emails to the owner. They hold counts,
  event names, dates and links only. This repository is public, so names and emails are never
  printed or saved. The Luma key is never printed or written to a file.
"""
import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# ---------- settings ----------
SEND_HOUR = 10          # Eastern. Sends on the first run at or after this hour.
DAYS_AHEAD = 6          # Monday events go out the Tuesday before, Wednesday events the Thursday before.
NOTE = ""               # Optional note on each invite (max 200 characters). Empty = Luma's standard invite.
TAG = "removed-from-group"
LIVE_FROM = date(2026, 11, 11)  # Go-live, with the organizer's OK on Nov 10, 2026. Set to None to stop sending.
MIN_CONTACTS = 100      # Fewer contacts than this means Luma's answer looks wrong: send nothing.
BATCH = 100             # Guests per send-invites call.
MIN_LEAD = timedelta(hours=24)

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
SENT = HERE / "sent.json"
TZ = ZoneInfo("America/New_York")
API = "https://public-api.luma.com"
UA = "spanish-club-automation/1.0 (luma-invites)"
LOG_ISSUE = "Luma invite log"
IN_ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"
EMAIL = re.compile(r"[^\s@]+@[^\s@]+")


class Problem(Exception):
    """Something went wrong; the message is safe to show (no key, no emails)."""


# ---------- pure logic (tested) ----------

def start_of(event):
    return datetime.fromisoformat(event["start_at"].replace("Z", "+00:00"))


def send_day(start_at):
    """The Eastern date on which this event's invites go out."""
    start = datetime.fromisoformat(start_at.replace("Z", "+00:00")).astimezone(TZ)
    return start.date() - timedelta(days=DAYS_AHEAD)


def is_due(event, now, sent, live_from):
    """Due once its send day has come (catching up a missed day), never before live_from,
    never twice, and never for an event less than 24 hours away."""
    if live_from is None or event["id"] in sent:
        return False
    if not (live_from <= send_day(event["start_at"]) <= now.astimezone(TZ).date()):
        return False
    return start_of(event) - now >= MIN_LEAD


def recipients(contacts, removed_ids, guest_emails):
    """Everyone on the calendar who isn't being removed and isn't already a guest of the event."""
    out, seen = [], set()
    for c in contacts:
        email = (c.get("email") or "").strip()
        low = email.lower()
        if not email or c.get("id") in removed_ids or low in guest_emails or low in seen:
            continue
        seen.add(low)
        out.append({"email": email, "name": c.get("name")})
    return out


def guard_contacts(contacts):
    if len(contacts) < MIN_CONTACTS:
        raise Problem(f"Luma returned only {len(contacts)} calendar contacts (expected at least "
                      f"{MIN_CONTACTS}), so nothing was sent to be safe.")


def nice(start_at):
    dt = datetime.fromisoformat(start_at.replace("Z", "+00:00")).astimezone(TZ)
    return f"{dt:%a %b} {dt.day}, {dt:%I:%M %p}".replace(" 0", " ")


def report(removed, done, problems):
    """The log comment. Empty when nothing happened. Never contains an email address."""
    if not (removed or done or problems):
        return ""
    lines = []
    if removed:
        lines.append(f"Removed {removed} {'person' if removed == 1 else 'people'} tagged {TAG} from the calendar.")
    for d in done:
        lines.append(f"Invited {d['invited']} people to {d['name']} ({nice(d['start_at'])}) {d['url']}"
                     + (f". Luma skipped {d['skipped']} (unsubscribed or blocked)." if d.get("skipped") else "."))
    for p in problems:
        lines.append("Problem: " + p)
    return EMAIL.sub("[email]", "\n".join(lines))


# ---------- Luma ----------

_KEY = None


def key():
    """The Luma key: from the environment (GitHub secret), or on Windows from the user's env vars."""
    global _KEY
    if _KEY is None:
        _KEY = os.environ.get("LUMA_API_KEY", "").strip()
        if not _KEY and os.name == "nt":
            _KEY = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command",
                 "[Environment]::GetEnvironmentVariable('LUMA_API_KEY','User')"],
                capture_output=True, text=True).stdout.strip()
        if not _KEY:
            raise Problem("LUMA_API_KEY isn't set.")
    return _KEY


def safe(text):
    text = str(text)
    if _KEY:
        text = text.replace(_KEY, "***")
    return EMAIL.sub("[email]", text)


def luma(method, path, body=None):
    req = urllib.request.Request(API + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"x-luma-api-key": key(), "user-agent": UA,
                                          "content-type": "application/json", "accept": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                time.sleep(30)
                continue
            detail = e.read().decode("utf-8", "replace")[:400]
            raise Problem(safe(f"Luma {method} {path.split('?')[0]} failed ({e.code}): {detail}")) from None
        except urllib.error.URLError as e:
            raise Problem(safe(f"Luma {method} {path.split('?')[0]} failed: {e.reason}")) from None


def paginate(path):
    out, cursor = [], None
    while True:
        sep = "&" if "?" in path else "?"
        page = luma("GET", f"{path}{sep}pagination_limit=100"
                    + (f"&pagination_cursor={urllib.parse.quote(cursor)}" if cursor else ""))
        out += page.get("entries", [])
        if not page.get("has_more"):
            return out
        cursor = page["next_cursor"]


# ---------- GitHub (log commits and notices) ----------

def load_sent():
    return json.loads(SENT.read_text(encoding="utf-8")) if SENT.exists() else {}


def save_sent(sent, message):
    SENT.write_text(json.dumps(sent, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    if not IN_ACTIONS:
        return

    def git(*args):
        subprocess.run(["git", *args], cwd=REPO, check=True, capture_output=True, text=True)

    git("config", "user.name", "github-actions[bot]")
    git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    git("add", str(SENT.relative_to(REPO)))
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=REPO).returncode == 0:
        return
    git("commit", "-m", message)
    for _ in range(3):
        try:
            git("pull", "--rebase", "--quiet")
            git("push", "--quiet")
            return
        except subprocess.CalledProcessError:
            time.sleep(5)
    raise Problem("Couldn't save luma-invites/sent.json to GitHub.")


def notify(text):
    """Comment on the 'Luma invite log' issue so GitHub emails the owner. Prints when testing."""
    print("NOTICE:", text)
    if not IN_ACTIONS:
        return
    repo, gh_token = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"]
    owner = os.environ.get("GITHUB_REPOSITORY_OWNER", "")
    api = f"https://api.github.com/repos/{repo}/issues"

    def call(method, url, payload=None):
        req = urllib.request.Request(url, method=method, data=json.dumps(payload).encode() if payload else None,
                                     headers={"authorization": f"Bearer {gh_token}", "user-agent": UA,
                                              "accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())

    issues = call("GET", api + "?state=open&per_page=100")
    issue = next((i for i in issues if i["title"] == LOG_ISSUE and "pull_request" not in i), None)
    if not issue:
        issue = call("POST", api, {"title": LOG_ISSUE,
                                   "body": "The Luma invite script comments here when it removes people, "
                                           "sends invites, or hits a problem."})
    call("POST", f"{api}/{issue['number']}/comments", {"body": f"@{owner} {text}" if owner else text})


# ---------- the run ----------

def run(live, as_of=None):
    now = datetime.fromisoformat(as_of).replace(tzinfo=TZ) if as_of else datetime.now(TZ)
    if live and LIVE_FROM is None:
        print("Not switched on yet (LIVE_FROM isn't set), so nothing was done.")
        return 0
    if live and now.hour < SEND_HOUR:
        print(f"It's {now:%H:%M} Eastern, before {SEND_HOUR}:00, so nothing to do yet.")
        return 0
    # A dry run before go-live shows what would happen if it were switched on today.
    live_from = LIVE_FROM or now.date()
    print(f"{'LIVE' if live else 'DRY RUN'} at {now:%a %Y-%m-%d %H:%M} Eastern")

    removed, done, problems = 0, [], []
    sent = load_sent()

    tagged = paginate(f"/v1/calendars/contacts/list?tags={urllib.parse.quote(TAG)}")
    removed_ids = {c["id"] for c in tagged}
    for c in tagged:
        if not live:
            removed += 1
            continue
        try:
            luma("POST", "/v1/calendars/contacts/remove", {"contact_id": c["id"]})
            removed += 1
        except Problem as e:
            problems.append(f"Couldn't remove a tagged contact: {e}")
    print(f"Tagged {TAG}: {len(tagged)} ({'removed' if live else 'would remove'} {removed})")

    contacts = paginate("/v1/calendars/contacts/list")
    guard_contacts(contacts)
    print(f"Calendar contacts: {len(contacts)}")

    after = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    events = [e.get("event", e) for e in paginate(f"/v1/calendars/events/list?after={after}&sort_direction=asc")]
    for eid, info in sent.items():
        if info.get("status") == "sending" and any(e["id"] == eid for e in events):
            problems.append(f"Invites for event {eid} were interrupted mid-send on {info.get('at', '?')}. "
                            "They weren't resent automatically; check the event's guest list on Luma.")
    due = [e for e in events if is_due(e, now, sent, live_from)]
    print(f"Due now: {len(due)}" + "".join(f"\n  {nice(e['start_at'])}  {e['name']}  {e.get('url', '')}" for e in due))

    for ev in due:
        try:
            guests = paginate(f"/v1/events/guests/list?event_id={ev['id']}")
            guest_emails = {(g.get("guest", g).get("user_email") or "").strip().lower() for g in guests}
            to = recipients(contacts, removed_ids, guest_emails)
            print(f"  {ev['name']} ({nice(ev['start_at'])}): {len(guests)} already on the guest list, "
                  f"{'inviting' if live else 'would invite'} {len(to)}")
            if not live:
                continue
            sent[ev["id"]] = {"status": "sending", "event": send_day(ev["start_at"]).isoformat(),
                              "at": now.isoformat(timespec="minutes")}
            save_sent(sent, f"Luma invites: sending for {ev['id']}")
            skipped = 0
            for i in range(0, len(to), BATCH):
                body = {"event_id": ev["id"], "guests": to[i:i + BATCH]}
                if NOTE:
                    body["message"] = NOTE[:200]
                skipped += len(luma("POST", "/v1/events/guests/send-invites", body).get("skipped", []))
            sent[ev["id"]] = {"status": "sent", "event": send_day(ev["start_at"]).isoformat(),
                              "invited": len(to), "skipped": skipped, "at": now.isoformat(timespec="minutes")}
            save_sent(sent, f"Luma invites: sent for {ev['id']}")
            done.append({"name": ev["name"], "start_at": ev["start_at"], "url": ev.get("url", ""),
                         "invited": len(to), "skipped": skipped})
        except Problem as e:
            problems.append(f"{ev['name']} ({nice(ev['start_at'])}): {e}")

    text = report(removed if live else 0, done, problems)
    if live and text:
        notify(text)
    elif text:
        print(text)
    return 1 if problems else 0


def sample(event_id, email):
    """Invite one address to one event so the organizer can see the real email. Writes no log."""
    r = luma("POST", "/v1/events/guests/send-invites", {"event_id": event_id, "guests": [{"email": email}]})
    print("Sample invite sent." if not r.get("skipped") else
          "Luma skipped that address (unsubscribed, removed or blocked).")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--live", action="store_true")
    r.add_argument("--as-of")
    s = sub.add_parser("sample")
    s.add_argument("event_id")
    s.add_argument("email")
    a = ap.parse_args()
    try:
        if a.cmd == "sample":
            if IN_ACTIONS:
                raise Problem("Run the sample from the organizer's PC, so no address appears in public logs.")
            sample(a.event_id, a.email)
            return 0
        return run(a.live, a.as_of)
    except Problem as e:
        msg = safe(e)
        if a.cmd == "run" and a.live:
            notify("Problem: " + msg)
        else:
            print("Problem:", msg)
        return 1


if __name__ == "__main__":
    sys.exit(main())
