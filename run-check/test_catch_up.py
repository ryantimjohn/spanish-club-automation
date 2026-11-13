"""Tests for the daily check-in. Run: python -X utf8 -m unittest -v run-check/test_catch_up.py"""
import importlib.util
import pathlib
import unittest
from datetime import date, datetime
from types import SimpleNamespace

_spec = importlib.util.spec_from_file_location("catch_up", pathlib.Path(__file__).resolve().parent / "catch_up.py")
cu = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cu)

TZ = cu.TZ
FRI_1230 = datetime(2026, 11, 13, 12, 30, tzinfo=TZ)
CAROUSEL = {"id": "2026-11-13-carousel", "what": "Weekly carousel", "post_at": "2026-11-13T09:00",
            "fingerprint": "f1", "events": {}}


def with_records(records):
    cu.on_github = lambda path, default: records.get(path, default)


POST = SimpleNamespace(
    FB_LIVE_FROM=None,
    ig_deadline=lambda item: cu.et(item["post_at"]).replace(hour=19),
    fb_due=lambda *a: ([], []))


class Instagram(unittest.TestCase):
    def manifest(self, approved=True):
        return {"manifest.json": {"items": [CAROUSEL], "approved": {CAROUSEL["id"]: "f1" if approved else "old"}}}

    def test_due_and_not_posted_starts_the_job(self):
        with_records(self.manifest())
        lines, pending, needs = cu.instagram(FRI_1230, POST)
        self.assertTrue(pending)
        self.assertEqual(needs, [])
        self.assertIn("hasn't posted yet", lines[0])

    def test_posted_is_reported_with_its_link(self):
        rec = self.manifest()
        rec["log/posted.json"] = {CAROUSEL["id"]: {"status": "posted", "at": "2026-11-13T12:45-05:00",
                                                   "permalink": "https://instagram.com/p/x"}}
        with_records(rec)
        lines, pending, needs = cu.instagram(FRI_1230, POST)
        self.assertFalse(pending)
        self.assertIn("posted at 12:45 PM https://instagram.com/p/x", lines[0])

    def test_too_late_needs_the_organizer(self):
        with_records(self.manifest())
        lines, pending, needs = cu.instagram(datetime(2026, 11, 13, 19, 30, tzinfo=TZ), POST)
        self.assertFalse(pending)
        self.assertTrue(needs)

    def test_not_yet_due_and_unapproved(self):
        with_records(self.manifest())
        self.assertFalse(cu.instagram(datetime(2026, 11, 13, 8, 0, tzinfo=TZ), POST)[1])
        with_records(self.manifest(approved=False))
        lines, pending, needs = cu.instagram(FRI_1230, POST)
        self.assertFalse(pending)
        self.assertIn("isn't approved", lines[0])

    def test_marked_missed_needs_the_organizer(self):
        rec = self.manifest()
        rec["log/posted.json"] = {CAROUSEL["id"]: {"status": "missed", "at": "2026-11-13T19:10-05:00"}}
        with_records(rec)
        self.assertTrue(cu.instagram(FRI_1230, POST)[2])


class Discord(unittest.TestCase):
    weekly = SimpleNamespace(LIVE_FROM=date(2026, 11, 13), POST_HOUR=9, CHANNEL_ID="c",
                             is_due=lambda now, posted, lf: now.hour >= 9 and now.date().isoformat() not in posted)

    def test_friday_without_a_post_starts_the_job(self):
        with_records({})
        self.assertTrue(cu.discord(FRI_1230, self.weekly)[1])

    def test_posted_has_a_link(self):
        with_records({"discord/posted.json": {"2026-11-13": {"status": "posted", "at": "2026-11-13T12:44-05:00",
                                                              "message_id": "m1"}}})
        lines, pending, _ = cu.discord(FRI_1230, self.weekly)
        self.assertFalse(pending)
        self.assertTrue(lines[0].endswith("/c/m1"))

    def test_other_days_say_nothing(self):
        with_records({})
        self.assertEqual(cu.discord(datetime(2026, 11, 12, 12, 30, tzinfo=TZ), self.weekly), ([], False, []))


class Invites(unittest.TestCase):
    EVENT = {"id": "evt-lib", "name": "Liberation Center", "start_at": "2026-11-20T00:00:00.000Z"}

    def invite(self):
        return SimpleNamespace(LIVE_FROM=date(2026, 11, 11), SEND_HOUR=10,
                               paginate=lambda path: [{"event": self.EVENT}],
                               nice=lambda s: "Thu Nov 19",
                               is_due=lambda e, now, sent, lf: e["id"] not in sent)

    def test_due_after_ten_starts_the_job(self):
        with_records({})
        lines, pending, _ = cu.invites(FRI_1230, self.invite())
        self.assertTrue(pending)
        self.assertIn("not sent yet", lines[0])

    def test_before_ten_waits(self):
        with_records({})
        self.assertFalse(cu.invites(datetime(2026, 11, 13, 9, 30, tzinfo=TZ), self.invite())[1])

    def test_sent_today_is_reported(self):
        with_records({"luma-invites/sent.json": {"evt-lib": {"status": "sent", "at": "2026-11-13T12:44-05:00",
                                                             "invited": 757}}})
        lines, pending, _ = cu.invites(FRI_1230, self.invite())
        self.assertFalse(pending)
        self.assertIn("757 invited", lines[0])


class Sync(unittest.TestCase):
    mod = SimpleNamespace(LIVE_FROM=date(2026, 11, 11))

    def run_(self, conclusion, status="completed"):
        return {"event": "schedule", "status": status, "conclusion": conclusion, "createdAt": "2026-11-13T15:00:00Z"}

    def test_synced_today(self):
        self.assertEqual(cu.sync_job("Eventbrite", FRI_1230, self.mod, [self.run_("success")], "off")[1], False)

    def test_no_run_starts_the_job(self):
        self.assertTrue(cu.sync_job("Eventbrite", FRI_1230, self.mod, [], "off")[1])

    def test_failed_run_needs_the_organizer_not_a_restart(self):
        lines, pending, needs = cu.sync_job("Eventbrite", FRI_1230, self.mod, [self.run_("failure")], "off")
        self.assertFalse(pending)
        self.assertTrue(needs)

    def test_off(self):
        off = SimpleNamespace(LIVE_FROM=None)
        self.assertEqual(cu.sync_job("Meetup", FRI_1230, off, [], "off until approved."),
                         (["Meetup: off until approved."], False, []))

    def test_busy(self):
        self.assertTrue(cu.busy([self.run_(None, status="in_progress")]))
        self.assertFalse(cu.busy([self.run_("success")]))


if __name__ == "__main__":
    unittest.main()
