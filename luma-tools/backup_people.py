"""Back up the Luma calendar's people list to a dated CSV on the organizer's computer.

Usage:  python -I -X utf8 luma-tools/backup_people.py

The file goes to D:\\Spanish Club for Socialists\\Luma people backups\\YYYY-MM-DD luma-people.csv
(or the folder in the LUMA_BACKUP_DIR env var). It holds names and emails, so it stays on this
computer only: never commit it, upload it or share it. The script prints counts only, never names
or emails, and reads the key from LUMA_API_KEY (the Windows user env var), which it never prints.
"""
import csv
import importlib.util
import os
import sys
import urllib.parse
from datetime import date
from pathlib import Path

DEFAULT_DIR = r"D:\Spanish Club for Socialists\Luma people backups"

spec = importlib.util.spec_from_file_location("ce", Path(__file__).with_name("create_events.py"))
ce = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ce)

FIELDS = ["name", "first_name", "last_name", "email", "created_at", "event_approved_count",
          "event_checked_in_count", "tags", "id", "user_id"]


def contacts():
    out, cursor = [], None
    while True:
        q = "/v1/calendars/contacts/list?pagination_limit=100"
        if cursor:
            q += f"&pagination_cursor={urllib.parse.quote(cursor)}"
        page = ce.call("GET", q)
        out += page.get("entries", [])
        if not page.get("has_more"):
            return out
        cursor = page["next_cursor"]


def main():
    folder = Path(os.environ.get("LUMA_BACKUP_DIR") or DEFAULT_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    people = contacts()
    if len(people) < 100:
        sys.exit(f"Luma returned only {len(people)} people, which looks wrong, so no backup was written.")
    path = folder / f"{date.today():%Y-%m-%d} luma-people.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for p in people:
            row = dict(p)
            row["tags"] = "; ".join((t.get("name") if isinstance(t, dict) else str(t)) for t in (p.get("tags") or []))
            w.writerow(row)
    print(f"Backed up {len(people)} people to {path}")


if __name__ == "__main__":
    main()
