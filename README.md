# Spanish Club for Socialists: Instagram auto-poster

This posts the club's Instagram posts on schedule, so nobody has to remember to.

- **Fridays, 9 AM Eastern:** next week's carousel (the cover, that week's flyers, the closing slide) and its caption. The cover also goes to the Story.
- **Event days, 11 AM Eastern:** that night's flyer as a Story.
- **Breaks:** a heads-up before the winter break, and "We're back!" before Jan 4.

Carousels with a St. Stephen's night, and the winter break post, are tagged with the church's location, so they also show on its Instagram location page. The tag uses the church's Facebook "delegate Page ID", `13539212969` (not secret). If the St. Stephen's night is cancelled, the tag is left off. If Instagram refuses the tag, the post goes out without it and the notice says so. Stories can't be tagged with a location through the API.

Reels and link stickers stay manual. Instagram's API can't add stickers.

## Keep this repository public
Instagram downloads each image from its public GitHub link, so if this repository is made private, every post fails. Nothing in it is secret: the Instagram key is stored separately as a GitHub secret.

## What keeps it safe
- It only posts what's in `manifest.json` **and** was approved. If an image or caption changes, that post needs approval again.
- Before each post it reads the club's public Luma calendar. A flyer whose event is gone or has moved is left out, along with its caption line and the cover that lists it. If nothing is left, the post is skipped.
- Each post goes out at most once. `log/posted.json` records every post, skip and miss.
- The Instagram key is a GitHub secret. It's never printed or stored in this repository.

## How you'll hear about it
After every post, skip or problem, a comment appears on the **"Instagram log"** issue in this repository, and GitHub emails it to you. GitHub also emails you if a run fails outright.

## If a notice says something failed
- **"FAILED … will be tried again"**: nothing to do. It retries every hour for up to 4 hours.
- **"PROBLEM … may or may not have posted"**: open Instagram and check. It won't be retried, to avoid a double post.
- **"Missed"**: GitHub didn't run the schedule in time. Post it by hand if it still matters (the images are in `images/`).
- **Anything about the key**: see "Secrets" below.

## Secret (Settings → Secrets and variables → Actions)
- `INSTAGRAM_ACCESS_TOKEN`: the "System-user access token" from the club's Meta business portfolio (Instagram API with Unified login). Every Sunday the workflow checks the key still works, without posting, and emails you if it doesn't.

That's the only secret. The club's Instagram account ID (`17841429526104661`, @spanishclubforsocialists) is in `post.py`; it isn't secret, and the account's username is checked before every post. Luma is read from its public calendar feed, so no Luma key is needed.

## Running it by hand (Actions → Instagram → Run workflow)
- `test`: shows what's due right now. Posts nothing.
- `post-one`: posts one approved post now. Enter its id, e.g. `2026-11-13-carousel`.
- `check-key`: checks the Instagram key without posting.

To pause everything: Actions → Instagram → "…" → Disable workflow.
