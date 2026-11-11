#!/usr/bin/env python3
"""Spanish Club for Socialists: post next week's events to Discord every Friday.

Every Friday from 9 AM Eastern (see .github/workflows/discord.yml) this script reads the club's
Luma calendar and posts one message in #announcements through a Discord webhook:
- a usual week: "Next week at Spanish Club", then one line per night with its RSVP link, plus a
  line for any regular night that's off ("No Online session Wed Nov 25.");
- the Friday before a week with no events: one break note ("No Spanish Club Dec 21 – Jan 3.
  We're back Mon Jan 4 at St. Stephen's!"), and nothing the Friday after;
- nothing at all when Luma has no events ahead.
It never posts the Zoom link, never pings anyone, and never mentions conversation guides.
Only the Python standard library is used.

Commands:
    python discord/weekly.py run                     Dry run: show the coming Friday's post. Posts nothing.
    python discord/weekly.py run --as-of 2026-12-18T09:05
                                                     Dry run, pretending it is that Eastern time.
    python discord/weekly.py run --live              Post for real (GitHub Actions does this on Fridays).
    python discord/weekly.py check                   Check the #announcements webhook without posting.
    python discord/weekly.py sample [--as-of DATE]   Post the coming Friday's message to the test webhook in
                                                     #server-bots (DISCORD_SERVER_BOTS_WEBHOOK), to check it.

Safety rules built in:
- Nothing is posted until LIVE_FROM is set, and never before that date.
- It only posts through a webhook that belongs to the #announcements channel (CHANNEL_ID).
- Each Friday is posted once. posted.json marks it "posting" (and is pushed) before the message
  goes out, so a crash can't cause a second post. A run that finds "posting" reports it instead.
- Reports go to the "Discord log" issue, which GitHub emails to the owner. The webhook link and
  the Luma key are never printed or written to a file.
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
POST_HOUR = 9           # Eastern, on Fridays. Posts on the first run at or after this hour.
LIVE_FROM = None        # The first Friday to post. None = switched off.
CHANNEL_ID = None       # The #announcements channel ID (not secret). The webhook must belong to it.
LOOK_BACK = 21          # Days of past events read from Luma, to tell a night that's off from a season's end.
LOOK_AHEAD = 70         # Days of future events read from Luma, to find the night after a break.

# Regular nights, by the end of the Luma title: (weekday, Monday = 0), and the name used when it's off.
REGULAR = {"St. Stephen's": (0, "St. Stephen's night"),
           "Dupont": (1, "Dupont night"),
           "Online": (2, "Online session")}

# Known places, by street address: (how a line shows it, how a break note says "we're back ...").
VENUES = {"1525 Newton St NW": ("St. Stephen's, 1525 Newton St NW (side door)", "at St. Stephen's"),
          "337 H St NE": ("DC Liberation Center, 337 H St NE", "at the DC Liberation Center"),
          "1901 18th St NW": ("Patchwork, 1901 18th St NW", "at Patchwork")}

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
POSTED = HERE / "posted.json"
TZ = ZoneInfo("America/New_York")
LUMA_API = "https://public-api.luma.com"
UA = "DiscordBot (https://github.com/ryantimjohn/spanish-club-automation, 1.0)"
LOG_ISSUE = "Discord log"
IN_ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"
WEBHOOK = re.compile(r"^https://(?:canary\.|ptb\.)?discord(?:app)?\.com/api/webhooks/(\d+)/([\w-]+)$")
SUPPRESS_EMBEDS = 1 << 2


class Problem(Exception):
    """Something went wrong; the message is safe to show (no key, no webhook link)."""


class NotSent(Problem):
    """Discord turned the request down, so nothing was posted and it can be tried again."""


# ---------- pure logic (tested) ----------

def local(event, field="start_at"):
    return datetime.fromisoformat(event[field].replace("Z", "+00:00")).astimezone(TZ)


def day(d):
    return f"{d:%a %b} {d.day}"


def span(first, last):
    if (first.year, first.month) == (last.year, last.month):
        return f"{first:%b} {first.day}–{last.day}"
    return f"{first:%b} {first.day} – {last:%b} {last.day}"


def clock(dt):
    return f"{dt:%I:%M}".lstrip("0")


def times(event):
    start, end = local(event), local(event, "end_at")
    if f"{start:%p}" == f"{end:%p}":
        return f"{clock(start)}–{clock(end)} {end:%p}"
    return f"{clock(start)} {start:%p}–{clock(end)} {end:%p}"


def kind(event):
    """'St. Stephen's', 'Online' or 'Dupont' for a regular night (the end of its Luma title), else None."""
    tail = (event.get("name") or "").rsplit(" - ", 1)[-1].strip()
    return tail if tail in REGULAR else None


def _place(event):
    """(line text, 'we're back ...' text) for an event's place. Never includes the Zoom link."""
    geo = event.get("geo_address_json") or {}
    full = (geo.get("full_address") or "").strip()
    if not full:
        return "Online on Zoom", "on Zoom"
    for street, known in VENUES.items():
        if street in full:
            return known
    name = (geo.get("address") or "").strip()
    rest = full[len(name) + 2:] if name and full.startswith(name + ", ") else full
    street = rest.split(", ")[0]
    label = f"{name}, {street}" if name and name != street else street
    return label, f"at {name or street}"


def venue(event):
    return _place(event)[0]


def back_at(event):
    return _place(event)[1]


def line(event):
    return f"**{day(local(event))}** · {times(event)} · {venue(event)} · [RSVP]({event['url']})"


def missing_nights(events, monday):
    """A line for each regular night that's off in the week starting `monday`: there's one of
    that kind within LOOK_BACK days before it and within LOOK_BACK days after it, but not that week."""
    out = []
    week = [e for e in events if monday <= local(e).date() < monday + timedelta(days=7)]
    for name, (weekday, label) in REGULAR.items():
        if any(kind(e) == name for e in week):
            continue
        night = monday + timedelta(days=weekday)
        dates = [local(e).date() for e in events if kind(e) == name]
        before = any(night - timedelta(days=LOOK_BACK) <= d < night for d in dates)
        after = any(night < d <= night + timedelta(days=LOOK_BACK) for d in dates)
        if before and after:
            out.append((night, f"No {label} {day(night)}."))
    return [text for _, text in sorted(out)]


def build(events, friday):
    """(what, text) for the post on `friday`. what is 'week' or 'break' (text to post), or
    'break-continues' / 'nothing-ahead' (text is None: nothing to post)."""
    events = sorted(events, key=local)
    monday = friday + timedelta(days=3)
    next_monday = monday + timedelta(days=7)
    week = [e for e in events if monday <= local(e).date() < next_monday]
    if week:
        text = "**Next week at Spanish Club**\n\n" + "\n".join(line(e) for e in week)
        missing = missing_nights(events, monday)
        if missing:
            text += "\n\n" + "\n".join(missing)
        return "week", text
    later = [e for e in events if local(e).date() >= next_monday]
    if not later:
        return "nothing-ahead", None
    if not any(monday - timedelta(days=7) <= local(e).date() < monday for e in events):
        return "break-continues", None
    back = later[0]
    back_day = local(back).date()
    return "break", (f"No Spanish Club {span(monday, back_day - timedelta(days=1))}. "
                     f"We're back {day(back_day)} {back_at(back)}!")


def is_due(now, posted, live_from):
    """Due on a Friday from POST_HOUR, on or after live_from, and only once per Friday."""
    now = now.astimezone(TZ)
    return (live_from is not None and now.weekday() == 4 and now.hour >= POST_HOUR
            and now.date() >= live_from and now.date().isoformat() not in posted)


def next_friday(today):
    return today + timedelta(days=(4 - today.weekday()) % 7)


# ---------- secrets ----------

_SECRETS = []


def safe(text):
    text = str(text)
    for s in sorted(_SECRETS, key=len, reverse=True):
        if s:
            text = text.replace(s, "***")
    return text


def secret(name):
    """From the environment (GitHub secret), or on Windows from the user's env vars. Never printed."""
    value = os.environ.get(name, "").strip()
    if not value and os.name == "nt":
        value = subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                                f"[Environment]::GetEnvironmentVariable('{name}','User')"],
                               capture_output=True, text=True).stdout.strip()
    if not value:
        raise Problem(f"{name} isn't set.")
    _SECRETS.append(value)
    return value


def webhook(name):
    url = secret(name)
    m = WEBHOOK.match(url)
    if not m:
        raise Problem(f"{name} doesn't look like a Discord webhook link.")
    _SECRETS.append(m.group(2))
    return url


# ---------- Luma ----------

_LUMA_KEY = None


def luma(path):
    global _LUMA_KEY
    if _LUMA_KEY is None:
        _LUMA_KEY = secret("LUMA_API_KEY")
    req = urllib.request.Request(LUMA_API + path, headers={
        "x-luma-api-key": _LUMA_KEY, "user-agent": "spanish-club-automation/1.0 (discord)",
        "accept": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                time.sleep(30)
                continue
            raise Problem(safe(f"Luma {path.split('?')[0]} failed ({e.code}).")) from None
        except urllib.error.URLError as e:
            raise Problem(safe(f"Luma {path.split('?')[0]} failed: {e.reason}")) from None


def luma_events(friday):
    """Luma events from LOOK_BACK days before `friday` to LOOK_AHEAD days after it."""
    def utc(d):
        return datetime(d.year, d.month, d.day, tzinfo=TZ).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    path = (f"/v1/calendars/events/list?after={utc(friday - timedelta(days=LOOK_BACK))}"
            f"&before={utc(friday + timedelta(days=LOOK_AHEAD))}&sort_direction=asc&pagination_limit=100")
    out, cursor = [], None
    while True:
        page = luma(path + (f"&pagination_cursor={urllib.parse.quote(cursor)}" if cursor else ""))
        out += [e.get("event", e) for e in page.get("entries", [])]
        if not page.get("has_more"):
            return out
        cursor = page["next_cursor"]


# ---------- Discord ----------

def discord(method, url, body=None):
    req = urllib.request.Request(url, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"user-agent": UA, "content-type": "application/json",
                                          "accept": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                try:
                    wait = float(json.loads(e.read()).get("retry_after", 5))
                except (ValueError, AttributeError):
                    wait = 5
                time.sleep(min(wait, 60) + 1)
                continue
            if e.code in (401, 404):
                raise NotSent("Discord says the webhook doesn't exist any more (it was deleted or "
                              "reset). Make a new one and save it again (see the README).") from None
            detail = e.read().decode("utf-8", "replace")[:300]
            cls = NotSent if 400 <= e.code < 500 else Problem
            raise cls(safe(f"Discord {method} failed ({e.code}): {detail}")) from None
        except urllib.error.URLError as e:
            raise Problem(safe(f"Discord {method} failed: {e.reason}")) from None


def webhook_info(url):
    info = discord("GET", url)
    return {"channel_id": str(info.get("channel_id")), "guild_id": str(info.get("guild_id")),
            "name": info.get("name")}


def send(url, text):
    msg = discord("POST", url + "?wait=true",
                  {"content": text, "flags": SUPPRESS_EMBEDS, "allowed_mentions": {"parse": []}})
    return str(msg.get("id", ""))


# ---------- GitHub (record and notices) ----------

def load_posted():
    return json.loads(POSTED.read_text(encoding="utf-8")) if POSTED.exists() else {}


def save_posted(posted, message):
    POSTED.write_text(json.dumps(posted, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    if not IN_ACTIONS:
        return

    def git(*args):
        subprocess.run(["git", *args], cwd=REPO, check=True, capture_output=True, text=True)

    git("config", "user.name", "github-actions[bot]")
    git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    git("add", str(POSTED.relative_to(REPO)))
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
    raise Problem("Couldn't save discord/posted.json to GitHub.")


def notify(text):
    """Comment on the 'Discord log' issue so GitHub emails the owner. Prints when testing."""
    text = safe(text)
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
                                   "body": "The Discord script comments here after each Friday post, "
                                           "break note or problem."})
    call("POST", f"{api}/{issue['number']}/comments", {"body": f"@{owner} {text}" if owner else text})


# ---------- commands ----------

def check_announcements(url):
    if CHANNEL_ID is None:
        raise Problem("CHANNEL_ID isn't set in discord/weekly.py, so nothing was posted.")
    info = webhook_info(url)
    if info["channel_id"] != str(CHANNEL_ID):
        raise Problem(f"The webhook posts to channel {info['channel_id']}, not #announcements "
                      f"({CHANNEL_ID}), so nothing was posted.")
    return info


def run(live, as_of=None):
    now = datetime.fromisoformat(as_of).replace(tzinfo=TZ) if as_of else datetime.now(TZ)
    posted = load_posted()
    if live:
        if LIVE_FROM is None:
            print("Not switched on yet (LIVE_FROM isn't set), so nothing was done.")
            return 0
        today = now.date().isoformat()
        if posted.get(today, {}).get("status") == "posting":
            # Report once, then mark it so later runs today stay quiet.
            posted[today]["status"] = "unknown"
            save_posted(posted, f"Discord: post for {today} interrupted")
            notify(f"PROBLEM: the Discord post for Fri {today} was interrupted and may or may not have gone "
                   "out. Check #announcements. It won't be retried, to avoid a double post.")
            return 1
        if not is_due(now, posted, LIVE_FROM):
            print(f"Nothing due at {now:%a %Y-%m-%d %H:%M} Eastern.")
            return 0
        friday = now.date()
    else:
        friday = next_friday(now.date())
    print(f"{'LIVE' if live else 'DRY RUN'} at {now:%a %Y-%m-%d %H:%M} Eastern, for the post on {day(friday)}")

    events = luma_events(friday)
    what, text = build(events, friday)
    print(f"Luma: {len(events)} events read. Post type: {what}")
    print("----\n" + (text or "(nothing to post)") + "\n----")
    if not live:
        return 0

    key = friday.isoformat()
    if text is None:
        before = [v.get("what") for k, v in sorted(posted.items()) if k < key]
        posted[key] = {"status": "skipped", "what": what}
        save_posted(posted, f"Discord: nothing to post for {key}")
        if what == "nothing-ahead" and (not before or before[-1] != "nothing-ahead"):
            notify(f"Skipped the Discord post for {day(friday)}: Luma shows no events next week or after. "
                   "Add the next season's events on Luma, or pause the workflow.")
        return 0

    url = webhook("DISCORD_ANNOUNCEMENTS_WEBHOOK")
    info = check_announcements(url)
    posted[key] = {"status": "posting", "what": what, "at": now.isoformat(timespec="minutes")}
    save_posted(posted, f"Discord: posting for {key}")
    try:
        message_id = send(url, text)
    except NotSent as e:
        del posted[key]
        save_posted(posted, f"Discord: post for {key} refused, will retry")
        raise Problem(f"{e} Nothing was posted; the next run today tries again.") from None
    posted[key] = {"status": "posted", "what": what, "message_id": message_id,
                   "at": now.isoformat(timespec="minutes")}
    save_posted(posted, f"Discord: posted for {key}")
    link = f"https://discord.com/channels/{info['guild_id']}/{info['channel_id']}/{message_id}"
    nights = text.count("[RSVP]")
    summary = (f"{nights} night{'' if nights == 1 else 's'}" if what == "week" else "the break note")
    notify(f"Posted next week's Discord message in #announcements ({summary}): {link}")
    return 0


def check():
    url = webhook("DISCORD_ANNOUNCEMENTS_WEBHOOK")
    info = webhook_info(url)
    print(f"The webhook works. Its name is \"{info['name']}\". It posts to channel {info['channel_id']} "
          f"in server {info['guild_id']}.")
    if CHANNEL_ID is None:
        print("CHANNEL_ID isn't set yet in discord/weekly.py.")
    elif info["channel_id"] == str(CHANNEL_ID):
        print("That's #announcements, as expected.")
    else:
        raise Problem(f"That isn't #announcements ({CHANNEL_ID}).")
    return 0


def sample(as_of=None):
    friday = next_friday(datetime.fromisoformat(as_of).date() if as_of else datetime.now(TZ).date())
    what, text = build(luma_events(friday), friday)
    if text is None:
        print(f"Nothing would be posted on {day(friday)} ({what}), so no sample was sent.")
        return 0
    url = webhook("DISCORD_SERVER_BOTS_WEBHOOK")
    info = webhook_info(url)
    if CHANNEL_ID is not None and info["channel_id"] == str(CHANNEL_ID):
        raise Problem("The test webhook points at #announcements. Use one in #server-bots.")
    send(url, text)
    print(f"Sample for {day(friday)} sent to channel {info['channel_id']} (the test webhook).")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--live", action="store_true")
    r.add_argument("--as-of")
    sub.add_parser("check")
    s = sub.add_parser("sample")
    s.add_argument("--as-of")
    a = ap.parse_args()
    try:
        if a.cmd == "check":
            return check()
        if a.cmd == "sample":
            return sample(a.as_of)
        return run(a.live, a.as_of)
    except Problem as e:
        if a.cmd == "run" and a.live:
            notify("Problem: " + safe(e))
        else:
            print("Problem:", safe(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
