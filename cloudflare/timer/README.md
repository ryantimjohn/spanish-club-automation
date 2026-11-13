# Spanish Club timer (Cloudflare)

GitHub's own schedule ran hours late, or not at all, on Nov 11 to 13, 2026. A job started through GitHub's API ("Run workflow"), though, starts within seconds. So this small Cloudflare Worker is the club's timer. It wakes at :07 and :37 past every hour, and starts the GitHub jobs due at that Eastern time. Each start uses the same live action the job's GitHub schedule runs. The jobs still check the time themselves and do each thing only once, so an extra start never double-posts.

| Job | Started at (Eastern) |
| --- | --- |
| Instagram (`post-due`) | every hour at :07, 8:07 AM to 6:07 PM |
| Luma invites (`send-due`) | 10:07 AM, 12:07 PM, 3:07 PM |
| Discord (`post-due`) | Fridays 9:07 AM, 10:07 AM, 12:07 PM |
| Eventbrite (`sync-now`) | 10:37 AM |
| Meetup (`sync-now`) | 10:37 AM (does nothing until Meetup's `LIVE_FROM` is set) |

There are three backups:
- **GitHub's own schedules** still run, late. The extra runs are harmless, and Eventbrite and Meetup skip once that day's sync has run.
- **The daily check-in** (`run-check/catch_up.py`) runs on the organizer's computer at 9:30 AM, 11:30 AM and 1:30 PM. It starts anything that still hasn't gone out.
- **The Sunday Instagram key check** stays on GitHub's schedule.

## The GitHub key

The Worker's secret `GITHUB_TOKEN` is a fine-grained GitHub personal access token. It has access to this repository only, with **Actions: Read and write** and nothing else. It can start the repo's workflows, but it can't read their secrets or change code. It's never printed or logged.
- **Make it:** github.com → Settings → Developer settings → Fine-grained tokens.
- **When it expires:** starts fail with a 401, and the check-in reports the misses. Make a new key, then run `npx wrangler@4 secret put GITHUB_TOKEN` in this folder and paste it.

## Deploying (from this folder)

```bash
npx wrangler@4 login                      # the organizer signs in to Cloudflare in the browser
npx wrangler@4 deploy                     # uploads src/index.js and the cron
npx wrangler@4 secret put GITHUB_TOKEN    # the organizer pastes the GitHub key at the prompt
npx wrangler@4 tail                       # optional: watch its log live
```

`workers_dev = false`, so the Worker has no public web address. It only runs on its timer.

**To change a time,** edit `SCHEDULE` in `src/index.js` (times must be at :07 or :37), run the tests, then deploy again. **To pause it,** go to the Cloudflare dashboard → Workers → spanish-club-timer → Settings → Triggers, and delete the cron. Or run `npx wrangler@4 delete`.

## Tests

```bash
node --test cloudflare/timer/test/schedule.test.mjs
```
