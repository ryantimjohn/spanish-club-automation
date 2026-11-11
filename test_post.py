"""Tests for the Facebook Page step in post.py (the pure logic only; nothing is posted).
Run: python -m unittest -v test_post.py"""
import copy
import pathlib
import sys
import unittest
from datetime import date, timedelta

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "facebook"))
import collect_squares  # noqa: E402
import post  # noqa: E402
from post import attached_media, fb_caption, fb_due, fb_plan, fb_slides, parse_et  # noqa: E402

STEPHENS, ONLINE, LIBERATION = "https://luma.com/fdfiostx", "https://luma.com/6yd9kbw8", "https://luma.com/xvjrwk39"

NOV_13 = {
    "id": "2026-11-13-carousel", "kind": "carousel", "what": "Weekly carousel: Nov 16–19",
    "post_at": "2026-11-13T09:00", "fingerprint": "f13",
    "slides": [
        {"image": "images/2026-11-13-cover.jpg", "sha256": "a", "event": None,
         "lists_events": [STEPHENS, ONLINE, LIBERATION]},
        {"image": "images/2026-11-16-st-stephens-45.jpg", "sha256": "b", "event": STEPHENS},
        {"image": "images/2026-11-18-online-45.jpg", "sha256": "c", "event": ONLINE},
        {"image": "images/2026-11-19-liberation-center-45.jpg", "sha256": "d", "event": LIBERATION},
        {"image": "images/2026-11-13-end-slide.jpg", "sha256": "e", "event": None},
    ],
    "caption_lines": [
        {"text": "Next week at Spanish Club:"},
        {"text": ""},
        {"text": "Mon Nov 16 · 7:00–8:30 PM · St. Stephen's (@ststephensdc), 1525 Newton St NW (side door)",
         "event": STEPHENS},
        {"text": "Wed Nov 18 · 7:00–7:45 PM · Online on Zoom", "event": ONLINE},
        {"text": "Thu Nov 19 · 7:00–8:30 PM · DC Liberation Center (@dcliberationcenter), 337 H St NE",
         "event": LIBERATION},
        {"text": ""},
        {"text": "Free and open to all levels. RSVP at the link in bio."},
        {"text": "¡Nos vemos!"},
    ],
    "events": {STEPHENS: "2026-11-16T19:00", ONLINE: "2026-11-18T19:00", LIBERATION: "2026-11-19T19:00"},
    "depends_on": None,
    "location": {"id": "13539212969", "name": "St. Stephen & the Incarnation Episcopal Church", "events": [STEPHENS]},
}

JAN_4 = "https://luma.com/spanish-p01y"
BREAK = {
    "id": "2026-12-18-break", "kind": "image", "what": "Winter break heads-up", "post_at": "2026-12-18T09:00",
    "fingerprint": "fb", "depends_on": None,
    "slides": [{"image": "images/2026-12-18-winter-break.jpg", "sha256": "w", "event": None, "lists_events": [JAN_4]}],
    "caption_lines": [
        {"text": "Spanish Club is on winter break from Dec 21 to Jan 3."},
        {"text": ""},
        {"text": "We're back Monday, Jan 4 at St. Stephen's (@ststephensdc)!"},
        {"text": ""},
        {"text": "¡Felices fiestas!"},
    ],
    "events": {JAN_4: "2027-01-04T19:00"},
}

COVER_STORY = {"id": "2026-11-13-cover-story", "kind": "story", "what": "Cover as a Story",
               "post_at": "2026-11-13T09:05", "fingerprint": "s", "depends_on": "2026-11-13-carousel",
               "slides": [], "caption_lines": [], "events": {}}
EVENT_STORY = {"id": "2026-11-16-story", "kind": "story", "what": "Day-of Story", "post_at": "2026-11-16T11:00",
               "fingerprint": "s2", "depends_on": None, "slides": [], "caption_lines": [], "events": {}}

LUMA_OK = {STEPHENS: "2026-11-16T19:00", ONLINE: "2026-11-18T19:00", LIBERATION: "2026-11-19T19:00",
           JAN_4: "2027-01-04T19:00"}


def square(slug, approved=True):
    return {"image": f"facebook/images/{slug}-square.jpg", "sha256": f"sq-{slug}", "approved": approved}


SQUARES = {"images/2026-11-16-st-stephens-45.jpg": square("2026-11-16-st-stephens"),
           "images/2026-11-18-online-45.jpg": square("2026-11-18-online"),
           "images/2026-11-19-liberation-center-45.jpg": square("2026-11-19-liberation-center")}
NOV_13_SQUARES = ["facebook/images/2026-11-16-st-stephens-square.jpg", "facebook/images/2026-11-18-online-square.jpg",
                  "facebook/images/2026-11-19-liberation-center-square.jpg"]
NOV_13_45S = [s["image"] for s in NOV_13["slides"]]


class Caption(unittest.TestCase):
    def test_nov_13_adds_each_events_luma_link(self):
        self.assertEqual(fb_caption(NOV_13, {}),
                         "Next week at Spanish Club:\n\n"
                         "Mon Nov 16 · 7:00–8:30 PM · St. Stephen's, 1525 Newton St NW (side door)\n"
                         f"RSVP: {STEPHENS}\n\n"
                         "Wed Nov 18 · 7:00–7:45 PM · Online on Zoom\n"
                         f"RSVP: {ONLINE}\n\n"
                         "Thu Nov 19 · 7:00–8:30 PM · DC Liberation Center, 337 H St NE\n"
                         f"RSVP: {LIBERATION}\n\n"
                         "Free and open to all levels.\n"
                         "¡Nos vemos!")

    def test_no_instagram_handles_or_link_in_bio(self):
        text = fb_caption(NOV_13, {})
        self.assertNotIn("@", text)
        self.assertNotIn("link in bio", text)

    def test_a_gone_event_loses_its_line_and_link(self):
        text = fb_caption(NOV_13, {ONLINE: "is no longer on Luma"})
        self.assertNotIn("Wed Nov 18", text)
        self.assertNotIn(ONLINE, text)
        self.assertIn(f"RSVP: {STEPHENS}\n\nThu Nov 19", text)

    def test_break_post_links_the_night_it_names(self):
        self.assertEqual(fb_caption(BREAK, {}),
                         "Spanish Club is on winter break from Dec 21 to Jan 3.\n\n"
                         "We're back Monday, Jan 4 at St. Stephen's!\n"
                         f"RSVP: {JAN_4}\n\n"
                         "¡Felices fiestas!")

    def test_the_approved_manifest_item_is_left_unchanged(self):
        before = copy.deepcopy(NOV_13)
        fb_caption(NOV_13, {ONLINE: "moved"})
        self.assertEqual(NOV_13, before)


class Plan(unittest.TestCase):
    def test_square_flyers_only_when_luma_matches(self):
        slides, caption, notes = fb_plan(NOV_13, LUMA_OK, SQUARES)
        self.assertEqual([s["image"] for s in slides], NOV_13_SQUARES)
        self.assertEqual([s["event"] for s in slides], [STEPHENS, ONLINE, LIBERATION])
        self.assertEqual(slides[0]["sha256"], "sq-2026-11-16-st-stephens")
        self.assertEqual(notes, [])

    def test_moved_event_drops_its_square(self):
        luma = dict(LUMA_OK, **{ONLINE: "2026-11-19T19:00"})
        slides, caption, notes = fb_plan(NOV_13, luma, SQUARES)
        self.assertEqual([s["image"] for s in slides], [NOV_13_SQUARES[0], NOV_13_SQUARES[2]])
        self.assertNotIn(ONLINE, caption)
        self.assertTrue(any("moved" in n for n in notes))

    def test_no_location_note_on_facebook(self):
        luma = {k: v for k, v in LUMA_OK.items() if k != STEPHENS}
        _, _, notes = fb_plan(NOV_13, luma, SQUARES)
        self.assertFalse(any("location" in n.lower() for n in notes))

    def test_nothing_left_means_no_slides(self):
        slides, _, _ = fb_plan(NOV_13, {JAN_4: "2027-01-04T19:00"}, SQUARES)
        self.assertEqual(slides, [])

    def test_break_post_keeps_its_own_image(self):
        slides, _, notes = fb_plan(BREAK, LUMA_OK, SQUARES)
        self.assertEqual([s["image"] for s in slides], ["images/2026-12-18-winter-break.jpg"])
        self.assertEqual(notes, [])


class Squares(unittest.TestCase):
    def test_an_unapproved_square_means_instagrams_images(self):
        squares = dict(SQUARES, **{"images/2026-11-18-online-45.jpg": square("2026-11-18-online", approved=False)})
        slides, notes = fb_slides(NOV_13["slides"], squares)
        self.assertEqual([s["image"] for s in slides], NOV_13_45S)
        self.assertEqual(len(notes), 1)
        self.assertIn("images/2026-11-18-online-45.jpg", notes[0])

    def test_no_squares_at_all_means_instagrams_images(self):
        slides, notes = fb_slides(NOV_13["slides"], {})
        self.assertEqual([s["image"] for s in slides], NOV_13_45S)
        self.assertEqual(len(notes), 1)

    def test_a_post_without_flyers_is_unchanged(self):
        self.assertEqual(fb_slides(BREAK["slides"], SQUARES), (BREAK["slides"], []))

    def test_square_names_follow_the_instagram_flyer(self):
        self.assertEqual(collect_squares.square_for("images/2026-11-16-st-stephens-45.jpg"),
                         ("flyers/St. Stephen's/2026-11-16 Spanish Club for Socialists St. Stephen's – square.png",
                          "facebook/images/2026-11-16-st-stephens-square.jpg"))
        self.assertEqual(collect_squares.square_for("images/2026-11-19-liberation-center-45.jpg")[0],
                         "flyers/Liberation Center/2026-11-19 Spanish Club for Socialists Liberation Center – square.png")

    def test_only_flyers_have_squares(self):
        self.assertIsNone(collect_squares.square_for("images/2026-11-13-cover.jpg"))
        self.assertIsNone(collect_squares.square_for("images/2026-11-13-end-slide.jpg"))

    def test_a_changed_square_needs_approval_again(self):
        old = {"images/a-45.jpg": {"image": "facebook/images/a-square.jpg", "sha256": "old", "approved": True}}
        new = collect_squares.merge(old, {"images/a-45.jpg": {"image": "facebook/images/a-square.jpg", "sha256": "new"}})
        self.assertFalse(new["images/a-45.jpg"]["approved"])
        same = collect_squares.merge(old, {"images/a-45.jpg": {"image": "facebook/images/a-square.jpg", "sha256": "old"}})
        self.assertTrue(same["images/a-45.jpg"]["approved"])


class Due(unittest.TestCase):
    ITEMS = [NOV_13, COVER_STORY, EVENT_STORY, BREAK]
    APPROVED = {"2026-11-13-carousel": "f13", "2026-11-13-cover-story": "s", "2026-11-16-story": "s2",
                "2026-12-18-break": "fb"}

    def due(self, now, live_from=date(2026, 11, 13), fb_log=None, approved=None):
        due, missed = fb_due(self.ITEMS, fb_log or {}, parse_et(now), live_from,
                             self.APPROVED if approved is None else approved)
        return [i["id"] for i in due], [i["id"] for i in missed]

    def test_off_until_live_from_is_set(self):
        self.assertEqual(self.due("2026-11-13T09:07", live_from=None), ([], []))

    def test_friday_carousel_is_due_at_nine(self):
        self.assertEqual(self.due("2026-11-13T09:07"), (["2026-11-13-carousel"], []))

    def test_not_before_its_time(self):
        self.assertEqual(self.due("2026-11-13T08:59"), ([], []))

    def test_stories_never_go_to_facebook(self):
        due, _ = self.due("2026-11-16T11:30")
        self.assertEqual(due, [])

    def test_posts_before_live_from_are_ignored_not_missed(self):
        self.assertEqual(self.due("2026-11-20T09:07", live_from=date(2026, 11, 20)), ([], []))

    def test_already_logged_is_not_due_again(self):
        for status in ("posted", "publishing", "skipped", "missed"):
            self.assertEqual(self.due("2026-11-13T09:07", fb_log={"2026-11-13-carousel": {"status": status}}),
                             ([], []))

    def test_over_four_hours_late_is_missed(self):
        self.assertEqual(self.due("2026-11-13T13:01"), ([], ["2026-11-13-carousel"]))

    def test_unapproved_late_post_is_not_reported_missed(self):
        self.assertEqual(self.due("2026-11-13T13:01", approved={}), ([], []))

    def test_break_post_goes_too(self):
        self.assertEqual(self.due("2026-12-18T10:07", fb_log={"2026-11-13-carousel": {"status": "posted"}}),
                         (["2026-12-18-break"], []))


class Helpers(unittest.TestCase):
    def test_attached_media_lists_photos_in_order(self):
        self.assertEqual(attached_media(["11", "22"]),
                         {"attached_media[0]": '{"media_fbid":"11"}', "attached_media[1]": '{"media_fbid":"22"}'})

    def test_scrub_hides_every_key(self):
        saved = post._TOKEN, post._FB_TOKEN, post._PAGE_TOKEN
        try:
            post._TOKEN, post._FB_TOKEN, post._PAGE_TOKEN = "ig-secret", "fb-secret", "page-secret"
            self.assertEqual(post.scrub("a ig-secret b fb-secret c page-secret"), "a *** b *** c ***")
        finally:
            post._TOKEN, post._FB_TOKEN, post._PAGE_TOKEN = saved

    def test_live_from_is_a_friday_or_off(self):
        self.assertTrue(post.FB_LIVE_FROM is None or post.FB_LIVE_FROM.weekday() == 4)

    def test_feed_kinds(self):
        self.assertEqual(post.FEED_KINDS, ("carousel", "image"))
        self.assertEqual(timedelta(hours=4), post.LATE_LIMIT)


if __name__ == "__main__":
    unittest.main()
