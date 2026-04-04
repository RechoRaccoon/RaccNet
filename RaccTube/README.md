# RaccNet 🦝▶

A YouTube-style video frontend for [Bluesky](https://bsky.app), built on the AT Protocol.

## Quick Start

```bash
# No installation needed — just Python 3.8+
python racctube_server.py

# Open http://localhost:8080
# Sign in with your Bluesky handle and an App Password
# (bsky.app → Settings → Privacy & Security → App Passwords)
# Make sure "Direct Messages" is enabled on the app password
```

## What it does

- Browse videos from people you follow on Bluesky
- Watch, like, repost, and comment on videos
- Upload videos to Bluesky
- See videos your friends shared with you via DMs
- Full DM chat interface
- Custom feeds, subscriptions, watch history
- Customizable accent color

## Requirements

- Python 3.8+
- FFmpeg (optional — only needed for video upload thumbnail stitching)
- A Bluesky account

## Tech

Single Python file. No npm, no bundler, no framework install. The frontend is Preact + htm loaded from CDN, embedded in the Python server file.
