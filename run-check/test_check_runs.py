"""Tests for the GitHub run check. Run: python -X utf8 -m unittest -v run-check/test_check_runs.py"""
import contextlib
import importlib.util
import io
import pathlib
import unittest
from datetime import date, datetime

_spec = importlib.util.spec_from_file_location("check_runs", pathlib.Path(__file__).resolve().parent / "check_runs.py")
cr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cr)

JOB = {j.name: j for j in cr.JOBS}
THU = date(2026, 11, 12)   # Eastern standard time (UTC-5)
FRI = date(2026, 11, 13)
AT_1230 = datetime(2026, 11, 12, 12, 30, tzinfo=cr.TZ)


def run(utc, event="schedule", conclusion="success", status="completed"):
    return {"event": event, "status": status, "conclusion": conclusion, "created_at": utc,
            "html_url": f"https://github.com/x/runs/{utc}"}


class Schedule(unittest.TestCase):
    def test_expand(self):
        self.assertEqual(cr.expand("12-17", 0, 23), set(range(12, 18)))
        self.assertEqual(cr.expand("14,15", 0, 23), {14, 15})
        self.assertEqual(cr.expand("*/30", 0, 59), {0, 30})

    def test_slots_are_in_eastern_and_follow_weekday(self):
        t = cr.slots("3 14,15 * * *", THU)
        self.assertEqual([x.strftime("%H:%M") for x in t], ["09:03", "10:03"])
        self.assertEqual(cr.slots("7 13,14,15,16 * * 5", THU), [])
        self.assertEqual(len(cr.slots("7 13,14,15,16 * * 5", FRI)), 4)

    def test_summer_time(self):
        t = cr.slots("3 14,15 * * *", date(2026, 7, 1))
        self.assertEqual([x.strftime("%H:%M") for x in t], ["10:03", "11:03"])

    def test_every_job_matches_a_workflow_with_a_schedule(self):
        files = {p.name for p in cr.WORKFLOWS.glob("*.yml") if cr.crons(p)}
        self.assertEqual(files, {j.file for j in cr.JOBS})
        for j in cr.JOBS:
            on_days = [d for d in range(7) if d in j.days]
            day = THU if 3 in on_days else FRI
            slots = cr.expected(j, day)
            self.assertTrue(any(datetime.combine(day, j.start, cr.TZ) <= s <= datetime.combine(day, j.deadline, cr.TZ)
                                for s in slots), f"{j.name} has no scheduled time inside its window")


class Judge(unittest.TestCase):
    def test_ok_in_window(self):
        s, d = cr.judge(JOB["Luma invites"], [run("2026-11-12T15:04:00Z")], THU, AT_1230)
        self.assertEqual(s, "OK")
        self.assertIn("10:04 AM", d)

    def test_run_before_send_hour_does_not_count(self):
        s, _ = cr.judge(JOB["Luma invites"], [run("2026-11-12T14:04:00Z")], THU, AT_1230)
        self.assertEqual(s, "MISSED")

    def test_missed_when_nothing_ran(self):
        self.assertEqual(cr.judge(JOB["Instagram"], [], THU, AT_1230)[0], "MISSED")

    def test_waiting_before_deadline(self):
        early = datetime(2026, 11, 12, 9, 30, tzinfo=cr.TZ)
        self.assertEqual(cr.judge(JOB["Luma invites"], [], THU, early)[0], "WAITING")

    def test_late(self):
        later = datetime(2026, 11, 12, 17, 0, tzinfo=cr.TZ)
        s, _ = cr.judge(JOB["Meetup"], [run("2026-11-12T21:27:00Z")], THU, later)
        self.assertEqual(s, "LATE")

    def test_failed(self):
        s, d = cr.judge(JOB["Eventbrite"], [run("2026-11-12T14:36:00Z", conclusion="failure")], THU, AT_1230)
        self.assertEqual(s, "FAILED")
        self.assertIn("1 failed", d)

    def test_ok_with_an_earlier_failure(self):
        runs = [run("2026-11-12T13:08:00Z", conclusion="failure"), run("2026-11-12T14:08:00Z")]
        self.assertEqual(cr.judge(JOB["Instagram"], runs, THU, AT_1230)[0], "OK, with failures")

    def test_running(self):
        r = run("2026-11-12T15:04:00Z", status="in_progress", conclusion=None)
        self.assertEqual(cr.judge(JOB["Luma invites"], [r], THU, AT_1230)[0], "RUNNING")

    def test_manual_runs_do_not_count_but_are_noted(self):
        s, d = cr.judge(JOB["Instagram"], [run("2026-11-12T15:05:00Z", event="workflow_dispatch")], THU, AT_1230)
        self.assertEqual(s, "MISSED")
        self.assertIn("1 started by hand", d)

    def test_other_days_are_ignored(self):
        s, _ = cr.judge(JOB["Luma invites"], [run("2026-11-11T15:04:00Z")], THU, AT_1230)
        self.assertEqual(s, "MISSED")

    def test_latest_day(self):
        self.assertEqual(cr.latest_day(AT_1230), THU)
        self.assertEqual(cr.latest_day(datetime(2026, 11, 13, 8, 0, tzinfo=cr.TZ)), THU)

    def test_discord_only_on_fridays(self):
        self.assertEqual(cr.judge(JOB["Discord"], [], THU, AT_1230)[0], "OFF")
        self.assertEqual(cr.judge(JOB["Discord"], [], FRI, datetime(2026, 11, 13, 12, 30, tzinfo=cr.TZ))[0], "MISSED")


# Days with the Cloudflare timer's current times (live since Nov 16, 2026).
THU2 = date(2026, 11, 19)
FRI2 = date(2026, 11, 20)
THU2_1230 = datetime(2026, 11, 19, 12, 30, tzinfo=cr.TZ)


def hhmm(times):
    return [t.strftime("%H:%M") for t in times]


class TimerSchedule(unittest.TestCase):
    def test_reads_the_timer_times(self):
        ig = hhmm(cr.timer_slots(JOB["Instagram"], THU2))
        self.assertEqual(len(ig), 14)
        self.assertEqual(ig[0], "08:07")
        self.assertEqual(ig[-3:], ["21:07", "21:37", "22:07"])
        self.assertEqual(hhmm(cr.timer_slots(JOB["Luma invites"], THU2)), ["10:07", "12:07", "15:07"])
        self.assertEqual(hhmm(cr.timer_slots(JOB["Eventbrite"], THU2)), ["10:37"])

    def test_discord_timer_only_on_fridays(self):
        self.assertEqual(cr.timer_slots(JOB["Discord"], THU2), [])
        self.assertEqual(hhmm(cr.timer_slots(JOB["Discord"], FRI2)), ["09:07", "10:07", "12:07"])

    def test_days_before_the_timer_have_no_timer_starts(self):
        self.assertEqual(cr.timer_slots(JOB["Luma invites"], THU), [])

    def test_every_timer_workflow_is_a_job(self):
        self.assertTrue(cr.timer_schedule())
        self.assertLessEqual(set(cr.timer_schedule()), {j.file for j in cr.JOBS})

    def test_schedule_lists_the_timer_times(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cr.main(["--schedule", "--date", "2026-11-19"]), 0)
        self.assertIn("the timer starts it at 10:07 AM, 12:07 PM, 3:07 PM", out.getvalue())


class JudgeWithTimer(unittest.TestCase):
    def test_timer_start_counts_as_on_time(self):
        runs = [run("2026-11-19T15:07:49Z", event="workflow_dispatch"),
                run("2026-11-19T17:07:48Z", event="workflow_dispatch")]
        s, d = cr.judge(JOB["Luma invites"], runs, THU2, THU2_1230)
        self.assertEqual(s, "OK")
        self.assertIn("timer started it at: 10:07 AM, 12:07 PM", d)

    def test_missed_timer_start_is_flagged_even_when_ok(self):
        # At 12:30 PM the 12:07 start is overdue; the 3:07 PM one hasn't come yet.
        s, d = cr.judge(JOB["Luma invites"], [run("2026-11-19T15:07:49Z", event="workflow_dispatch")], THU2, THU2_1230)
        self.assertEqual(s, "OK, timer missed 1")
        self.assertIn("timer missed 12:07 PM", d)
        self.assertNotIn("3:07 PM", d)

    def test_check_in_start_is_not_the_timer(self):
        # The 9:40 AM check-in started Eventbrite, but the timer's 10:37 start never came.
        s, d = cr.judge(JOB["Eventbrite"], [run("2026-11-19T14:40:14Z", event="workflow_dispatch")], THU2, THU2_1230)
        self.assertEqual(s, "MISSED")
        self.assertIn("1 started by hand or by the check-in", d)
        self.assertIn("timer missed 10:37 AM", d)

    def test_github_schedule_still_counts(self):
        runs = [run("2026-11-19T15:37:48Z", event="workflow_dispatch"), run("2026-11-19T21:27:00Z")]
        s, d = cr.judge(JOB["Meetup"], runs, THU2, datetime(2026, 11, 19, 17, 0, tzinfo=cr.TZ))
        self.assertEqual(s, "OK")
        self.assertIn("GitHub's schedule ran at: 4:27 PM", d)

    def test_which_statuses_need_a_look(self):
        for s in ("OK", "RUNNING", "WAITING", "OFF"):
            self.assertFalse(cr.needs_look(s), s)
        for s in ("MISSED", "LATE", "FAILED", "OK, with failures", "OK, timer missed 1"):
            self.assertTrue(cr.needs_look(s), s)


if __name__ == "__main__":
    unittest.main()
