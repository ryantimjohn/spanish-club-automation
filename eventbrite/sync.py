#!/usr/bin/env python3
"""Spanish Club for Socialists: keep Eventbrite in step with Luma.

Every day (see .github/workflows/eventbrite.yml) this script:
1. lists each Luma event on Eventbrite once it's within WEEKS_AHEAD weeks: Luma's exact title, date, time
   and place, a free ticket, Luma's description ending with a line back to Luma, and the event's wide flyer;
2. updates an Eventbrite event when its Luma event changes, and cancels it if the Luma event is gone.
Luma is the source of truth. Online events get the Zoom link only on Eventbrite's attendee-only
Online Event Page, never in the public description. Only the Python standard library is used.

Commands:
    python eventbrite/sync.py run                      Dry run: show what would happen. Changes nothing.
    python eventbrite/sync.py run --as-of 2026-11-17T10:05
    python eventbrite/sync.py run --live --draft --only LUMA_EVENT_ID
                                                       Make or update one event as an unpublished DRAFT (the sample).
    python eventbrite/sync.py run --live               The daily run (GitHub Actions). Needs LIVE_FROM.
    python eventbrite/sync.py publish --live           Publish the drafts recorded in events.json (after the OK).
    python eventbrite/sync.py check                    Read-only: check the token and show the account's ids.

Safety rules built in:
- Nothing changes without --live, and the daily run does nothing until LIVE_FROM is set.
- Only events recorded in eventbrite/events.json are ever edited or cancelled.
- An Eventbrite event is cancelled (never deleted) only if Luma no longer has it, checked twice.
- An event whose wide flyer isn't in meetup/images/ waits; it never goes up without its image.
- An in-person event whose Luma address doesn't match its venue below waits, so nothing is listed at the wrong place.
- The repository is public: no token, Zoom link, names or emails are printed or saved.
"""
import argparse
import hashlib
import html
import json
import os
import pathlib
import re
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# ---------- settings ----------
WEEKS_AHEAD = 5
LIVE_FROM = None        # date(YYYY, M, D): when the daily run may start changing Eventbrite, set with the organizer's OK.
CAPACITY = 100          # free tickets per event (Luma has no cap)
TICKET_NAME = "General Admission"
CATEGORY_ID = "113"     # Community & Culture (Eventbrite ids, not secret; None leaves one unset)
SUBCATEGORY_ID = "13009"  # Language
FORMAT_ID = "10"        # Meeting or Networking Event
FOLLOW = "Get weekly reminders and see every event: follow us on Luma, https://luma.com/spanishclubforsocialists"
SUMMARY = "Free Spanish conversation practice{where}. Beginner, intermediate and advanced are all welcome."

KINDS = {  # Luma name contains -> (kind, image slug, venue key, extra line for the description)
    "St. Stephen": ("St. Stephen's", "st-stephens", "st-stephens", "Please come in the side door!"),
    "Online": ("Online", "online", "online", None),
    "Liberation Center": ("Liberation Center", "liberation-center", "liberation-center", None),
    "Dupont": ("Dupont", "dupont", "dupont", None),
}
VENUES = {  # created on Eventbrite the first time they're needed
    "st-stephens": {"name": "St. Stephen and the Incarnation Episcopal Church",
                    "address": {"address_1": "1525 Newton St NW", "city": "Washington", "region": "DC",
                                "postal_code": "20010", "country": "US"}},
    "liberation-center": {"name": "DC Liberation Center",
                          "address": {"address_1": "337 H St NE", "city": "Washington", "region": "DC",
                                      "postal_code": "20002", "country": "US"}},
    "dupont": {"name": "Patchwork",
               "address": {"address_1": "1901 18th St NW", "city": "Washington", "region": "DC",
                           "postal_code": "20009", "country": "US"}},
}

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
EVENTS = HERE / "events.json"
IMAGES = REPO / "meetup" / "images"   # the same wide flyers as Meetup ("python meetup/sync.py collect")
TZ_NAME = "America/New_York"
TZ = ZoneInfo(TZ_NAME)
LUMA = "https://public-api.luma.com"
API = "https://www.eventbriteapi.com/v3"
UA = "spanish-club-automation/1.0 (eventbrite-sync)"
LOG_ISSUE = "Eventbrite log"
IN_ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"
EMAIL = re.compile(r"[^\s@]+@[^\s@]+")
ZOOM = re.compile(r"https?://[\w.-]*zoom\.us/\S*")
SECRET_HINT = re.compile(r"pwd=|passcode", re.I)


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


def utc(ts):
    return parse(ts).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def local_date(ts):
    return parse(ts).astimezone(TZ).strftime("%Y-%m-%d")


def _line_html(line):
    text = html.escape(line, quote=False)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", lambda m: f'<a href="{m[2]}">{m[1]}</a>', text)
    return re.sub(r"(?<![\"'>])(https?://[^\s<]+)", lambda m: f'<a href="{m[1]}">{m[1]}</a>', text)


def html_description(md, extra=None):
    """Luma's markdown as Eventbrite's simple HTML (paragraphs and links only), without any Zoom link or
    passcode, with the extra line after the map link, ending with the line back to Luma."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", md or "") if p.strip()]
    paras = [p for p in paras if not ZOOM.search(p) and not SECRET_HINT.search(p)]
    if extra:
        at = next((i + 1 for i, p in enumerate(paras) if "google maps" in p.lower()), min(1, len(paras)))
        paras.insert(at, extra)
    lines = [ln.strip() for p in paras + [FOLLOW] for ln in p.split("\n") if ln.strip()]
    return "".join(f"<p>{_line_html(ln)}</p>" for ln in lines)


def place_ok(event):
    """In-person events must be at the address the venue table expects (Luma decides; this only checks)."""
    venue = _kind(event)[2]
    if venue == "online":
        return True
    full = ((event.get("geo_address_json") or {}).get("full_address") or "").lower()
    return VENUES[venue]["address"]["address_1"].lower() in full


def eventbrite_fields(event):
    kind, slug, venue, extra = _kind(event)
    online = venue == "online"
    return {"title": event["name"], "start": utc(event["start_at"]), "end": utc(event["end_at"]),
            "venue": venue, "online": online, "online_url": event.get("meeting_url") if online else None,
            "summary": SUMMARY.format(where=" on Zoom" if online else ""),
            "description": html_description(event.get("description_md", ""), extra),
            "image": f"{local_date(event['start_at'])}-{slug}.png"}


def fingerprint(fields):
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()[:16]


def plan(luma_events, mapping, now, has_flyer):
    """[(action, event_or_luma_id)]: create / update / wait_flyer / wait_place / cancel / skip.
    New events are listed inside the window; events already listed follow Luma wherever they move."""
    end = now + timedelta(weeks=WEEKS_AHEAD)
    actions, live_ids = [], {e["id"] for e in luma_events}
    for e in luma_events:
        start = parse(e["start_at"])
        m = mapping.get(e["id"])
        if start <= now or (not m and start > end) or (m and m.get("status") == "cancelled"):
            continue
        if kind_of(e) is None:
            actions.append(("skip", e))
            continue
        if m and m.get("status") != "incomplete" and m.get("fingerprint") == fingerprint(eventbrite_fields(e)):
            continue
        if not place_ok(e):
            actions.append(("wait_place", e))
        elif not has_flyer(e):
            actions.append(("wait_flyer", e))
        else:
            actions.append(("update" if m else "create", e))
    for lid, m in mapping.items():
        if lid.startswith("_") or m.get("status") == "cancelled" or lid in live_ids:
            continue
        if m.get("start") and parse(m["start"]) > now:
            actions.append(("cancel", lid))
    return actions


def crop_mask(png):
    """The centred 2:1 window Eventbrite shows, from the PNG's own size."""
    w, h = struct.unpack(">II", png[16:24])
    cw, ch = (w, w // 2) if h * 2 >= w else (h * 2, h)
    return {"top_left": {"x": (w - cw) // 2, "y": (h - ch) // 2}, "width": cw, "height": ch}


def event_body(f, acct, logo_id=None):
    ev = {"name": {"html": html.escape(f["title"], quote=False)}, "summary": f["summary"],
          "start": {"timezone": TZ_NAME, "utc": f["start"]}, "end": {"timezone": TZ_NAME, "utc": f["end"]},
          "currency": "USD", "online_event": f["online"], "listed": True, "shareable": True,
          "show_remaining": False, "organizer_id": acct["organizer_id"]}
    if not f["online"]:
        ev["venue_id"] = acct["venues"][f["venue"]]
    for key, val in (("category_id", CATEGORY_ID), ("subcategory_id", SUBCATEGORY_ID), ("format_id", FORMAT_ID)):
        if val:
            ev[key] = val
    if logo_id:
        ev["logo_id"] = logo_id
    return {"event": ev}


def ticket_body(f):
    return {"ticket_class": {"name": TICKET_NAME, "free": True, "capacity": CAPACITY, "quantity_total": CAPACITY,
                             "minimum_quantity": 1, "maximum_quantity": 10, "sales_end": f["end"]}}


def text_module(text_html):
    return {"type": "text", "data": {"body": {"type": "text", "text": text_html, "alignment": "left"}}}


def online_page_html(url):
    u = html.escape(url)
    return f'<p><a href="{u}">Join the Zoom meeting</a></p><p>{u}</p>'


# ---------- secrets ----------

def env(name):
    value = os.environ.get(name, "").strip()
    if not value and os.name == "nt":
        value = subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                                f"[Environment]::GetEnvironmentVariable('{name}','User')"],
                               capture_output=True, text=True).stdout.strip()
    if not value:
        raise Problem(f"{name} isn't set.")
    if value not in _SECRETS:
        _SECRETS.append(value)
    return value


_SECRETS = []


def safe(text):
    text = str(text)
    for s in _SECRETS:
        text = text.replace(s, "***")
    return ZOOM.sub("[zoom link]", EMAIL.sub("[email]", text))


def http(method, url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, method=method, headers={"user-agent": UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise Problem(safe(f"{method} {url.split('?')[0]} failed ({e.code}): {e.read().decode('utf-8', 'replace')[:400]}")) from None
    except urllib.error.URLError as e:
        raise Problem(safe(f"{method} {url.split('?')[0]} failed: {e.reason}")) from None


# ---------- Luma ----------

def luma(path):
    return http("GET", LUMA + path, headers={"x-luma-api-key": env("LUMA_API_KEY"), "accept": "application/json"})


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
    for e in out:  # description, address and the Zoom link (meeting_url) need the full event
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


# ---------- Eventbrite ----------

_WAYPOINT = []  # Eventbrite's consistency token from the last write, sent with the next request


def eb(method, path, body=None):
    headers = {"authorization": f"Bearer {env('EVENTBRITE_TOKEN')}", "accept": "application/json", "user-agent": UA}
    if body is not None:
        headers["content-type"] = "application/json"
    if _WAYPOINT:
        headers["eventbrite-api-waypoint-token"] = _WAYPOINT[-1]
    req = urllib.request.Request(API + path, method=method, headers=headers,
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            if r.headers.get("eventbrite-api-waypoint-token"):
                _WAYPOINT.append(r.headers["eventbrite-api-waypoint-token"])
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        try:
            j = json.loads(detail)
            detail = f"{j.get('error')}: {j.get('error_description')}"
            if j.get("error_detail"):
                detail += " " + json.dumps(j["error_detail"])[:300]
        except ValueError:
            detail = detail[:300]
        raise Problem(safe(f"Eventbrite {method} {path.split('?')[0]} failed ({e.code}): {detail}")) from None
    except urllib.error.URLError as e:
        raise Problem(safe(f"Eventbrite {method} {path.split('?')[0]} failed: {e.reason}")) from None


def account(events):
    """The organization and organizer ids, found once and kept in events.json (they aren't secret)."""
    acct = events.setdefault("_account", {})
    if not acct.get("organization_id"):
        orgs = eb("GET", "/users/me/organizations/").get("organizations", [])
        if not orgs:
            raise Problem("The Eventbrite account has no organization.")
        org = next((o for o in orgs if "spanish club" in (o.get("name") or "").lower()), orgs[0])
        acct["organization_id"] = org["id"]
    if not acct.get("organizer_id"):
        found = eb("GET", f"/organizations/{acct['organization_id']}/organizers/").get("organizers", [])
        if not found:
            raise Problem("The Eventbrite account has no organizer profile yet.")
        o = next((o for o in found if "spanish club" in (o.get("name") or "").lower()), found[0])
        acct["organizer_id"] = o["id"]
    acct.setdefault("venues", {})
    return acct


def ensure_venue(acct, key, place_id=None):
    if key == "online" or key in acct["venues"]:
        return
    v = VENUES[key]
    found = eb("GET", f"/organizations/{acct['organization_id']}/venues/").get("venues", [])
    match = next((x["id"] for x in found
                  if ((x.get("address") or {}).get("address_1") or "").lower() == v["address"]["address_1"].lower()), None)
    if not match:
        body = {"venue": dict(v, **({"google_place_id": place_id} if place_id else {}))}
        match = eb("POST", f"/organizations/{acct['organization_id']}/venues/", body)["id"]
    acct["venues"][key] = match


def multipart(fields, file_field, filename, data, ctype="image/png"):
    b = uuid.uuid4().hex
    parts = [f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields.items()]
    parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
                 f"Content-Type: {ctype}\r\n\r\n".encode() + data + b"\r\n")
    parts.append(f"--{b}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={b}"


def upload_logo(path):
    data = path.read_bytes()
    inst = eb("GET", "/media/upload/?type=image-event-logo")
    body, ctype = multipart(inst.get("upload_data") or {}, inst.get("file_parameter_name") or "file", path.name, data)
    req = urllib.request.Request(inst["upload_url"], data=body, method=inst.get("upload_method") or "POST",
                                 headers={"content-type": ctype, "user-agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            r.read()
    except urllib.error.HTTPError as e:
        raise Problem(f"The image upload failed ({e.code}).") from None
    got = eb("POST", "/media/upload/", {"upload_token": inst["upload_token"], "crop_mask": crop_mask(data)})
    return got["id"]


def set_content(eid, purpose, modules, access_type=None):
    """Save and publish a new version of the event's description ("listing") or Online Event Page."""
    cur = eb("GET", f"/events/{eid}/structured_content/edit/?purpose={purpose}")
    version = int(cur.get("page_version_number") or 0) + 1
    body = {"modules": modules, "publish": True, "purpose": purpose}
    if access_type:
        body["access_type"] = access_type
    eb("POST", f"/events/{eid}/structured_content/{version}/", body)


# ---------- the record and the log ----------

def load_events():
    return json.loads(EVENTS.read_text(encoding="utf-8")) if EVENTS.exists() else {}


def save_events(events, message=None):
    """Write events.json; with a message, also commit and push it when running on GitHub."""
    EVENTS.write_text(json.dumps(events, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    if not IN_ACTIONS or not message:
        return

    def git(*a):
        subprocess.run(["git", *a], cwd=REPO, check=True, capture_output=True, text=True)
    git("config", "user.name", "github-actions[bot]")
    git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    git("add", "eventbrite/events.json")
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
    raise Problem("Couldn't save eventbrite/events.json to GitHub.")


def notify(text):
    print("NOTICE:", text)
    if not IN_ACTIONS:
        return
    owner = os.environ.get("GITHUB_REPOSITORY_OWNER", "")
    api = f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}/issues"
    hdr = {"authorization": f"Bearer {os.environ['GITHUB_TOKEN']}", "accept": "application/vnd.github+json"}
    issues = http("GET", api + "?state=open&per_page=100", headers=hdr)
    issue = next((i for i in issues if i["title"] == LOG_ISSUE and "pull_request" not in i), None)
    if not issue:
        issue = http("POST", api, data=json.dumps({"title": LOG_ISSUE, "body": "The Eventbrite sync comments here when "
                     "it lists, changes or cancels events, or hits a problem."}).encode(), headers=hdr)
    http("POST", f"{api}/{issue['number']}/comments",
         data=json.dumps({"body": f"@{owner} {text}" if owner else text}).encode(), headers=hdr)


def nice(start_at):
    dt = parse(start_at).astimezone(TZ)
    return f"{dt:%a %b} {dt.day}, {dt:%I:%M %p}".replace(" 0", " ")


# ---------- doing it ----------

def apply(events, e, draft):
    """Create or update one Eventbrite event so it matches its Luma event, then publish it unless it's a draft."""
    acct = account(events)
    f = eventbrite_fields(e)
    ensure_venue(acct, f["venue"], (e.get("geo_address_json") or {}).get("google_maps_place_id"))
    m = events.get(e["id"])
    img = IMAGES / f["image"]
    digest = hashlib.sha256(img.read_bytes()).hexdigest()[:16]
    logo_id = upload_logo(img) if not m or m.get("image_sha") != digest else None
    if not m:
        got = eb("POST", f"/organizations/{acct['organization_id']}/events/", event_body(f, acct, logo_id))
        m = events[e["id"]] = {"eventbrite_id": got["id"], "eventbrite_url": got.get("url", ""), "status": "incomplete",
                               "draft": bool(draft), "made_by": "sync", "luma_url": e.get("url", ""),
                               "title": f["title"], "start": e["start_at"], "image_sha": digest}
        save_events(events)  # on disk at once, so a crash can't lead to a second copy
    else:
        eb("POST", f"/events/{m['eventbrite_id']}/", event_body(f, acct, logo_id))
        if logo_id:
            m["image_sha"] = digest
    eid = m["eventbrite_id"]
    if not m.get("ticket_class_id"):
        m["ticket_class_id"] = eb("POST", f"/events/{eid}/ticket_classes/", ticket_body(f))["id"]
        save_events(events)
    elif m.get("ticket_end") != f["end"]:
        eb("POST", f"/events/{eid}/ticket_classes/{m['ticket_class_id']}/", {"ticket_class": {"sales_end": f["end"]}})
    m["ticket_end"] = f["end"]
    set_content(eid, "listing", [text_module(f["description"])])
    if f["online"] and f["online_url"]:  # "private" = Eventbrite's "Who can access this page? Ticket holders only"
        set_content(eid, "digital_content", [text_module(online_page_html(f["online_url"]))], access_type="private")
    if not m.get("draft") and m.get("status") != "published":
        eb("POST", f"/events/{eid}/publish/")
    m.update(status="draft" if m.get("draft") else "published", luma_url=e.get("url", ""), title=f["title"],
             start=e["start_at"], fingerprint=fingerprint(f))
    return m


def cancel(events, lid):
    """Cancel an event that's gone from Luma. Returns a message, or None if Luma still has it."""
    if not luma_gone(lid):
        return None  # second read says it's still there: leave it
    m = events[lid]
    label = f"{m.get('title', lid)} ({nice(m['start'])})"
    try:
        eb("POST", f"/events/{m['eventbrite_id']}/cancel/")
    except Problem as err:
        if eb("GET", f"/events/{m['eventbrite_id']}/").get("status") not in ("canceled", "deleted"):
            raise Problem(f"{label} is gone from Luma, but Eventbrite wouldn't cancel it ({err}). If it has RSVPs, "
                          f"cancel it by hand on Eventbrite, which emails its guests: {m.get('eventbrite_url', '')}") from None
    m["status"] = "cancelled"
    return f"Cancelled on Eventbrite (gone from Luma): {label} {m.get('eventbrite_url', '')}"


def run(live, as_of=None, draft=False, only=None):
    now = datetime.fromisoformat(as_of).replace(tzinfo=TZ) if as_of else datetime.now(TZ)
    if live and not draft and not only and (LIVE_FROM is None or now.date() < LIVE_FROM):
        print("Not switched on yet (LIVE_FROM), so nothing was done.")
        return 0
    events = load_events()
    luma_events = luma_upcoming(now)
    if only:
        luma_events = [e for e in luma_events if e["id"] == only]
    actions = plan(luma_events, events, now, lambda e: (IMAGES / eventbrite_fields(e)["image"]).exists())
    if only:
        actions = [a for a in actions if a[0] != "cancel"]
    print(f"{'LIVE' if live else 'DRY RUN'}{' (drafts)' if draft else ''} at {now:%a %Y-%m-%d %H:%M} Eastern: "
          f"{len(luma_events)} upcoming Luma events, {len(actions)} actions")
    done, waits, problems = [], [], []
    for action, e in actions:
        label = e if isinstance(e, str) else f"{e['name']} ({nice(e['start_at'])})"
        print(f"  {action:10} {label}")
        if not live or action == "skip":
            continue
        if action == "wait_flyer":
            waits.append(f"{label} is waiting for its wide flyer (meetup/images/{eventbrite_fields(e)['image']}).")
            continue
        if action == "wait_place":
            waits.append(f"{label} is waiting: its Luma address doesn't match the venue in eventbrite/sync.py.")
            continue
        key = e if isinstance(e, str) else e["id"]
        try:
            if action == "cancel":
                msg = cancel(events, e)
                if msg:
                    done.append(msg)
                continue
            m = apply(events, e, draft)
            verb = "Listed" if action == "create" else "Updated"
            done.append(f"{verb}{' as an unpublished draft' if m['status'] == 'draft' else ''}: {label} {m['eventbrite_url']}")
        except Problem as err:
            problems.append(f"{label}: {err}")
        finally:
            save_events(events, f"Eventbrite: {action} {key}")
    text = "\n".join(done + ["Waiting: " + w for w in waits] + ["Problem: " + p for p in problems])
    if live and text:
        notify(safe(text))
    return 1 if problems else 0


def publish(live):
    events = load_events()
    drafts = [(k, m) for k, m in events.items() if not k.startswith("_") and m.get("status") == "draft"]
    for k, m in drafts:
        print(f"  {'publishing' if live else 'would publish'}: {m.get('title')} ({nice(m['start'])}) {m.get('eventbrite_url', '')}")
        if live:
            eb("POST", f"/events/{m['eventbrite_id']}/publish/")
            m.update(status="published", draft=False)
            save_events(events, f"Eventbrite: published {k}")
    if live and drafts:
        notify("Published on Eventbrite: " + "; ".join(f"{m.get('title')} ({nice(m['start'])}) {m.get('eventbrite_url', '')}"
                                                      for _, m in drafts))
    print(f"{len(drafts)} drafts")


def check():
    acct = account({})
    org = eb("GET", f"/organizations/{acct['organization_id']}/organizers/").get("organizers", [])
    name = next((o.get("name") for o in org if o["id"] == acct["organizer_id"]), "?")
    venues = eb("GET", f"/organizations/{acct['organization_id']}/venues/").get("venues", [])
    print(f"Eventbrite token OK. Organization {acct['organization_id']}; organizer \"{name}\" ({acct['organizer_id']}); "
          f"{len(venues)} venues.")


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
    sub.add_parser("check")
    a = ap.parse_args()
    try:
        if a.cmd == "run":
            return run(a.live, a.as_of, a.draft, a.only)
        if a.cmd == "publish":
            publish(a.live)
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
