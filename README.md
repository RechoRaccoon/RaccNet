# RaccNet
A YouTube-style video platform built on top of the AT Protocol — browse, watch, upload, and share videos using your existing ATmosphere/Bluesky account!
---
## Installation
1. Go to the [**Releases**](../../releases) section on the right side of this page
2. On the latest version, open "Assets" and Download **`RaccNet.exe`**
3. Run it — no install needed
4. Sign in with your ATmosphere handle and an [App Password](https://bsky.app/settings/app-passwords)
   - Your App Password **must have Direct Messages enabled** if you want DM features
   - *(ATmosphere Settings → Privacy & Security → App Passwords)*
> **Note:** Windows may show a SmartScreen warning since the exe is unsigned. Click "More info" → "Run anyway."
---
## Long Video Support & Thumbnails
ATmosphere has a 3-minute video limit per post — RaccNet works around this automatically. When you upload a video longer than 3 minutes, RaccNet splits it into parts and posts them as a thread, with each part replying to the last. When you watch it, RaccNet plays the entire thread as one continuous video.
You can also provide a custom thumbnail at upload time. RaccNet uses FFmpeg to stitch the thumbnail into the first frame of the video before uploading — since ATmosphere doesn't have a separate thumbnail field, this is how the thumbnail shows up as a preview across the platform without any special handling on the viewer's end.
---
## Work in Progress
RaccNet is actively being built and is not feature-complete. Some things are placeholder buttons with no backend yet (Dislike, etc.), some things are partially implemented, and some things may break without warning. Mobile layout doesn't exist yet. There are known quirks, missing features, and rough edges throughout. New versions are released as things get built out — check the Releases page for updates.
If something doesn't work, that's probably why. Feedback and bug reports are welcome anyway.
---
## Vibe Coded Disclaimer
This project is being vibe coded :3
---
## Built With
- **Frontend:** Preact + htm (loaded from CDN — no build step, no npm)
- **Backend:** Python standard library HTTP server
- **Protocol:** ATmosphere AT Protocol (atproto)
- **Video:** ATmosphere's native HLS video infrastructure
