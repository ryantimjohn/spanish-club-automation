"""Tests for the Discord weekly post. Run: python -m unittest -v discord/test_weekly.py"""
import pathlib
import sys
import unittest
from datetime import date, datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import weekly  # noqa: E402
from weekly import TZ, build, missing_nights, safe, span, venue  # noqa: E402

STEPHENS_GEO = {"address": "St. Stephen and the Incarnation Episcopal Church",
                "full_address": "St. Stephen and the Incarnation Episcopal Church, 1525 Newton St NW, "
                                "Washington, DC 20010, USA"}
LIBERATION_GEO = {"address": "DC Liberation Center",
                  "full_address": "DC Liberation Center, 337 H St NE, Washington, DC 20002, USA"}


def ev(day, kind, hours=(19, 0, 20, 30), url="https://luma.com/x"):
    """A Luma event on an Eastern date. Luma stores 7 PM Eastern as the next day in UTC."""
    sh, sm, eh, em = hours
    start = datetime(*day, sh, sm, tzinfo=TZ).astimezone(weekly.timezone.utc)
    end = datetime(*day, eh, em, tzinfo=TZ).astimezone(weekly.timezone.utc)
    e = {"id": f"evt-{day}-{kind}", "start_at": start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
         "end_at": end.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "url": url}
    if kind == "stephens":
        e.update(name="Spanish Club for Socialists - St. Stephen's", geo_address_json=STEPHENS_GEO)
    elif kind == "online":
        e.update(name="Spanish Club for Socialists - Online", geo_address_json=None,
                 meeting_url="https://zoom.us/j/123?pwd=secret")
    elif kind == "liberation":
        e.update(name="Spanish Club for Socialists H Street at the DC Liberation Center",
                 geo_address_json=LIBERATION_GEO)
    return e


def mondays_and_wednesdays(start, weeks, skip=()):
    """A regular season: St. Stephen's on Mondays and Online on Wednesdays."""
    out = []
    d = date(*start)
    for _ in range(weeks):
        for offset, kind, hours in ((0, "stephens", (19, 0, 20, 30)), (2, "online", (19, 0, 19, 45))):
            day = date.fromordinal(d.toordinal() + offset)
            if day not in skip:
                out.append(ev((day.year, day.month, day.day), kind, hours))
        d = date.fromordinal(d.toordinal() + 7)
    return out


class Week(unittest.TestCase):
    def test_nov_13_post_matches_the_approved_draft(self):
        events = mondays_and_wednesdays((2026, 10, 26), 6) + [ev((2026, 11, 19), "liberation", url="https://luma.com/lib")]
        kind, text = build(events, date(2026, 11, 13))
        self.assertEqual(kind, "week")
        self.assertEqual(text, "**Next week at Spanish Club**\n\n"
                         "**Mon Nov 16** · 7:00–8:30 PM · St. Stephen's, 1525 Newton St NW (side door) · [RSVP](https://luma.com/x)\n"
                         "**Wed Nov 18** · 7:00–7:45 PM · Online on Zoom · [RSVP](https://luma.com/x)\n"
                         "**Thu Nov 19** · 7:00–8:30 PM · DC Liberation Center, 337 H St NE · [RSVP](https://luma.com/lib)")

    def test_never_shows_the_zoom_link(self):
        _, text = build(mondays_and_wednesdays((2026, 11, 2), 4), date(2026, 11, 13))
        self.assertNotIn("zoom.us", text)
        self.assertNotIn("pwd", text)

    def test_missing_online_night_is_mentioned(self):
        events = mondays_and_wednesdays((2026, 11, 2), 6, skip={date(2026, 11, 25)})
        kind, text = build(events, date(2026, 11, 20))
        self.assertEqual(kind, "week")
        self.assertTrue(text.endswith("[RSVP](https://luma.com/x)\n\nNo Online session Wed Nov 25."))
        self.assertEqual(text.count("**Mon Nov 23**"), 1)
        self.assertNotIn("Wed Nov 25**", text)

    def test_missing_monday_is_mentioned(self):
        events = mondays_and_wednesdays((2027, 1, 4), 4, skip={date(2027, 1, 18)})
        _, text = build(events, date(2027, 1, 15))
        self.assertIn("No St. Stephen's night Mon Jan 18.", text)

    def test_no_missing_line_at_season_end(self):
        # Online's last night is the Wednesday before; nothing after it, so nothing is "missing".
        events = mondays_and_wednesdays((2027, 4, 26), 2) + [ev((2027, 5, 10), "stephens")]
        self.assertEqual(missing_nights(events, date(2027, 5, 10)), [])

    def test_no_missing_line_at_season_start(self):
        events = [ev((2026, 11, 2), "stephens")] + mondays_and_wednesdays((2026, 11, 9), 3)
        self.assertEqual(missing_nights(events, date(2026, 11, 2)), [])

    def test_event_on_the_weekend_after_is_in_the_week(self):
        events = mondays_and_wednesdays((2026, 11, 9), 3) + [ev((2026, 11, 22), "liberation", hours=(14, 0, 15, 30))]
        _, text = build(events, date(2026, 11, 13))
        self.assertIn("**Sun Nov 22** · 2:00–3:30 PM · DC Liberation Center", text)

    def test_times_across_noon(self):
        events = mondays_and_wednesdays((2026, 11, 9), 3) + [ev((2026, 11, 21), "liberation", hours=(11, 30, 13, 0))]
        _, text = build(events, date(2026, 11, 13))
        self.assertIn("11:30 AM–1:00 PM", text)


class Break(unittest.TestCase):
    def setUp(self):
        self.events = mondays_and_wednesdays((2026, 11, 30), 3) + mondays_and_wednesdays((2027, 1, 4), 3)

    def test_break_note_the_friday_before(self):
        kind, text = build(self.events, date(2026, 12, 18))
        self.assertEqual(kind, "break")
        self.assertEqual(text, "No Spanish Club Dec 21 – Jan 3. We're back Mon Jan 4 at St. Stephen's!")

    def test_skips_the_friday_after(self):
        self.assertEqual(build(self.events, date(2026, 12, 25)), ("break-continues", None))

    def test_back_to_normal_after_the_break(self):
        kind, text = build(self.events, date(2027, 1, 1))
        self.assertEqual(kind, "week")
        self.assertIn("**Mon Jan 4**", text)

    def test_back_online(self):
        events = [ev((2027, 3, 1), "stephens"), ev((2027, 3, 17), "online", (19, 0, 19, 45))]
        kind, text = build(events, date(2027, 3, 5))
        self.assertEqual(kind, "break")
        self.assertEqual(text, "No Spanish Club Mar 8–16. We're back Wed Mar 17 on Zoom!")

    def test_nothing_ahead(self):
        self.assertEqual(build(mondays_and_wednesdays((2027, 4, 26), 2), date(2027, 5, 14)), ("nothing-ahead", None))
        self.assertEqual(build([], date(2027, 5, 14)), ("nothing-ahead", None))


class Helpers(unittest.TestCase):
    def test_span(self):
        self.assertEqual(span(date(2026, 12, 21), date(2027, 1, 3)), "Dec 21 – Jan 3")
        self.assertEqual(span(date(2027, 3, 8), date(2027, 3, 14)), "Mar 8–14")

    def test_unknown_venue_uses_luma_address(self):
        e = ev((2026, 11, 21), "liberation")
        e["geo_address_json"] = {"address": "Some Hall", "full_address": "Some Hall, 100 K St NW, Washington, DC 20001, USA"}
        self.assertEqual(venue(e), "Some Hall, 100 K St NW")

    def test_patchwork(self):
        e = ev((2027, 2, 2), "liberation")
        e["geo_address_json"] = {"address": "Patchwork", "full_address": "1901 18th St NW, Washington, DC 20009, USA"}
        self.assertEqual(venue(e), "Patchwork, 1901 18th St NW")

    def test_safe_hides_the_webhook(self):
        weekly._SECRETS[:] = ["https://discord.com/api/webhooks/123/abcDEF-ghi", "abcDEF-ghi"]
        try:
            msg = safe("failed: https://discord.com/api/webhooks/123/abcDEF-ghi?wait=true and abcDEF-ghi")
            self.assertNotIn("abcDEF-ghi", msg)
        finally:
            weekly._SECRETS[:] = []


class Due(unittest.TestCase):
    def test_due_only_on_friday_from_9(self):
        live = date(2026, 11, 13)
        self.assertTrue(weekly.is_due(datetime(2026, 11, 13, 9, 7, tzinfo=TZ), {}, live))
        self.assertFalse(weekly.is_due(datetime(2026, 11, 13, 8, 7, tzinfo=TZ), {}, live))
        self.assertFalse(weekly.is_due(datetime(2026, 11, 12, 10, 0, tzinfo=TZ), {}, live))

    def test_not_before_live_from_or_when_off(self):
        self.assertFalse(weekly.is_due(datetime(2026, 11, 13, 9, 7, tzinfo=TZ), {}, date(2026, 11, 20)))
        self.assertFalse(weekly.is_due(datetime(2026, 11, 13, 9, 7, tzinfo=TZ), {}, None))

    def test_never_twice(self):
        for status in ("posted", "posting", "skipped"):
            self.assertFalse(weekly.is_due(datetime(2026, 11, 13, 10, 7, tzinfo=TZ),
                                           {"2026-11-13": {"status": status}}, date(2026, 11, 13)))


if __name__ == "__main__":
    unittest.main()
