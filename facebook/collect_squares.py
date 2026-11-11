#!/usr/bin/env python3
"""Copy each night's square flyer into the repo for the Facebook Page post.

Facebook crops every photo of a multi-photo post to a tile in its grid, which cuts the top and
bottom off Instagram's 4:5 flyers. So the Facebook step (post.py) posts each night's square
flyer instead, without the weekly cover and closing slide. This script runs on the organizer's
computer, where the flyers are: for every flyer in an Instagram feed post in manifest.json, it
converts "flyers/<place>/<date> Spanish Club for Socialists <place> – square.png" to a JPEG in
facebook/images/ and records it in facebook/squares.json with its fingerprint. Then commit both.

A square is only posted once it's approved. A square whose image changed needs approval again,
and until every flyer of a post has an approved square, that post uses Instagram's images.

Usage:
    python facebook/collect_squares.py            Copy new or changed squares (keeps approvals that still match).
    python facebook/collect_squares.py --approve  Record approval of every square (only with the organizer's OK).
"""
import hashlib
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
FLYERS_ROOT = REPO.parent.parent  # Documents\Spanish Club Flyers (the flyers live outside the repo)
SQUARES = HERE / "squares.json"

PLACES = {"st-stephens": "St. Stephen's", "online": "Online", "liberation-center": "Liberation Center",
          "dupont": "Dupont"}


def square_for(ig_image):
    """(square flyer's path under Documents\\Spanish Club Flyers, its JPEG's path in the repo) for an Instagram
    flyer like 'images/2026-11-16-st-stephens-45.jpg'. None for covers, closing slides and anything else."""
    m = re.fullmatch(r"images/(\d{4}-\d{2}-\d{2})-([a-z-]+)-45\.jpg", ig_image)
    if not m or m[2] not in PLACES:
        return None
    place = PLACES[m[2]]
    return (f"flyers/{place}/{m[1]} Spanish Club for Socialists {place} – square.png",
            f"facebook/images/{m[1]}-{m[2]}-square.jpg")


def wide_for(ig_image):
    """The night's wide (16:9) flyer already in the repo for Meetup and Eventbrite, which leads a 3-night Facebook
    post (Facebook shows a 3-photo post's first photo as a wide strip). None for covers and closing slides."""
    m = re.fullmatch(r"images/(\d{4}-\d{2}-\d{2})-([a-z-]+)-45\.jpg", ig_image)
    if not m or m[2] not in PLACES:
        return None
    return f"meetup/images/{m[1]}-{m[2]}.png"


def merge(old, new):
    """New entries, keeping an approval only where the image's fingerprint is unchanged."""
    out = {}
    for key, entry in new.items():
        before = old.get(key, {})
        out[key] = dict(entry, approved=bool(before.get("approved")) and before.get("sha256") == entry["sha256"])
    return out


def to_jpeg(src, dest):
    from PIL import Image  # only needed here, on the organizer's computer
    im = Image.open(src).convert("RGB")
    if im.size[0] != im.size[1]:
        raise ValueError(f"{src.name} is {im.size[0]}x{im.size[1]}, not square")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    im.save(tmp, "JPEG", quality=92, optimize=True)
    if dest.exists() and dest.read_bytes() == tmp.read_bytes():
        tmp.unlink()
    else:
        tmp.replace(dest)
    return hashlib.sha256(dest.read_bytes()).hexdigest()


def main(approve=False):
    manifest = json.loads((REPO / "manifest.json").read_text(encoding="utf-8"))
    old = json.loads(SQUARES.read_text(encoding="utf-8")) if SQUARES.exists() else {}
    new, new_wides, missing = {}, {}, []
    for item in manifest["items"]:
        if item["kind"] not in ("carousel", "image"):
            continue
        flyers = [s for s in item["slides"] if s.get("event") and square_for(s["image"])]
        for slide in flyers:
            src_rel, dest_rel = square_for(slide["image"])
            src, dest = FLYERS_ROOT / src_rel, REPO / dest_rel
            if not src.exists():
                missing.append(src_rel)
                continue
            new[slide["image"]] = {"image": dest_rel, "sha256": to_jpeg(src, dest)}
        if len(flyers) == 3:  # only a 3-night post leads with a wide flyer
            wide = wide_for(flyers[0]["image"])
            if (REPO / wide).exists():
                new_wides[flyers[0]["image"]] = {"image": wide,
                                                 "sha256": hashlib.sha256((REPO / wide).read_bytes()).hexdigest()}
            else:
                missing.append(wide)
    squares, wides = merge(old.get("squares", {}), new), merge(old.get("wides", {}), new_wides)
    if approve:
        squares = {k: dict(v, approved=True) for k, v in squares.items()}
        wides = {k: dict(v, approved=True) for k, v in wides.items()}
    SQUARES.write_text(json.dumps({
        "about": "Square flyers posted to the Facebook Page in place of Instagram's 4:5 flyers, and the wide flyer "
                 "that leads a 3-night post (see post.py). Made by facebook/collect_squares.py. Only approved images "
                 "with a matching fingerprint are posted.",
        "squares": dict(sorted(squares.items())),
        "wides": dict(sorted(wides.items()))}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    waiting = sum(not v["approved"] for v in [*squares.values(), *wides.values()])
    print(f"{len(squares)} square and {len(wides)} wide flyers in facebook/squares.json; {waiting} waiting for approval.")
    for m in missing:
        print("No square flyer found:", m)
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main(approve="--approve" in sys.argv[1:]))
