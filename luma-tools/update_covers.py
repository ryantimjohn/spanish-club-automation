"""Set each upcoming Luma event's cover image to its dated square flyer.

Usage:
  python -I -X utf8 update_covers.py                 dry run: shows each event and the flyer it would get
  python -I -X utf8 update_covers.py --only EVENT_ID  uploads and sets one event's cover (the sample)
  python -I -X utf8 update_covers.py --apply          does every matched event
  add --skip EVENT_ID (repeatable) to leave an event out

The flyer is flyers\\<folder>\\YYYY-MM-DD Spanish Club for Socialists <name> – square.png, with the date
taken from Luma (Eastern time). Events with no matching flyer are listed and left alone. Uploads are
recorded in covers_done.json (event id -> the flyer's SHA-256 and the Luma image URL), so running it
again only re-uploads a flyer that has changed. Edits go out with suppress_email, so guests get no emails.
"""
import hashlib
import importlib.util
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
# The club folder's "flyers" directory: the nearest one above this script (the repo sits inside the club folder).
FLYERS = next((p / "flyers" for p in HERE.parents if (p / "flyers").is_dir()), HERE.parent / "flyers")
DONE = HERE / "covers_done.json"
ET = ZoneInfo("America/New_York")

spec = importlib.util.spec_from_file_location("ce", HERE / "create_events.py")
ce = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ce)

# Luma event name -> flyer folder and the name used in the flyer's file name.
KINDS = [("St. Stephen", "St. Stephen's"), ("Online", "Online"),
         ("Liberation Center", "Liberation Center"), ("Dupont", "Dupont")]


def upcoming():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out, cursor = [], None
    while True:
        q = f"/v1/calendars/events/list?after={now}&pagination_limit=100&sort_direction=asc"
        if cursor:
            q += f"&pagination_cursor={cursor}"
        page = ce.call("GET", q)
        out += [e.get("event", e) for e in page["entries"]]
        if not page.get("has_more"):
            return out
        cursor = page["next_cursor"]


def flyer_for(ev):
    day = datetime.fromisoformat(ev["start_at"].replace("Z", "+00:00")).astimezone(ET).date()
    for key, folder in KINDS:
        if key in ev["name"]:
            p = FLYERS / folder / f"{day:%Y-%m-%d} Spanish Club for Socialists {folder} – square.png"
            return day, p if p.exists() else None, p.name
    return day, None, "(no flyer kind for this event name)"


def upload(path):
    up = ce.call("POST", "/v1/images/create-upload-url", {"content_type": "image/png"})
    req = urllib.request.Request(up["upload_url"], data=path.read_bytes(), method="PUT",
                                 headers={"Content-Type": "image/png"})
    with urllib.request.urlopen(req, timeout=60) as r:
        if r.status not in (200, 201, 204):
            raise RuntimeError(f"upload failed: HTTP {r.status}")
    return up["file_url"]


def main():
    apply_all = "--apply" in sys.argv
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    skip = {sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--skip"}
    done = json.loads(DONE.read_text(encoding="utf-8")) if DONE.exists() else {}
    todo, missing = [], []
    for ev in upcoming():
        if ev["id"] in skip:
            print(f"(skipped on request) {ev['id']}  {ev['name']}")
            continue
        day, path, name = flyer_for(ev)
        if path is None:
            missing.append(f"{day:%a %Y-%m-%d}  {ev['name']}  -> missing {name}")
            continue
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        state = "already set" if done.get(ev["id"], {}).get("sha256") == sha else "to do"
        todo.append((ev, day, path, sha, state))
        print(f"{day:%a %Y-%m-%d}  {ev['id']}  {state:11}  {path.parent.name}\\{path.name}")
    if missing:
        print("\nNo flyer, left alone:\n  " + "\n  ".join(missing))
    print(f"\n{sum(1 for t in todo if t[4] == 'to do')} to do, "
          f"{sum(1 for t in todo if t[4] != 'to do')} already set, {len(missing)} without a flyer")
    if not (apply_all or only):
        return
    for ev, day, path, sha, state in todo:
        if state != "to do" or (only and ev["id"] != only):
            continue
        url = upload(path)
        ce.call("POST", "/v1/events/update", {"event_id": ev["id"], "cover_url": url, "suppress_email": True})
        after = ce.call("GET", f"/v1/events/get?event_id={ev['id']}")
        after = after.get("event", after)
        ok = after.get("cover_url") == url
        print(f"{'UPDATED ' if ok else 'MISMATCH'} {day:%Y-%m-%d} {ev['id']} {ev.get('url', '')} {url}")
        if not ok:
            sys.exit(1)
        done[ev["id"]] = {"sha256": sha, "file": f"{path.parent.name}/{path.name}", "cover_url": url}
        DONE.write_text(json.dumps(done, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
