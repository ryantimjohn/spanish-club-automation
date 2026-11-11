# Luma tools

These scripts manage the club's Luma calendar through Luma's API. They run on the organizer's computer, not on GitHub. They read the API key from the Windows user env var `LUMA_API_KEY`, and none of them prints it. Luma's API reference is at https://public-api.luma.com/openapi.json.

This repository is public, so the Online Zoom passcode isn't in the code. `create_events.py` reads it from `ZOOM_PWD`: the Windows user env var on the organizer's computer, and a GitHub secret of the same name. If Zoom's passcode changes, update both.

The automatic invites are separate: see `luma-invites/` and the main README.

Before running a script, load the key into Git Bash:

```bash
export LUMA_API_KEY=$(powershell.exe -NoProfile -Command "[Environment]::GetEnvironmentVariable('LUMA_API_KEY','User')" | tr -d '\r\n')
```

Run Python with `python -I -X utf8`.

## create_events.py
Makes a season of weekly events: St. Stephen's on Mondays, Online on Wednesdays, and Dupont on Tuesdays when it's running.
- **Every event gets:** the map link, the Venmo note, the Discord invite line and the optional "How did you hear about us?" question.
- **St. Stephen's events get:** that week's conversation guide.
- **Before a new season, edit:** `FIRST_WEEK`, the `DOCS` guide list, `SKIP` (holidays and breaks), and the Dupont address. Patchwork's new space is 1901 18th St NW.
- **How to run it:** a plain run prints the schedule. `--only N` creates event N, so make one as a sample first. `--apply` creates the rest and skips events that already exist.

## luma_cleanup.py
The Nov 10, 2026 cleanup: it swapped the free-course line for the Discord line and added the question. A plain run is a dry run, `--only EVENT_ID` changes one event, and `--apply` changes all of them. Edits go out with `suppress_email`, so guests get no emails. You can run it again safely: events that are already done are skipped.

## update_covers.py
Sets each upcoming event's Luma cover to its dated square flyer from the club folder's `flyers` directory. Uploads are logged in `covers_done.json`, so a rerun only uploads flyers that changed. A plain run is a dry run, `--only EVENT_ID` does one event, `--apply` does all of them, and `--skip EVENT_ID` leaves one out.

## backup_people.py
Saves Luma's people list (names, emails, event counts, tags) as a dated CSV in `D:\Spanish Club for Socialists\Luma people backups` on the organizer's computer. It stays there only: never commit it, upload it or share it. It prints counts only.

## count_events.py
A read-only count of past events on Luma, by name and by month. Cancelled events are deleted from Luma, so they never count. To get the club's all-time total, add the fixed number of pre-Luma (Google Group) events, which is kept in the Plan tab's "Where we are".

## Rules
- Get the organizer's OK before creating or changing events. Show one sample first.
- Cancelling a Luma event deletes it for good and emails its guests. Back up its text first.
- After any change, update the Handbook and the Change log in the Plan & Handbook doc.
