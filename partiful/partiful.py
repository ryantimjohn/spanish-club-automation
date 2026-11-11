"""Partiful listings for Spanish Club for Socialists, kept in step with Luma.

Partiful has no public API, so a browser agent (Claude in Chrome, in the organizer's
signed-in Chrome) makes the listings. This script does everything that doesn't need
the browser: it reads Luma, decides what to list, builds each listing's text, and
keeps the record in events.json (Luma event id -> Partiful URL).

Usage (run with: python -I -X utf8 partiful.py ...):
  plan                       what to do now: create / update / cancel, within the next 5 weeks
  show LUMA_ID               every field for one listing (Zoom link never shown)
  copy-desc LUMA_ID          put the listing's description on the clipboard, for pasting
  copy-zoom                  put the Online Zoom link on the clipboard (never printed)
  clear-clip                 empty the clipboard
  record LUMA_ID URL STATUS  save a listing in events.json. STATUS: creating (about to save it on Partiful),
                             private (saved, link-only, waiting for the organizer's OK), published (live:
                             Public for in-person nights, Private for Online), cancelled
  list                       show events.json

LUMA_API_KEY and ZOOM_PWD are read from the environment or the Windows user env vars.
They are never printed.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# ---------- settings ----------
WEEKS_AHEAD = 5
# Online nights: "skip" (not listed; the organizer's decision on Nov 10, 2026), or "private" (link-only
# event; the Zoom link would go in Partiful's Location, which Partiful hides before RSVP only on private events).
ONLINE = "skip"
FOLLOW = "Get weekly reminders and see every event: follow us on Luma, https://luma.com/spanishclubforsocialists"
ZOOM_MEETING = "https://us02web.zoom.us/j/83064162001?pwd="

FLYERS = Path(r"C:\Users\John\Documents\Spanish Club Flyers\flyers")
KINDS = {  # Luma name contains -> kind, flyer folder, what to search in Partiful's Location, the location's
           # display name, the "Based on your poster" theme swatch to pick, and a line before the description
    "St. Stephen": {"kind": "St. Stephen's", "folder": "St. Stephen's",
                    "location": "St. Stephen and the Incarnation Episcopal Church", "display_name": "St. Stephen's",
                    "swatch": "navy", "first_line": "Please come in the side door!"},
    "Online": {"kind": "Online", "folder": "Online", "location": None, "display_name": "Zoom", "swatch": "green",
               "first_line": None},
    "Liberation Center": {"kind": "Liberation Center", "folder": "Liberation Center",
                          "location": "337 H St NE, Washington, DC", "display_name": "DC Liberation Center",
                          "swatch": "dark red", "first_line": None},
    "Dupont": {"kind": "Dupont", "folder": "Dupont", "location": "1901 18th St NW, Washington, DC 20009",
               "display_name": "Patchwork", "swatch": "plum", "first_line": None},
}

HERE = Path(__file__).resolve().parent
EVENTS = HERE / "events.json"
TZ = ZoneInfo("America/New_York")
LUMA = "https://public-api.luma.com"
ZOOM = re.compile(r"https?://[\w.-]*zoom\.us/\S*")
MD_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


class Problem(Exception):
    """Something went wrong; the message is safe to show."""


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


def safe(text):
    return ZOOM.sub("[zoom link]", str(text))


# ---------- Luma ----------

def luma(path):
    req = urllib.request.Request(LUMA + path, headers={"x-luma-api-key": env("LUMA_API_KEY"),
                                                       "accept": "application/json", "user-agent": "curl/8.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise Problem(safe(f"Luma {path.split('?')[0]} failed ({e.code}): {e.read().decode('utf-8', 'replace')[:300]}")) from None
    except urllib.error.URLError as e:
        raise Problem(f"Luma {path.split('?')[0]} failed: {e.reason}") from None


def luma_window(now):
    """Every Luma event starting in the next WEEKS_AHEAD weeks, with full details."""
    after = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    before = (now + timedelta(weeks=WEEKS_AHEAD)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out, cursor = [], None
    while True:
        page = luma(f"/v1/calendars/events/list?after={after}&before={before}&sort_direction=asc&pagination_limit=100"
                    + (f"&pagination_cursor={urllib.parse.quote(cursor)}" if cursor else ""))
        out += [e.get("event", e) for e in page.get("entries", [])]
        if not page.get("has_more"):
            break
        cursor = page["next_cursor"]
    return [luma_event(e["id"]) for e in out]


def luma_event(event_id):
    got = luma(f"/v1/events/get?event_id={event_id}")
    return got.get("event", got)


def luma_gone(event_id):
    try:
        ev = luma_event(event_id)
    except Problem as e:
        if "(404)" in str(e):
            return True
        raise
    return not ev or ev.get("status") in ("cancelled", "deleted")


# ---------- listing fields ----------

def parse(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def kind_info(event):
    for key, val in KINDS.items():
        if key in (event.get("name") or ""):
            return val
    return None


def _bare(url):
    return re.sub(r"^https?://(www\.)?", "", url).rstrip("/")


def plain_description(event):
    """Luma's text as Partiful's plain text: no Zoom link, links written out, the Luma line at the end."""
    info = kind_info(event) or {}
    paras = [p.strip() for p in re.split(r"\n\s*\n", event.get("description_md") or "") if p.strip()]
    out = [info["first_line"]] if info.get("first_line") else []
    for p in paras:
        if ZOOM.search(p):
            continue  # the Zoom link never goes in a description
        p = p.replace("  \n", "\n").replace("\\", "")
        p = MD_LINK.sub(lambda m: m[2] if _bare(m[1]) == _bare(m[2]) else f"{m[1]}: {m[2]}", p)
        out.append(p)
    out.append(FOLLOW)
    return "\n\n".join(out)


def flyer(event):
    info = kind_info(event)
    if not info:
        return None
    day = parse(event["start_at"]).astimezone(TZ).strftime("%Y-%m-%d")
    hits = sorted((FLYERS / info["folder"]).glob(f"{day} *– square.png"))
    return str(hits[0]) if hits else None


def fields(event):
    info = kind_info(event) or {}
    start, end = parse(event["start_at"]).astimezone(TZ), parse(event["end_at"]).astimezone(TZ)
    online = info.get("kind") == "Online"
    return {
        "luma_id": event["id"],
        "title": event["name"],
        "kind": info.get("kind"),
        "date": start.strftime("%A, %B %d, %Y").replace(" 0", " "),
        "start_time": start.strftime("%I:%M %p").lstrip("0"),
        "end_time": end.strftime("%I:%M %p").lstrip("0"),
        "start": start.strftime("%Y-%m-%dT%H:%M"),
        "end": end.strftime("%Y-%m-%dT%H:%M"),
        "audience": "Private" if online else "Public",
        "location": "ZOOM LINK FROM CLIPBOARD (copy-zoom)" if online else info.get("location"),
        "location_display_name": info.get("display_name"),
        "theme_swatch": info.get("swatch"),
        "image": flyer(event),
        "description": plain_description(event),
        "luma_url": event.get("url"),
    }


def fingerprint(f):
    keep = {k: f[k] for k in ("title", "start", "end", "location", "description", "audience")}
    keep["image"] = Path(f["image"]).name if f.get("image") else None
    return hashlib.sha256(json.dumps(keep, sort_keys=True).encode()).hexdigest()[:16]


# ---------- record ----------

def load():
    return json.loads(EVENTS.read_text(encoding="utf-8")) if EVENTS.exists() else {}


def save(events):
    EVENTS.write_text(json.dumps(events, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


# ---------- plan ----------

def plan(now=None):
    now = now or datetime.now(timezone.utc)
    events, record = luma_window(now), load()
    rows, live = [], set()
    for e in events:
        live.add(e["id"])
        f, m = fields(e), record.get(e["id"])
        when = f"{f['date']}, {f['start_time']}–{f['end_time']}"
        if not f["kind"]:
            rows.append(("ask", e["id"], f"{when}: {f['title']} (unknown kind of event; ask the organizer)"))
        elif f["kind"] == "Online" and ONLINE == "skip":
            rows.append(("skip", e["id"], f"{when}: {f['title']} (Online isn't listed on Partiful)"))
        elif not m:
            rows.append(("create" if f["image"] else "no_flyer", e["id"], f"{when}: {f['title']}"))
        elif m.get("status") == "private" and f["kind"] != "Online":
            rows.append(("waiting", e["id"], f"{when}: {f['title']} -> {m.get('partiful_url')} (private, waiting for the organizer's OK to go Public)"))
        elif m.get("status") == "creating":
            rows.append(("check", e["id"], f"{when}: {f['title']} (marked 'creating': look on Partiful before making it again)"))
        elif m.get("status") == "cancelled":
            rows.append(("cancelled", e["id"], f"{when}: {f['title']} (cancelled on Partiful; back on Luma, ask the organizer)"))
        elif m.get("fingerprint") != fingerprint(f):
            changed = [k for k in ("title", "start", "end", "location", "description", "audience", "image")
                       if k in m.get("fields", {}) and m["fields"][k] != (Path(f[k]).name if k == "image" and f[k] else f[k])]
            rows.append(("update", e["id"], f"{when}: {f['title']} -> {m.get('partiful_url')} (changed: {', '.join(changed) or 'details'})"))
        else:
            rows.append(("ok", e["id"], f"{when}: {f['title']} -> {m.get('partiful_url')}"))
    for lid, m in record.items():
        if lid.startswith("_") or lid in live or m.get("status") in ("cancelled", None):
            continue
        if datetime.fromisoformat(m["start"]).replace(tzinfo=TZ) > now:
            if luma_gone(lid):
                rows.append(("cancel", lid, f"{m['start']}: {m['title']} -> {m.get('partiful_url')} (gone from Luma)"))
            else:
                ev = luma_event(lid)
                rows.append(("update", lid, f"{m['title']} -> {m.get('partiful_url')} (moved on Luma to {fields(ev)['date']})"))
    return rows


# ---------- clipboard ----------

def clip(text):
    """Put text on the Windows clipboard through stdin, so it never shows in a command line or a file."""
    subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                    "[Console]::InputEncoding = [Text.Encoding]::UTF8; Set-Clipboard -Value ([Console]::In.ReadToEnd())"],
                   input=text.encode("utf-8"), check=True, capture_output=True)


def clip_matches(text):
    got = subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                          "[Console]::OutputEncoding = [Text.Encoding]::UTF8; Get-Clipboard -Raw"],
                         capture_output=True).stdout.decode("utf-8").replace("\r\n", "\n").strip()
    return got == text.strip()


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, args = argv[0], argv[1:]
    if cmd == "plan":
        rows = plan()
        for action, lid, text in rows:
            print(f"{action:9} {lid}  {text}")
        if not rows:
            print("Nothing on Luma in the next 5 weeks.")
    elif cmd == "show":
        f = fields(luma_event(args[0]))
        f["fingerprint"] = fingerprint(f)
        print(json.dumps(f, indent=1, ensure_ascii=False))
    elif cmd == "copy-desc":
        text = fields(luma_event(args[0]))["description"]
        clip(text)
        if not clip_matches(text):
            raise Problem("The clipboard doesn't match the description (encoding?). Don't paste it.")
        print("Description copied to the clipboard (checked).")
    elif cmd == "copy-zoom":
        clip(ZOOM_MEETING + env("ZOOM_PWD"))
        print("Zoom link copied to the clipboard (not shown). Run clear-clip after pasting.")
    elif cmd == "clear-clip":
        subprocess.run(["powershell.exe", "-NoProfile", "-Command", "Set-Clipboard -Value ' '"], check=True, capture_output=True)
        print("Clipboard cleared.")
    elif cmd == "record":
        lid, url, status = args[0], args[1], args[2]
        if status not in ("creating", "private", "published", "cancelled"):
            raise Problem("STATUS must be creating, private, published or cancelled.")
        record = load()
        m = record.get(lid, {})
        if status == "cancelled":
            m.update(status="cancelled", cancelled=datetime.now(TZ).strftime("%Y-%m-%d"))
        else:
            f = fields(luma_event(lid))
            m.update(title=f["title"], start=f["start"], luma_url=f["luma_url"], status=status,
                     fingerprint=fingerprint(f), updated=datetime.now(TZ).strftime("%Y-%m-%d %H:%M"),
                     fields={k: (Path(f[k]).name if k == "image" and f[k] else f[k])
                             for k in ("title", "start", "end", "location", "description", "audience", "image")})
            m.setdefault("listed", datetime.now(TZ).strftime("%Y-%m-%d"))
        if url != "-":
            m["partiful_url"] = url
        record[lid] = m
        save(record)
        print(f"Recorded {lid}: {status} {m.get('partiful_url', '')}")
    elif cmd == "list":
        for lid, m in sorted(load().items(), key=lambda kv: kv[1].get("start", "")):
            if not lid.startswith("_"):
                print(f"{m.get('start')}  {m.get('status'):9}  {m.get('partiful_url')}  {m.get('title')}  ({lid})")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Problem as e:
        print(f"PROBLEM: {e}")
        sys.exit(1)
