"""Tests for partiful.py's text rules. Run: python -I -X utf8 test_partiful.py"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import partiful as p  # noqa: E402

ST = "Spanish Club for Socialists - St. Stephen's"
MD = """This week's conversation guide:
[Las Fronteras](https://docs.google.com/document/d/abc/edit)

Keep practicing on our Discord: [discord.gg/HuefQSfHVm](https://discord.gg/HuefQSfHVm)

Follow us on Instagram: [instagram.com/spanishclubforsocialists](https://www.instagram.com/spanishclubforsocialists/)

See every event on [Luma](https://luma.com/spanishclubforsocialists)

Or the short link: lu.ma/spanishclub

RSVP at https://LUMA.com/abc123 please

[[https://us02web.zoom.us/j/1?pwd=x](https://us02web.zoom.us/j/1?pwd=x)]

We accept Venmo donations to help cover the cost of the space: [@timjohnryan](https://venmo.com/code?user_id=1).

Please help support the space with a [donation to the DC Liberation Center](https://www.paypal.com/donate?campaign_id=X).

¡Nos vemos!"""


def event(name=ST, md=MD):
    return {"id": "evt-test", "name": name, "description_md": md}


class Description(unittest.TestCase):
    def test_no_luma_or_zoom(self):
        text = p.plain_description(event())
        self.assertNotRegex(text.lower(), r"luma\.com|lu\.ma|zoom\.us")
        self.assertNotIn("Luma", text)

    def test_keeps_the_rest(self):
        text = p.plain_description(event())
        self.assertTrue(text.startswith("Please come in the side door!\n\n"))
        self.assertIn("Las Fronteras: https://docs.google.com/document/d/abc/edit", text)
        self.assertIn("Keep practicing on our Discord: https://discord.gg/HuefQSfHVm", text)
        self.assertIn("Follow us on Instagram: https://www.instagram.com/spanishclubforsocialists/", text)
        self.assertTrue(text.endswith("¡Nos vemos!"))

    def test_no_venmo_or_paypal(self):
        text = p.plain_description(event())
        self.assertNotRegex(text.lower(), r"venmo|paypal|timjohnryan|donation")

    def test_no_follow_line(self):
        self.assertFalse(hasattr(p, "FOLLOW"))
        self.assertNotIn("follow us on Luma", p.plain_description(event()))

    def test_words_like_luma_are_fine(self):
        # only Luma links are dropped, not paragraphs that merely contain similar letters
        text = p.plain_description(event(md="Bring your illuma.community badge.\n\nSee you there"))
        self.assertIn("illuma.community", text)

    def test_other_kinds_have_no_first_line(self):
        text = p.plain_description(event(name="Spanish Club for Socialists H Street at the DC Liberation Center",
                                         md="Hello"))
        self.assertEqual(text, "Hello")


class Flyer(unittest.TestCase):
    def test_only_the_partiful_square(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d) / "St. Stephen's"
            folder.mkdir()
            (folder / "2026-11-16 Spanish Club for Socialists St. Stephen's – square.png").write_bytes(b"")
            old, p.FLYERS = p.FLYERS, Path(d)
            try:
                ev = {"name": ST, "start_at": "2026-11-17T00:00:00.000Z"}
                self.assertIsNone(p.flyer(ev))  # never falls back to the flyer with the Luma line
                (folder / "2026-11-16 Spanish Club for Socialists St. Stephen's – Partiful square.png").write_bytes(b"")
                self.assertTrue(p.flyer(ev).endswith("– Partiful square.png"))
            finally:
                p.FLYERS = old


class Patterns(unittest.TestCase):
    def test_luma_link(self):
        for s in ("https://luma.com/x", "luma.com/x", "http://lu.ma/x", "www.luma.com", "LUMA.COM/abc",
                  "public-api.luma.com/v1"):
            self.assertRegex(s, p.LUMA_LINK, s)
        for s in ("illuma.community", "luma.community", "Luma is our hub", "plu.mail"):
            self.assertNotRegex(s, p.LUMA_LINK, s)


class PublicCheck(unittest.TestCase):
    PAGE = ('{"isPublic":true,"status":"PUBLISHED","description":"Hi\n\n%s",'
            '"bio":{"value":"Every date is on Luma: luma.com/spanishclubforsocialists","visibility":"PUBLIC"}}')

    def test_bio_alone_is_ok(self):
        c = p.public_check(self.PAGE % "Nos vemos")
        self.assertEqual(c["luma_links"], [])
        self.assertTrue(c["public"] and c["published"] and c["luma_in_profile_bio"])

    def test_payment_link_is_caught(self):
        c = p.public_check(self.PAGE % "Venmo: https://venmo.com/code?user_id=1")
        self.assertEqual(c["payment_links"], ["venmo.com/code?user_id=1"])

    def test_description_link_is_caught(self):
        c = p.public_check(self.PAGE % "follow us on Luma, https://luma.com/spanishclubforsocialists")
        self.assertEqual(c["luma_links"], ["https://luma.com"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
