"""Tests for the Instagram Luma check in post.py (plan()); nothing is posted.
Run: python -m unittest -v test_instagram_plan.py"""
import hashlib
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import post  # noqa: E402
from post import Problem, ig_deadline, make_containers, nice, plan  # noqa: E402

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


class Deadline(unittest.TestCase):
    """GitHub's timer can run hours late, so a post may go out up to 10 hours late, but never after its night starts."""

    def test_friday_carousel_until_seven_pm(self):
        item = dict(WEEK, post_at="2026-11-13T09:00")
        self.assertEqual(nice(ig_deadline(item)), "Fri Nov 13, 7:00 PM")

    def test_tonight_story_stops_when_the_night_starts(self):
        story = {"post_at": "2026-11-16T11:00", "events": {EVENT: "2026-11-16T19:00"}}
        self.assertEqual(nice(ig_deadline(story)), "Mon Nov 16, 7:00 PM")
        early = {"post_at": "2026-11-16T11:00", "events": {EVENT: "2026-11-16T15:30"}}
        self.assertEqual(nice(ig_deadline(early)), "Mon Nov 16, 3:30 PM")

    def test_post_with_no_events(self):
        self.assertEqual(nice(ig_deadline(dict(ABOUT, post_at="2026-11-14T09:00"))), "Sat Nov 14, 7:00 PM")


class VideoStory(unittest.TestCase):
    """A Story with music goes up as its video, uploaded straight to Meta; any trouble falls back to the image."""

    VIDEO = b"pretend mp4 bytes"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
        (root / "videos").mkdir()
        (root / "videos" / "2026-11-18-online-story.mp4").write_bytes(self.VIDEO)
        self.slide = {"image": "images/2026-11-18-online-story.jpg", "sha256": "x", "event": EVENT,
                      "video": "videos/2026-11-18-online-story.mp4",
                      "video_sha256": hashlib.sha256(self.VIDEO).hexdigest(),
                      "music": {"title": "Son de máquina", "performer": "Orquesta Antonio M. Romeu", "year": "1915"}}
        self.story = {"id": "2026-11-18-story", "kind": "story", "events": {EVENT: "2026-11-18T19:00"}}
        self.calls = []
        patches = [mock.patch.object(post, "ROOT", root), mock.patch.object(post, "wait_ready", lambda c: None),
                   mock.patch.object(post, "graph", self.fake_graph)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def fake_graph(self, method, path, **params):
        self.calls.append(params)
        if params.get("upload_type") == "resumable":
            return {"id": "video-container", "uri": "https://rupload.facebook.com/ig-api-upload/v27.0/video-container"}
        return {"id": "image-container"}

    def test_video_is_uploaded(self):
        sent = []
        with mock.patch.object(post, "upload_video", lambda uri, data: sent.append((uri, data))):
            notes = []
            c = make_containers(self.story, [self.slide], "", "123", notes=notes)
        self.assertEqual(c, "video-container")
        self.assertEqual(sent, [("https://rupload.facebook.com/ig-api-upload/v27.0/video-container", self.VIDEO)])
        self.assertEqual(self.calls, [{"media_type": "STORIES", "upload_type": "resumable"}])
        self.assertEqual(notes, [])

    def test_failed_upload_falls_back_to_the_image(self):
        def boom(uri, data):
            raise Problem("The video upload failed (500): oops")
        with mock.patch.object(post, "upload_video", boom):
            notes = []
            c = make_containers(self.story, [self.slide], "", "123", notes=notes)
        self.assertEqual(c, "image-container")
        self.assertIn("image_url", self.calls[-1])
        self.assertTrue(notes[0].startswith("Posted without music"))

    def test_changed_video_falls_back_without_uploading(self):
        (post.ROOT / self.slide["video"]).write_bytes(b"something else")
        with mock.patch.object(post, "upload_video", lambda uri, data: self.fail("uploaded a changed video")):
            notes = []
            c = make_containers(self.story, [self.slide], "", "123", notes=notes)
        self.assertEqual(c, "image-container")
        self.assertEqual(len(self.calls), 1)
        self.assertIn("has changed", notes[0])

    def test_story_without_music_is_unchanged(self):
        slide = {k: v for k, v in self.slide.items() if k not in ("video", "video_sha256", "music")}
        c = make_containers(self.story, [slide], "", "123", notes=[])
        self.assertEqual(c, "image-container")
        self.assertEqual(self.calls[0]["media_type"], "STORIES")

    def test_key_only_goes_to_meta_upload_host(self):
        with mock.patch.object(urllib_request(), "urlopen", lambda *a, **k: self.fail("sent the key elsewhere")):
            with self.assertRaises(Problem):
                post.upload_video("https://example.com/ig-api-upload/v27.0/1", b"x")
            with self.assertRaises(Problem):
                post.upload_video("http://rupload.facebook.com/ig-api-upload/v27.0/1", b"x")


def urllib_request():
    return post.urllib.request


if __name__ == "__main__":
    unittest.main()
