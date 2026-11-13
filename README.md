# Spanish Club for Socialists: automation

This repository (renamed from `spanish-club-instagram` on Nov 10, 2026) runs five jobs on GitHub Actions:
- **Instagram auto-poster** (`post.py`, the "Instagram" workflow), described first below. The same workflow posts the Friday post to the club's **Facebook Page** (see "Facebook Page").
- **Luma invites** (`luma-invites/`, the "Luma invites" workflow).
- **Meetup listings** (`meetup/`, the "Meetup" workflow).
- **Eventbrite listings** (`eventbrite/`, the "Eventbrite" workflow).
- **Discord weekly post** (`discord/`, the "Discord" workflow).

A daily check on the organizer's computer (`run-check/`, see "Checking that the jobs ran" at the end) reports any job that GitHub ran late or skipped.

The season scripts for Luma (`luma-tools/`) run on the organizer's computer.

## Instagram auto-poster

This posts the club's Instagram posts on schedule, so nobody has to remember to.

- **Fridays, 9 AM Eastern:** next week's carousel (the cover, that week's flyers, the closing slide) and its caption. The cover also goes to the Story.
- **Event days, 11 AM Eastern:** that night's flyer as a Story.
- **Breaks:** a heads-up before the winter break, and "We're back!" before Jan 4.
- **About the club:** carousels about the club itself: "Our story" (Thu Nov 12) and "How it works" (Sat Nov 14), both at 9 AM. They list no events, so there's nothing to check on Luma and they go out as approved. Pin them to the top of the profile in the app (the API can't pin).

Carousels with a St. Stephen's night, the winter break post and "How it works" are tagged with the church's location, so they also show on its Instagram location page. The tag uses the church's Facebook "delegate Page ID", `13539212969` (not secret). If the St. Stephen's night is cancelled, the tag is left off. If Instagram refuses the tag, the post goes out without it and the notice says so. Stories can't be tagged with a location through the API.

Reels and link stickers stay manual. Instagram's API can't add stickers.

## Keep this repository public
Instagram downloads each image from its public GitHub link, so if this repository is made private, every post fails. Nothing in it is secret: the Instagram key is stored separately as a GitHub secret.

## What keeps it safe
- It only posts what's in `manifest.json` **and** was approved. If an image or caption changes, that post needs approval again.
- Before each post it reads the club's public Luma calendar. A flyer whose event is gone or has moved is left out, along with its caption line and the cover that lists it. If nothing is left, the post is skipped.
- Each post goes out at most once. `log/posted.json` records every post, skip and miss.
- The Instagram key is a GitHub secret. It's never printed or stored in this repository.

## How you'll hear about it
After every post, skip or problem, a comment appears on the **"Instagram log"** issue in this repository, and GitHub emails it to you. GitHub also emails you if a run fails outright.

**When GitHub runs late:** GitHub's timer often runs hours late or skips runs (on Nov 11–12, 2026 it ran 5–7 hours late). So the workflow is asked to run twice an hour from about 7 AM to 7 PM Eastern, and an Instagram post can still go out up to 10 hours after its time. It never goes out once the night it lists has started, so a "Tonight!" Story stops at 7 PM. To post a late one now: Actions → Instagram → Run workflow → `post-due`. (Facebook still allows 4 hours.)

## If a notice says something failed
- **"FAILED … will be tried again"**: nothing to do. It tries again on each run until the time in the notice.
- **"PROBLEM … may or may not have posted"**: open Instagram and check. It won't be retried, to avoid a double post.
- **"Missed"**: GitHub didn't run the schedule in time. Post it by hand if it still matters (the images are in `images/`).
- **Anything about the key**: see "Secrets" below.

## Secret (Settings → Secrets and variables → Actions)
- `INSTAGRAM_ACCESS_TOKEN`: the "System-user access token" from the club's Meta business portfolio (Instagram API with Unified login). Every Sunday the workflow checks the key still works, without posting, and emails you if it doesn't.

That's the only secret the Instagram poster uses. The Luma invites use `LUMA_API_KEY` (see below). The club's Instagram account ID (`17841429526104661`, @spanishclubforsocialists) is in `post.py`; it isn't secret, and the account's username is checked before every post. Luma is read from its public calendar feed, so no Luma key is needed.

## Running it by hand (Actions → Instagram → Run workflow)
- `test`: shows what's due right now. Posts nothing.
- `post-one`: posts one approved post now. Enter its id, e.g. `2026-11-13-carousel`.
- `check-key`: checks the Instagram key without posting.

To pause everything: Actions → Instagram → "…" → Disable workflow.

## Facebook Page

The same Instagram workflow also posts the Friday post to the club's Facebook Page ("Spanish Club for Socialists"):
- **Fridays, 9 AM Eastern:** one post with each of next week's nights as its **square flyer**, without the cover and closing slide (Facebook crops 4:5 photos in a multi-photo post, and the text already lists the week). The winter break post goes too, as it is. Stories don't.
- **Square flyers:** `facebook/collect_squares.py`, run on the organizer's computer, copies each night's "– square.png" flyer into `facebook/images/` and lists it in `facebook/squares.json`. A square is used only once it's approved there (`--approve`, with the organizer's OK); a changed image needs approval again. Until every flyer of a post has an approved square, that post uses Instagram's own images.
- **Text:** the approved Instagram caption, with each event's Luma link (`RSVP: https://luma.com/…`) under its line, since links work on Facebook. Instagram @handles and "link in bio" are left out.
- **Same safety rules:** only approved posts; Luma is checked first, and a flyer whose event is gone or has moved is left out with its caption line, link and cover (if nothing is left, the post is skipped); each post goes out at most once, recorded under `"facebook"` in `log/posted.json`.
- **Separate from Instagram:** it's its own step, run after Instagram even if Instagram failed, with its own key. A Facebook problem never stops, repeats or double-posts an Instagram post, and the reverse.
- **Switching it on:** nothing is posted until `FB_LIVE_FROM` (a Friday) is set in `post.py`. `FB_PAGE_ID` is the Page's ID, which isn't secret.

**How you'll hear about it:** a comment on the **"Facebook log"** issue after every post, skip or problem.

**Secret:** `FACEBOOK_ACCESS_TOKEN`, a system-user token of the club's second Meta app, "Spanish Club Page Poster" (Live mode, since Meta hides posts made by apps in Development mode). Permissions: `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`, `business_management`. The Page's own token is fetched with it at each run and never saved. Each Sunday, once it's switched on, the workflow checks the key without posting. The app's privacy policy is `PRIVACY.md`.

**Running it by hand (Actions → Instagram → Run workflow):**
- `test`: shows what's due right now for Instagram and Facebook. Posts nothing.
- `facebook-post-one`: posts one approved Friday post to the Page now. Enter its id, e.g. `2026-11-20-carousel`.
- `facebook-check-key`: checks the Facebook key and Page without posting.

On your own computer, `python post.py facebook --as-of 2026-11-20T09:07` shows exactly what would post that Friday.

**To pause only Facebook:** set `FB_LIVE_FROM = None` in `post.py`. (Disabling the workflow pauses Instagram too.)

## Luma invites

Every day at about 10 AM Eastern, `luma-invites/invite.py`:
1. Removes everyone tagged **removed-from-group** from the Luma calendar. The Google Group sync adds that tag. They're removed, not blocked, so they can follow again on their own.
2. Invites everyone on the calendar to each event that starts **6 days later**: Monday nights go out the Tuesday before, Wednesday nights the Thursday before, and one-offs 6 days ahead. Anyone already on that event's guest list (invited, registered, declined…) is skipped, and Luma skips anyone who unsubscribed.

Settings are at the top of `invite.py`:
- `SEND_HOUR`: 10.
- `DAYS_AHEAD`: 6.
- `NOTE`: empty, so Luma's standard invite goes out.
- `LIVE_FROM`: nothing is sent before this date, and nothing at all while it's unset.

**What keeps it safe:**
- Each event is sent once. `luma-invites/sent.json` marks it "sending" before any invite goes out, so a crash can't cause a second send. It's reported instead.
- If Luma's contact list looks wrong (under 100 people), nothing is sent.
- This repository is public, so the log shows counts, event names and links only. It never shows names or emails.

**How you'll hear about it:** a comment on the **"Luma invite log"** issue whenever it removes people, sends invites, or hits a problem. GitHub emails it to you.

**Secret:** `LUMA_API_KEY`, the Luma API key. It's never printed. Luma Plus allows 5,000 invites a week once the calendar is verified (Luma → calendar → Settings → Send Limit). About 760 people × 3 events is about 2,300.

**Running it by hand (Actions → Luma invites → Run workflow):**
- `test`: shows what's due, with counts. Changes nothing.
- `send-due`: does a real run now.

To see a real invite, run `python luma-invites/invite.py sample EVENT_ID YOUR_EMAIL` on your own computer, never in GitHub, where the address would show in public logs.

To pause: Actions → Luma invites → "…" → Disable workflow.

## Meetup listings

`meetup/sync.py` keeps the Meetup group (meetup.com/spanishclubforsocialists) in step with Luma, which is always the source of truth:
- **5 weeks ahead:** each Luma event goes up on Meetup once it's within 5 weeks, under Luma's exact title, so people know it's a club for socialists. Meetup then sends its usual announcement.
- **Follows Luma:** if a Luma event changes, its Meetup event is updated. If it's gone from Luma (checked twice), the Meetup event is *cancelled*, never deleted.
- **Online:** the Zoom link goes only in Meetup's attendee link, never in the description.
- **Description:** Luma's text, ending with a line pointing back to Luma.
- **Photo:** the event's wide flyer. Run `python meetup/sync.py collect` on the organizer's computer to copy the "– Meetup 16 9.png" flyers into `meetup/images/`, then commit them. An event without its photo waits.
- **Only its own events:** it edits or cancels only events recorded in `meetup/events.json`, never anything posted by hand.

**Secrets:**
- `MEETUP_CLIENT_KEY`, `MEETUP_SIGNING_KEY_ID`, `MEETUP_PRIVATE_KEY`: from the "Spanish Club events" API client at meetup.com/api/oauth/list. Meetup API access needs the Pro subscription.
- `LUMA_API_KEY`.

**Switching it on:** nothing changes on Meetup until `LIVE_FROM` is set in `sync.py`. The first event is made by hand as a draft (`run --live --draft --only LUMA_EVENT_ID`) for the organizer to check. Drafts are published with `publish --live` after their OK.

**Running it by hand (Actions → Meetup → Run workflow):**
- `test`: shows what it would do.
- `sync-now`: a real run.
- `check-keys`: checks the Meetup keys, read-only.

**How you'll hear about it:** the **"Meetup log"** issue.

**To pause:** Actions → Meetup → "…" → Disable workflow.

## Eventbrite listings

`eventbrite/sync.py` keeps the club's Eventbrite events in step with Luma, the same way as Meetup:
- **5 weeks ahead:** each Luma event goes up once it's within 5 weeks, under Luma's exact title, with Luma's date, time and place and one free ticket ("General Admission", 100 places, open until the event ends).
- **Follows Luma:** if a Luma event changes, its Eventbrite event is updated, even if it moved past the 5 weeks. If it's gone from Luma (checked twice), the Eventbrite event is *cancelled*, never deleted. Eventbrite won't cancel an event that has RSVPs through its API: the log then asks you to cancel it by hand, which emails its guests.
- **Online:** the Zoom link goes only on Eventbrite's attendee-only Online Event Page, never in the description.
- **Description:** Luma's text (St. Stephen's adds "Please come in the side door!"), ending with "Get weekly reminders and see every event: follow us on Luma". The short summary under the title is set in `SUMMARY`.
- **Image:** the event's wide flyer from `meetup/images/` (shared with Meetup), shown in Eventbrite's 2:1 crop. An event without its flyer waits.
- **Place:** an in-person event whose Luma address doesn't match its venue in `sync.py` waits, so nothing is listed at the wrong place.
- **Only its own events:** it edits or cancels only events recorded in `eventbrite/events.json`.

**Secrets:** `EVENTBRITE_TOKEN` (the private token of the club's Eventbrite API key: Account settings → Developer links → API keys) and `LUMA_API_KEY`.

**Switching it on:** nothing changes until `LIVE_FROM` is set in `sync.py`. The sample is made as an unpublished draft (`run --live --draft --only LUMA_EVENT_ID`) for the organizer to check; `publish --live` publishes the drafts after their OK.

**Running it by hand (Actions → Eventbrite → Run workflow):**
- `test`: shows what it would do.
- `sync-now`: a real run.
- `check-token`: checks the Eventbrite token, read-only.

**How you'll hear about it:** the **"Eventbrite log"** issue.

**To pause:** Actions → Eventbrite → "…" → Disable workflow.

## Discord weekly post

Every Friday at about 9 AM Eastern, `discord/weekly.py` (the "Discord" workflow) posts next week's events from Luma in the Discord server's #announcements, through a webhook. Nothing is posted until `LIVE_FROM` is set. Secrets: `DISCORD_ANNOUNCEMENTS_WEBHOOK`, `DISCORD_SERVER_BOTS_WEBHOOK` (samples only) and `LUMA_API_KEY`. Results go to the **"Discord log"** issue. The details, and how to run or pause it, are in `discord/README.md`.

## Checking that the jobs ran

GitHub's schedule can run late or skip runs. On Nov 11 and 12, 2026 every job missed its morning runs. So once a day at about 12:15 PM Eastern, a scheduled task on the organizer's computer runs `run-check/check_runs.py`. It reads each workflow's cron lines and GitHub's run history, and gives each job one status for the day:
- **OK:** it ran in its window.
- **LATE:** it ran, but only after its window.
- **MISSED:** it didn't run.
- **FAILED:** it ran and failed.

If any job isn't OK, the organizer gets a notification. The check never starts or re-runs a job itself.

Each job's window (Eastern), all of them ending at noon:
- **Instagram:** 8 AM to noon.
- **Luma invites:** 10 AM to noon. They only send at or after 10.
- **Meetup and Eventbrite:** 6 AM to noon.
- **Discord:** Fridays, 9 AM to noon.

The windows end at noon, but the jobs keep running after that, so a job that missed the morning can still catch up. The check is the early warning:
- **Instagram:** twice an hour, until about 7 PM.
- **Luma invites:** every hour, until about 6 PM.
- **Discord:** every hour on Fridays, until about 6 PM.
- **Meetup and Eventbrite:** every hour, until about 6 PM. Only the first scheduled run that works each day syncs; later runs that day see it and stop, so waiting-for-a-flyer notices still come once a day.

Before noon, `--latest` checks yesterday. If you add or move a schedule, update `JOBS` in the script; a test fails if a scheduled workflow is missing from it.

```bash
python -I -X utf8 run-check/check_runs.py --schedule   # today's expected times
python -I -X utf8 run-check/check_runs.py              # today so far
python -I -X utf8 run-check/check_runs.py --latest     # the latest finished day
python -X utf8 -m unittest run-check/test_check_runs.py
```

## Daily check-in (starts what GitHub missed)

`run-check/catch_up.py` runs from a scheduled task on the organizer's computer at about 9:30 AM, 11:30 AM and 1:30 PM Eastern, while the Claude app is open. It looks at what actually went out, not just whether GitHub ran. It reads each job's own record on GitHub:
- **Instagram:** `log/posted.json` against `manifest.json`.
- **Discord:** `discord/posted.json`.
- **Luma invites:** `luma-invites/sent.json`, plus Luma's event list.
- **Eventbrite and Meetup:** today's successful runs.

When something due hasn't gone out and no run of that job is already going, it starts the job with the same live action its schedule uses: `post-due`, `send-due` or `sync-now`. Each job still decides what is due and does each thing only once. This was the organizer's OK on Nov 13, 2026, after GitHub ran nothing on time for three days.

From 1 PM, or whenever something needs the organizer, it sends its summary to the **"Daily check-in"** issue through the "Check-in note" workflow (`checkin-note.yml`). The comment comes from GitHub's bot, so GitHub emails it. A comment made with the owner's own login wouldn't be emailed.

```bash
python -I -X utf8 run-check/catch_up.py          # report only, changes nothing
python -I -X utf8 run-check/catch_up.py --auto   # start missed jobs; send the summary from 1 PM
python -X utf8 -m unittest run-check/test_catch_up.py
```

To pause it, turn off the "Check the GitHub jobs ran" task under Scheduled in the Claude app.
