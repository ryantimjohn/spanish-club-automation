"""Tests for the Instagram Luma check in post.py (plan()); nothing is posted.
Run: python -m unittest -v test_instagram_plan.py"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from post import plan  # noqa: E402

EVENT = "https://luma.com/fdfiostx"
LOCATION = {"id": "13539212969", "name": "St. Stephen & the Incarnation Episcopal Church", "events": []}

ABOUT = {
    "id": "2026-11-14-how-it-works", "kind": "carousel", "events": {}, "location": LOCATION,
    "slides": [{"image": f"images/2026-11-14-how-it-works-{n}.jpg", "sha256": "x", "event": None} for n in (1, 2, 3)],
    "caption_lines": [{"text": "Never been to Spanish Club?"}, {"text": ""}, {"text": "¡Nos vemos!"}],
}
ABOUT_STORY = {"id": "2026-11-14-how-it-works-story-1", "kind": "story", "events": {}, "caption_lines": [],
               "slides": [{"image": "images/2026-11-14-how-it-works-1-story.jpg", "sha256": "x", "event": None}]}
WEEK = {
    "id": "2026-11-13-carousel", "kind": "carousel", "events": {EVENT: "2026-11-16T19:00"},
    "slides": [{"image": "images/2026-11-13-cover.jpg", "sha256": "a", "event": None, "lists_events": [EVENT]},
               {"image": "images/2026-11-16-st-stephens-45.jpg", "sha256": "b", "event": EVENT},
               {"image": "images/2026-11-13-end-slide.jpg", "sha256": "c", "event": None}],
    "caption_lines": [{"text": "Next week:"}, {"text": "Mon Nov 16", "event": EVENT}],
}


class AboutTheClub(unittest.TestCase):
    def test_posts_whole_with_no_events(self):
        slides, caption, notes, cover, location = plan(ABOUT, {"https://luma.com/other": "2026-11-18T19:00"})
        self.assertEqual(slides, ABOUT["slides"])
        self.assertEqual(caption, "Never been to Spanish Club?\n\n¡Nos vemos!")
        self.assertEqual((notes, cover, location), ([], False, LOCATION))

    def test_story_posts(self):
        slides, _, notes, _, _ = plan(ABOUT_STORY, {})
        self.assertEqual((slides, notes), (ABOUT_STORY["slides"], []))

    def test_weekly_post_with_its_only_event_gone_is_still_skipped(self):
        slides, _, notes, _, _ = plan(WEEK, {})
        self.assertEqual(slides, [])
        self.assertTrue(notes)

    def test_weekly_post_unchanged(self):
        slides, _, notes, cover, _ = plan(WEEK, {EVENT: "2026-11-16T19:00"})
        self.assertEqual((len(slides), notes, cover), (3, [], True))


if __name__ == "__main__":
    unittest.main()
