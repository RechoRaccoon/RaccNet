# RaccTube

A YouTube-style video platform built on top of ATmosphere's AT Protocol — browse, watch, upload, and share videos using your existing ATmosphere account!

---

## Installation

1. Go to the [**Releases**](../../releases) section on the right side of this page
2. On the latest version, open "Assets" and Download **`RaccTube.exe`**
3. Run it — no install needed
4. Sign in with your ATmosphere handle and an [App Password](https://bsky.app/settings/app-passwords)
   - Your App Password **must have Direct Messages enabled** if you want DM features
   - *(ATmosphere Settings → Privacy & Security → App Passwords)*

> **Note:** Windows may show a SmartScreen warning since the exe is unsigned. Click "More info" → "Run anyway."

---

## Long Video Support & Thumbnails

ATmosphere has a 3-minute video limit per post — RaccTube works around this automatically. When you upload a video longer than 3 minutes, RaccTube splits it into parts and posts them as a thread, with each part replying to the last. When you watch it, RaccTube plays the entire thread as one continuouas video.

You can also provide a custom thumbnail at upload time. RaccTube uses FFmpeg to stitch the thumbnail into the first frame of the video before uploading — since ATmosphere doesn't have a separate thumbnail field, this is how the thumbnail shows up as a preview across the platform without any special handling on the viewer's end.

---

## Work in Progress

RaccTube is actively being built and is not feature-complete. Some things are placeholder buttons with no backend yet (Dislike, etc.), some things are partially implemented, and some things may break without warning. Mobile layout doesn't exist yet. There are known quirks, missing features, and rough edges throughout. New versions are released as things get built out — check the Releases page for updates.

If something doesn't work, that's probably why. Feedback and bug reports are welcome anyway.

---

## Vibe Coded Disclaier

This project is being vibe coded :3

---

## Features

- **Watch Videos** — Browse and watch ATmosphere-hosted videos from people you follow or the wider network
- **Your Feed** — "From Friends" tab shows videos + posts from your ATmosphere follows
- **Subscriptions** — Dedicated subscriptions feed with Videos and Posts sub-tabs
- **Search** — Search for channels and videos across the network
- **Custom Feeds** — Browse any ATmosphere feed generator as a video feed
- **DMs** — Full direct message chat UI inside channel pages (requires App Password with DM access)
- **Like, Repost, Share** — Full ATmosphere social interactions
- **Watch History** — Locally stored watch history (up to 500 entries)
- **Content Filter** — Toggle between All / SFW / NSFW content
- **Customizable Accent Color** — Pick your own theme color (default: `#00FF07` neon green)
- **No account required to browse** — Sign in with any ATmosphere handle + App Password to unlock social features

---

## Built With

- **Frontend:** Preact + htm (loaded from CDN — no build step, no npm)
- **Backend:** Python standard library HTTP server
- **Protocol:** ATmosphere AT Protocol (atproto)
- **Video:** ATmosphere's native HLS video infrastructure
