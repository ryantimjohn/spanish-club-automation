"""Tests for the Eventbrite sync logic. Run: python -m unittest -v eventbrite/test_sync.py"""
import importlib.util
import pathlib
import struct
import unittest
from datetime import datetime

_spec = importlib.util.spec_from_file_location("eventbrite_sync", pathlib.Path(__file__).resolve().parent / "sync.py")
sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync)

ZOOM = "https://us02web.zoom.us/j/83064162001?pwd=SECRETSECRET.1"
ONLINE = {"id": "evt-on", "name": "Spanish Club for Socialists - Online", "url": "https://luma.com/on",
          "start_at": "2026-11-19T00:00:00.000Z", "end_at": "2026-11-19T00:45:00.000Z", "meeting_url": ZOOM,
          "description_md": f"[{ZOOM}]({ZOOM})\n\nHey friends. Join us on Zoom.\n\n"
                            "Keep practicing between sessions on our Discord: [discord.gg/x](https://discord.gg/x)"}
STS_GEO = {"full_address": "St. Stephen and the Incarnation Episcopal Church, 1525 Newton St NW, Washington, DC 20010, USA",
           "google_maps_place_id": "ChIJplace"}
STS = {"id": "evt-st", "name": "Spanish Club for Socialists - St. Stephen's", "url": "https://luma.com/st",
       "start_at": "2026-11-17T00:00:00.000Z", "end_at": "2026-11-17T01:30:00.000Z", "geo_address_json": STS_GEO,
       "description_md": "Event is from 7:00 pm to 8:30 pm at St Stephen's:\n\n1525 Newton St NW\n\n"
                         "[Open in Google Maps](https://maps.example/x)\n\nWe accept Venmo <donations> & thanks."}
LIB = {"id": "evt-lib", "name": "Spanish Club for Socialists H Street at the DC Liberation Center",
       "start_at": "2026-11-20T00:00:00.000Z", "end_at": "2026-11-20T01:30:00.000Z",
       "geo_address_json": {"full_address": "DC Liberation Center, 337 H St NE, Washington, DC 20002, USA"},
       "description_md": "Please help support the space! You can donate at their website below:  \n"
                         "[https://share.google/abc](https://share.google/abc)"}
NOW = datetime(2026, 11, 10, 22, 0, tzinfo=sync.TZ)
ALWAYS = lambda e: True  # noqa: E731


class Fields(unittest.TestCase):
    def test_kind_of(self):
        self.assertEqual(sync.kind_of(ONLINE), "Online")
        self.assertEqual(sync.kind_of(STS), "St. Stephen's")
        self.assertEqual(sync.kind_of(LIB), "Liberation Center")
        self.assertIsNone(sync.kind_of({"name": "Board meeting"}))

    def test_times_are_utc_with_the_right_image_date(self):
        f = sync.eventbrite_fields(STS)
        self.assertEqual((f["start"], f["end"]), ("2026-11-17T00:00:00Z", "2026-11-17T01:30:00Z"))
        self.assertEqual(f["image"], "2026-11-16-st-stephens.png")  # Monday night in DC

    def test_online_link_only_on_the_online_page(self):
        f = sync.eventbrite_fields(ONLINE)
        for public in (f["description"], f["summary"], f["title"]):
            self.assertNotIn("zoom.us", public)
            self.assertNotIn("SECRET", public)
        self.assertTrue(f["online"])
        self.assertEqual(f["online_url"], ZOOM)
        self.assertIn(ZOOM.replace("&", "&amp;"), sync.online_page_html(ZOOM))
        self.assertIsNone(sync.eventbrite_fields(STS)["online_url"])

    def test_passcode_lines_never_copied(self):
        self.assertNotIn("123456", sync.html_description("Hi\n\nPasscode: 123456"))

    def test_description_html(self):
        h = sync.eventbrite_fields(STS)["description"]
        self.assertIn('<a href="https://maps.example/x">Open in Google Maps</a>', h)
        self.assertIn("&lt;donations&gt; &amp; thanks", h)
        self.assertLess(h.index("Google Maps"), h.index("side door"))
        self.assertLess(h.index("side door"), h.index("Venmo"))
        self.assertTrue(h.endswith("spanishclubforsocialists</a></p>"))
        self.assertIn(sync.FOLLOW.split(":")[0], h)
        self.assertNotIn("<br", h)

    def test_side_door_first_when_there_is_no_map_link(self):
        h = sync.html_description("This week's conversation guide:  \n[Guía](https://docs.google.com/x)\n\nAbout",
                                  "Please come in the side door!")
        self.assertTrue(h.startswith("<p>Please come in the side door!</p>"))

    def test_line_breaks_and_markdown_link_with_url_text(self):
        h = sync.eventbrite_fields(LIB)["description"]
        self.assertIn("below:</p><p><a href=\"https://share.google/abc\">https://share.google/abc</a></p>", h)
        self.assertNotIn("side door", h)

    def test_title_is_lumas_word_for_word(self):
        self.assertEqual(sync.eventbrite_fields(LIB)["title"], LIB["name"])
        acct = {"organizer_id": "o1", "venues": {"st-stephens": "v1"}}
        self.assertEqual(sync.event_body(sync.eventbrite_fields(STS), acct)["event"]["name"]["html"], STS["name"])

    def test_event_body(self):
        acct = {"organizer_id": "o1", "venues": {"st-stephens": "v1"}}
        ev = sync.event_body(sync.eventbrite_fields(STS), acct, logo_id="L")["event"]
        self.assertEqual((ev["venue_id"], ev["logo_id"], ev["online_event"], ev["currency"]), ("v1", "L", False, "USD"))
        self.assertEqual(ev["start"], {"timezone": "America/New_York", "utc": "2026-11-17T00:00:00Z"})
        on = sync.event_body(sync.eventbrite_fields(ONLINE), acct)["event"]
        self.assertTrue(on["online_event"])
        self.assertNotIn("venue_id", on)
        self.assertNotIn("logo_id", on)
        self.assertLessEqual(len(on["summary"]), 140)
        tc = sync.ticket_body(sync.eventbrite_fields(STS))["ticket_class"]
        self.assertTrue(tc["free"])
        self.assertEqual(tc["sales_end"], "2026-11-17T01:30:00Z")

    def test_place_must_match(self):
        self.assertTrue(sync.place_ok(STS) and sync.place_ok(LIB) and sync.place_ok(ONLINE))
        self.assertFalse(sync.place_ok(dict(STS, geo_address_json={"full_address": "1506 19th St NW"})))

    def test_fingerprint_changes_with_time_not_order(self):
        a = sync.fingerprint(sync.eventbrite_fields(STS))
        moved = dict(STS, start_at="2026-11-18T00:00:00.000Z", end_at="2026-11-18T01:30:00.000Z")
        self.assertEqual(a, sync.fingerprint(sync.eventbrite_fields(dict(STS))))
        self.assertNotEqual(a, sync.fingerprint(sync.eventbrite_fields(moved)))

    def test_crop_mask_is_centred_two_to_one(self):
        png = b"\x89PNG\r\n\x1a\n" + b"\0\0\0\rIHDR" + struct.pack(">II", 1920, 1080)
        self.assertEqual(sync.crop_mask(png), {"top_left": {"x": 0, "y": 60}, "width": 1920, "height": 960})
        tall = b"\x89PNG\r\n\x1a\n" + b"\0\0\0\rIHDR" + struct.pack(">II", 2400, 1000)
        self.assertEqual(sync.crop_mask(tall), {"top_left": {"x": 200, "y": 0}, "width": 2000, "height": 1000})

    def test_multipart_puts_the_file_last(self):
        body, ctype = sync.multipart({"key": "a/b", "policy": "p"}, "file", "x.png", b"PNGDATA")
        self.assertTrue(ctype.startswith("multipart/form-data; boundary="))
        self.assertLess(body.index(b'name="policy"'), body.index(b'name="file"'))
        self.assertIn(b"PNGDATA", body)


class Plan(unittest.TestCase):
    def kinds(self, actions):
        return [(a, e if isinstance(e, str) else e["id"]) for a, e in actions]

    def test_creates_inside_five_weeks_only(self):
        far = dict(STS, id="evt-far", start_at="2026-12-29T00:00:00.000Z", end_at="2026-12-29T01:30:00.000Z")
        acts = self.kinds(sync.plan([STS, far, ONLINE], {}, NOW, ALWAYS))
        self.assertEqual(acts, [("create", "evt-st"), ("create", "evt-on")])

    def test_waits_for_flyer_and_place(self):
        self.assertEqual(self.kinds(sync.plan([STS], {}, NOW, lambda e: False)), [("wait_flyer", "evt-st")])
        wrong = dict(STS, geo_address_json={"full_address": "somewhere else"})
        self.assertEqual(self.kinds(sync.plan([wrong], {}, NOW, ALWAYS)), [("wait_place", "evt-st")])

    def test_update_only_when_luma_changed_or_unfinished(self):
        fp = sync.fingerprint(sync.eventbrite_fields(STS))
        same = {"evt-st": {"eventbrite_id": "1", "fingerprint": fp, "start": STS["start_at"], "status": "published"}}
        self.assertEqual(sync.plan([STS], same, NOW, ALWAYS), [])
        half = {"evt-st": dict(same["evt-st"], status="incomplete")}
        self.assertEqual(self.kinds(sync.plan([STS], half, NOW, ALWAYS)), [("update", "evt-st")])
        old = {"evt-st": dict(same["evt-st"], fingerprint="old")}
        self.assertEqual(self.kinds(sync.plan([STS], old, NOW, ALWAYS)), [("update", "evt-st")])

    def test_listed_event_follows_luma_past_the_window(self):
        moved = dict(STS, start_at="2027-01-05T00:00:00.000Z", end_at="2027-01-05T01:30:00.000Z")
        listed = {"evt-st": {"eventbrite_id": "1", "fingerprint": "old", "start": STS["start_at"]}}
        self.assertEqual(self.kinds(sync.plan([moved], listed, NOW, ALWAYS)), [("update", "evt-st")])

    def test_cancel_when_gone_from_luma(self):
        mapping = {"evt-gone": {"eventbrite_id": "9", "start": "2026-11-20T00:00:00.000Z"},
                   "evt-old": {"eventbrite_id": "8", "start": "2026-11-01T00:00:00.000Z"},
                   "evt-done": {"eventbrite_id": "7", "start": "2026-11-21T00:00:00.000Z", "status": "cancelled"},
                   "_account": {"organization_id": "1"}}
        self.assertEqual(self.kinds(sync.plan([], mapping, NOW, ALWAYS)), [("cancel", "evt-gone")])

    def test_skips_unknown_past_and_cancelled(self):
        past = dict(STS, id="evt-past", start_at="2026-11-01T00:00:00.000Z", end_at="2026-11-01T01:30:00.000Z")
        other = dict(STS, id="evt-x", name="Board meeting")
        done = {"evt-st": {"eventbrite_id": "1", "status": "cancelled", "start": STS["start_at"]}}
        self.assertEqual(self.kinds(sync.plan([past, other, STS], done, NOW, ALWAYS)), [("skip", "evt-x")])


if __name__ == "__main__":
    unittest.main()
