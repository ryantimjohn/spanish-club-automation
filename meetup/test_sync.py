"""Tests for the Meetup sync logic. Run: python -m unittest -v meetup/test_sync.py"""
import base64
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sync import (TZ, FOLLOW, Problem, description, differences, duration_iso, fingerprint, jpeg, jwt,  # noqa: E402
                  kind_of, meetup_fields, place_ok, plan, rs256, rsa_key, start_local, when)

ZOOM = "https://us02web.zoom.us/j/83064162001?pwd=SECRETSECRET.1"
ONLINE = {"id": "evt-on", "name": "Spanish Club for Socialists - Online", "url": "https://luma.com/on",
          "start_at": "2026-11-19T00:00:00.000Z", "end_at": "2026-11-19T00:45:00.000Z", "meeting_url": ZOOM,
          "description_md": f"[{ZOOM}]({ZOOM})\n\nPasscode: 123456\n\nHey friends. Join us on Zoom.\n\n"
                            "Keep practicing between sessions on our Discord: [discord.gg/x](https://discord.gg/x)"}
STS = {"id": "evt-st", "name": "Spanish Club for Socialists - St. Stephen's", "url": "https://luma.com/st",
       "start_at": "2026-11-17T00:00:00.000Z", "end_at": "2026-11-17T01:30:00.000Z",
       "geo_address_json": {"full_address": "1525 Newton St NW, Washington, DC 20010, USA"},
       "description_md": "This week's conversation guide:\n[Guide](https://docs.example/g)\n\n"
                         "**About the event:**\n\nWe accept Venmo <donations> & thanks."}
LIB = {"id": "evt-lib", "name": "Spanish Club for Socialists H Street at the DC Liberation Center",
       "start_at": "2026-11-20T00:00:00.000Z", "end_at": "2026-11-20T01:30:00.000Z", "description_md": "Hi",
       "geo_address_json": {"full_address": "337 H St NE, Washington, DC 20002"}}
NOW = datetime(2026, 11, 10, 22, 0, tzinfo=TZ)
YES = lambda e: True  # noqa: E731


class Fields(unittest.TestCase):
    def test_kind_of(self):
        self.assertEqual(kind_of(ONLINE), "Online")
        self.assertEqual(kind_of(STS), "St. Stephen's")
        self.assertEqual(kind_of(LIB), "Liberation Center")
        self.assertIsNone(kind_of({"name": "Board meeting"}))

    def test_start_is_eastern_local(self):
        self.assertEqual(start_local("2026-11-17T00:00:00.000Z"), "2026-11-16T19:00")

    def test_hand_made_start_without_offset_is_eastern(self):
        self.assertEqual(when("2026-11-16T19:00"), when("2026-11-17T00:00:00.000Z"))

    def test_duration(self):
        self.assertEqual(duration_iso(STS), "PT1H30M")
        self.assertEqual(duration_iso(ONLINE), "PT45M")

    def test_online_link_and_passcode_never_in_description(self):
        f = meetup_fields(ONLINE)
        self.assertNotIn("zoom.us", f["description"])
        self.assertNotIn("SECRET", f["description"])
        self.assertNotIn("123456", f["description"])
        self.assertEqual(f["venue"], "online")
        self.assertEqual(f["online_url"], ZOOM)
        self.assertIsNone(f["how_to_find_us"])

    def test_description_is_lumas_markdown(self):
        md = description("This week's conversation guide:  \n[Guide](https://docs.example/g)\n\n\n"
                         "**About the event:**\n\nWe accept Venmo donations & thanks.")
        self.assertEqual(md, "This week's conversation guide:\n[Guide](https://docs.example/g)\n\n**About the event:**\n\n"
                             "We accept Venmo donations & thanks.\n\n" + FOLLOW.split(", ")[0] + ", "
                             "[https://luma.com/spanishclubforsocialists](https://luma.com/spanishclubforsocialists)")

    def test_side_door_goes_in_how_to_find_us(self):
        f = meetup_fields(STS)
        self.assertEqual(f["how_to_find_us"], "Please come in the side door!")
        self.assertEqual(f["venue"], "st-stephens")
        self.assertEqual(f["image"], "2026-11-16-st-stephens.png")

    def test_title_is_lumas_word_for_word(self):
        self.assertEqual(meetup_fields(LIB)["title"], LIB["name"])

    def test_place_must_match_venue(self):
        self.assertTrue(place_ok(STS))
        self.assertTrue(place_ok(ONLINE))
        self.assertFalse(place_ok(dict(STS, geo_address_json={"full_address": "1500 Harvard St NW"})))

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
        acts = self.kinds(plan([STS, far], {}, NOW, YES))
        self.assertIn(("create", "evt-st"), acts)
        self.assertNotIn(("create", "evt-far"), acts)

    def test_listed_event_follows_luma_past_the_window(self):
        far = dict(STS, start_at="2026-12-29T00:00:00.000Z", end_at="2026-12-29T01:30:00.000Z")
        listed = {"evt-st": {"meetup_event_id": "1", "fingerprint": fingerprint(meetup_fields(STS)), "start": STS["start_at"]}}
        self.assertEqual(self.kinds(plan([far], listed, NOW, YES)), [("update", "evt-st")])

    def test_waits_for_flyer_and_place(self):
        self.assertEqual(self.kinds(plan([STS], {}, NOW, lambda e: False)), [("wait_flyer", "evt-st")])
        elsewhere = dict(STS, geo_address_json={"full_address": "somewhere else"})
        self.assertEqual(self.kinds(plan([elsewhere], {}, NOW, YES)), [("wait_place", "evt-st")])

    def test_update_when_luma_changed_and_adopts_hand_made(self):
        same = {"evt-st": {"meetup_event_id": "1", "fingerprint": fingerprint(meetup_fields(STS)), "start": STS["start_at"]}}
        self.assertEqual(plan([STS], same, NOW, YES), [])
        self.assertEqual(self.kinds(plan([STS], same, NOW, YES, force=True)), [("update", "evt-st")])
        hand = {"evt-st": {"meetup_event_id": "1", "status": "published", "start": "2026-11-16T19:00"}}
        self.assertEqual(self.kinds(plan([STS], hand, NOW, YES)), [("update", "evt-st")])
        broken = dict(same["evt-st"], status="incomplete")
        self.assertEqual(self.kinds(plan([STS], {"evt-st": broken}, NOW, YES)), [("update", "evt-st")])

    def test_cancel_when_gone_from_luma(self):
        mapping = {"evt-gone": {"meetup_event_id": "9", "start": "2026-11-20T00:00:00.000Z"},
                   "evt-old": {"meetup_event_id": "8", "start": "2026-11-01T00:00:00.000Z"},
                   "evt-hand-past": {"meetup_event_id": "6", "start": "2026-11-09T19:00"},
                   "evt-hand-gone": {"meetup_event_id": "5", "start": "2026-11-23T19:00"},
                   "evt-done": {"meetup_event_id": "7", "start": "2026-11-21T00:00:00.000Z", "status": "cancelled"}}
        self.assertEqual(self.kinds(plan([], mapping, NOW, YES)), [("cancel", "evt-gone"), ("cancel", "evt-hand-gone")])

    def test_skips_unknown_and_past(self):
        past = dict(STS, id="evt-past", start_at="2026-11-01T00:00:00.000Z", end_at="2026-11-01T01:30:00.000Z")
        other = dict(STS, id="evt-x", name="Board meeting")
        self.assertEqual(self.kinds(plan([past, other], {}, NOW, YES)), [("skip", "evt-x")])


class ReadBack(unittest.TestCase):
    def good(self, e, vid, how):
        f = meetup_fields(e)
        start = when(e["start_at"]).astimezone(TZ)
        end = when(e["end_at"]).astimezone(TZ)
        return f, {"title": f["title"], "dateTime": start.isoformat(), "endTime": end.isoformat(),
                   "eventType": "ONLINE" if f["venue"] == "online" else "PHYSICAL", "venue": {"id": vid},
                   "howToFindUs": how, "description": "About the event"}

    def test_matching_event_has_no_differences(self):
        f, got = self.good(STS, "28472528", "Please come in the side door!")
        self.assertEqual(differences(got, STS, f, "28472528"), [])
        f, got = self.good(ONLINE, "26906060", ZOOM)
        self.assertEqual(differences(got, ONLINE, f, "online"), [])

    def test_reports_each_difference_without_the_link(self):
        f, got = self.good(ONLINE, "26906060", "https://zoom.us/j/other")
        got.update(endTime=got["dateTime"], description=f"Join: {ZOOM}")
        out = differences(got, ONLINE, f, "online")
        self.assertEqual(out, ["the end time", "the attendee-only Zoom link", "a Zoom link in the public description"])
        self.assertNotIn("zoom.us", " ".join(out))
        f, got = self.good(STS, "999", "")
        self.assertEqual(differences(got, STS, f, "28472528"), ["the venue", "\"how to find us\""])


def _der_int(n):
    b = n.to_bytes(n.bit_length() // 8 + 1, "big")
    return _der(0x02, b)


def _der(tag, body):
    n = len(body)
    size = bytes([n]) if n < 128 else bytes([0x80 | ((n.bit_length() + 7) // 8)]) + n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([tag]) + size + body


def _test_pem(pkcs8=False, mangle=None):
    """A throwaway RSA key built from two Mersenne primes, so no key is ever stored in the repository."""
    p, q, e = 2 ** 607 - 1, 2 ** 521 - 1, 65537
    n, d = p * q, pow(e, -1, (p - 1) * (q - 1))
    pkcs1 = _der(0x30, b"".join(_der_int(x) for x in (0, n, e, d, p, q, d % (p - 1), d % (q - 1), pow(q, -1, p))))
    der, label = pkcs1, "RSA PRIVATE KEY"
    if pkcs8:
        alg = _der(0x30, _der(0x06, bytes.fromhex("2a864886f70d010101")) + b"\x05\x00")
        der, label = _der(0x30, _der_int(0) + alg + _der(0x04, pkcs1)), "PRIVATE KEY"
    b = base64.b64encode(der).decode()
    pem = f"-----BEGIN {label}-----\n" + "\n".join(b[i:i + 64] for i in range(0, len(b), 64)) + f"\n-----END {label}-----\n"
    return (n, e, d), (mangle(pem) if mangle else pem)


class Photo(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("PIL"), "Pillow isn't installed")
    def test_flyer_becomes_a_same_size_jpeg(self):
        flyer = sorted((pathlib.Path(__file__).resolve().parent / "images").glob("*.png"))[0]
        data = jpeg(flyer)
        self.assertEqual(data[:3], b"\xff\xd8\xff")
        from io import BytesIO
        from PIL import Image
        self.assertEqual(Image.open(BytesIO(data)).size, Image.open(flyer).size)


class Signing(unittest.TestCase):
    def test_reads_pkcs1_and_pkcs8_however_mangled(self):
        want, _ = _test_pem()
        for pkcs8 in (False, True):
            for mangle in (None, lambda s: s.replace("\n", "\r\n"), lambda s: s.replace("\n", "\\n"),
                           lambda s: s.replace("\n", " "), lambda s: "﻿" + s.strip()):
                self.assertEqual(rsa_key(_test_pem(pkcs8, mangle)[1]), want)

    def test_rejects_something_that_isnt_a_key(self):
        with self.assertRaises(Problem):
            rsa_key("not a key")
        with self.assertRaises(Problem):
            rsa_key("-----BEGIN RSA PRIVATE KEY-----\nAAAA\n-----END RSA PRIVATE KEY-----")

    def test_signature_verifies(self):
        key, _ = _test_pem()
        n, e, _ = key
        sig = int.from_bytes(rs256(b"hello", key), "big")
        self.assertTrue(pow(sig, e, n).to_bytes((n.bit_length() + 7) // 8, "big").endswith(
            bytes.fromhex("2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824")))

    @unittest.skipUnless(shutil.which("openssl"), "openssl isn't installed")
    def test_signature_matches_openssl(self):
        key, pem = _test_pem()
        fd, path = tempfile.mkstemp(suffix=".pem")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(pem)  # the throwaway test key above, never a real one
            want = subprocess.run(["openssl", "dgst", "-sha256", "-sign", path], input=b"head.body",
                                  capture_output=True, check=True).stdout
        finally:
            os.remove(path)
        self.assertEqual(rs256(b"head.body", key), want)

    def test_jwt_claims(self):
        key, _ = _test_pem()
        head, body, sig = jwt("client-key", "kid-1", "4255800", key, 1000).split(".")
        pad = lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))  # noqa: E731
        self.assertEqual(json.loads(pad(head)), {"alg": "RS256", "typ": "JWT", "kid": "kid-1"})
        self.assertEqual(json.loads(pad(body)), {"sub": "4255800", "iss": "client-key", "aud": "api.meetup.com", "exp": 1120})
        self.assertEqual(len(pad(sig)), (key[0].bit_length() + 7) // 8)


if __name__ == "__main__":
    unittest.main()
