#!/usr/bin/env python3
"""Spanish Club for Socialists: keep the Meetup group in step with Luma.

Every day (see .github/workflows/meetup.yml) this script:
1. lists each Luma event on Meetup once it's within WEEKS_AHEAD weeks, under Luma's exact title;
2. updates a Meetup event when its Luma event changes, and cancels it if the Luma event is gone.
Luma is the source of truth. Online events get the Zoom link only in Meetup's attendee-only link
field, never in the description. Only the Python standard library and the `openssl` command are used.

Commands:
    python meetup/sync.py run                      Dry run: show what would happen. Changes nothing.
    python meetup/sync.py run --as-of 2026-11-17T10:05
    python meetup/sync.py run --live --draft --only LUMA_EVENT_ID
                                                   Create or update one event as a Meetup DRAFT (the sample).
    python meetup/sync.py run --live               The daily run (GitHub Actions). Needs LIVE_FROM.
    python meetup/sync.py publish --live           Publish the drafts recorded in events.json (after the OK).
    python meetup/sync.py collect                  Copy the wide flyers from the club's flyers folder into
                                                   meetup/images/ (run on the organizer's PC, then commit).
    python meetup/sync.py check                    Read-only: check the Meetup keys work.

Safety rules built in:
- Nothing changes without --live, and the daily run does nothing until LIVE_FROM is set.
- Only events recorded in meetup/events.json are ever edited or cancelled, never ones posted by hand.
- A Meetup event is cancelled (never deleted) only if Luma no longer has it, checked twice.
- An event whose wide flyer isn't in meetup/images/ yet waits; it never goes up without its photo.
- The repository is public: no keys, Zoom link, names or emails are printed or saved.
"""
import argparse
import base64
import hashlib
import html
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# ---------- settings ----------
GROUP = "spanish-club-for-socialists"
WEEKS_AHEAD = 5
LIVE_FROM = None        # date(YYYY, M, D): when the daily run may start changing Meetup, set with the organizer's OK.
MEMBER_ID = "4255800"   # Timothy Ryan, the group's organizer. Not secret.
FOLLOW = "Get weekly reminders and see every event: follow us on Luma, https://luma.com/spanishclubforsocialists"

KINDS = {  # Luma name contains -> (kind, image slug, venue key, "how to find us")
    "St. Stephen": ("St. Stephen's", "st-stephens", "st-stephens", "Please come in the side door!"),
    "Online": ("Online", "online", "online", None),
    "Liberation Center": ("Liberation Center", "liberation-center", "liberation-center", None),
    "Dupont": ("Dupont", "dupont", "dupont", None),
}
VENUES = {  # created on Meetup the first time they're needed
    "st-stephens": {"name": "St. Stephen and the Incarnation Episcopal Church", "address": "1525 Newton St NW",
                    "city": "Washington", "state": "DC", "country": "us"},
    "liberation-center": {"name": "DC Liberation Center", "address": "337 H St NE",
                          "city": "Washington", "state": "DC", "country": "us"},
    "dupont": {"name": "Patchwork", "address": "1901 18th St NW", "city": "Washington", "state": "DC", "country": "us"},
}

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
EVENTS = HERE / "events.json"
VENUE_IDS = HERE / "venues.json"
IMAGES = HERE / "images"
TZ = ZoneInfo("America/New_York")
LUMA = "https://public-api.luma.com"
GQL = "https://api.meetup.com/gql-ext"
TOKEN_URL = "https://secure.meetup.com/oauth2/access"
UA = "spanish-club-automation/1.0 (meetup-sync)"
LOG_ISSUE = "Meetup log"
IN_ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"
EMAIL = re.compile(r"[^\s@]+@[^\s@]+")
ZOOM = re.compile(r"https?://[\w.-]*zoom\.us/\S*")


class Problem(Exception):
    """Something went wrong; the message is safe to show."""


# ---------- pure logic (tested) ----------

def parse(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def kind_of(event):
    for key, val in KINDS.items():
        if key in (event.get("name") or ""):
            return val[0]
    return None


def _kind(event):
    return next(v for k, v in KINDS.items() if k in event["name"])


def start_local(start_at):
    return parse(start_at).astimezone(TZ).strftime("%Y-%m-%dT%H:%M")


def duration_iso(event):
    mins = int((parse(event["end_at"]) - parse(event["start_at"])).total_seconds() // 60)
    h, m = divmod(mins, 60)
    return "PT" + (f"{h}H" if h else "") + (f"{m}M" if m else "")


def html_description(md):
    """Luma's markdown as Meetup's simple HTML, without any Zoom link, ending with the Luma line."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", md or "") if p.strip()]
    out = []
    for p in paras + [FOLLOW]:
        if ZOOM.search(p):
            continue  # the Zoom link only ever goes in the attendee-only field
        text = html.escape(p.replace("  \n", "\n"), quote=False)
        text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", lambda m: f'<a href="{m[2]}">{m[1]}</a>', text)
        text = re.sub(r"(?<![\"'>])(https?://[^\s<]+)", lambda m: f'<a href="{m[1]}">{m[1]}</a>', text)
        out.append("<p>" + text.replace("\n", "<br>") + "</p>")
    return "\n".join(out)


def meetup_fields(event):
    kind, slug, venue, how = _kind(event)
    online = venue == "online"
    return {"title": event["name"][:80], "start": start_local(event["start_at"]), "duration": duration_iso(event),
            "venue": venue, "how_to_find_us": how, "online_url": event.get("meeting_url") if online else None,
            "description": html_description(event.get("description_md", "")),
            "image": f"{start_local(event['start_at'])[:10]}-{slug}.png"}


def fingerprint(fields):
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()[:16]


def plan(luma_events, mapping, now, has_flyer):
    """[(action, event_or_luma_id)]: create / wait_flyer / update / cancel / skip."""
    end = now + timedelta(weeks=WEEKS_AHEAD)
    actions, live_ids = [], {e["id"] for e in luma_events}
    for e in luma_events:
        if not (now < parse(e["start_at"]) <= end):
            continue
        if kind_of(e) is None:
            actions.append(("skip", e))
            continue
        m = mapping.get(e["id"])
        if not m:
            actions.append(("create", e) if has_flyer(e) else ("wait_flyer", e))
        elif m.get("status") != "cancelled" and m.get("fingerprint") != fingerprint(meetup_fields(e)):
            actions.append(("update", e))
    for lid, m in mapping.items():
        if lid.startswith("_") or m.get("status") == "cancelled" or lid in live_ids:
            continue
        if m.get("start") and parse(m["start"]) > now:
            actions.append(("cancel", lid))
    return actions


# ---------- secrets ----------

def env(name):
    value = os.environ.get(name, "").strip()
    if not value and os.name == "nt":
        value = subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                                f"[Environment]::GetEnvironmentVariable('{name}','User')"],
                               capture_output=True, text=True).stdout.strip()
    if not value:
        raise Problem(f"{name} isn't set.")
    return value


_SECRETS = []


def safe(text):
    text = str(text)
    for s in _SECRETS:
        text = text.replace(s, "***")
    return ZOOM.sub("[zoom link]", EMAIL.sub("[email]", text))


def http(method, url, data=None, headers=None, raw=False):
    req = urllib.request.Request(url, data=data, method=method, headers={"user-agent": UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read()
            return body if raw else json.loads(body or b"{}")
    except urllib.error.HTTPError as e:
        raise Problem(safe(f"{method} {url.split('?')[0]} failed ({e.code}): {e.read().decode('utf-8', 'replace')[:400]}")) from None
    except urllib.error.URLError as e:
        raise Problem(safe(f"{method} {url.split('?')[0]} failed: {e.reason}")) from None


# ---------- Luma ----------

def luma(path):
    key = env("LUMA_API_KEY")
    _SECRETS.append(key)
    return http("GET", LUMA + path, headers={"x-luma-api-key": key, "accept": "application/json"})


def luma_upcoming(now):
    out, cursor = [], None
    after = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    while True:
        page = luma(f"/v1/calendars/events/list?after={after}&sort_direction=asc&pagination_limit=100"
                    + (f"&pagination_cursor={urllib.parse.quote(cursor)}" if cursor else ""))
        out += [e.get("event", e) for e in page.get("entries", [])]
        if not page.get("has_more"):
            break
        cursor = page["next_cursor"]
    full = []
    for e in out:  # description and the Zoom link (meeting_url) need the full event
        got = luma(f"/v1/events/get?event_id={e['id']}")
        full.append(got.get("event", got))
    return full


def luma_gone(event_id):
    try:
        got = luma(f"/v1/events/get?event_id={event_id}")
    except Problem as e:
        return "404" in str(e)
    ev = got.get("event", got)
    return not ev or ev.get("status") in ("cancelled", "deleted")


# ---------- Meetup ----------

_TOKEN = None


def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def token():
    """JWT flow: sign an assertion with the private key (via openssl), swap it for an access token."""
    global _TOKEN
    if _TOKEN:
        return _TOKEN
    client, kid = env("MEETUP_CLIENT_KEY"), env("MEETUP_SIGNING_KEY_ID")
    pem = env("MEETUP_PRIVATE_KEY").replace("\\n", "\n")
    _SECRETS.append(pem)
    head = b64(json.dumps({"alg": "RS256", "typ": "JWT", "kid": kid}).encode())
    body = b64(json.dumps({"sub": MEMBER_ID, "iss": client, "aud": "api.meetup.com",
                           "exp": int(time.time()) + 120}).encode())
    fd, path = tempfile.mkstemp(suffix=".pem")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(pem)
        sig = subprocess.run(["openssl", "dgst", "-sha256", "-sign", path], input=f"{head}.{body}".encode(),
                             capture_output=True, check=True).stdout
    except subprocess.CalledProcessError:
        raise Problem("openssl couldn't sign with MEETUP_PRIVATE_KEY. Is the whole PEM key saved?") from None
    finally:
        os.remove(path)  # a temp copy of the key made just above, never anything else
    form = urllib.parse.urlencode({"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                   "assertion": f"{head}.{body}.{b64(sig)}"}).encode()
    got = http("POST", TOKEN_URL, data=form, headers={"content-type": "application/x-www-form-urlencoded"})
    _TOKEN = got.get("access_token") or ""
    if not _TOKEN:
        raise Problem("Meetup didn't return an access token.")
    _SECRETS.append(_TOKEN)
    return _TOKEN


def gql(query, variables=None):
    got = http("POST", GQL, data=json.dumps({"query": query, "variables": variables or {}}).encode(),
               headers={"authorization": f"Bearer {token()}", "content-type": "application/json"})
    if got.get("errors"):
        raise Problem(safe("Meetup said: " + "; ".join(e.get("message", "?") for e in got["errors"])))
    return got["data"]


def payload_ok(p, what):
    errs = p.get("errors") or ([p["error"]] if p.get("error") else [])
    if errs:
        raise Problem(safe(f"Meetup refused {what}: " + "; ".join(f"{e.get('field') or ''} {e.get('message')}" for e in errs)))
    return p


def group_id():
    return gql("query($u:String!){ groupByUrlname(urlname:$u){ id } }", {"u": GROUP})["groupByUrlname"]["id"]


def venue_id(key, live):
    if key == "online":
        return "online"
    ids = json.loads(VENUE_IDS.read_text(encoding="utf-8")) if VENUE_IDS.exists() else {}
    if key in ids:
        return ids[key]
    v = VENUES[key]
    found = gql("query($u:String!){ groupByUrlname(urlname:$u){ venues(first:100){ edges{ node{ id address } } } } }",
                {"u": GROUP})["groupByUrlname"]["venues"]["edges"]
    match = next((n["node"]["id"] for n in found if (n["node"].get("address") or "").startswith(v["address"])), None)
    if not match and live:
        p = payload_ok(gql("mutation($i:CreateVenueInput!){ createVenue(input:$i){ venue{ id } errors{ code field message } } }",
                           {"i": dict(v, groupId=group_id())})["createVenue"], "the venue")
        match = p["venue"]["id"]
    if match:
        ids[key] = match
        VENUE_IDS.write_text(json.dumps(ids, indent=1) + "\n", encoding="utf-8")
    return match or f"<new venue: {v['name']}>"


def upload_photo(path):
    p = payload_ok(gql("mutation($i:GroupEventPhotoCreateInput!){ createGroupEventPhoto(input:$i){ uploadUrl "
                       "photo{ id } error{ code field message } } }",
                       {"i": {"groupId": group_id(), "photoType": "EVENT_PHOTO", "contentType": "PNG",
                              "setAsMain": False}})["createGroupEventPhoto"], "the photo")
    http("PUT", p["uploadUrl"], data=path.read_bytes(), headers={"content-type": "image/png"}, raw=True)
    return int(p["photo"]["id"])


def event_input(f, live):
    i = {"title": f["title"], "description": f["description"], "startDateTime": f["start"],
         "duration": f["duration"], "venueId": venue_id(f["venue"], live)}
    if f["online_url"] or f["how_to_find_us"]:
        i["howToFindUs"] = f["online_url"] or f["how_to_find_us"]
    return i


# ---------- the log ----------

def load_events():
    return json.loads(EVENTS.read_text(encoding="utf-8")) if EVENTS.exists() else {}


def save_events(events, message):
    EVENTS.write_text(json.dumps(events, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    if not IN_ACTIONS:
        return

    def git(*a):
        subprocess.run(["git", *a], cwd=REPO, check=True, capture_output=True, text=True)
    git("config", "user.name", "github-actions[bot]")
    git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    git("add", "meetup/events.json", "meetup/venues.json") if VENUE_IDS.exists() else git("add", "meetup/events.json")
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
    raise Problem("Couldn't save meetup/events.json to GitHub.")


def notify(text):
    print("NOTICE:", text)
    if not IN_ACTIONS:
        return
    repo, gh = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"]
    owner, api = os.environ.get("GITHUB_REPOSITORY_OWNER", ""), f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}/issues"
    hdr = {"authorization": f"Bearer {gh}", "accept": "application/vnd.github+json"}
    issues = http("GET", api + "?state=open&per_page=100", headers=hdr)
    issue = next((i for i in issues if i["title"] == LOG_ISSUE and "pull_request" not in i), None)
    if not issue:
        issue = http("POST", api, data=json.dumps({"title": LOG_ISSUE, "body": "The Meetup sync comments here when it "
                     "lists, changes or cancels events, or hits a problem."}).encode(), headers=hdr)
    http("POST", f"{api}/{issue['number']}/comments", data=json.dumps({"body": f"@{owner} {text}" if owner else text}).encode(), headers=hdr)


def nice(start_at):
    dt = parse(start_at).astimezone(TZ)
    return f"{dt:%a %b} {dt.day}, {dt:%I:%M %p}".replace(" 0", " ")


# ---------- commands ----------

def run(live, as_of=None, draft=False, only=None):
    now = datetime.fromisoformat(as_of).replace(tzinfo=TZ) if as_of else datetime.now(TZ)
    if live and not draft and not only and (LIVE_FROM is None or now.date() < LIVE_FROM):
        print("Not switched on yet (LIVE_FROM), so nothing was done.")
        return 0
    events = load_events()
    luma_events = luma_upcoming(now)
    if only:
        luma_events = [e for e in luma_events if e["id"] == only]
    actions = plan(luma_events, events, now, lambda e: (IMAGES / meetup_fields(e)["image"]).exists())
    if only:
        actions = [a for a in actions if a[0] != "cancel"]
    print(f"{'LIVE' if live else 'DRY RUN'}{' (drafts)' if draft else ''} at {now:%a %Y-%m-%d %H:%M} Eastern: "
          f"{len(luma_events)} upcoming Luma events, {len(actions)} actions")
    done, problems = [], []
    for action, e in actions:
        label = e if isinstance(e, str) else f"{e['name']} ({nice(e['start_at'])})"
        print(f"  {action:10} {label}")
        if not live or action in ("skip", "wait_flyer"):
            if live and action == "wait_flyer":
                problems.append(f"{label} is waiting for its wide flyer (meetup/images/{meetup_fields(e)['image']}).")
            continue
        try:
            if action == "cancel":
                if not luma_gone(e):
                    continue  # second read says it's still there: leave it
                m = events[e]
                payload_ok(gql("mutation($i:DeleteEventInput!){ deleteEvent(input:$i){ success errors{ code field message } } }",
                               {"i": {"eventId": m["meetup_event_id"], "removeFromCalendar": False}})["deleteEvent"], "the cancel")
                m["status"] = "cancelled"
                save_events(events, f"Meetup: cancelled {e}")
                done.append(f"Cancelled on Meetup (gone from Luma): {m.get('title', e)} {m.get('meetup_url', '')}")
                continue
            f = meetup_fields(e)
            i = event_input(f, live)
            img = IMAGES / f["image"]
            m = events.get(e["id"], {})
            if img.exists() and not m.get("photo"):
                i["featuredPhotoId"] = upload_photo(img)
            if action == "create":
                i.update(groupUrlname=GROUP, publishStatus="DRAFT" if draft else "PUBLISHED")
                p = payload_ok(gql("mutation($i:CreateEventInput!){ createEvent(input:$i){ event{ id eventUrl } "
                                   "errors{ code field message } } }", {"i": i})["createEvent"], "the event")
                m = {"meetup_event_id": p["event"]["id"], "meetup_url": p["event"]["eventUrl"],
                     "status": "draft" if draft else "published", "made_by": "sync"}
            else:
                i["eventId"] = m["meetup_event_id"]
                payload_ok(gql("mutation($i:EditEventInput!){ editEvent(input:$i){ event{ id } errors{ code field message } } }",
                               {"i": i})["editEvent"], "the change")
            m.update(luma_url=e.get("url", ""), title=f["title"], start=e["start_at"], fingerprint=fingerprint(f),
                     photo=m.get("photo") or ("featuredPhotoId" in i))
            events[e["id"]] = m
            save_events(events, f"Meetup: {action} {e['id']}")
            done.append(f"{'Listed' if action == 'create' else 'Updated'}{' as a draft' if draft and action == 'create' else ''}: "
                        f"{label} {m.get('meetup_url', '')}")
        except Problem as err:
            problems.append(f"{label}: {err}")
    text = "\n".join(done + ["Problem: " + p for p in problems])
    if live and text:
        notify(safe(text))
    return 1 if any("Problem" not in p and "waiting" not in p for p in problems) else 0


def publish(live):
    events = load_events()
    drafts = [(k, m) for k, m in events.items() if not k.startswith("_") and m.get("status") == "draft"]
    for k, m in drafts:
        print(f"  {'publishing' if live else 'would publish'}: {m.get('title')} {m.get('meetup_url', '')}")
        if live:
            payload_ok(gql("mutation($i:PublishEventDraftInput!){ publishEventDraft(input:$i){ event{ id } errors{ code field message } } }",
                           {"i": {"eventId": m["meetup_event_id"]}})["publishEventDraft"], "publishing")
            m["status"] = "published"
            save_events(events, f"Meetup: published {k}")
    print(f"{len(drafts)} drafts")


def collect():
    flyers = next((p / "flyers" for p in HERE.parents if (p / "flyers").is_dir()), None)
    if not flyers:
        raise Problem("Couldn't find the club's flyers folder above this repository.")
    IMAGES.mkdir(exist_ok=True)
    n = 0
    for kind, slug, _, _ in KINDS.values():
        for src in sorted((flyers / kind).glob(f"* Spanish Club for Socialists {kind} – Meetup 16 9.png")):
            dst = IMAGES / f"{src.name[:10]}-{slug}.png"
            if not dst.exists() or dst.read_bytes() != src.read_bytes():
                shutil.copyfile(src, dst)
                n += 1
    print(f"Copied {n} wide flyers into meetup/images/ (commit them so GitHub can upload them).")


def check():
    print("Meetup group id:", group_id(), "| token OK")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--live", action="store_true")
    r.add_argument("--draft", action="store_true")
    r.add_argument("--as-of")
    r.add_argument("--only")
    p = sub.add_parser("publish")
    p.add_argument("--live", action="store_true")
    sub.add_parser("collect")
    sub.add_parser("check")
    a = ap.parse_args()
    try:
        if a.cmd == "run":
            return run(a.live, a.as_of, a.draft, a.only)
        if a.cmd == "publish":
            publish(a.live)
        elif a.cmd == "collect":
            collect()
        else:
            check()
        return 0
    except Problem as e:
        msg = safe(e)
        if getattr(a, "live", False):
            notify("Problem: " + msg)
        else:
            print("Problem:", msg)
        return 1


if __name__ == "__main__":
    sys.exit(main())
