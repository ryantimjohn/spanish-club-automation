"""Tests for the GitHub run check. Run: python -X utf8 -m unittest -v run-check/test_check_runs.py"""
import importlib.util
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


if __name__ == "__main__":
    unittest.main()
