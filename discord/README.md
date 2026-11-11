# Discord weekly post

Every Friday at about 9 AM Eastern, `discord/weekly.py` reads Luma and posts next week's events in **#announcements** on the club's Discord server, through a webhook (no bot):
- **A usual week:** "Next week at Spanish Club", then one line per night (day, time, place, RSVP link). A regular night that's off gets its own line, e.g. "No Online session Wed Nov 25."
- **The Friday before a week with no events:** one break note, e.g. "No Spanish Club Dec 21 – Jan 3. We're back Mon Jan 4 at St. Stephen's!" Nothing is posted the Friday after.
- **Nothing on Luma ahead:** no post, and one notice asking for the next season's events.

It never posts the Zoom link, never pings anyone (no @everyone), shows no link previews, and never mentions the conversation guides.

## What keeps it safe
- Nothing is posted until `LIVE_FROM` is set at the top of `weekly.py`, and never before that Friday.
- It posts only through a webhook that belongs to #announcements (`CHANNEL_ID`, not secret).
- Each Friday is posted once. `discord/posted.json` marks it "posting" before the message goes out, so a crash can't cause a double post. It's reported instead. If Discord turns the post down, the next run that morning tries again.

## Secrets (Settings → Secrets and variables → Actions)
- `DISCORD_ANNOUNCEMENTS_WEBHOOK`: the full webhook link for #announcements (Discord → #announcements → Edit Channel → Integrations → Webhooks).
- `DISCORD_SERVER_BOTS_WEBHOOK`: the full link of a test webhook in the private #server-bots, used only for samples.
- `LUMA_API_KEY`.

A webhook link works like a password for posting in its channel. Never paste it anywhere else. If one leaks, delete that webhook in Discord, make a new one and save the new link as the secret.

## Running it by hand (Actions → Discord → Run workflow)
- `test`: shows the coming Friday's post. Posts nothing. Put a date in `as_of` to see another Friday's.
- `sample`: posts the coming Friday's message (or `as_of`'s) to #server-bots, to see how it looks.
- `check-webhook`: checks the #announcements webhook without posting.
- `post-due`: a real run now. It still posts only on a Friday from 9 AM, and only once.

On the organizer's PC, `python discord/weekly.py run --as-of 2026-12-18T09:05` shows any Friday's post (it needs `LUMA_API_KEY`).

## How you'll hear about it
A comment on the **"Discord log"** issue after each post and for any problem. GitHub emails it to you.
- **"PROBLEM … may or may not have gone out"**: look in #announcements. It isn't retried, to avoid a double post.
- **"Discord says the webhook doesn't exist any more"**: the webhook was deleted or reset. Make a new one and save it as `DISCORD_ANNOUNCEMENTS_WEBHOOK`.

## To pause
Actions → Discord → "…" → Disable workflow. Or set `LIVE_FROM = None` in `weekly.py`.
