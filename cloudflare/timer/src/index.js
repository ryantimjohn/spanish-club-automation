// Spanish Club for Socialists: the timer that starts the GitHub jobs on time.
//
// GitHub's own schedule ran hours late or not at all on Nov 11-13, 2026, but a job started through
// GitHub's API ("Run workflow") starts within seconds. So this Cloudflare Worker wakes at :07 and :37
// past every hour and starts the jobs due at that Eastern time, with the same live action their
// GitHub schedule runs. Each job still checks the time itself and does each thing only once, so a
// start never double-posts. The GitHub key is the Worker's secret GITHUB_TOKEN: a fine-grained key
// that can only start this repository's workflows (Actions: read and write). It is never logged.

export const REPO = "ryantimjohn/spanish-club-automation";
const DAILY = [0, 1, 2, 3, 4, 5, 6]; // Sunday is 0
const FRIDAY = [5];

// Eastern times, at :07 or :37 (the Worker's cron wakes then). Keep in step with the Handbook.
export const SCHEDULE = [
  { workflow: "instagram.yml", action: "post-due", days: DAILY,
    times: ["08:07", "09:07", "10:07", "11:07", "12:07", "13:07", "14:07", "15:07", "16:07", "17:07", "18:07"] },
  { workflow: "luma-invites.yml", action: "send-due", days: DAILY, times: ["10:07", "12:07", "15:07"] },
  { workflow: "discord.yml", action: "post-due", days: FRIDAY, times: ["09:07", "10:07", "12:07"] },
  { workflow: "eventbrite.yml", action: "sync-now", days: DAILY, times: ["10:37"] },
  { workflow: "meetup.yml", action: "sync-now", days: DAILY, times: ["10:37"] }, // off until LIVE_FROM is set
];

const EASTERN = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York", weekday: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});
const DAY = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 };

/** The Eastern weekday (Sunday 0) and "HH:MM" of a moment. */
export function eastern(date) {
  const p = Object.fromEntries(EASTERN.formatToParts(date).map((x) => [x.type, x.value]));
  return { day: DAY[p.weekday], time: `${p.hour}:${p.minute}` };
}

/** The jobs to start at this moment. */
export function dueAt(date) {
  const { day, time } = eastern(date);
  return SCHEDULE.filter((j) => j.days.includes(day) && j.times.includes(time));
}

async function start(job, token) {
  const url = `https://api.github.com/repos/${REPO}/actions/workflows/${job.workflow}/dispatches`;
  const body = JSON.stringify({ ref: "main", inputs: { action: job.action } });
  let res;
  for (let attempt = 1; attempt <= 2; attempt++) {
    res = await fetch(url, {
      method: "POST",
      body,
      headers: {
        authorization: `Bearer ${token}`,
        accept: "application/vnd.github+json",
        "x-github-api-version": "2022-11-28",
        "user-agent": "spanish-club-timer",
        "content-type": "application/json",
      },
    });
    if (res.status === 204) return `${job.workflow} ${job.action}: started`;
    if (res.status < 500) break; // a key or input problem won't fix itself on a retry
  }
  throw new Error(`${job.workflow} ${job.action}: GitHub said ${res.status} ${(await res.text()).slice(0, 200)}`);
}

export default {
  async scheduled(controller, env, ctx) {
    // scheduledTime is the planned wake time, so a late wake still matches its slot.
    const jobs = dueAt(new Date(controller.scheduledTime));
    if (!jobs.length) return;
    if (!env.GITHUB_TOKEN) throw new Error("The GITHUB_TOKEN secret isn't set, so nothing was started.");
    const results = await Promise.allSettled(jobs.map((j) => start(j, env.GITHUB_TOKEN)));
    for (const r of results) console.log(r.status === "fulfilled" ? r.value : `FAILED ${r.reason.message}`);
    if (results.some((r) => r.status === "rejected")) throw new Error("Some jobs didn't start; see the log above.");
  },
};
