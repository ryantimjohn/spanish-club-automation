import importlib.util
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("ZOOM_PWD", "TESTPWD")
spec = importlib.util.spec_from_file_location("ud", Path(__file__).with_name("update_descriptions.py"))
ud = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ud)
ce = ud.ce

OLD_STS = ("Event is from 7:00 pm to 8:30 pm at St Stephen's:\n\n1525 Newton St NW\n\n"
           "[Open in Google Maps](https://maps.app.goo.gl/x)\n\nHere's the conversation guide:\n\n"
           "[Club de Conversación - 29 - Las Fronteras y la Libertad de Movimiento - Google Docs]"
           "(https://docs.google.com/document/d/ABC/edit)\n\nKeep practicing...\n\nWe accept Venmo donations.")
STS = {"name": "Spanish Club for Socialists - St. Stephen's", "description_md": OLD_STS}
LIB = {"name": "Spanish Club for Socialists H Street at the DC Liberation Center", "description_md": OLD_STS}
ONLINE = {"name": "Spanish Club for Socialists - Online", "meeting_url": ce.ZOOM_URL, "description_md": "x"}


class Descriptions(unittest.TestCase):
    def test_guide_title_alone_at_the_top(self):
        new, problem = ud.new_description(STS)
        self.assertIsNone(problem)
        self.assertTrue(new.startswith("This week's conversation guide:  \n"
                                       "[Las Fronteras y la Libertad de Movimiento](https://docs.google.com/document/d/ABC/edit)"))
        self.assertIn(ce.VENMO, new)
        for gone in ("1525 Newton", "7:00 pm", "Google Maps", "{"):
            self.assertNotIn(gone, new)

    def test_rerun_keeps_the_same_text(self):
        new, _ = ud.new_description(STS)
        self.assertEqual(ud.new_description(dict(STS, description_md=new))[0], new)

    def test_liberation_center_asks_for_their_donations(self):
        new, _ = ud.new_description(LIB)
        self.assertIn("paypal.com/donate", new)
        self.assertNotIn("Venmo", new)

    def test_online_has_no_guide_and_zoom_alone_in_its_paragraph(self):
        new, problem = ud.new_description(ONLINE)
        self.assertIsNone(problem)
        self.assertNotIn("guide", new.lower())
        zoom_paras = [p for p in new.split("\n\n") if "zoom.us" in p]
        self.assertEqual(len(zoom_paras), 1)
        self.assertTrue(zoom_paras[0].startswith("Zoom link: ["))

    def test_refuses_without_a_guide_or_with_another_zoom(self):
        self.assertIsNone(ud.new_description(dict(STS, description_md="no link"))[0])
        self.assertIsNone(ud.new_description(dict(ONLINE, meeting_url="https://zoom.us/j/1"))[0])
        self.assertIsNone(ud.new_description({"name": "Something else"})[0])

    def test_masked(self):
        self.assertNotIn("TESTPWD", ud.masked(ce.ONLINE))


if __name__ == "__main__":
    unittest.main()
