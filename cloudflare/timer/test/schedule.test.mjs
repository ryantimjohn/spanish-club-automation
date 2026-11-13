// Tests for the timer's schedule. Run: node --test cloudflare/timer/test/schedule.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { dueAt, eastern, SCHEDULE } from "../src/index.js";

const names = (d) => dueAt(new Date(d)).map((j) => `${j.workflow} ${j.action}`);

test("Eastern time in winter and summer", () => {
  assert.deepEqual(eastern(new Date("2026-11-13T14:07:00Z")), { day: 5, time: "09:07" });
  assert.deepEqual(eastern(new Date("2026-07-03T13:07:00Z")), { day: 5, time: "09:07" });
  assert.deepEqual(eastern(new Date("2026-11-14T04:37:00Z")), { day: 5, time: "23:37" });
});

test("Friday 9:07 AM starts Instagram and Discord", () => {
  assert.deepEqual(names("2026-11-13T14:07:00Z"), ["instagram.yml post-due", "discord.yml post-due"]);
  assert.deepEqual(names("2026-07-03T13:07:00Z"), ["instagram.yml post-due", "discord.yml post-due"]);
});

test("Discord only on Fridays, invites at 10:07, syncs at 10:37", () => {
  assert.deepEqual(names("2026-11-12T14:07:00Z"), ["instagram.yml post-due"]);
  assert.deepEqual(names("2026-11-12T15:07:00Z"), ["instagram.yml post-due", "luma-invites.yml send-due"]);
  assert.deepEqual(names("2026-11-12T15:37:00Z"), ["eventbrite.yml sync-now", "meetup.yml sync-now"]);
});

test("nothing at night", () => {
  assert.deepEqual(names("2026-11-13T07:07:00Z"), []);
  assert.deepEqual(names("2026-11-14T01:07:00Z"), []); // 8:07 PM Eastern
});

test("every time is on the cron's :07 or :37", () => {
  const toml = readFileSync(new URL("../wrangler.toml", import.meta.url), "utf8");
  assert.match(toml, /crons = \["7,37 \* \* \* \*"\]/);
  for (const j of SCHEDULE) for (const t of j.times) assert.match(t, /^\d\d:(07|37)$/, `${j.workflow} ${t}`);
});

test("each job is a real workflow with that action", () => {
  const dir = new URL("../../../.github/workflows/", import.meta.url);
  const files = new Set(readdirSync(dir));
  for (const j of SCHEDULE) {
    assert.ok(files.has(j.workflow), j.workflow);
    const yml = readFileSync(new URL(j.workflow, dir), "utf8");
    assert.match(yml, new RegExp(`options: \\[[^\\]]*\\b${j.action}\\b`), `${j.workflow} has ${j.action}`);
  }
});
