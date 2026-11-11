# Meetup listings: design

Date: 2026-11-10. Status: waiting for the organizer's OK. The build waits for Meetup to approve the API client.

## Why
Meetup is for discovery: people searching Meetup for Spanish conversation in DC should find the club. Luma stays the hub and the source of truth. The organizer shouldn't have to copy events across by hand.

## What it does
A daily script in the `spanish-club-automation` repo (`meetup/`, workflow "Meetup") keeps the Meetup group `spanish-club-for-socialists` in step with Luma:
1. **Posts events 5 weeks ahead.** Every Luma event starting within the next 5 weeks that isn't on Meetup yet is created and published there. Meetup then sends its usual announcement to group members.
2. **Follows Luma.** If a Luma event's time, place or text changes, the Meetup event is updated. If a Luma event disappears (cancelled on Luma), the Meetup event is cancelled. Meetup tells its own RSVPs.
3. **Reports.** It comments on a "Meetup log" issue (counts, event names, dates and links, never names or emails), which GitHub emails to the organizer.

## Each Meetup event
- **Title:** Luma's title, word for word (for example "Spanish Club for Socialists - St. Stephen's"). This is the organizer's choice, so people know it's a club for socialists before they come.
- **Time and place:** from Luma.
  - In-person events use a Meetup venue for the address: St. Stephen's, 1525 Newton St NW; the DC Liberation Center, 337 H St NE; Patchwork's new space when Dupont returns.
  - Online events are Meetup online events. The Zoom link goes only in Meetup's attendee-only link field, so it shows only to people who RSVP. It's never in the description.
- **Description:** Luma's description, with these changes:
  - the Zoom link is taken out (Online);
  - no free-course line, which Luma no longer has anyway;
  - a closing line pointing back to Luma: "Get weekly reminders and see every event: follow us on Luma, https://luma.com/spanishclubforsocialists".
  - Conversation-guide links stay as they are on Luma. Guide topics are never promoted.
- **Photo:** the event's wide flyer, `flyers\<event>\YYYY-MM-DD Spanish Club for Socialists <Event> – Meetup 16 9.png` (1920×1080, made by the flyer session; the organizer approved the St. Stephen's design on Nov 10). If the wide flyer for an event doesn't exist yet, the event waits a day rather than going up without one, and the log says so.
- **RSVPs:** people may RSVP on Meetup. That list stays on Meetup, which is why every description carries the Luma link. There's no attendance limit and no fee.

## How it runs
- A plain Python script on GitHub Actions, like the Instagram poster and the Luma invites. It isn't an AI agent: copying Luma onto Meetup should be the same every time.
- Meetup auth uses the JWT flow:
  - the client key, signing key ID and private key are GitHub secrets `MEETUP_CLIENT_KEY`, `MEETUP_SIGNING_KEY_ID` and `MEETUP_PRIVATE_KEY`, and Windows user env vars of the same names on the organizer's PC;
  - the member ID is 4255800 (Timothy Ryan, the group's organizer), which isn't secret.
- Luma is read with `LUMA_API_KEY`, because the Online Zoom link (`meeting_url`) isn't in the public feed. The Zoom link is also `ZOOM_PWD`-based, as in create_events.py.
- The repo is public, so the code holds no keys, Zoom passcode, names or emails. Meetup event IDs and the Luma ↔ Meetup mapping are kept in `meetup/events.json`.
- Meetup API access needs the Pro subscription to stay active.

## Safety
- **Dry run by default.** `--live` only on GitHub's schedule, or when run by hand.
- **Rollout:**
  1. Create one event as a Meetup DRAFT for the organizer to check.
  2. With their OK, publish it.
  3. With their OK, switch on the daily run, controlled by `LIVE_FROM`, the same as the invites.
- **Cancels:** it never cancels a Meetup event unless Luma's API says that event is gone and a second read confirms it. It never deletes past events.
- **Changes:** it only edits events it created, which are recorded in events.json. It doesn't touch anything the organizer posts on Meetup by hand.

## Not part of this
- The group URL change. That's waiting on Meetup support.
- Eventbrite, which is next on the roadmap and will use the same pattern.
- Messaging Meetup members, or reading their details.

## Would make it a failure
- A Zoom link visible to people who haven't RSVPed.
- A Meetup event whose day, time or place differs from Luma.
- Double listings of the same night.
- A title or description that hides that the club is for socialists.
