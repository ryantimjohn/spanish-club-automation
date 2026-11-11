"""Tests for the Luma invite logic. Run: python -m unittest -v luma-invites/test_invite.py"""
import pathlib
import sys
import unittest
from datetime import date, datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import invite  # noqa: E402
from invite import TZ, Problem, guard_contacts, is_due, recipients, report, send_day  # noqa: E402

# Mon Nov 16, 2026, 7 PM Eastern is stored by Luma as the next day in UTC.
MON_NOV_16 = {"id": "evt-mon", "name": "St. Stephen's", "start_at": "2026-11-17T00:00:00.000Z",
              "url": "https://luma.com/x"}
LIVE = date(2026, 11, 10)


def et(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=TZ)


class SendDay(unittest.TestCase):
    def test_due_uses_eastern_date(self):
        self.assertEqual(send_day("2026-11-20T00:00:00.000Z"), date(2026, 11, 13))


class IsDue(unittest.TestCase):
    def test_due_on_send_day(self):
        self.assertTrue(is_due(MON_NOV_16, et(2026, 11, 10, 10, 5), {}, LIVE))

    def test_due_not_early(self):
        self.assertFalse(is_due(MON_NOV_16, et(2026, 11, 9, 10, 5), {}, LIVE))

    def test_due_catches_up_missed_day(self):
        self.assertTrue(is_due(MON_NOV_16, et(2026, 11, 11, 10, 5), {}, LIVE))

    def test_due_skips_under_24h(self):
        self.assertFalse(is_due(MON_NOV_16, et(2026, 11, 15, 20, 0), {}, LIVE))

    def test_due_skips_sent_and_sending(self):
        for status in ("sent", "sending"):
            self.assertFalse(is_due(MON_NOV_16, et(2026, 11, 10, 10, 5), {"evt-mon": {"status": status}}, LIVE))

    def test_due_skips_before_live_from(self):
        self.assertFalse(is_due(MON_NOV_16, et(2026, 11, 12, 10, 5), {}, date(2026, 11, 12)))
        self.assertFalse(is_due(MON_NOV_16, et(2026, 11, 10, 10, 5), {}, None))


class Recipients(unittest.TestCase):
    def test_recipients_ignores_case(self):
        contacts = [
            {"id": "usr-1", "email": "a@x.org", "name": "A"},
            {"id": "usr-2", "email": "b@x.org", "name": "B"},
            {"id": "usr-3", "email": "c@x.org", "name": "C"},
            {"id": "usr-4", "email": "C@X.org", "name": "C again"},
            {"id": "usr-5", "email": "", "name": "No email"},
        ]
        out = recipients(contacts, removed_ids={"usr-2"}, guest_emails={"A@X.org".lower()})
        self.assertEqual([r["email"] for r in out], ["c@x.org"])
        self.assertEqual(out[0], {"email": "c@x.org", "name": "C"})


class Guard(unittest.TestCase):
    def test_guard_contacts_refuses_small_list(self):
        with self.assertRaises(Problem):
            guard_contacts([{"id": str(i)} for i in range(5)])
        guard_contacts([{"id": str(i)} for i in range(150)])


class Report(unittest.TestCase):
    def test_report_has_no_emails(self):
        done = [{"name": "St. Stephen's", "start_at": MON_NOV_16["start_at"], "url": "https://luma.com/x",
                 "invited": 700, "skipped": 3}]
        text = report(2, done, ["Luma said no for someone@x.org"])
        self.assertNotIn("@", text)
        self.assertIn("700", text)
        self.assertIn("Mon Nov 16", text)

    def test_report_empty_when_nothing_happened(self):
        self.assertEqual(report(0, [], []), "")


if __name__ == "__main__":
    unittest.main()
