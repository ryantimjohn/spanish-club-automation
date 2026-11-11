# Automatic Luma invites: design

Date: 2026-11-10. Status: approved by the organizer on Nov 10 ("Yes, go ahead"), along with the repo rename.

## Why
The organizer sends Luma invites by hand to everyone on the Spanish Club for Socialists calendar. This makes that automatic, on a steady schedule, so every event is announced about a week ahead without anyone remembering to do it. People who have left the Google Group (the sync tags them "removed-from-group" on Luma) must stop getting invites.

## What it does
Every day at 10:00 AM Eastern, a script:
1. **Removes people who left.** Every calendar contact tagged `removed-from-group` is removed from the calendar with `POST /v1/calendars/contacts/remove`. This isn't a block: a removed person can follow the calendar again on their own. This step runs on every run, before any invite goes out, even on days with nothing to send.
2. **Finds events due an invite.** These are events on the calendar whose start date (Eastern) is exactly 6 days after today. Mon St. Stephen's goes out the Tuesday before, Wed Online the Thursday before, and one-offs like the Liberation Center night 6 days before. The dates always come from Luma.
3. **Invites everyone on the calendar** to each of those events with `POST /v1/events/guests/send-invites`, leaving out:
   - anyone already on that event's guest list in any status (invited, registered, waitlist, declined and so on), so nobody gets a second invite to the same event;
   - anyone who was just removed in step 1.

   Luma itself also skips people who unsubscribed, were removed or blocked.
4. **Reports.** It posts one comment on an "Invite log" issue in the repo, and GitHub emails it to the organizer. The comment covers how many were removed, each event invited (name, date, link, how many invited, how many Luma skipped), and any failure. It gives counts only, never names or emails. If a run does nothing, it doesn't comment.

## Settings (at the top of the script)
- Send time: 10:00 AM Eastern. It's one setting, so a later test at 6 PM is easy.
- Days ahead: 6.
- Note on invites: empty. The organizer prefers Luma's standard invite; Luma allows up to 200 characters.
- The tag name: `removed-from-group`.

## How it runs
- It's a plain Python script, not an AI agent, because it emails about 760 people several times a week.
- It runs in GitHub Actions in the existing auto-poster repo, renamed from `spanish-club-instagram` to **`spanish-club-automation`** (organizer's choice, Nov 10). That repo must stay **public** for Instagram, so the invite log shows counts only. The script and its log live in `luma-invites/`, and the workflow is `.github/workflows/luma-invites.yml`.
- The season scripts in `luma-tools` stay off GitHub because `create_events.py` holds the Online Zoom link.
- The Luma API key is the repo secret `LUMA_API_KEY`. It's never printed or written anywhere else. The workflow only runs on its schedule or by hand, so the secret is never exposed to pull requests.
- The one-address sample is run from the organizer's PC, not from GitHub, so no email address appears in public run logs.
- Nothing goes out until a go-live date (`LIVE_FROM`) is set in the script with the organizer's OK. Only events whose send day falls on or after that date are handled, so events the organizer already invited by hand aren't sent again.
- To pause: Actions → the workflow → Disable workflow.

## Safety
- **Missed runs:** if GitHub skips or delays a run, the next run still sends any event that is now 1 to 5 days away and hasn't had its calendar-wide invite yet, rather than skipping it. Nothing is sent for an event less than 24 hours away or already started.
- **Late-made events:** an event created less than 6 days ahead is sent at the next 10 AM run, under the same rule.
- **No double sends:** the event's guest list is the record. Anyone already on it in any status is skipped, so a rerun can't double-send. The script also keeps a small committed log of events (`luma-invites/sent.json`). An event is marked "sending" and pushed before its invites go out. If a run finds an event still marked "sending" (a crash mid-send), it doesn't resend and reports it instead.
- **Fail closed:** if Luma's contact list or guest list comes back empty or errors, it sends nothing and reports the problem.
- **Send limit:** Luma Plus allows 5,000 invites a week once the calendar is verified (Settings → Send Limit). About 760 people × 3 events is about 2,300 a week. If Luma refuses a send, the log says so, and that event is retried at the next run.

## Modes, for setup and testing
- `--dry-run` shows what would be removed and sent, with counts, and changes nothing.
- `--sample EVENT_ID EMAIL` invites one address to one event, so the organizer can see the real invite email first.

## Rollout
1. The organizer verifies the Luma calendar's send limit.
2. A dry run is shown to the organizer.
3. A sample invite goes to an address the organizer gives, and they check the email.
4. The workflow turns on with the organizer's OK. The first real send is whichever event is next due.

## Not part of this
- The Google Group → Luma sync.
- Luma newsletters, reminders to registered guests (Luma sends its own), Meetup, Partiful, Eventbrite.
- Choosing who's invited beyond "everyone on the calendar, minus removed-from-group".

## Would make it a failure
- Anyone tagged removed-from-group getting an invite.
- Anyone getting two invites for the same event.
- An invite going out on the wrong date, or for an event that no longer exists.
- Names, emails or the API key showing up in logs or the repo.
