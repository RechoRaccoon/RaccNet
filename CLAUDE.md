# RaccNet

A Bluesky AT Protocol video platform — like YouTube built on Bluesky's social graph and video infrastructure.

## How to run
```bash
python raccnet_server.py
# Open http://localhost:8080
# Sign in with a Bluesky handle + App Password
# App Password must have Direct Messages enabled
# (Bluesky Settings → Privacy & Security → App Passwords)
```

## Architecture
Single-file Python server (`raccnet_server.py`) that serves a complete Preact/htm frontend. No build step, no npm, no bundler.

- **Frontend**: Preact + htm loaded from CDN, written entirely inside a Python heredoc string
- **Backend**: Python `ThreadedHTTPServer` acting as an API proxy to Bluesky
- **Storage**: All user data stays in the browser's `localStorage`

## Proxy routes (Python server → Bluesky APIs)
| Route | Target | Auth |
|---|---|---|
| `/proxy/pub/` | `https://public.api.bsky.app/xrpc` | None |
| `/proxy/auth/` | `https://bsky.social/xrpc` | Bearer token |
| `/proxy/video/` | `https://video.bsky.app/xrpc` | Service auth |
| `/proxy/chat/` | User's PDS + `atproto-proxy: did:web:api.bsky.chat#bsky_chat` | Bearer token |

The chat proxy extracts `_pds` query param to route to the correct PDS host.

## localStorage keys
| Key | Purpose |
|---|---|
| `raccnet_session` | Login session (handle, DID, accessJwt, pdsDid, avatar) |
| `raccnet_volume` | Video player volume (0–1), persists across videos |
| `raccnet_accent` | Accent color hex (default `#00FF07`) |
| `raccnet_filter` | Content filter: `all` / `sfw` / `nsfw` |
| `idkijab_history` | Watch history, up to 500 entries |
| `idkijab_lastpage` | Last visited page (restored on refresh) |
| `idkijab_lastchan` | Last visited channel handle |
| `idkijab_lastfeed` | Last visited feed URI |
| `idkijab_chantab` | Last channel tab (Content/Liked/DMs) |
| `idkijab_feedtab` | Last feed tab (Videos/Posts) |

## CSS / Design rules
- Dark theme only: `#0f0f0f` background, `#f1f1f1` text
- All accent colors use `var(--accent)` CSS variable (default `#00FF07` green)
- Secondary dim: `var(--accent-dim)` = rgba version at 12% opacity
- Solid dim: `var(--accent-solid-dim)` = dark version for button backgrounds
- **No border-radius** on most elements — sharp corners are intentional
- No external CSS frameworks — all inline styles in JS
- Font: Roboto (Google Fonts CDN)

## Frontend component map
```
App
├── Header (logo, search bar, upload button, avatar)
├── Sidebar (nav items, feeds list, settings)
│   └── SideItem
├── Pages (only one rendered at a time):
│   ├── FriendsFeed       — "From Friends" tab, Videos + Posts sub-tabs
│   ├── SubsPage          — Subscriptions, Videos + Posts tabs
│   ├── HistoryPage       — Watch history from localStorage
│   ├── FeedPage          — Custom Bluesky feeds
│   ├── SearchPage        — Search channels + videos
│   ├── WatchPage         — HLS video player + comments + related
│   ├── ChannelPage       — Profile, Content/Liked/DMs tabs
│   ├── PostDetailPage    — Single post with thread
│   └── SettingsPage      — Accent color, content filter, sign out
├── Modals:
│   ├── LoginModal
│   ├── UploadModal       — Video upload with FFmpeg thumbnail stitching
│   └── ShareModal        — Share post via DM
└── Components:
    ├── VideoGrid         — Scroll-paginated video grid
    ├── VideoCard         — Thumbnail + hover preview (HLS)
    ├── FriendCard        — DM-shared video card
    ├── PostCard          — Bluesky post with like/repost/share
    ├── ChannelPostsFeed  — Vertical post list with scroll loading
    ├── ChannelDMsTab     — Full DM chat UI (send/receive)
    ├── LikedTab          — Liked videos + posts
    ├── FollowStrip       — Horizontal scrollable channel avatars
    ├── VideoPlayer       — HLS player with volume persistence
    ├── Avatar            — Circular avatar with fallback initials
    └── Thumb             — Thumbnail image with fallback
```

## Key helper functions
- `isVid(post)` — checks hydrated `embed.$type` for video
- `isVidRaw(post)` — checks raw `record.embed.$type`
- `isAdultPost(post)` — checks Bluesky content labels
- `filterByContent(posts, mode)` — applies sfw/nsfw/all filter
- `loadFilter()` / `saveFilter()` — content filter persistence
- `loadAccent()` / `saveAccent()` / `applyAccent(color)` — theme
- `loadVolume()` / `saveVolume(v)` — volume persistence
- `ago(dateStr)` — relative time formatting
- `fmt(n)` — number formatting (1.2K, 3.4M)
- `api(url, opts)` — fetch wrapper

## AT Protocol API calls used
| Lexicon | Purpose |
|---|---|
| `com.atproto.server.createSession` | Login |
| `app.bsky.actor.getProfile` | Channel profile |
| `app.bsky.actor.getPreferences` | Get saved feeds |
| `app.bsky.feed.getTimeline` | Subscriptions posts tab |
| `app.bsky.feed.getAuthorFeed` | Channel videos |
| `app.bsky.feed.getFeed` | Custom feeds |
| `app.bsky.feed.getFeedGenerators` | Feed metadata |
| `app.bsky.feed.searchPosts` | Search |
| `app.bsky.feed.getPosts` | Hydrate post URIs |
| `app.bsky.feed.getPostThread` | Post detail + comments |
| `app.bsky.feed.getActorLikes` | Liked posts/videos |
| `app.bsky.feed.like` | Like a post |
| `app.bsky.feed.repost` | Repost |
| `app.bsky.graph.follow` | Follow/unfollow |
| `app.bsky.graph.getFollows` | Who a user follows |
| `app.bsky.graph.getFollowers` | Who follows a user |
| `app.bsky.graph.getBlocks` | Blocked accounts |
| `app.bsky.actor.searchActors` | Search channels |
| `com.atproto.repo.createRecord` | Post/like/repost/follow |
| `com.atproto.repo.deleteRecord` | Unlike/unrepost/unfollow |
| `com.atproto.repo.putRecord` | Edit profile |
| `com.atproto.repo.uploadBlob` | Upload avatar/banner |
| `app.bsky.video.uploadVideo` | Upload video |
| `app.bsky.video.getJobStatus` | Poll upload job |
| `chat.bsky.convo.listConvos` | List DM conversations |
| `chat.bsky.convo.getMessages` | Get DM messages |
| `chat.bsky.convo.getConvoForMembers` | Get/create convo |
| `chat.bsky.convo.sendMessage` | Send DM |

## Known issues / TODO
- [ ] Custom background image — attempted 6+ times, `body{background:#0f0f0f}` CSS always overrides. Need Claude Code to debug with real browser DevTools
- [ ] Mobile responsive layout — currently desktop only
- [ ] OAuth login — currently uses app passwords
- [ ] Notifications
- [ ] Dislike + "Came to this" — placeholder buttons, no AT Protocol backend
- [ ] Mutual follows count only fetches first 100 followers (no pagination)
- [ ] Follow strip caps at 40 accounts
- [ ] FFmpeg required for video thumbnail stitching during upload

## Deployment path (turning it into a real website)
1. Get a VPS (Hetzner, DigitalOcean, Linode — ~$5/mo)
2. Register domain: `raccnet.com`, `racc.tube`, or `raccnet.app`
3. Install nginx + certbot (Let's Encrypt for HTTPS)
4. Run `raccnet_server.py` as a systemd service on port 8080
5. Nginx proxies domain → localhost:8080
6. Done — no code changes needed, it works as-is

## Name / branding
- Name: **RaccNet** (confirmed clear — no existing website, app, or trademark)
- Logo: Green raccoon character with a play button on its chest
- Accent color: `#00FF07` (neon green) by default, user-customizable
- Tagline idea: "Bluesky, but make it video"
