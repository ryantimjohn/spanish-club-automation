"""Tests for the Meetup sync logic. Run: python -m unittest -v meetup/test_sync.py"""
import pathlib
import sys
import unittest
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sync import TZ, FOLLOW, duration_iso, fingerprint, html_description, kind_of, meetup_fields, plan, start_local  # noqa: E402

ZOOM = "https://us02web.zoom.us/j/83064162001?pwd=SECRETSECRET.1"
ONLINE = {"id": "evt-on", "name": "Spanish Club for Socialists - Online", "url": "https://luma.com/on",
          "start_at": "2026-11-19T00:00:00.000Z", "end_at": "2026-11-19T00:45:00.000Z", "meeting_url": ZOOM,
          "description_md": f"[{ZOOM}]({ZOOM})\n\nHey friends. Join us on Zoom.\n\n"
                            "Keep practicing between sessions on our Discord: [discord.gg/x](https://discord.gg/x)"}
STS = {"id": "evt-st", "name": "Spanish Club for Socialists - St. Stephen's", "url": "https://luma.com/st",
       "start_at": "2026-11-17T00:00:00.000Z", "end_at": "2026-11-17T01:30:00.000Z",
       "description_md": "Event is from 7:00 pm to 8:30 pm at St Stephen's:\n\n1525 Newton St NW\n\n"
                         "[Open in Google Maps](https://maps.example/x)\n\nWe accept Venmo <donations> & thanks."}
LIB = {"id": "evt-lib", "name": "Spanish Club for Socialists H Street at the DC Liberation Center",
       "start_at": "2026-11-20T00:00:00.000Z", "end_at": "2026-11-20T01:30:00.000Z", "description_md": "Hi"}
NOW = datetime(2026, 11, 10, 22, 0, tzinfo=TZ)


class Fields(unittest.TestCase):
    def test_kind_of(self):
        self.assertEqual(kind_of(ONLINE), "Online")
        self.assertEqual(kind_of(STS), "St. Stephen's")
        self.assertEqual(kind_of(LIB), "Liberation Center")
        self.assertIsNone(kind_of({"name": "Board meeting"}))

    def test_start_is_eastern_local(self):
        self.assertEqual(start_local("2026-11-17T00:00:00.000Z"), "2026-11-16T19:00")

    def test_duration(self):
        self.assertEqual(duration_iso(STS), "PT1H30M")
        self.assertEqual(duration_iso(ONLINE), "PT45M")

    def test_online_link_never_in_description(self):
        f = meetup_fields(ONLINE)
        self.assertNotIn("zoom.us", f["description"])
        self.assertNotIn("SECRET", f["description"])
        self.assertEqual(f["venue"], "online")
        self.assertEqual(f["online_url"], ZOOM)

    def test_description_html(self):
        html = html_description(STS["description_md"])
        self.assertIn('<a href="https://maps.example/x">Open in Google Maps</a>', html)
        self.assertIn("&lt;donations&gt; &amp; thanks", html)
        self.assertTrue(html.rstrip().endswith("</p>"))
        self.assertIn("luma.com/spanishclubforsocialists", html)
        self.assertIn(FOLLOW.split(":")[0], html)

    def test_title_is_lumas_word_for_word(self):
        self.assertEqual(meetup_fields(LIB)["title"], LIB["name"])

    def test_fingerprint_changes_with_time_not_order(self):
        a = fingerprint(meetup_fields(STS))
        moved = dict(STS, start_at="2026-11-18T00:00:00.000Z", end_at="2026-11-18T01:30:00.000Z")
        self.assertEqual(a, fingerprint(meetup_fields(dict(STS))))
        self.assertNotEqual(a, fingerprint(meetup_fields(moved)))


class Plan(unittest.TestCase):
    def kinds(self, actions):
        return [(a, e if isinstance(e, str) else e["id"]) for a, e in actions]

    def test_creates_inside_five_weeks_only(self):
        far = dict(STS, id="evt-far", start_at="2026-12-29T00:00:00.000Z", end_at="2026-12-29T01:30:00.000Z")
        acts = self.kinds(plan([STS, far], {}, NOW, lambda e: True))
        self.assertIn(("create", "evt-st"), acts)
        self.assertNotIn(("create", "evt-far"), acts)

    def test_waits_for_flyer(self):
        self.assertEqual(self.kinds(plan([STS], {}, NOW, lambda e: False)), [("wait_flyer", "evt-st")])

    def test_update_when_luma_changed_and_adopts_hand_made(self):
        same = {"evt-st": {"meetup_event_id": "1", "fingerprint": fingerprint(meetup_fields(STS)), "start": STS["start_at"]}}
        self.assertEqual(plan([STS], same, NOW, lambda e: True), [])
        hand = {"evt-st": {"meetup_event_id": "1", "status": "draft", "start": STS["start_at"]}}
        self.assertEqual(self.kinds(plan([STS], hand, NOW, lambda e: True)), [("update", "evt-st")])

    def test_cancel_when_gone_from_luma(self):
        mapping = {"evt-gone": {"meetup_event_id": "9", "start": "2026-11-20T00:00:00.000Z"},
                   "evt-old": {"meetup_event_id": "8", "start": "2026-11-01T00:00:00.000Z"},
                   "evt-done": {"meetup_event_id": "7", "start": "2026-11-21T00:00:00.000Z", "status": "cancelled"}}
        self.assertEqual(self.kinds(plan([], mapping, NOW, lambda e: True)), [("cancel", "evt-gone")])

    def test_skips_unknown_and_past(self):
        past = dict(STS, id="evt-past", start_at="2026-11-01T00:00:00.000Z", end_at="2026-11-01T01:30:00.000Z")
        other = dict(STS, id="evt-x", name="Board meeting")
        self.assertEqual(self.kinds(plan([past, other], {}, NOW, lambda e: True)), [("skip", "evt-x")])


if __name__ == "__main__":
    unittest.main()
