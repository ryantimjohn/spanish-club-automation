"""Count past events on the club's Luma calendar (read-only).
Cancelled events are deleted from Luma, so they never count.
Add the fixed pre-Luma Google Calendar total to get the club's all-time count."""
import collections
import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

spec = importlib.util.spec_from_file_location(
    "ce", Path(__file__).with_name("create_events.py"))
ce = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ce)

now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
out, cursor = [], None
while True:
    q = f"/v1/calendars/events/list?after=2020-01-01T00:00:00Z&before={now}&pagination_limit=100&sort_direction=asc"
    if cursor:
        q += f"&pagination_cursor={cursor}"
    page = ce.call("GET", q)
    out += [e.get("event", e) for e in page["entries"]]
    if not page.get("has_more"):
        break
    cursor = page["next_cursor"]

ET = ZoneInfo("America/New_York")
by_name, by_month = collections.Counter(), collections.Counter()
for ev in out:
    start = datetime.fromisoformat(ev["start_at"].replace("Z", "+00:00")).astimezone(ET)
    end = datetime.fromisoformat(ev["end_at"].replace("Z", "+00:00")) if ev.get("end_at") else None
    if end and end > datetime.now(timezone.utc):
        continue  # still running
    by_name[ev["name"]] += 1
    by_month[start.strftime("%Y-%m")] += 1
    print(f"{start:%a %Y-%m-%d %H:%M}  {ev['name']}  {ev.get('url','')}")
print(f"\nTOTAL past events: {sum(by_name.values())}")
print("\nBy name:")
for n, c in by_name.most_common():
    print(f"  {c:3}  {n}")
print("\nBy month:")
for m, c in sorted(by_month.items()):
    print(f"  {m}  {c}")
