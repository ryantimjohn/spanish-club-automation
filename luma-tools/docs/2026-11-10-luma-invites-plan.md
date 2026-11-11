# Automatic Luma Invites Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A daily GitHub Actions job removes calendar contacts tagged `removed-from-group`, then invites everyone on the Luma calendar to each event 6 days before it, at about 10 AM Eastern.

**Architecture:** This is one standard-library Python script, `luma-invites/invite.py`, in the renamed public repo `ryantimjohn/spanish-club-automation`, next to the Instagram poster. Its pure functions (which events are due, who to invite, the report text) are unit-tested. A thin I/O layer calls Luma's public API, commits `luma-invites/sent.json`, and comments on the "Luma invite log" issue.

**Tech Stack:** Python 3.12, standard library only, `unittest`, GitHub Actions.

**Spec:** `luma-tools/docs/2026-11-10-luma-invites-design.md`

## Global Constraints
- Luma API base `https://public-api.luma.com`, header `x-luma-api-key`, and a user-agent on every call.
- The key comes from the `LUMA_API_KEY` env var or the Windows user env var. It's never printed: every error message is scrubbed.
- Send settings: `SEND_HOUR = 10`, `DAYS_AHEAD = 6`, `NOTE = ""` (sent as no message), `TAG = "removed-from-group"`, `LIVE_FROM = None` until go-live.
- Logs and issue comments contain counts, event names, dates and links only. Never an email or a name.
- Removal uses `POST /v1/calendars/contacts/remove` (not block). Invites use `POST /v1/events/guests/send-invites` in batches of 100.
- Live actions happen only with `--live` (or the `sample` command), and only when `LIVE_FROM` is set.

## Review Focus
1. An event at 7 PM Eastern is stored as the next day in UTC (Nov 19 7 PM = `2026-11-20T00:00Z`), so the send day must use the Eastern date. Test `test_due_uses_eastern_date`.
2. Guest-list emails differ in case from contact emails. Test `test_recipients_ignores_case`.
3. Luma returns an empty or tiny contact list. The script fails closed and sends nothing. Test `test_guard_contacts_refuses_small_list`.
4. The second daily cron in summer time runs again at 11 AM. It must not resend. Test `test_due_skips_sent_and_sending`.
5. A missed day or an event created late is still sent if it's at least 24 hours away, but never before `LIVE_FROM`. Tests `test_due_catches_up_missed_day`, `test_due_skips_under_24h`, `test_due_skips_before_live_from`.

---

### Task 1: Rename the repo
- [ ] `gh repo rename spanish-club-automation -R ryantimjohn/spanish-club-instagram --yes`
- [ ] In `social/instagram-autopost`, run `git remote set-url origin https://github.com/ryantimjohn/spanish-club-automation.git`, then `git fetch` succeeds.
- [ ] Check that `https://raw.githubusercontent.com/ryantimjohn/spanish-club-automation/main/README.md` returns 200.
- [ ] Tell the Instagram session about the rename and the new `luma-invites/` folder and workflow, which it shouldn't edit.

### Task 2: Pure logic and tests

**Files:** create `luma-invites/invite.py` and `luma-invites/test_invite.py`.

**Interfaces (produced):**
- `send_day(start_at: str) -> date`: the Eastern start date minus `DAYS_AHEAD`.
- `is_due(event: dict, now: datetime, sent: dict, live_from: date | None) -> bool`: the event is due when all of these hold:
  - its id isn't in `sent` (any status);
  - `live_from` is set and `live_from <= send_day <= now's Eastern date`;
  - the start is at least 24 hours after `now`.
- `recipients(contacts: list[dict], removed_ids: set[str], guest_emails: set[str]) -> list[dict]`: returns `{"email", "name"}` for each contact that isn't removed and isn't already a guest, compared on lowercased email, without duplicates.
- `guard_contacts(contacts: list) -> None`: raises `Problem` if there are fewer than `MIN_CONTACTS = 100` contacts.
- `report(removed: int, done: list[dict], problems: list[str]) -> str`: plain text. It returns an empty string when nothing happened.

- [ ] Write the tests:
  - `test_due_uses_eastern_date`: `send_day("2026-11-20T00:00:00.000Z") == date(2026, 11, 13)`.
  - `test_due_on_send_day`: an event Mon Nov 16 7 PM ET, now Tue Nov 10 10:05 ET, live_from Nov 10 → True.
  - `test_due_not_early`: the same event, now Mon Nov 9 → False.
  - `test_due_catches_up_missed_day`: now Wed Nov 11 → True.
  - `test_due_skips_under_24h`: now Sun Nov 15 8 PM ET → False.
  - `test_due_skips_sent_and_sending`: the id in sent with status "sent", and with "sending" → False.
  - `test_due_skips_before_live_from`: live_from Nov 12 and send day Nov 10 → False; live_from None → False.
  - `test_recipients_ignores_case`: a guest "A@X.org" removes contact "a@x.org"; a removed id is excluded; a duplicate email appears once.
  - `test_guard_contacts_refuses_small_list`: 5 contacts raise `Problem`; 150 contacts pass.
  - `test_report_has_no_emails`: a report built from data containing emails has no "@".
  - `test_report_empty_when_nothing_happened`: `report(0, [], []) == ""`.
- [ ] `python -m unittest -v luma-invites/test_invite.py` fails: there's no module yet.
- [ ] Implement the five functions plus the constants.
- [ ] Tests pass. Commit: "Luma invites: due/recipient logic with tests".

### Task 3: Luma calls, run flow and CLI

**Files:** modify `luma-invites/invite.py`.

**Interfaces (produced):**
- `luma(method, path, body=None) -> dict` and `paginate(path) -> list`.
- `run(live: bool, as_of: str | None) -> int`.
- `sample(event_id: str, email: str) -> None`.
- CLI: `python luma-invites/invite.py run [--live] [--as-of YYYY-MM-DDTHH:MM]` and `python luma-invites/invite.py sample EVENT_ID EMAIL`.

**How `run` works:**
- [ ] Live runs stop with a notice when the Eastern hour is before `SEND_HOUR`, or when `LIVE_FROM` is None.
- [ ] It lists the tagged contacts with `?tags=removed-from-group` and removes each one, live only.
- [ ] It loads all contacts and calls `guard_contacts`.
- [ ] It loads upcoming events and keeps the ones that are due.
- [ ] For each due event:
  - it lists the guests (all statuses);
  - it works out the recipients;
  - it marks the event `"sending"` and pushes;
  - it sends in batches of 100, with `message` omitted when `NOTE` is empty, and adds up Luma's `skipped`;
  - it marks the event `"sent"`, with the count, skipped count and time, and pushes.
- [ ] It sends one `notify(report(...))` when the report isn't empty.
- [ ] Any `Problem` is reported and leaves the exit code at 1.
- [ ] A dry run prints the same counts and changes nothing.
- [ ] `sample` sends a single invite and writes nothing to `sent.json`.
- [ ] Locally, `python -I -X utf8 luma-invites/invite.py run` shows the removal count, the contact count and the due events (none while `LIVE_FROM` is None), and prints no "@".
- [ ] Commit: "Luma invites: Luma calls, run flow, sample command".

### Task 4: Workflow and secret

**Files:** create `.github/workflows/luma-invites.yml`, and modify `README.md` (a "Luma invites" section).

- [ ] Workflow:
  - schedule `"3 14,15 * * *"`;
  - `workflow_dispatch` with a choice of `test` or `send-due`;
  - permissions: `contents: write`, `issues: write`;
  - concurrency group `luma-invites`;
  - Python 3.12;
  - env: `LUMA_API_KEY: ${{ secrets.LUMA_API_KEY }}` and `GITHUB_TOKEN: ${{ github.token }}`;
  - a scheduled run uses `run --live`.
- [ ] `notify` uses the issue titled "Luma invite log", and `push_log` adds only `luma-invites/sent.json`.
- [ ] Set the secret from the Windows env var without echoing it: `powershell ... | gh secret set LUMA_API_KEY -R ryantimjohn/spanish-club-automation`.
- [ ] Push, then run the workflow by hand with `test`. It succeeds, and the log shows counts only.
- [ ] Commit: "Luma invites: daily workflow and README".

### Task 5: Go-live (organizer)
- [ ] The organizer verifies the calendar send limit.
- [ ] Send one sample invite from the PC to an address the organizer gives, and they check the email.
- [ ] With their OK, set `LIVE_FROM` to the go-live date and push.
- [ ] Update the Handbook and the Change log.
