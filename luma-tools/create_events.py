"""Create the weekly Spanish Club events (St. Stephen's / Dupont / Online) on Luma
until conversation guide #52 is used, skipping holidays.

Usage:
  python create_events.py                 # dry run: print the schedule
  python create_events.py --only N        # create only event number N from the schedule
  python create_events.py --apply         # create every event in the schedule
Needs LUMA_API_KEY in the environment for --only / --apply.
"""
import json
import os
import sys
import urllib.request
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = "https://public-api.luma.com"
TZ = ZoneInfo("America/New_York")


def _user_env(name):
    """A value from the environment, or on Windows from the user's env vars. Never printed."""
    value = os.environ.get(name, "").strip()
    if not value and os.name == "nt":
        import subprocess
        value = subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                                f"[Environment]::GetEnvironmentVariable('{name}','User')"],
                               capture_output=True, text=True).stdout.strip()
    return value


# The Online Zoom meeting. Its passcode is the ZOOM_PWD env var (Windows user env var / GitHub secret),
# kept out of this file because the repository is public.
ZOOM_PWD = _user_env("ZOOM_PWD")
ZOOM_URL = f"https://us02web.zoom.us/j/83064162001?pwd={ZOOM_PWD}"

# Patchwork's old space (1506 19th St NW) on Google Maps. Dupont events can't be created while the
# template still points here; Patchwork moved to 1901 18th St NW.
OLD_PATCHWORK_PLACE_ID = "ChIJ-c1uWqW3t4kRzeVoo-OqgNk"

LOG = Path(__file__).with_name("created_events.json")

FIRST_WEEK = date(2026, 11, 23)  # Monday after the last existing event
FIRST_DOC, LAST_DOC = 30, 52

DOCS = {
    30: ("1_522LPQy42H9SBifydnvq14J-wa1qXg4ZGxqJs30NIc", "El Idioma y la Justicia Lingüística"),
    31: ("19pMO5M_8Z67C6-LvEBDEmKyvdx3gJ4U6L7z0sPndvaU", "La Diáspora y la Identidad"),
    32: ("14pe_J2ZhK_kuuQ597tK-TjmheVaVODyWY20yodLfA1E", "El Asilo y las Ciudades Santuario"),
    33: ("1B9LqRp9Pb4GpLVbMjMV43bSmFBNz_VD5EHp5kg4_SSM", "El Internacionalismo"),
    34: ("14HdZa4t08rkl77H1u6Z7FXS1MTUi3zzNunyfQdtA7Bg", "El Imperialismo y el Sur Global"),
    35: ("1cW_6eHwy5w-Xtlf-VF1Or6KtbVlotZsEfvp32dmA53M", "Los Derechos Indígenas y la Devolución de Tierras"),
    36: ("1dgVuDc7mmdMLU8g771gbhYZxib5jrXRCtBf3P8XxTO8", "Las Sanciones y el Bloqueo Económico"),
    37: ("1MhmIKxnqmVuN_xFxn_43xF11w-4bDcfYycHCdGspAUk", "Los Roles de Género y el Patriarcado"),
    38: ("1-qGtbrKLbz8Nnd3BKsebge8R8c2IdKtAqhJrnn8ovvI", "La Liberación Queer y la Diversidad Sexual"),
    39: ("1JfvpEF3iNs4FqPLN8V5sypWYow1UpHx23FafizrIHD0", "La Justicia por la Discapacidad y el Capacitismo"),
    40: ("1ZNwedPta38wt5-55hU4C0udXBCmajC1iKnczlT8jij0", "La Neurodivergencia"),
    41: ("1AdIgRCl7Yea7baTLT-2sZrgMS2cvHZ_3HH0QkV6ajmc", "La Justicia Reproductiva"),
    42: ("1RX6D_RYJcHCqdWLdLrGty6RdgvLi9sbroDcoJ4wLLb8", "El Envejecimiento y la Discriminación por Edad"),
    43: ("1Y8g2dv7_6QK9_TcnNLTCxVQDEhMROfvsGMbqWvljpho", "Los Estándares de Belleza y la Gordofobia"),
    44: ("1YZqyBdwuNDQrAZ3x1-1x9PsNXNzrl2xIv0MrFYz4P_c", "Los Derechos de la Infancia y la Juventud"),
    45: ("12BdR2ZqagpU7MYzG9btZ-c0wKAChuSkEMjmvHqnGxMo", "El Abolicionismo y la Justicia Restaurativa"),
    46: ("1I6nJJxzB707cJBNS_AGFQkQlfspPTQl57LGVo2EFAu4", "Las Grandes Tecnológicas y la Vigilancia"),
    47: ("15p3zSKsEsBwNdB5ok8TZP37qAllL0Dj__bOUgxoQG5M", "La Agricultura Urbana y los Huertos"),
    48: ("1FbqieCcUgBYEsUURkbGZbdmmiHRK-Klq1aErYZAh67o", "El Deporte, el Ocio y el Capitalismo"),
    49: ("1ATeN_mZkvy2xGvEfY0Z8JvGegnNNxKNg9g18OMT7-b8", "La Historia Local y la Memoria"),
    50: ("1VB7hmkWpOpFCU-oK1kqVLh28dzSUhKr5WXF2_Vs9yV4", "La Utopía y la Imaginación Radical"),
    51: ("1REDrWQEGXYaEYmpnL8H8Pud95xtSl_bJFH_-bgLoJ48", "El Desgaste Militante y la Sostenibilidad"),
    52: ("16rlwcpIvykx_6x50T_VD8yQhgcX76QAQJ5CQ8_U8lmk", "La Alegría, la Celebración y la Sanación Colectiva"),
}

COURSE = ("Want to practice Spanish between conversation classes? "
          "[Click here for a free course!](https://drive.google.com/drive/folders/1oR3n51CovoJjCD5IjdrbWGQ33pZnXsDo)")

# Since Nov 10, 2026 the course line is gone from Luma (it belongs in Discord).
# COURSE stays here only so luma_cleanup.py can find old copies of it.

# Event descriptions, written by the organizer and approved Nov 11, 2026 (see the Handbook's "About the
# club"). Luma shows the date, time, address, map and door note itself, so the text leaves them out.
# Meetup, Eventbrite and Partiful copy this text and drop any paragraph with the Zoom link, so the Zoom
# link stays alone in its paragraph. The guide's title leads the in-person text, for regulars.
DISCORD_LINK = "[discord.gg/HuefQSfHVm](https://discord.gg/HuefQSfHVm)"
VENMO = "@timjohnryan"  # the organizer's Venmo; fine to show publicly (organizer, Nov 11, 2026)
VENMO_URL = "https://venmo.com/code?user_id=1670525508124672305&created=1794452651"  # organizer's link
VENMO_LINE = (f"We accept Venmo donations to help cover the cost of the space: [{VENMO}]({VENMO_URL}). "
              "If Venmo asks for the last 4 digits of a phone number, just tap \"Pay Without Confirming\".")
LIBERATION_LINE = ("We're at the DC Liberation Center this week. Please help support the space with a "
                   "[donation to the DC Liberation Center](https://www.paypal.com/donate?campaign_id=62V54PRH9J97Q).")
BASICS = ("This isn't a Spanish class, so even in the beginner group, it helps everyone if you have the "
          "basics down: introducing yourself, what you do for fun, where you live.")
IN_PERSON = (
    "This week's conversation guide:  \n{guide}\n\n"
    "About the event:\n\n"
    "A free Spanish conversation club for socialists. Many of us first came to practice for organizing work "
    "like food distribution, legal aid, anti-ICE patrols and tenant organizing. Everyone's welcome, and nobody "
    "will talk your ear off about crypto.\n\n"
    f"{BASICS}\n\n"
    "We split into three levels, beginner, intermediate and advanced, and you pick yours. Not sure which "
    "level you're at? Each week's conversation guide is broken out by level, so looking it over is a great "
    "way to find out.\n\n"
    "Keep practicing between sessions on our Discord, which also has resources if you're just starting "
    f"out: {DISCORD_LINK}\n\n"
    "{support}\n\n"
    "¡Nos vemos!"
)
ONLINE = (
    "A free Spanish conversation club for socialists, on Zoom. Everyone's welcome, wherever you are.\n\n"
    f"Zoom link: [{ZOOM_URL}]({ZOOM_URL})\n\n"
    f"{BASICS}\n\n"
    "We split into Zoom breakout rooms by level, beginner, intermediate and advanced, and you pick yours. "
    "The host often brings conversation topics to get things going.\n\n"
    "Our Discord has resources if you're just starting out, and it's where we keep practicing between "
    f"sessions: {DISCORD_LINK}\n\n"
    "¡Nos vemos!"
)


def in_person(guide, support=VENMO_LINE):
    """The in-person text, given the guide as a markdown link and the closing support line."""
    return IN_PERSON.replace("{guide}", guide).replace("{support}", support)
QUESTION = {
    "id": "how-did-you-hear",
    "label": "How did you hear about us?",
    "required": False,
    "question_type": "select",
    "multiple": False,
    "options": ["A friend", "Instagram", "Meetup", "Partiful", "Eventbrite", "Discord",
                "DSA newsletter", "Browsing Luma", "Other"],
}

# Dates with no event. A week where every day is skipped uses no guide.
SKIP = {
    date(2026, 11, 25): "Day before Thanksgiving (travel)",
    date(2026, 12, 21): "Christmas week",
    date(2026, 12, 22): "Christmas week",
    date(2026, 12, 23): "Christmas week",
    date(2026, 12, 28): "Week between Christmas and New Year's",
    date(2026, 12, 29): "Week between Christmas and New Year's",
    date(2026, 12, 30): "Week between Christmas and New Year's",
    date(2027, 1, 18): "Martin Luther King Jr. Day",
    date(2027, 2, 15): "Presidents' Day",
    date(2027, 4, 21): "First night of Passover",
    date(2027, 5, 31): "Memorial Day",
    date(2027, 7, 5): "Independence Day (observed)",
}

SERIES = [
    {
        "key": "St. Stephen's",
        "weekday": 0,
        "start": time(19, 0),
        "minutes": 90,
        "with_guide": True,
        "fields": {
            "name": "Spanish Club for Socialists - St. Stephen's",
            "geo_address_json": {
                "type": "google",
                "place_id": "ChIJhwgXSSLIt4kRpctxLbkAlxk",
                "description": "Please come in the side door!",
            },
            "cover_url": "https://images.lumacdn.com/uploads/nl/176c0f83-c9f5-4e25-878a-2bfa99047907.png",
            "font": "snpro",
            "tint_color": "#26374b",
        },
        "description": IN_PERSON.replace("{support}", VENMO_LINE),
    },
    {
        "key": "Dupont",
        "weekday": 1,
        "start": time(19, 30),
        "minutes": 90,
        "with_guide": True,
        "fields": {
            "name": "Spanish Club for Socialists - Dupont",
            # Patchwork moved to 1901 18th St NW (organizer, Nov 10, 2026). Before a Dupont season, set
            # this to the new space's Google place_id and door directions; until then the script refuses
            # to create Dupont events (see OLD_PATCHWORK_PLACE_ID).
            "geo_address_json": {
                "type": "google",
                "place_id": "ChIJ-c1uWqW3t4kRzeVoo-OqgNk",
                "description": "",
            },
            "cover_url": "https://images.lumacdn.com/uploads/h6/05a373ac-2c17-4514-a6fc-ae9a78bf5a00.png",
            "font": "geist-mono",
            "tint_color": "#422544",
        },
        "description": IN_PERSON.replace("{support}", VENMO_LINE),
    },
    {
        "key": "Online",
        "weekday": 2,
        "start": time(19, 0),
        "minutes": 45,
        "with_guide": False,
        "fields": {
            "name": "Spanish Club for Socialists - Online",
            "meeting_url": ZOOM_URL,
            "cover_url": "https://images.lumacdn.com/uploads/90/40810e7d-117b-47cd-83e4-3a676424f991.png",
            "font": "new-spirit",
            "tint_color": "#486849",
        },
        "description": ONLINE,
    },
]

# Settings copied from the existing events.
COMMON = {
    "timezone": "America/New_York",
    "theme": "standard",  # existing pages use "legacy", which the API can't set
    "visibility": "public",
    "location_visibility": "public",
    "registration_open": True,
    "waitlist_status": "disabled",
    "can_register_for_multiple_tickets": False,
    "max_capacity": None,
    "registration_questions": [QUESTION],
    "feedback_email": {"enabled": False},
}


def guide_link(num):
    doc_id, title = DOCS[num]
    return f"[{title}](https://docs.google.com/document/d/{doc_id}/edit)"


def build_schedule():
    """Return (events, skipped) where events are dicts ready to send."""
    events, skipped = [], []
    week, doc = FIRST_WEEK, FIRST_DOC
    while doc <= LAST_DOC:
        held = []
        for s in SERIES:
            day = week + timedelta(days=s["weekday"])
            if day in SKIP:
                skipped.append((day, s["key"], SKIP[day]))
                continue
            start = datetime.combine(day, s["start"], TZ)
            end = start + timedelta(minutes=s["minutes"])
            body = dict(COMMON, **s["fields"])
            body["start_at"] = start.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z")
            body["end_at"] = end.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z")
            body["description_md"] = s["description"].replace("{guide}", guide_link(doc))
            held.append({"local_start": start, "series": s["key"],
                         "doc": doc if s["with_guide"] else None, "body": body})
        if held:
            events.extend(held)
            doc += 1
        week += timedelta(days=7)
    return events, skipped


def call(method, path, body=None):
    req = urllib.request.Request(
        BASE + path,
        method=method,
        headers={"x-luma-api-key": os.environ["LUMA_API_KEY"], "content-type": "application/json",
                 "user-agent": "curl/8.0"},
        data=json.dumps(body).encode() if body is not None else None,
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {path} -> HTTP {e.code}: {e.read().decode(errors='replace')}")


def existing_keys(events):
    """(name, start_at) of events already on the calendar in the schedule's range."""
    after = min(e["body"]["start_at"] for e in events)
    before = max(e["body"]["end_at"] for e in events)
    keys, cursor = set(), None
    while True:
        q = f"/v1/calendars/events/list?after={after}&before={before}&pagination_limit=100"
        if cursor:
            q += f"&pagination_cursor={cursor}"
        page = call("GET", q)
        for entry in page.get("entries", []):
            ev = entry.get("event", entry)
            keys.add((ev["name"], ev["start_at"].replace(".000Z", "Z")))
        if not page.get("has_more"):
            return keys
        cursor = page["next_cursor"]


def main():
    events, skipped = build_schedule()
    for i, e in enumerate(events, 1):
        guide = f"#{e['doc']}" if e["doc"] else "-"
        print(f"{i:3}  {e['local_start']:%a %Y-%m-%d %I:%M%p}  {e['series']:<14} guide {guide}")
    print(f"\n{len(events)} events, guides #{FIRST_DOC}-#{LAST_DOC}")
    print("Skipped:")
    for day, key, why in skipped:
        print(f"     {day:%a %Y-%m-%d}  {key:<14} {why}")

    if "--only" in sys.argv:
        targets = [events[int(sys.argv[sys.argv.index("--only") + 1]) - 1]]
    elif "--apply" in sys.argv:
        targets = events
    else:
        return

    have = existing_keys(targets)
    old_patchwork = [e for e in targets if e["series"] == "Dupont"
                     and (e["body"]["name"], e["body"]["start_at"]) not in have
                     and e["body"].get("geo_address_json", {}).get("place_id") == OLD_PATCHWORK_PLACE_ID]
    if old_patchwork:
        sys.exit("New Dupont events would point at Patchwork's old space on Google Maps. Set the new space's "
                 "place_id and door directions in the Dupont template first.")
    log = json.loads(LOG.read_text(encoding="utf-8")) if LOG.exists() else []
    for e in targets:
        b = e["body"]
        label = f"{e['local_start']:%a %Y-%m-%d} {e['series']}"
        if (b["name"], b["start_at"]) in have:
            print(f"SKIP {label}: already on the calendar")
            continue
        created = call("POST", "/v1/events/create", b)
        ev = call("GET", f"/v1/events/get?event_id={created['id']}")
        log.append({"id": created["id"], "url": ev["url"], "series": e["series"],
                    "start_at": b["start_at"], "doc": e["doc"]})
        LOG.write_text(json.dumps(log, indent=1), encoding="utf-8")
        ok = ev["description_md"].strip() == b["description_md"].strip() and ev["start_at"].startswith(b["start_at"][:16])
        print(f"CREATED {label}: {ev['url']} ({created['id']}){'' if ok else '  <-- CHECK: fields differ'}")


if __name__ == "__main__":
    main()
