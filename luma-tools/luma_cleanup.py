"""Luma cleanup for the weekly events (approved Nov 10, 2026):
- replace the free-course line with a Discord invite line
- add the optional question "How did you hear about us?"

Usage: python luma_cleanup.py                 # dry run: counts + one before/after per series
       python luma_cleanup.py --only EVENT_ID # apply to one event
       python luma_cleanup.py --apply         # apply to every weekly event in range
"""
import importlib.util
import re
import sys
from pathlib import Path

CE = Path(__file__).with_name("create_events.py")
spec = importlib.util.spec_from_file_location("ce", CE)
ce = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ce)

AFTER, BEFORE = "2026-11-10T12:00:00Z", "2027-06-01T00:00:00Z"  # skips the past Nov 9 event
WEEKLY = {"Spanish Club for Socialists - St. Stephen's", "Spanish Club for Socialists - Online"}
DISCORD = ("Keep practicing between sessions on our Discord: "
           "[discord.gg/HuefQSfHVm](https://discord.gg/HuefQSfHVm)")
QUESTION = {
    "id": "how-did-you-hear",
    "label": "How did you hear about us?",
    "required": False,
    "question_type": "select",
    "multiple": False,
    "options": ["A friend", "Instagram", "Meetup", "Partiful", "Eventbrite", "Discord",
                "DSA newsletter", "Browsing Luma", "Other"],
}

APPLY = "--apply" in sys.argv
ONLY = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None


def list_events():
    out, cursor = [], None
    while True:
        q = (f"/v1/calendars/events/list?after={AFTER}&before={BEFORE}"
             f"&pagination_limit=100&sort_direction=asc")
        if cursor:
            q += f"&pagination_cursor={cursor}"
        page = ce.call("GET", q)
        out += [e.get("event", e) for e in page["entries"]]
        if not page.get("has_more"):
            return out
        cursor = page["next_cursor"]


def new_description(md):
    if DISCORD in md:
        return md
    if md.count(ce.COURSE) != 1:
        return None
    return re.sub(r"\n{3,}", "\n\n", md.replace(ce.COURSE, DISCORD)).strip()


def new_questions(qs):
    qs = qs or []
    if any(q.get("label") == QUESTION["label"] for q in qs):
        return qs
    return qs + [QUESTION]


ids = [ONLY] if ONLY else [e["id"] for e in list_events()]
print(f"{len(ids)} events")
shown, counts, failed = set(), {}, False
for eid in ids:
    ev = ce.call("GET", f"/v1/events/get?event_id={eid}")
    series = ev["name"].split(" - ")[-1]
    label = f"{ev['start_at'][:10]} {series}"
    if ev["name"] not in WEEKLY:
        print(f"SKIP {label}: not a weekly event")
        continue
    md, qs = ev["description_md"], ev.get("registration_questions") or []
    new_md, new_qs = new_description(md), new_questions(qs)
    if new_md is None:
        print(f"FAIL {label}: course line found {md.count(ce.COURSE)} times")
        failed = True
        continue
    if new_md == md and new_qs == qs:
        print(f"SKIP {label}: already done")
        continue
    counts[series] = counts.get(series, 0) + 1
    if not (APPLY or ONLY):
        if series not in shown:
            shown.add(series)
            print(f"\n===== {label} {ev['id']} ({ev['url']}) existing questions: {len(qs)} =====\n{new_md}\n")
        continue
    body = {"event_id": ev["id"], "suppress_email": True}
    if new_md != md:
        body["description_md"] = new_md
    if new_qs != qs:
        body["registration_questions"] = new_qs
    ce.call("POST", "/v1/events/update", body)
    after = ce.call("GET", f"/v1/events/get?event_id={ev['id']}")
    ok = (after["description_md"].strip() == new_md.strip()
          and any(q.get("label") == QUESTION["label"] for q in after.get("registration_questions") or []))
    print(f"UPDATED {label} {ev['url']}" + ("" if ok else "  MISMATCH"))
    if ONLY:
        print("questions now:", after.get("registration_questions"))
    failed |= not ok
print("\n" + "\n".join(f"{s:<15} {n}" for s, n in sorted(counts.items())))
sys.exit(1 if failed else 0)
