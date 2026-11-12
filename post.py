#!/usr/bin/env python3
"""Spanish Club for Socialists: post the approved Instagram posts on schedule.

Runs on GitHub Actions (see .github/workflows/instagram.yml), or on a computer for testing.
Only the Python standard library is used.

Commands:
    python post.py run                     Test mode (the default): show what is due now. Posts nothing.
    python post.py run --as-of 2026-11-13T09:07
                                           Test mode, pretending it is that Eastern time.
    python post.py run --live              Post whatever is due now (GitHub Actions does this).
    python post.py run --live --item ID    Post one approved post now, ignoring its scheduled time.
    python post.py check                   Read-only check that the Instagram key works (GitHub runs it weekly).

    python post.py facebook                Test mode for the Facebook Page: show what is due now. Posts nothing.
    python post.py facebook --as-of 2026-11-13T09:07
    python post.py facebook --live         Post the Friday post to the Page if due (GitHub Actions does this).
    python post.py facebook --live --item ID
                                           Post one approved Friday post to the Page now.
    python post.py check-facebook          Read-only check of the Facebook key and Page (GitHub runs it weekly).

Facebook: the Friday feed posts (the weekly carousel and the winter break post, never Stories) also
go to the club's Facebook Page as one post: each night's approved square flyer (facebook/squares.json;
Facebook's photo grid crops 4:5 images) without the cover and closing slide, and the approved caption
with each event's Luma link added (Instagram @handles and "link in bio" left out). It runs
as its own step after Instagram, with its own key, log entry ("facebook" in log/posted.json) and
issue ("Facebook log"), so neither can block, retry or double-post the other. Nothing goes to
Facebook before FB_LIVE_FROM.

Safety rules built in:
- Only posts listed in manifest.json under "approved", with an unchanged fingerprint, are posted.
- Before each post, Luma's public calendar feed is read. A flyer whose event is gone or has
  moved is left out, along with its caption line and the cover that lists it. If nothing is
  left, the post is skipped. Nothing new is ever added. A post about the club itself, with no
  events at all, has nothing to check and goes out as approved.
- Each image is downloaded from its public link and checked against its fingerprint before
  Instagram is asked to fetch it.
- A post is marked "publishing" in log/posted.json (and pushed) before it is published, so a
  crash can never cause a double post. An unclear result is reported, never retried.
- Every post, skip and failure is reported as a comment on the "Instagram log" issue, which
  GitHub emails to the repository owner.
- The Instagram key is never printed or written to a file.
"""
import argparse
import hashlib
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

ROOT = pathlib.Path(__file__).resolve().parent
TZ = ZoneInfo("America/New_York")
GRAPH = "https://graph.facebook.com/v27.0"  # Instagram API with Unified login (system-user key)
NEEDED_SCOPES = {"instagram_basic", "instagram_content_publish"}
# The club's Instagram account. Not secret; checked against the username before every post.
IG_USER_ID = os.environ.get("IG_USER_ID", "17841429526104661")
IG_USERNAME = "spanishclubforsocialists"
WARN_BEFORE_EXPIRY = timedelta(days=21)
LUMA_FEED = "https://api.lu.ma/ics/get?entity=calendar&id=cal-ITgDf27EcKUkodA"
LATE_LIMIT = timedelta(hours=4)  # a post more than 4 hours late is reported as missed, not posted
UA = "spanish-club-instagram/1.0"
IN_ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"

# Facebook Page. Its own key: FACEBOOK_ACCESS_TOKEN, a system-user token of the "Spanish Club Page Poster" app.
FB_LIVE_FROM = date(2026, 11, 20)  # first Friday posted to the Page (organizer's OK after the Nov 11 sample). None = off.
FB_PAGE_ID = "1349858851551538"  # the club's Page (not secret), made Nov 11, 2026
FB_PAGE_NAME = "Spanish Club for Socialists"  # checked before every post
FB_NEEDED_SCOPES = {"pages_show_list", "pages_read_engagement", "pages_manage_posts"}
FEED_KINDS = ("carousel", "image")  # what goes to Facebook: the Friday feed posts, never Stories
IG_ONLY = re.compile(r" ?\(@[\w.]+\)|\s*RSVP at the link in bio\.")  # Instagram-only bits of a caption


# ---------- small helpers ----------

def now_et():
    return datetime.now(TZ)


def parse_et(s):
    return datetime.fromisoformat(s).replace(tzinfo=TZ)


def nice(dt):
    return f"{dt:%a %b} {dt.day}, {dt:%I:%M %p}".replace(" 0", " ")


def load(path, default):
    p = ROOT / path
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def save(path, data):
    p = ROOT / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


_TOKEN = None
_FB_TOKEN = None
_PAGE_TOKEN = None  # the Page's own token, fetched with the Facebook key at run time; only ever in memory


def secret(name):
    """A key from the environment (GitHub secret), or on Windows from the user's env vars. Never printed."""
    value = os.environ.get(name, "").strip()
    if not value and os.name == "nt":
        value = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", f"[Environment]::GetEnvironmentVariable('{name}','User')"],
            capture_output=True, text=True).stdout.strip()
    return value


def token():
    """The Instagram key."""
    global _TOKEN
    if _TOKEN is None:
        _TOKEN = secret("INSTAGRAM_ACCESS_TOKEN")
    return _TOKEN


def fb_token():
    """The Facebook key (a system-user token of the Page poster app)."""
    global _FB_TOKEN
    if _FB_TOKEN is None:
        _FB_TOKEN = secret("FACEBOOK_ACCESS_TOKEN")
    return _FB_TOKEN


def scrub(text):
    text = str(text)
    for t in (_TOKEN, _FB_TOKEN, _PAGE_TOKEN):
        if t:
            text = text.replace(t, "***")
    return text


class Problem(Exception):
    """Something went wrong; the message is safe to show (no key in it)."""


def http(method, url, params=None, auth=True, raw=False, timeout=60, key=None):
    """auth=True adds the Instagram key (or `key`, if given) as the access_token parameter (Meta's documented way)."""
    headers = {"user-agent": UA}
    data = None
    if auth:
        params = dict(params or {}, access_token=key if key is not None else token())
    if params and method == "GET":
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    elif params:
        data = urllib.parse.urlencode(params).encode()
        headers["content-type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            return (body, r.headers.get("content-type", "")) if raw else json.loads(body)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:600]
        raise Problem(scrub(f"{method} {url.split('?')[0]} failed ({e.code}): {detail}")) from None
    except urllib.error.URLError as e:
        raise Problem(scrub(f"{method} {url.split('?')[0]} failed: {e.reason}")) from None


def graph(method, path, **params):
    return http(method, f"{GRAPH}/{path}", params or None)


def fb_graph(method, path, key, **params):
    """A Graph API call with a Facebook key (the system-user key, or the Page's own token)."""
    if not key:
        raise Problem("No Facebook key is set (FACEBOOK_ACCESS_TOKEN).")
    return http(method, f"{GRAPH}/{path}", params or None, key=key)


# ---------- Luma ----------

def luma_events():
    """{event link: start time 'YYYY-MM-DDTHH:MM' Eastern} for every event in Luma's public feed that isn't cancelled."""
    body, _ = http("GET", LUMA_FEED, auth=False, raw=True)
    text = body.decode("utf-8", "replace").replace("\r\n", "\n").replace("\n ", "")
    events = {}
    for block in text.split("BEGIN:VEVENT")[1:]:
        fields = {}
        for line in block.split("\n"):
            if ":" in line:
                k, v = line.split(":", 1)
                fields.setdefault(k.split(";")[0], v)
        if fields.get("STATUS", "").upper() == "CANCELLED":
            continue
        m = re.search(r"https://luma\.com/[A-Za-z0-9_-]+", fields.get("DESCRIPTION", ""))
        start = fields.get("DTSTART", "")
        if not m or not re.fullmatch(r"\d{8}T\d{6}Z", start):
            continue
        dt = datetime.strptime(start, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).astimezone(TZ)
        events[m[0]] = dt.strftime("%Y-%m-%dT%H:%M")
    if len(events) < 3:
        raise Problem("Luma's calendar feed came back nearly empty, so nothing was posted to be safe.")
    return events


def event_problems(item, luma):
    """{event link: what's wrong} for this post's events that are gone or have moved."""
    out = {}
    for url, start in item["events"].items():
        if url not in luma:
            out[url] = "is no longer on Luma"
        elif luma[url] != start:
            out[url] = f"moved from {nice(parse_et(start))} to {nice(parse_et(luma[url]))}"
    return out


def plan(item, luma):
    """What would actually be posted after the Luma check: (slides, caption, notes, cover_included, location).
    location is the post's place tag, or None if it has none or all the events it rests on are gone."""
    bad = event_problems(item, luma)
    notes = [f"The {nice(parse_et(item['events'][url]))} event ({url}) {why}." for url, why in bad.items()]
    slides, cover_included = [], False
    for s in item["slides"]:
        if s.get("event") and s["event"] in bad:
            continue  # its flyer goes
        if any(e in bad for e in s.get("lists_events", [])):
            continue  # a cover or notice that lists a gone event goes too
        slides.append(s)
        if s.get("lists_events") and item["kind"] in ("carousel", "story"):
            cover_included = True
    caption = "\n".join(line["text"] for line in item["caption_lines"]
                        if not (line.get("event") and line["event"] in bad))
    has_event_content = any(s.get("event") or s.get("lists_events") for s in slides)
    # A post about the club itself (like "how it works") lists no events, so there's nothing on Luma to lose.
    about_club = not item["events"] and not any(s.get("event") or s.get("lists_events") for s in item["slides"])
    if not has_event_content and not about_club:
        slides = []
    if bad and slides:
        notes.append("Left out of this post: the flyer, caption line and cover that mention it.")
    location = item.get("location")
    if location and location["events"] and all(e in bad for e in location["events"]):
        location = None
        if slides:
            notes.append("The location tag was left off, since its events are gone.")
    return slides, caption, notes, cover_included, location


# ---------- GitHub (log commits and notices) ----------

def git(*args):
    subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True)


def push_log(message):
    if not IN_ACTIONS:
        return
    git("config", "user.name", "github-actions[bot]")
    git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    git("add", "log")
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode == 0:
        return
    git("commit", "-m", message)
    for _ in range(3):
        try:
            git("pull", "--rebase", "--quiet")
            git("push", "--quiet")
            return
        except subprocess.CalledProcessError:
            time.sleep(5)
    raise Problem("Couldn't save the post log to GitHub.")


def notify(text, issue_title="Instagram log"):
    """Comment on the 'Instagram log' (or 'Facebook log') issue so GitHub emails the owner. Prints when testing."""
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
    issue = next((i for i in issues if i["title"] == issue_title and "pull_request" not in i), None)
    if not issue:
        issue = call("POST", api, {"title": issue_title,
                                   "body": "The auto-poster comments here after every post, skip or problem."})
    call("POST", f"{api}/{issue['number']}/comments", {"body": f"@{owner} {text}" if owner else text})


# ---------- Instagram ----------

def public_url(path):
    repo, sha = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_SHA")
    if not (repo and sha):
        return f"https://raw.githubusercontent.com/<owner>/<repo>/<commit>/{path}"
    return f"https://raw.githubusercontent.com/{repo}/{sha}/{path}"


def check_image(slide, online):
    """Check the image matches its approved fingerprint: on disk, and (on GitHub) at its public link."""
    data = (ROOT / slide["image"]).read_bytes()
    if hashlib.sha256(data).hexdigest() != slide["sha256"]:
        raise Problem(f"{slide['image']} has changed since it was approved.")
    if online:
        body, ctype = http("GET", public_url(slide["image"]), auth=False, raw=True)
        if hashlib.sha256(body).hexdigest() != slide["sha256"]:
            raise Problem(f"The public copy of {slide['image']} doesn't match the approved image.")


def wait_ready(container):
    for _ in range(6):  # Meta: check once a minute, for no more than 5 minutes
        status = graph("GET", container, fields="status_code").get("status_code")
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise Problem(f"Instagram couldn't prepare the post (status {status}).")
        time.sleep(60 if status == "IN_PROGRESS" else 5)
    raise Problem("Instagram took more than 5 minutes to prepare the post.")


def make_containers(item, slides, caption, user_id, location=None, notes=None):
    """Prepare the post on Instagram (not published yet). A location goes on a single image or the carousel itself
    (Meta doesn't allow it on carousel slides or Stories). If Instagram refuses the location, the post is made
    without it and a note says so, so the post still goes out."""
    def feed_container(**params):
        if location:
            try:
                return graph("POST", f"{user_id}/media", location_id=location["id"], **params)["id"]
            except Problem as e:
                if "location" not in str(e).lower():
                    raise
                if notes is not None:
                    notes.append(f"Posted without the {location['name']} location tag, because Instagram refused it. "
                                 f"Details: {e}")
        return graph("POST", f"{user_id}/media", **params)["id"]

    if item["kind"] == "story":
        c = graph("POST", f"{user_id}/media", image_url=public_url(slides[0]["image"]), media_type="STORIES")["id"]
    elif len(slides) == 1:
        c = feed_container(image_url=public_url(slides[0]["image"]), caption=caption)
    else:
        children = [graph("POST", f"{user_id}/media", image_url=public_url(s["image"]), is_carousel_item="true")["id"]
                    for s in slides]
        c = feed_container(media_type="CAROUSEL", children=",".join(children), caption=caption)
    wait_ready(c)
    return c


# ---------- the run ----------

def describe(item, slides, caption, notes, location=None):
    kind = {"story": "Story", "image": "single post"}.get(item["kind"], "carousel")
    if item["kind"] == "carousel" and len(slides) == 1:
        kind = "single post"
    lines = [f"  Would post: {kind} with {len(slides)} image(s)"]
    for n, s in enumerate(slides, 1):
        lines.append(f"    {n}. {s['image']}")
    if location:
        lines.append(f"  Location: {location['name']} (Facebook Page {location['id']})")
    if caption:
        lines.append("  Caption:")
        lines += [f"    | {line}" for line in caption.split("\n")]
    lines += [f"  Note: {n}" for n in notes]
    return "\n".join(lines)


def run(live, as_of=None, only=None):
    manifest = load("manifest.json", None)
    if not manifest:
        raise Problem("manifest.json is missing.")
    log = load("log/posted.json", {})
    now = parse_et(as_of) if as_of else now_et()
    approved = manifest.get("approved", {})
    items = {i["id"]: i for i in manifest["items"]}

    print(("LIVE" if live else "TEST MODE: nothing will be posted.") + f" Time: {nice(now)} Eastern.")

    if only:
        if only not in items:
            raise Problem(f"No post called {only} in the manifest.")
        if log.get(only, {}).get("status") in ("posted", "publishing"):
            raise Problem(f"{only} has already been posted (see log/posted.json).")
        due = [items[only]]
    else:
        due, missed = [], []
        for item in manifest["items"]:
            if item["id"] in log:
                continue
            at = parse_et(item["post_at"])
            if at <= now < at + LATE_LIMIT:
                due.append(item)
            elif now >= at + LATE_LIMIT and approved.get(item["id"]) == item["fingerprint"] and live:
                missed.append(item)
        for item in missed:
            log[item["id"]] = {"status": "missed", "at": now.isoformat(timespec="minutes")}
            save("log/posted.json", log)
            push_log(f"Missed {item['id']}")
            notify(f"Missed: {item['what']} was due {nice(parse_et(item['post_at']))} but the scheduler "
                   "didn't run in time, so it wasn't posted.")
    if not due:
        nxt = min((i for i in manifest["items"] if parse_et(i["post_at"]) > now and i["id"] not in log),
                  key=lambda i: i["post_at"], default=None)
        print("Nothing is due." + (f" Next: {nxt['what']}, {nice(parse_et(nxt['post_at']))}." if nxt else ""))
        return 0

    if live and not any(approved.get(i["id"]) == i["fingerprint"] for i in due):
        print("Due, but not approved, so nothing will be posted: " + ", ".join(i["id"] for i in due))
        return 0

    luma = luma_events()
    print(f"Luma: {len(luma)} events in the public calendar feed.")
    user_id = None
    if token():
        user_id, username = instagram_account()
        print(f"Instagram: posting to @{username}.")
    elif live:
        raise Problem("No Instagram key is set.")

    failures = 0
    cover_this_run = {}  # test mode: which carousels in this run would go out with their cover
    for item in due:
        print(f"\n{item['id']}: {item['what']} (due {nice(parse_et(item['post_at']))})")
        if approved.get(item["id"]) != item["fingerprint"]:
            if live:
                print("  Not approved (or changed since approval), so it won't be posted.")
                continue
            print("  NOT APPROVED YET: shown here, but a live run would hold it back.")
        dep = item.get("depends_on")
        dep_ok = (log.get(dep, {}).get("status") == "posted" and log[dep].get("cover_included")) \
            or (not live and cover_this_run.get(dep))
        if dep and not dep_ok:
            print(f"  Skipped: it goes out only after {dep} is posted with its cover.")
            if live:
                log[item["id"]] = {"status": "skipped", "at": now.isoformat(timespec="minutes"),
                                   "note": f"{dep} wasn't posted with its cover"}
                save("log/posted.json", log)
                push_log(f"Skipped {item['id']}")
            continue
        slides, caption, notes, cover_included, location = plan(item, luma)
        if not slides:
            msg = f"Skipped: {item['what']}. " + " ".join(notes)
            print("  " + msg)
            if live:
                log[item["id"]] = {"status": "skipped", "at": now.isoformat(timespec="minutes"), "note": " ".join(notes)}
                save("log/posted.json", log)
                push_log(f"Skipped {item['id']}")
                notify(msg)
            continue
        for s in slides:
            check_image(s, online=live)
        print(describe(item, slides, caption, notes, location))
        if not live:
            cover_this_run[item["id"]] = cover_included
            continue
        try:
            container = make_containers(item, slides, caption, user_id, location, notes)
            log[item["id"]] = {"status": "publishing", "at": now_et().isoformat(timespec="minutes")}
            save("log/posted.json", log)
            push_log(f"Publishing {item['id']}")
            media = graph("POST", f"{user_id}/media_publish", creation_id=container)["id"]
            link = graph("GET", media, fields="permalink").get("permalink", "")
            log[item["id"]] = {"status": "posted", "at": now_et().isoformat(timespec="minutes"), "media_id": media,
                               "permalink": link, "cover_included": cover_included, "notes": notes}
            save("log/posted.json", log)
            push_log(f"Posted {item['id']}")
            notify(f"Posted: {item['what']}. {link}" + (" " + " ".join(notes) if notes else ""))
        except Problem as e:
            failures += 1
            state = log.get(item["id"], {}).get("status")
            if state == "publishing":
                notify(f"PROBLEM: {item['what']} may or may not have posted. Please check Instagram. "
                       f"It won't be retried. Details: {e}")
            else:
                notify(f"FAILED: {item['what']} wasn't posted. It will be tried again on the next run "
                       f"within {LATE_LIMIT.seconds // 3600} hours of its time. Details: {e}")
    return 1 if failures else 0


def instagram_account():
    """(id, username) of the club's Instagram account, after checking the key reaches it."""
    acct = graph("GET", IG_USER_ID, fields="id,username")
    if acct.get("username", "").lower() != IG_USERNAME:
        raise Problem(f"Account {IG_USER_ID} is @{acct.get('username')}, not @{IG_USERNAME}, so nothing was posted.")
    return acct["id"], acct["username"]


def check():
    """Read-only health check of the key. Raises (and so emails, on GitHub) if anything is off."""
    if not token():
        raise Problem("No Instagram key found (INSTAGRAM_ACCESS_TOKEN).")
    me = graph("GET", "me", fields="id,name,client_business_id")
    granted = {p["permission"] for p in graph("GET", "me/permissions").get("data", []) if p.get("status") == "granted"}
    if not NEEDED_SCOPES <= granted:
        raise Problem("The key is missing permission(s): " + ", ".join(sorted(NEEDED_SCOPES - granted)) + ".")
    ig_id, username = instagram_account()
    print(f"The key works. It belongs to {me.get('name', 'a system user')} "
          f"(business {me.get('client_business_id', 'not shown')}) and posts to @{username}.")
    # Expiry: Meta only tells an app token, so this usually says "not shown". A dead key fails the calls above.
    try:
        info = graph("GET", "debug_token", input_token=token()).get("data", {})
    except Problem:
        info = {}
    expires = info.get("expires_at")
    if expires is None:
        print("Expiry: not shown to this key. (A key that stops working fails this check, which emails you.)")
    elif expires == 0:
        print("Expiry: never.")
    else:
        when = datetime.fromtimestamp(expires, TZ)
        print(f"Expiry: {nice(when)}.")
        if when - now_et() < WARN_BEFORE_EXPIRY:
            raise Problem(f"The Instagram key expires {nice(when)}. Make a new one and save it as the "
                          "INSTAGRAM_ACCESS_TOKEN secret, or posting will stop.")
    limit = graph("GET", f"{ig_id}/content_publishing_limit", fields="quota_usage,config")
    print("Publishing limit:", json.dumps(limit.get("data", limit)))


# ---------- Facebook Page ----------

FB_LOG = "Facebook log"


def fb_caption(item, bad):
    """The approved caption for Facebook. Each event line gets its Luma link under it (links are clickable on
    Facebook), Instagram @handles and "link in bio" are left out, and lines for gone or moved events are dropped.
    A link for an event that no line names (the break post's return night) ends the next-to-last paragraph."""
    lines, linked, after_event = [], set(), False
    for line in item["caption_lines"]:
        url = line.get("event")
        if url and url in bad:
            continue
        text = IG_ONLY.sub("", line["text"]).rstrip()
        if line["text"].strip() and not text:
            continue  # the line was only Instagram text
        if url:
            if after_event:
                lines.append("")
            lines += [text, f"RSVP: {url}"]
            linked.add(url)
        else:
            lines.append(text)
        after_event = bool(url)
    paragraphs = [p.strip("\n") for p in re.split(r"\n\s*\n", "\n".join(lines)) if p.strip()]
    leftover = [u for u in item["events"] if u not in bad and u not in linked]
    if leftover and paragraphs:
        at = len(paragraphs) - 2 if len(paragraphs) > 1 else 0
        paragraphs[at] += "".join(f"\nRSVP: {u}" for u in leftover)
    return "\n\n".join(paragraphs)


def fb_slides(slides, squares, wides=None):
    """Facebook's photos: each night's approved square flyer, without the weekly cover and closing slide (Facebook
    crops 4:5 photos in a multi-photo post's grid, and the post's text already lists the week). With exactly three
    nights, Facebook shows the first photo as a wide strip on top, so the first night's approved wide (16:9) flyer
    leads instead. If any flyer has no approved square, Instagram's own images are used, with a note. A post with no
    flyers (the break post) is unchanged. Returns (slides, notes)."""
    flyers = [s for s in slides if s.get("event")]
    if not flyers:
        return slides, []
    missing = [s["image"] for s in flyers if not (squares.get(s["image"]) or {}).get("approved")]
    if missing:
        return slides, [f"No approved square flyer for {', '.join(missing)}, so Instagram's 4:5 images were used."]
    pick = [squares[s["image"]] for s in flyers]
    wide = (wides or {}).get(flyers[0]["image"]) or {}
    if len(flyers) == 3 and wide.get("approved"):
        pick[0] = wide
    return [{"image": p["image"], "sha256": p["sha256"], "event": s["event"]} for p, s in zip(pick, flyers)], []


def fb_plan(item, luma, squares, wides=None):
    """(slides, caption, notes) for the Page after the Luma check: the square flyers and Facebook's caption."""
    slides, _, notes, _, _ = plan(item, luma)
    notes = [n for n in notes if "location tag" not in n]  # Facebook posts carry no location tag
    if slides:
        slides, more = fb_slides(slides, squares, wides)
        notes += more
    return slides, fb_caption(item, event_problems(item, luma)), notes


def fb_due(items, fb_log, now, live_from, approved):
    """(due, missed): the Friday feed posts (never Stories) due on the Page now. Nothing before live_from
    (None = off). Only approved posts are reported as missed."""
    due, missed = [], []
    if live_from is None:
        return due, missed
    for item in items:
        if item["kind"] not in FEED_KINDS or item["id"] in fb_log:
            continue
        at = parse_et(item["post_at"])
        if at.date() < live_from:
            continue
        if at <= now < at + LATE_LIMIT:
            due.append(item)
        elif now >= at + LATE_LIMIT and approved.get(item["id"]) == item["fingerprint"]:
            missed.append(item)
    return due, missed


def attached_media(photo_ids):
    """The feed post's attached_media parameters, in slide order (Meta's documented form)."""
    return {f"attached_media[{n}]": json.dumps({"media_fbid": pid}, separators=(",", ":"))
            for n, pid in enumerate(photo_ids)}


def facebook_page():
    """(id, name) of the club's Page after checking the key reaches it. Keeps the Page's own token in memory."""
    global _PAGE_TOKEN
    if not FB_PAGE_ID:
        raise Problem("FB_PAGE_ID isn't set in post.py, so nothing was posted to Facebook.")
    page = fb_graph("GET", FB_PAGE_ID, fb_token(), fields="id,name,access_token")
    _PAGE_TOKEN = page.get("access_token") or None
    if page.get("name", "") != FB_PAGE_NAME:
        raise Problem(f"Page {FB_PAGE_ID} is \"{page.get('name')}\", not \"{FB_PAGE_NAME}\", so nothing was posted.")
    if not _PAGE_TOKEN:
        raise Problem("The Facebook key can't get the Page's token. Check the system user has access to the Page.")
    return page["id"], page["name"]


def describe_fb(slides, caption, notes):
    lines = [f"  Would post to the Facebook Page: one post with {len(slides)} photo(s)"]
    lines += [f"    {n}. {s['image']}" for n, s in enumerate(slides, 1)]
    lines.append("  Text:")
    lines += [f"    | {line}" for line in caption.split("\n")]
    lines += [f"  Note: {n}" for n in notes]
    return "\n".join(lines)


def run_facebook(live, as_of=None, only=None):
    manifest = load("manifest.json", None)
    if not manifest:
        raise Problem("manifest.json is missing.")
    log = load("log/posted.json", {})
    fb = log.get("facebook", {})
    flyers = load("facebook/squares.json", {})
    squares, wides = flyers.get("squares", {}), flyers.get("wides", {})
    now = parse_et(as_of) if as_of else now_et()
    approved = manifest.get("approved", {})
    items = {i["id"]: i for i in manifest["items"]}

    def record(item_id, entry, message):
        fb[item_id] = entry
        log["facebook"] = fb
        save("log/posted.json", log)
        push_log(message)

    print(("LIVE" if live else "TEST MODE: nothing will be posted.") + f" Facebook Page. Time: {nice(now)} Eastern.")

    if only:
        if only not in items or items[only]["kind"] not in FEED_KINDS:
            raise Problem(f"No Friday feed post called {only} in the manifest.")
        if fb.get(only, {}).get("status") in ("posted", "publishing"):
            raise Problem(f"{only} has already been posted to Facebook (see log/posted.json).")
        due = [items[only]]
    else:
        if live and (FB_LIVE_FROM is None or now.date() < FB_LIVE_FROM):
            print("Not switched on yet (FB_LIVE_FROM), so nothing was done.")
            return 0
        due, missed = fb_due(manifest["items"], fb, now, FB_LIVE_FROM if live else date.min, approved)
        for item in missed if live else []:
            record(item["id"], {"status": "missed", "at": now.isoformat(timespec="minutes")},
                   f"Facebook: missed {item['id']}")
            notify(f"Missed on Facebook: {item['what']} was due {nice(parse_et(item['post_at']))} but the scheduler "
                   "didn't run in time, so it wasn't posted.", FB_LOG)
    if not due:
        nxt = min((i for i in manifest["items"] if i["kind"] in FEED_KINDS and i["id"] not in fb
                   and parse_et(i["post_at"]) > now), key=lambda i: i["post_at"], default=None)
        print("Nothing is due for Facebook." + (f" Next: {nxt['what']}, {nice(parse_et(nxt['post_at']))}." if nxt else ""))
        return 0

    if live and not any(approved.get(i["id"]) == i["fingerprint"] for i in due):
        print("Due, but not approved, so nothing will be posted: " + ", ".join(i["id"] for i in due))
        return 0

    luma = luma_events()
    print(f"Luma: {len(luma)} events in the public calendar feed.")
    page_id = None
    if fb_token() and FB_PAGE_ID:
        page_id, name = facebook_page()
        print(f"Facebook: posting to the Page \"{name}\".")
    elif live:
        raise Problem("No Facebook key is set (FACEBOOK_ACCESS_TOKEN)." if not fb_token() else
                      "FB_PAGE_ID isn't set in post.py.")

    failures = 0
    for item in due:
        print(f"\n{item['id']}: {item['what']} (due {nice(parse_et(item['post_at']))})")
        if approved.get(item["id"]) != item["fingerprint"]:
            if live:
                print("  Not approved (or changed since approval), so it won't be posted.")
                continue
            print("  NOT APPROVED YET: shown here, but a live run would hold it back.")
        slides, caption, notes = fb_plan(item, luma, squares, wides)
        if not slides:
            msg = f"Skipped on Facebook: {item['what']}. " + " ".join(notes)
            print("  " + msg)
            if live:
                record(item["id"], {"status": "skipped", "at": now.isoformat(timespec="minutes"),
                                    "note": " ".join(notes)}, f"Facebook: skipped {item['id']}")
                notify(msg, FB_LOG)
            continue
        print(describe_fb(slides, caption, notes))
        if not live:
            for s in slides:
                check_image(s, online=False)
            continue
        try:
            for s in slides:
                check_image(s, online=True)
            photos = [fb_graph("POST", f"{page_id}/photos", _PAGE_TOKEN, url=public_url(s["image"]),
                               published="false")["id"] for s in slides]
            record(item["id"], {"status": "publishing", "at": now_et().isoformat(timespec="minutes")},
                   f"Facebook: publishing {item['id']}")
            post_id = fb_graph("POST", f"{page_id}/feed", _PAGE_TOKEN, message=caption, **attached_media(photos))["id"]
            try:
                link = fb_graph("GET", post_id, _PAGE_TOKEN, fields="permalink_url").get("permalink_url", "")
            except Problem:
                link = ""
            record(item["id"], {"status": "posted", "at": now_et().isoformat(timespec="minutes"), "post_id": post_id,
                                "permalink": link, "photos": len(photos), "notes": notes},
                   f"Facebook: posted {item['id']}")
            notify(f"Posted on Facebook: {item['what']}. {link}" + (" " + " ".join(notes) if notes else ""), FB_LOG)
        except Problem as e:
            failures += 1
            if fb.get(item["id"], {}).get("status") in ("publishing", "posted"):
                notify(f"PROBLEM: {item['what']} may or may not have posted on Facebook. Please check the Page. "
                       f"It won't be retried. Details: {e}", FB_LOG)
            else:
                notify(f"FAILED: {item['what']} wasn't posted on Facebook. It will be tried again on the next run "
                       f"within {LATE_LIMIT.seconds // 3600} hours of its time. Details: {e}", FB_LOG)
    return 1 if failures else 0


def check_facebook(if_live=False):
    """Read-only check of the Facebook key and Page. Raises (and so emails, on GitHub) if anything is off."""
    if if_live and FB_LIVE_FROM is None:
        print("Facebook isn't switched on yet (FB_LIVE_FROM), so its key wasn't checked.")
        return
    key = fb_token()
    if not key:
        raise Problem("No Facebook key found (FACEBOOK_ACCESS_TOKEN).")
    granted = {p["permission"] for p in fb_graph("GET", "me/permissions", key).get("data", [])
               if p.get("status") == "granted"}
    if not FB_NEEDED_SCOPES <= granted:
        raise Problem("The Facebook key is missing permission(s): " + ", ".join(sorted(FB_NEEDED_SCOPES - granted)) + ".")
    pages = fb_graph("GET", "me/accounts", key, fields="id,name,tasks").get("data", [])
    print("The Facebook key works.")
    for p in pages:
        print(f"  It reaches the Page \"{p.get('name')}\" (ID {p.get('id')}). Tasks: {', '.join(p.get('tasks', []))}.")
    if not FB_PAGE_ID:
        raise Problem("FB_PAGE_ID isn't set in post.py yet. Set it to the club Page's ID.")
    page_id, name = facebook_page()
    tasks = next((p.get("tasks", []) for p in pages if p.get("id") == page_id), None)
    if tasks is not None and "CREATE_CONTENT" not in tasks:
        raise Problem("The Facebook key can see the Page but can't post to it. Give the system user Content access.")
    print(f"It can post to the Page \"{name}\" (ID {page_id}).")
    try:
        info = fb_graph("GET", "debug_token", key, input_token=key).get("data", {})
    except Problem:
        info = {}
    expires = info.get("expires_at")
    if expires is None:
        print("Expiry: not shown to this key. (A key that stops working fails this check, which emails you.)")
    elif expires == 0:
        print("Expiry: never.")
    else:
        when = datetime.fromtimestamp(expires, TZ)
        print(f"Expiry: {nice(when)}.")
        if when - now_et() < WARN_BEFORE_EXPIRY:
            raise Problem(f"The Facebook key expires {nice(when)}. Make a new one and save it as the "
                          "FACEBOOK_ACCESS_TOKEN secret, or Facebook posting will stop.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "facebook"):
        r = sub.add_parser(name)
        r.add_argument("--live", action="store_true")
        r.add_argument("--as-of")
        r.add_argument("--item")
    sub.add_parser("check")
    sub.add_parser("check-facebook").add_argument("--if-live", action="store_true")
    a = ap.parse_args()
    facebook = a.cmd in ("facebook", "check-facebook")
    try:
        if a.cmd in ("run", "facebook"):
            if a.live and a.as_of:
                raise Problem("--as-of is for test mode only.")
            return (run_facebook if facebook else run)(a.live, a.as_of, a.item)
        if a.cmd == "check-facebook":
            return check_facebook(a.if_live) or 0
        return check() or 0
    except Problem as e:
        if a.cmd in ("run", "facebook") and not a.live:
            print("PROBLEM:", e)
        else:
            notify(f"FAILED ({a.cmd}): {e}", FB_LOG if facebook else "Instagram log")
        return 1


if __name__ == "__main__":
    sys.exit(main())
