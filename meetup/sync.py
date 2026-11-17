#!/usr/bin/env python3
"""Spanish Club for Socialists: keep the Meetup group in step with Luma.

Every day (see .github/workflows/meetup.yml) this script:
1. lists each Luma event on Meetup once it's within WEEKS_AHEAD weeks: Luma's exact title, date, time and
   place, Luma's description ending with a line back to Luma, and the event's wide flyer;
2. updates a Meetup event when its Luma event changes (even past the window), and cancels it if the
   Luma event is gone.
Luma is the source of truth. Online events get the Zoom link only in Meetup's attendee-only link
field, never in the description. Only the Python standard library is used.

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
- Only events recorded in meetup/events.json are ever edited or cancelled.
- A Meetup event is cancelled (never deleted) only if Luma no longer has it, checked twice.
- An event whose wide flyer isn't in meetup/images/ yet waits; it never goes up without its photo.
- An in-person event whose Luma address doesn't match its venue below waits, so nothing is listed at the wrong place.
- After every change it reads the event back from Meetup and reports any difference from Luma.
- The repository is public: no keys, Zoom link, names or emails are printed or saved.
"""
import argparse
import base64
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# ---------- settings ----------
GROUP = "spanishclubforsocialists"  # the group's address; was spanish-club-for-socialists until Nov 13, 2026
WEEKS_AHEAD = 5
LIVE_FROM = date(2026, 11, 17)  # the daily run changes Meetup from this date (organizer's OK, Nov 17). None = off.
MEMBER_ID = "4255800"   # Timothy Ryan, the group's organizer, who owns the API client. Not secret.
FOLLOW = "Get weekly reminders and see every event: follow us on Luma, https://luma.com/spanishclubforsocialists"
FOLLOW_MD = FOLLOW.replace("https://luma.com/spanishclubforsocialists",
                           "[https://luma.com/spanishclubforsocialists](https://luma.com/spanishclubforsocialists)")

KINDS = {  # Luma name contains -> (kind, image slug, venue key, "how to find us")
    "St. Stephen": ("St. Stephen's", "st-stephens", "st-stephens", "Please come in the side door!"),
    "Online": ("Online", "online", "online", None),
    "Liberation Center": ("Liberation Center", "liberation-center", "liberation-center", None),
    "Dupont": ("Dupont", "dupont", "dupont", None),
}
VENUES = {  # Meetup venue ids are kept in venues.json; a missing one is found or created the first time
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
SECRET_HINT = re.compile(r"pwd=|passcode", re.I)


class Problem(Exception):
    """Something went wrong; the message is safe to show."""


# ---------- pure logic (tested) ----------

def parse(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def when(ts):
    """A recorded start time; the hand-made records of Nov 10 have Eastern time without an offset."""
    dt = parse(ts)
    return dt if dt.tzinfo else dt.replace(tzinfo=TZ)


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


def description(md):
    """Luma's markdown for Meetup, without any Zoom link or passcode, ending with the line back to Luma.
    Meetup stores descriptions as markdown: sent as HTML, its paragraphs run together and links turn into
    plain "text (url)" (tested on a draft, Nov 17, 2026), so the markdown goes as it is."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", md or "") if p.strip()]
    paras = [p for p in paras if not ZOOM.search(p) and not SECRET_HINT.search(p)]
    lines = lambda p: "\n".join(ln.strip() for ln in p.split("\n") if ln.strip())  # noqa: E731
    return "\n\n".join(lines(p) for p in paras + [FOLLOW_MD])


def place_ok(event):
    """In-person events must be at the address the venue table expects (Luma decides; this only checks)."""
    venue = _kind(event)[2]
    if venue == "online":
        return True
    full = ((event.get("geo_address_json") or {}).get("full_address") or "").lower()
    return VENUES[venue]["address"].lower() in full


def meetup_fields(event):
    kind, slug, venue, how = _kind(event)
    online = venue == "online"
    return {"title": event["name"][:80], "start": start_local(event["start_at"]), "duration": duration_iso(event),
            "venue": venue, "how_to_find_us": how, "online_url": event.get("meeting_url") if online else None,
            "description": description(event.get("description_md", "")),
            "image": f"{start_local(event['start_at'])[:10]}-{slug}.png"}


def fingerprint(fields):
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()[:16]


def plan(luma_events, mapping, now, has_flyer, force=False):
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
        if (m and not force and m.get("status") != "incomplete"
                and m.get("fingerprint") == fingerprint(meetup_fields(e))):
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
        if m.get("start") and when(m["start"]) > now:
            actions.append(("cancel", lid))
    return actions


def differences(got, e, f, venue_id):
    """What Meetup shows that doesn't match Luma, as safe text (never the Zoom link itself)."""
    out = []
    if (got.get("title") or "") != f["title"]:
        out.append("the title")
    start, end = parse(e["start_at"]), parse(e["end_at"])
    if not got.get("dateTime") or parse(got["dateTime"]) != start:
        out.append("the start time")
    if not got.get("endTime") or parse(got["endTime"]) != end:
        out.append("the end time")
    if f["venue"] == "online":
        if (got.get("eventType") or "").upper() != "ONLINE":
            out.append("online, not in person")
        if f["online_url"] and (got.get("howToFindUs") or "") != f["online_url"]:
            out.append("the attendee-only Zoom link")
    else:
        if str((got.get("venue") or {}).get("id")) != str(venue_id):
            out.append("the venue")
        if (got.get("howToFindUs") or "") != (f["how_to_find_us"] or ""):
            out.append("\"how to find us\"")
    if ZOOM.search(got.get("description") or "") or SECRET_HINT.search(got.get("description") or ""):
        out.append("a Zoom link in the public description")
    return out


# ---------- RS256 signing for Meetup's JWT flow (standard library only) ----------

_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")  # DER prefix for a SHA-256 digest


def _der(buf, i):
    """One DER element at i: (tag, contents, next index)."""
    tag, n = buf[i], buf[i + 1]
    i += 2
    if n & 0x80:
        k = n & 0x7F
        n = int.from_bytes(buf[i:i + k], "big")
        i += k
    return tag, buf[i:i + n], i + n


def rsa_key(pem):
    """(n, e, d) from a PEM RSA private key (PKCS#1 or PKCS#8), however its line breaks were mangled."""
    text = pem.replace("\\r", "").replace("\\n", "\n")
    m = re.search(r"-----BEGIN ((?:RSA )?PRIVATE KEY)-----(.*?)-----END \1-----", text, re.S)
    if not m:
        raise Problem("MEETUP_PRIVATE_KEY isn't a PEM private key.")
    try:
        der = base64.b64decode(re.sub(r"[^A-Za-z0-9+/=]", "", m[2]))
        _, seq, _ = _der(der, 0)
        if m[1] == "PRIVATE KEY":  # PKCS#8: version, algorithm, then the PKCS#1 key inside an OCTET STRING
            _, _, i = _der(seq, 0)
            _, _, i = _der(seq, i)
            _, inner, _ = _der(seq, i)
            _, seq, _ = _der(inner, 0)
        ints, i = [], 0
        while len(ints) < 4:
            _, val, i = _der(seq, i)
            ints.append(int.from_bytes(val, "big"))
    except (ValueError, IndexError):
        raise Problem("MEETUP_PRIVATE_KEY couldn't be read. Is the whole PEM key saved?") from None
    return ints[1], ints[2], ints[3]


def rs256(message, key):
    """RSASSA-PKCS1-v1_5 with SHA-256, checked with the public half before it's used."""
    n, e, d = key
    k = (n.bit_length() + 7) // 8
    t = _DIGEST_INFO + hashlib.sha256(message).digest()
    em = int.from_bytes(b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t, "big")
    sig = pow(em, d, n)
    if pow(sig, e, n) != em:
        raise Problem("MEETUP_PRIVATE_KEY doesn't sign correctly. Is the whole PEM key saved?")
    return sig.to_bytes(k, "big")


def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def jwt(client, kid, member, key, now):
    head = b64(json.dumps({"alg": "RS256", "typ": "JWT", "kid": kid}, separators=(",", ":")).encode())
    body = b64(json.dumps({"sub": member, "iss": client, "aud": "api.meetup.com", "exp": int(now) + 120},
                          separators=(",", ":")).encode())
    return f"{head}.{body}.{b64(rs256(f'{head}.{body}'.encode(), key))}"


# ---------- secrets ----------

_SECRETS = []


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


# ---------- Meetup ----------

_TOKEN = []


def token():
    """JWT flow: sign an assertion with the private key and swap it for an access token (kept in memory only)."""
    if _TOKEN:
        return _TOKEN[0]
    client, kid = env("MEETUP_CLIENT_KEY"), env("MEETUP_SIGNING_KEY_ID")
    key = rsa_key(env("MEETUP_PRIVATE_KEY"))
    form = urllib.parse.urlencode({"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                   "assertion": jwt(client, kid, MEMBER_ID, key, time.time())}).encode()
    got = http("POST", TOKEN_URL, data=form, headers={"content-type": "application/x-www-form-urlencoded"})
    if not got.get("access_token"):
        raise Problem("Meetup didn't return an access token.")
    _SECRETS.append(got["access_token"])
    _TOKEN.append(got["access_token"])
    return _TOKEN[0]


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


_GROUP_ID = []


def group_id():
    if not _GROUP_ID:
        _GROUP_ID.append(gql("query($u:String!){ groupByUrlname(urlname:$u){ id } }", {"u": GROUP})["groupByUrlname"]["id"])
    return _GROUP_ID[0]


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
        VENUE_IDS.write_text(json.dumps(ids, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return match or f"<new venue: {v['name']}>"


def jpeg(path):
    """The flyer as JPEG bytes. Meetup serves event photos only as .jpeg, and a PNG uploaded through the API
    is stored as .png and never shows up (tested Nov 17, 2026). Needs Pillow (pip install pillow)."""
    from io import BytesIO
    from PIL import Image
    out = BytesIO()
    Image.open(path).convert("RGB").save(out, "JPEG", quality=92, subsampling=0)  # 4:4:4 keeps the text crisp
    return out.getvalue()


def upload_photo(path, wait=150):
    """Upload a flyer the way Meetup's own event editor uploads a cover photo (ALBUM_PHOTO; never as the
    group's main photo), then wait until Meetup serves it. Returns the photo id."""
    data = jpeg(path)
    p = payload_ok(gql("mutation($i:GroupEventPhotoCreateInput!){ createGroupEventPhoto(input:$i){ uploadUrl "
                       "photo{ id highResUrl } error{ code field message } } }",
                       {"i": {"groupId": group_id(), "photoType": "ALBUM_PHOTO", "contentType": "JPEG",
                              "setAsMain": False}})["createGroupEventPhoto"], "the photo")
    http("PUT", p["uploadUrl"], data=data, headers={"content-type": "image/jpeg"}, raw=True)
    url, end = p["photo"].get("highResUrl"), time.time() + wait
    while url:
        try:
            with urllib.request.urlopen(urllib.request.Request(url, method="HEAD", headers={"user-agent": UA}), timeout=30):
                return int(p["photo"]["id"])
        except (urllib.error.HTTPError, urllib.error.URLError):
            if time.time() > end:
                break
            time.sleep(10)
    raise Problem(f"Meetup hasn't processed the flyer {path.name} yet, so the event waits for the next run.")


def event_input(f, vid):
    i = {"title": f["title"], "description": f["description"], "startDateTime": f["start"],
         "duration": f["duration"], "venueId": vid}
    if f["online_url"] or f["how_to_find_us"]:
        i["howToFindUs"] = f["online_url"] or f["how_to_find_us"]
    return i


def read_back(eid):
    return gql("query($id:ID!){ event(id:$id){ id title dateTime endTime eventType status howToFindUs description "
               "eventUrl venue{ id } featuredEventPhoto{ id } } }", {"id": eid})["event"]


# ---------- the record and the log ----------

def load_events():
    return json.loads(EVENTS.read_text(encoding="utf-8")) if EVENTS.exists() else {}


def save_events(events, message=None):
    """Write events.json; with a message, also commit and push it (and venues.json) when running on GitHub."""
    EVENTS.write_text(json.dumps(events, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    if not IN_ACTIONS or not message:
        return

    def git(*a):
        subprocess.run(["git", *a], cwd=REPO, check=True, capture_output=True, text=True)
    git("config", "user.name", "github-actions[bot]")
    git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    git("add", "meetup/events.json", *(["meetup/venues.json"] if VENUE_IDS.exists() else []))
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
    owner = os.environ.get("GITHUB_REPOSITORY_OWNER", "")
    api = f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}/issues"
    hdr = {"authorization": f"Bearer {os.environ['GITHUB_TOKEN']}", "accept": "application/vnd.github+json"}
    issues = http("GET", api + "?state=open&per_page=100", headers=hdr)
    issue = next((i for i in issues if i["title"] == LOG_ISSUE and "pull_request" not in i), None)
    if not issue:
        issue = http("POST", api, data=json.dumps({"title": LOG_ISSUE, "body": "The Meetup sync comments here when it "
                     "lists, changes or cancels events, or hits a problem."}).encode(), headers=hdr)
    http("POST", f"{api}/{issue['number']}/comments",
         data=json.dumps({"body": f"@{owner} {text}" if owner else text}).encode(), headers=hdr)


def nice(start_at):
    dt = when(start_at).astimezone(TZ)
    return f"{dt:%a %b} {dt.day}, {dt:%I:%M %p}".replace(" 0", " ")


# ---------- doing it ----------

def apply(events, e, draft):
    """Create or update one Meetup event so it matches its Luma event. Returns its record."""
    f = meetup_fields(e)
    vid = venue_id(f["venue"], True)
    i = event_input(f, vid)
    img = IMAGES / f["image"]
    digest = hashlib.sha256(img.read_bytes()).hexdigest()[:16]
    m = events.get(e["id"])
    if not m:  # a new event goes up as a draft first, so it's never public without its flyer
        p = payload_ok(gql("mutation($i:CreateEventInput!){ createEvent(input:$i){ event{ id eventUrl } "
                           "errors{ code field message } } }",
                           {"i": dict(i, groupUrlname=GROUP, publishStatus="DRAFT")})["createEvent"], "the event")
        m = events[e["id"]] = {"meetup_event_id": p["event"]["id"], "meetup_url": p["event"]["eventUrl"],
                               "status": "incomplete", "draft": bool(draft), "made_by": "sync",
                               "luma_url": e.get("url", ""), "title": f["title"], "start": e["start_at"]}
        save_events(events)  # on disk at once, so a crash can't lead to a second copy
    eid = m["meetup_event_id"]
    if m.get("image_sha") != digest:
        i["featuredPhotoId"] = upload_photo(img)
    payload_ok(gql("mutation($i:EditEventInput!){ editEvent(input:$i){ event{ id } errors{ code field message } } }",
                   {"i": dict(i, eventId=eid)})["editEvent"], "the change")
    if "featuredPhotoId" in i:
        m["image_sha"] = digest
    if not m.get("draft") and read_back(eid).get("status") == "DRAFT":
        payload_ok(gql("mutation($i:PublishEventDraftInput!){ publishEventDraft(input:$i){ event{ id } "
                       "errors{ code field message } } }", {"i": {"eventId": eid}})["publishEventDraft"], "publishing")
    got = read_back(m["meetup_event_id"])
    diff = differences(got, e, f, vid)
    if diff:
        m["status"] = "incomplete"  # tried again on the next run
        raise Problem(f"Meetup doesn't match Luma after saving: {', '.join(diff)}. {m.get('meetup_url', '')}")
    m.update(status="draft" if got.get("status") == "DRAFT" else "published", luma_url=e.get("url", ""),
             title=f["title"], start=e["start_at"], fingerprint=fingerprint(f), photo=True)
    return m


def cancel(events, lid):
    """Cancel an event that's gone from Luma. Returns a message, or None if Luma still has it."""
    if not luma_gone(lid):
        return None  # second read says it's still there: leave it
    m = events[lid]
    payload_ok(gql("mutation($i:DeleteEventInput!){ deleteEvent(input:$i){ success errors{ code field message } } }",
                   {"i": {"eventId": m["meetup_event_id"], "removeFromCalendar": False}})["deleteEvent"], "the cancel")
    m["status"] = "cancelled"
    return f"Cancelled on Meetup (gone from Luma): {m.get('title', lid)} ({nice(m['start'])}) {m.get('meetup_url', '')}"


def run(live, as_of=None, draft=False, only=None, force=False):
    now = datetime.fromisoformat(as_of).replace(tzinfo=TZ) if as_of else datetime.now(TZ)
    if live and not draft and not only and (LIVE_FROM is None or now.date() < LIVE_FROM):
        print("Not switched on yet (LIVE_FROM), so nothing was done.")
        return 0
    events = load_events()
    luma_events = luma_upcoming(now)
    if only:
        luma_events = [e for e in luma_events if e["id"] == only]
    actions = plan(luma_events, events, now, lambda e: (IMAGES / meetup_fields(e)["image"]).exists(), force)
    if only:
        actions = [a for a in actions if a[0] != "cancel"]
    print(f"{'LIVE' if live else 'DRY RUN'}{' (drafts)' if draft else ''} at {now:%a %Y-%m-%d %H:%M} Eastern: "
          f"{len(luma_events)} upcoming Luma events, {len(actions)} actions")
    done, waits, problems = [], [], []
    for action, e in actions:
        label = e if isinstance(e, str) else f"{e['name']} ({nice(e['start_at'])})"
        if isinstance(e, str) and e in events:
            label = f"{events[e].get('title', e)} ({nice(events[e]['start'])})"
        print(f"  {action:10} {label}")
        if not live or action == "skip":
            continue
        if action == "wait_flyer":
            waits.append(f"{label} is waiting for its wide flyer (meetup/images/{meetup_fields(e)['image']}).")
            continue
        if action == "wait_place":
            waits.append(f"{label} is waiting: its Luma address doesn't match the venue in meetup/sync.py.")
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
            done.append(f"{verb}{' as a draft' if m['status'] == 'draft' else ''}: {label} {m.get('meetup_url', '')}")
        except Problem as err:
            problems.append(f"{label}: {err}")
        finally:
            save_events(events, f"Meetup: {action} {key}")
    text = "\n".join(done + ["Waiting: " + w for w in waits] + ["Problem: " + p for p in problems])
    if live and text:
        notify(safe(text))
    return 1 if problems else 0


def publish(live):
    events = load_events()
    drafts = [(k, m) for k, m in events.items() if not k.startswith("_") and m.get("status") == "draft"]
    for k, m in drafts:
        print(f"  {'publishing' if live else 'would publish'}: {m.get('title')} ({nice(m['start'])}) {m.get('meetup_url', '')}")
        if live:
            payload_ok(gql("mutation($i:PublishEventDraftInput!){ publishEventDraft(input:$i){ event{ id } "
                           "errors{ code field message } } }", {"i": {"eventId": m["meetup_event_id"]}})["publishEventDraft"],
                       "publishing")
            m.update(status="published", draft=False)
            save_events(events, f"Meetup: published {k}")
    if live and drafts:
        notify("Published on Meetup: " + "; ".join(f"{m.get('title')} ({nice(m['start'])}) {m.get('meetup_url', '')}"
                                                   for _, m in drafts))
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
    gid = group_id()
    me = gql("query{ self{ id } }")["self"]["id"]
    print(f"Meetup keys OK. Group {GROUP} ({gid}); signed in as member {me}.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--live", action="store_true")
    r.add_argument("--draft", action="store_true")
    r.add_argument("--as-of")
    r.add_argument("--only")
    r.add_argument("--force", action="store_true", help="re-save listed events even if Luma hasn't changed")
    p = sub.add_parser("publish")
    p.add_argument("--live", action="store_true")
    sub.add_parser("collect")
    sub.add_parser("check")
    a = ap.parse_args()
    try:
        if a.cmd == "run":
            return run(a.live, a.as_of, a.draft, a.only, a.force)
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
