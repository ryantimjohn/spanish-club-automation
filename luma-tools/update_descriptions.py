"""Rewrite the upcoming Luma event descriptions with the new text (organizer's OK, Nov 11, 2026).

Usage:  python -I -X utf8 luma-tools/update_descriptions.py               # dry run: counts + one sample per kind
        python -I -X utf8 luma-tools/update_descriptions.py --only EVT_ID # change one event
        python -I -X utf8 luma-tools/update_descriptions.py --apply       # change every upcoming event

The text lives in create_events.py (IN_PERSON, ONLINE). In-person events keep the guide link they
already have, now shown under its title alone. Edits go out with suppress_email, so guests get no
emails. Safe to rerun: events that already have the new text are skipped. The Zoom passcode comes from
ZOOM_PWD and is masked in everything this prints.
"""
import importlib.util
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

spec = importlib.util.spec_from_file_location("ce", Path(__file__).with_name("create_events.py"))
ce = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ce)

GUIDE = re.compile(r"\[([^\]]+)\]\((https://docs\.google\.com/document/d/[^)\s]+)\)")


def kind(event):
    name = event.get("name") or ""
    if event.get("meeting_url") or name.endswith("Online"):
        return "Online"
    for k in ("St. Stephen", "Liberation Center", "Dupont"):
        if k in name:
            return k
    return None


def guide_from(md):
    """The event's guide as [Title](url), from either the old or the new text; None if there isn't one."""
    m = GUIDE.search(md or "")
    if not m:
        return None
    title = re.sub(r"^Club de Conversaci[oó]n\s*-\s*\d+\s*-\s*", "", m[1].strip())
    title = re.sub(r"\s*-\s*Google Docs$", "", title)
    return f"[{title}]({m[2]})"


def new_description(event):
    """(text, problem): the new description, or None and why it can't be built."""
    k = kind(event)
    if k == "Online":
        if not ce.ZOOM_PWD:
            return None, "ZOOM_PWD isn't set"
        if (event.get("meeting_url") or ce.ZOOM_URL) != ce.ZOOM_URL:
            return None, "its Zoom link isn't the usual one"
        return ce.ONLINE, None
    if k is None:
        return None, "not a club event this script knows"
    guide = guide_from(event.get("description_md"))
    if not guide:
        return None, "no conversation guide link found"
    return ce.in_person(guide, ce.LIBERATION_LINE if k == "Liberation Center" else ce.VENMO_LINE), None


def masked(text):
    return re.sub(r"pwd=[^)\s\]]+", "pwd=…", text)


def upcoming():
    out, cursor = [], None
    while True:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # events that haven't started yet
        q = f"/v1/calendars/events/list?after={now}&pagination_limit=100&sort_direction=asc"
        if cursor:
            q += f"&pagination_cursor={cursor}"
        page = ce.call("GET", q)
        out += [e.get("event", e) for e in page["entries"]]
        if not page.get("has_more"):
            return out
        cursor = page["next_cursor"]


def main(argv):
    apply_all = "--apply" in argv
    only = argv[argv.index("--only") + 1] if "--only" in argv else None
    ids = [only] if only else [e["id"] for e in upcoming()]
    shown, counts, failed = set(), {}, False
    for eid in ids:
        ev = ce.call("GET", f"/v1/events/get?event_id={eid}")
        label = f"{ev['start_at'][:10]} {ev['name']}"
        new, problem = new_description(ev)
        if problem:
            print(f"FAIL {label}: {problem}")
            failed = True
            continue
        if new.strip() == (ev.get("description_md") or "").strip():
            print(f"SKIP {label}: already done")
            continue
        k = kind(ev)
        counts[k] = counts.get(k, 0) + 1
        if not (apply_all or only):
            if k not in shown:
                shown.add(k)
                print(f"\n===== {label} ({ev['url']}) =====\n{masked(new)}\n")
            continue
        ce.call("POST", "/v1/events/update", {"event_id": ev["id"], "description_md": new, "suppress_email": True})
        after = ce.call("GET", f"/v1/events/get?event_id={ev['id']}")
        ok = (after.get("description_md") or "").strip() == new.strip()
        print(f"UPDATED {label} {ev['url']}" + ("" if ok else "  MISMATCH"))
        failed |= not ok
    print("\n" + "\n".join(f"{k:<18} {n}" for k, n in sorted(counts.items())))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
