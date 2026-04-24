import { useState, useEffect, useRef, useCallback } from "react";

// ─── API Endpoints ────────────────────────────────────────────────────────────
const PUB = "https://public.api.bsky.app/xrpc";
const AUTH = "https://bsky.social/xrpc";

// ─── Helpers ──────────────────────────────────────────────────────────────────
const isVid = (p) => p?.embed?.$type === "app.bsky.embed.video#view";

const ago = (d) => {
  const s = Math.floor((Date.now() - new Date(d)) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} minutes ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} hours ago`;
  if (s < 2592000) return `${Math.floor(s / 86400)} days ago`;
  if (s < 31536000) return `${Math.floor(s / 2592000)} months ago`;
  return `${Math.floor(s / 31536000)} years ago`;
};

const fmt = (n) => {
  if (!n) return "0";
  if (n >= 1e9) return (n / 1e9).toFixed(1) + "B";
  if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "K";
  return "" + n;
};

// ─── Video Player ─────────────────────────────────────────────────────────────
function VideoPlayer({ playlist, thumbnail }) {
  const ref = useRef();
  const hlsRef = useRef();

  useEffect(() => {
    if (!playlist || !ref.current) return;

    const init = () => {
      if (hlsRef.current) { hlsRef.current.destroy(); hlsRef.current = null; }
      if (window.Hls && window.Hls.isSupported()) {
        const hls = new window.Hls({ enableWorker: false });
        hlsRef.current = hls;
        hls.loadSource(playlist);
        hls.attachMedia(ref.current);
        hls.on(window.Hls.Events.MANIFEST_PARSED, () => {
          ref.current?.play().catch(() => {});
        });
      } else if (ref.current.canPlayType("application/vnd.apple.mpegurl")) {
        ref.current.src = playlist;
        ref.current.play().catch(() => {});
      }
    };

    if (window.Hls) {
      init();
    } else {
      const script = document.createElement("script");
      script.src = "https://cdnjs.cloudflare.com/ajax/libs/hls.js/1.4.10/hls.min.js";
      script.onload = init;
      document.head.appendChild(script);
    }

    return () => { hlsRef.current?.destroy(); };
  }, [playlist]);

  return (
    <video
      ref={ref}
      controls
      poster={thumbnail}
      style={{ width: "100%", background: "#000", display: "block", maxHeight: "75vh", minHeight: 300 }}
    />
  );
}

// ─── Skeleton Card ────────────────────────────────────────────────────────────
function SkeletonCard() {
  return (
    <div>
      <div style={{ width: "100%", aspectRatio: "16/9", borderRadius: 12, background: "#272727" }} />
      <div style={{ display: "flex", gap: 12, padding: "12px 0" }}>
        <div style={{ width: 36, height: 36, borderRadius: "50%", background: "#272727", flexShrink: 0 }} />
        <div style={{ flex: 1 }}>
          <div style={{ height: 14, background: "#272727", borderRadius: 4, marginBottom: 8, width: "90%" }} />
          <div style={{ height: 12, background: "#272727", borderRadius: 4, width: "60%" }} />
        </div>
      </div>
    </div>
  );
}

// ─── Video Card ───────────────────────────────────────────────────────────────
function VideoCard({ post, onWatch, onChannel, compact = false }) {
  const embed = post?.embed;
  const author = post?.author;
  const rec = post?.record;
  if (!embed || embed.$type !== "app.bsky.embed.video#view") return null;

  const thumb = embed.thumbnail;
  const title = rec?.text || "Untitled video";
  const likes = post.likeCount || 0;

  if (compact) {
    return (
      <div
        onClick={() => onWatch(post)}
        style={{ display: "flex", gap: 8, cursor: "pointer", padding: "8px 0", borderRadius: 8 }}
        onMouseEnter={(e) => (e.currentTarget.style.opacity = "0.8")}
        onMouseLeave={(e) => (e.currentTarget.style.opacity = "1")}
      >
        <div style={{ width: 168, flex: "0 0 168px", borderRadius: 8, overflow: "hidden", background: "#272727", aspectRatio: "16/9", position: "relative" }}>
          {thumb ? (
            <img src={thumb} alt="" style={{ width: "100%", height: "100%", objectFit: "cover" }} />
          ) : (
            <div style={{ width: "100%", height: "100%", display: "flex", alignItems: "center", justifyContent: "center" }}>
              <span style={{ fontSize: 20, color: "#aaa" }}>▶</span>
            </div>
          )}
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 500, color: "#f1f1f1", lineHeight: 1.4, display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>{title}</div>
          <div
            onClick={(e) => { e.stopPropagation(); onChannel(author.handle); }}
            style={{ fontSize: 12, color: "#aaa", marginTop: 4, cursor: "pointer" }}
            onMouseEnter={(e) => (e.currentTarget.style.color = "#f1f1f1")}
            onMouseLeave={(e) => (e.currentTarget.style.color = "#aaa")}
          >
            {author.displayName || author.handle}
          </div>
          <div style={{ fontSize: 12, color: "#aaa" }}>{fmt(likes)} likes • {ago(post.indexedAt)}</div>
        </div>
      </div>
    );
  }

  return (
    <div style={{ cursor: "pointer" }} onClick={() => onWatch(post)}>
      <div style={{ width: "100%", aspectRatio: "16/9", borderRadius: 12, overflow: "hidden", background: "#1a1a1a", position: "relative" }}>
        {thumb ? (
          <img src={thumb} alt="" style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }} />
        ) : (
          <div style={{ width: "100%", height: "100%", display: "flex", alignItems: "center", justifyContent: "center" }}>
            <div style={{ width: 48, height: 48, borderRadius: "50%", background: "rgba(255,255,255,0.12)", display: "flex", alignItems: "center", justifyContent: "center" }}>
              <span style={{ fontSize: 18, marginLeft: 4 }}>▶</span>
            </div>
          </div>
        )}
      </div>
      <div style={{ display: "flex", gap: 12, padding: "12px 0 4px" }}>
        <img
          src={author.avatar || ""}
          alt=""
          onClick={(e) => { e.stopPropagation(); onChannel(author.handle); }}
          style={{ width: 36, height: 36, borderRadius: "50%", flexShrink: 0, cursor: "pointer", objectFit: "cover", background: "#3f3f3f" }}
          onError={(e) => { e.target.style.background = "#3f3f3f"; e.target.src = ""; }}
        />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 14, fontWeight: 500, color: "#f1f1f1", lineHeight: 1.4, display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>{title}</div>
          <div
            onClick={(e) => { e.stopPropagation(); onChannel(author.handle); }}
            style={{ fontSize: 13, color: "#aaa", marginTop: 2, cursor: "pointer" }}
            onMouseEnter={(e) => (e.currentTarget.style.color = "#f1f1f1")}
            onMouseLeave={(e) => (e.currentTarget.style.color = "#aaa")}
          >
            {author.displayName || author.handle}
          </div>
          <div style={{ fontSize: 13, color: "#aaa" }}>{fmt(likes)} likes • {ago(post.indexedAt)}</div>
        </div>
      </div>
    </div>
  );
}

// ─── Header ───────────────────────────────────────────────────────────────────
function Header({ onHome, onSearch, session, onLogin, onLogout, input, setInput, toggleSidebar }) {
  const submit = (e) => {
    e.preventDefault();
    if (input.trim()) onSearch(input.trim());
  };

  return (
    <header style={{
      position: "fixed", top: 0, left: 0, right: 0, height: 56,
      background: "#0f0f0f", display: "flex", alignItems: "center",
      padding: "0 16px", gap: 16, zIndex: 200,
      borderBottom: "1px solid #272727"
    }}>
      {/* Hamburger + Logo */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexShrink: 0 }}>
        <button
          onClick={toggleSidebar}
          style={{ background: "none", border: "none", color: "#f1f1f1", cursor: "pointer", padding: 8, borderRadius: "50%", display: "flex", alignItems: "center", justifyContent: "center" }}
          onMouseEnter={(e) => (e.currentTarget.style.background = "#272727")}
          onMouseLeave={(e) => (e.currentTarget.style.background = "none")}
        >
          <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor">
            <path d="M3 18h18v-2H3v2zm0-5h18v-2H3v2zm0-7v2h18V6H3z" />
          </svg>
        </button>
        <div onClick={onHome} style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer", userSelect: "none" }}>
          <div style={{ width: 34, height: 24, background: "#FF0000", borderRadius: 6, display: "flex", alignItems: "center", justifyContent: "center" }}>
            <svg width="18" height="14" viewBox="0 0 18 14" fill="none">
              <polygon points="6,1 6,13 15,7" fill="white" />
            </svg>
          </div>
          <span style={{ fontSize: 20, fontWeight: 700, color: "#f1f1f1", letterSpacing: -0.5, fontFamily: "'Roboto', sans-serif" }}>
            Sky<span style={{ color: "#aaa", fontWeight: 400 }}>Tube</span>
          </span>
        </div>
      </div>

      {/* Search */}
      <div style={{ flex: 1, display: "flex", justifyContent: "center" }}>
        <form onSubmit={submit} style={{ display: "flex", width: "100%", maxWidth: 600 }}>
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Search"
            style={{
              flex: 1, height: 40, border: "1px solid #3f3f3f", borderRight: "none",
              borderRadius: "40px 0 0 40px", background: "#121212", color: "#f1f1f1",
              padding: "0 16px", fontSize: 16, fontFamily: "'Roboto', sans-serif",
              outline: "none"
            }}
            onFocus={(e) => (e.target.style.borderColor = "#1c62b9")}
            onBlur={(e) => (e.target.style.borderColor = "#3f3f3f")}
          />
          <button
            type="submit"
            style={{
              width: 64, height: 40, background: "#272727", border: "1px solid #3f3f3f",
              borderLeft: "none", borderRadius: "0 40px 40px 0", cursor: "pointer",
              color: "#f1f1f1", display: "flex", alignItems: "center", justifyContent: "center"
            }}
            onMouseEnter={(e) => (e.currentTarget.style.background = "#3f3f3f")}
            onMouseLeave={(e) => (e.currentTarget.style.background = "#272727")}
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
              <path d="M15.5 14h-.79l-.28-.27A6.471 6.471 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z" />
            </svg>
          </button>
        </form>
      </div>

      {/* Auth */}
      <div style={{ flexShrink: 0 }}>
        {session ? (
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <div style={{
              width: 32, height: 32, borderRadius: "50%", background: "#ff0000",
              display: "flex", alignItems: "center", justifyContent: "center",
              overflow: "hidden", fontSize: 14, fontWeight: 700, color: "#fff"
            }}>
              {session.avatar
                ? <img src={session.avatar} alt="" style={{ width: "100%", height: "100%", objectFit: "cover" }} onError={(e) => (e.target.style.display = "none")} />
                : (session.handle?.[0] || "?").toUpperCase()
              }
            </div>
            <button
              onClick={onLogout}
              style={{ background: "none", border: "1px solid #3f3f3f", color: "#f1f1f1", padding: "6px 12px", borderRadius: 4, cursor: "pointer", fontSize: 13, fontFamily: "'Roboto', sans-serif" }}
              onMouseEnter={(e) => (e.currentTarget.style.background = "#272727")}
              onMouseLeave={(e) => (e.currentTarget.style.background = "none")}
            >
              Sign out
            </button>
          </div>
        ) : (
          <button
            onClick={onLogin}
            style={{
              display: "flex", alignItems: "center", gap: 8, background: "none",
              border: "1px solid #3f3f3f", color: "#3ea6ff", padding: "6px 16px",
              borderRadius: 20, cursor: "pointer", fontSize: 14, fontWeight: 500,
              fontFamily: "'Roboto', sans-serif"
            }}
            onMouseEnter={(e) => (e.currentTarget.style.background = "rgba(62,166,255,0.1)")}
            onMouseLeave={(e) => (e.currentTarget.style.background = "none")}
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
              <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 3c1.66 0 3 1.34 3 3s-1.34 3-3 3-3-1.34-3-3 1.34-3 3-3zm0 14.2c-2.5 0-4.71-1.28-6-3.22.03-1.99 4-3.08 6-3.08 1.99 0 5.97 1.09 6 3.08-1.29 1.94-3.5 3.22-6 3.22z" />
            </svg>
            Sign in
          </button>
        )}
      </div>
    </header>
  );
}

// ─── Sidebar ──────────────────────────────────────────────────────────────────
function Sidebar({ open, page, onHome, onExplore, onSubscriptions, hasSession }) {
  const Item = ({ icon, label, active, onClick, badge }) => (
    <button
      onClick={onClick}
      title={!open ? label : ""}
      style={{
        display: "flex", alignItems: "center", gap: open ? 24 : 0,
        padding: open ? "10px 12px" : "18px 0",
        width: "100%", background: active ? "#272727" : "none",
        border: "none", color: "#f1f1f1", cursor: "pointer",
        borderRadius: 10, justifyContent: open ? "flex-start" : "center",
        fontFamily: "'Roboto', sans-serif", fontSize: 14,
        fontWeight: active ? 500 : 400, position: "relative",
        transition: "background 0.1s"
      }}
      onMouseEnter={(e) => !active && (e.currentTarget.style.background = "#272727")}
      onMouseLeave={(e) => !active && (e.currentTarget.style.background = "none")}
    >
      {icon}
      {open && <span>{label}</span>}
    </button>
  );

  return (
    <aside style={{
      position: "fixed", top: 56, left: 0, bottom: 0,
      width: open ? 240 : 72, background: "#0f0f0f",
      padding: open ? "12px 12px" : "12px 4px",
      overflowY: "auto", overflowX: "hidden",
      zIndex: 100, transition: "width 0.15s ease",
      boxSizing: "border-box"
    }}>
      <Item icon={<svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path d="M10 20v-6h4v6h5v-8h3L12 3 2 12h3v8z" /></svg>} label="Home" active={page === "home"} onClick={onHome} />
      <Item icon={<svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path d="M15.5 14h-.79l-.28-.27A6.471 6.471 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z" /></svg>} label="Explore" active={page === "search"} onClick={onExplore} />
      <Item icon={<svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path d="M10 18h4v-2h-4v2zM3 6v2h18V6H3zm3 7h12v-2H6v2z" /></svg>} label="Feed" active={false} onClick={() => onExplore("video OR #video")} />
      {hasSession && (
        <>
          <div style={{ height: 1, background: "#272727", margin: "8px 0" }} />
          <Item icon={<svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path d="M16 11c1.66 0 2.99-1.34 2.99-3S17.66 5 16 5c-1.66 0-3 1.34-3 3s1.34 3 3 3zm-8 0c1.66 0 2.99-1.34 2.99-3S9.66 5 8 5C6.34 5 5 6.34 5 8s1.34 3 3 3zm0 2c-2.33 0-7 1.17-7 3.5V19h14v-2.5c0-2.33-4.67-3.5-7-3.5zm8 0c-.29 0-.62.02-.97.05 1.16.84 1.97 1.97 1.97 3.45V19h6v-2.5c0-2.33-4.67-3.5-7-3.5z" /></svg>} label="Subscriptions" active={page === "subs"} onClick={onSubscriptions} />
        </>
      )}
      {open && (
        <>
          <div style={{ height: 1, background: "#272727", margin: "12px 0" }} />
          <div style={{ padding: "4px 12px" }}>
            <div style={{ color: "#aaa", fontSize: 12, marginBottom: 8 }}>Powered by</div>
            <a href="https://bsky.app" target="_blank" rel="noreferrer" style={{ color: "#3ea6ff", fontSize: 12, textDecoration: "none" }}>Bluesky AT Protocol</a>
          </div>
        </>
      )}
    </aside>
  );
}

// ─── Login Modal ──────────────────────────────────────────────────────────────
function LoginModal({ onClose, onSuccess }) {
  const [handle, setHandle] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError("");
    try {
      const res = await fetch(`${AUTH}/com.atproto.server.createSession`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ identifier: handle.trim(), password }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.message || "Login failed");
      onSuccess(data);
    } catch (e) {
      setError(e.message || "An error occurred. Please try again.");
    }
    setLoading(false);
  };

  return (
    <div
      style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.85)", zIndex: 1000, display: "flex", alignItems: "center", justifyContent: "center" }}
      onClick={onClose}
    >
      <div
        style={{ background: "#212121", borderRadius: 12, padding: 32, width: 420, maxWidth: "90vw", boxShadow: "0 8px 32px rgba(0,0,0,0.6)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
          <div style={{ width: 28, height: 20, background: "#FF0000", borderRadius: 5, display: "flex", alignItems: "center", justifyContent: "center" }}>
            <svg width="14" height="10" viewBox="0 0 14 10" fill="none"><polygon points="4,1 4,9 11,5" fill="white" /></svg>
          </div>
          <h2 style={{ color: "#f1f1f1", fontFamily: "'Roboto', sans-serif", fontSize: 20, fontWeight: 600 }}>Sign in to SkyTube</h2>
        </div>
        <p style={{ color: "#aaa", fontSize: 13, marginBottom: 24, fontFamily: "'Roboto', sans-serif" }}>Connect with your Bluesky account to access your personalized feed.</p>

        <form onSubmit={submit}>
          <div style={{ marginBottom: 16 }}>
            <label style={{ display: "block", color: "#aaa", fontSize: 13, marginBottom: 6, fontFamily: "'Roboto', sans-serif" }}>Handle or Email</label>
            <input
              value={handle}
              onChange={(e) => setHandle(e.target.value)}
              placeholder="you.bsky.social"
              style={{ width: "100%", padding: "10px 14px", background: "#121212", border: "1px solid #3f3f3f", borderRadius: 6, color: "#f1f1f1", fontSize: 14, fontFamily: "'Roboto', sans-serif", boxSizing: "border-box" }}
              onFocus={(e) => (e.target.style.borderColor = "#1c62b9")}
              onBlur={(e) => (e.target.style.borderColor = "#3f3f3f")}
            />
          </div>
          <div style={{ marginBottom: 8 }}>
            <label style={{ display: "block", color: "#aaa", fontSize: 13, marginBottom: 6, fontFamily: "'Roboto', sans-serif" }}>App Password</label>
            <input
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              type="password"
              placeholder="xxxx-xxxx-xxxx-xxxx"
              style={{ width: "100%", padding: "10px 14px", background: "#121212", border: "1px solid #3f3f3f", borderRadius: 6, color: "#f1f1f1", fontSize: 14, fontFamily: "'Roboto', sans-serif", boxSizing: "border-box" }}
              onFocus={(e) => (e.target.style.borderColor = "#1c62b9")}
              onBlur={(e) => (e.target.style.borderColor = "#3f3f3f")}
            />
          </div>
          <p style={{ color: "#aaa", fontSize: 12, marginBottom: 20, fontFamily: "'Roboto', sans-serif" }}>
            Use an App Password from Bluesky Settings → Privacy & Security → App Passwords
          </p>
          {error && <div style={{ color: "#ff4444", fontSize: 13, marginBottom: 12, fontFamily: "'Roboto', sans-serif" }}>{error}</div>}
          <button
            type="submit"
            disabled={loading || !handle || !password}
            style={{
              width: "100%", padding: 12, background: "#ff0000", color: "#fff",
              border: "none", borderRadius: 6, fontSize: 15, fontWeight: 600,
              cursor: loading ? "not-allowed" : "pointer", fontFamily: "'Roboto', sans-serif",
              opacity: loading || !handle || !password ? 0.6 : 1,
              transition: "opacity 0.15s"
            }}
          >
            {loading ? "Signing in..." : "Sign In"}
          </button>
        </form>
      </div>
    </div>
  );
}

// ─── Home Page ────────────────────────────────────────────────────────────────
function HomePage({ videos, loading, onWatch, onChannel, onExplore }) {
  return (
    <div style={{ padding: "24px 24px" }}>
      {/* Category chips */}
      <div style={{ display: "flex", gap: 8, overflowX: "auto", marginBottom: 24, paddingBottom: 4 }}>
        {["All", "Gaming", "Music", "Tech", "Art", "Vlogs", "News", "Live"].map((chip) => (
          <button key={chip} onClick={() => chip !== "All" && onExplore(chip)}
            style={{
              flexShrink: 0, padding: "6px 12px", borderRadius: 8,
              background: chip === "All" ? "#f1f1f1" : "#272727",
              color: chip === "All" ? "#0f0f0f" : "#f1f1f1",
              border: "none", cursor: "pointer", fontSize: 14,
              fontFamily: "'Roboto', sans-serif", fontWeight: chip === "All" ? 500 : 400
            }}
          >{chip}</button>
        ))}
      </div>

      {loading ? (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: "24px 16px" }}>
          {Array(12).fill(0).map((_, i) => <SkeletonCard key={i} />)}
        </div>
      ) : videos.length === 0 ? (
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: "40vh", gap: 16, color: "#aaa" }}>
          <svg width="64" height="64" viewBox="0 0 24 24" fill="#3f3f3f"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 14.5v-9l6 4.5-6 4.5z" /></svg>
          <p style={{ fontSize: 16, fontFamily: "'Roboto', sans-serif" }}>Loading videos from Bluesky...</p>
          <p style={{ fontSize: 14, color: "#555", fontFamily: "'Roboto', sans-serif" }}>Sign in for a personalized feed, or try Explore.</p>
        </div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: "24px 16px" }}>
          {videos.map((post, i) => <VideoCard key={post.uri || i} post={post} onWatch={onWatch} onChannel={onChannel} />)}
        </div>
      )}
    </div>
  );
}

// ─── Watch Page ───────────────────────────────────────────────────────────────
function WatchPage({ post, related, thread, onWatch, onChannel }) {
  const embed = post.embed;
  const author = post.author;
  const rec = post.record;
  const replies = thread?.replies?.filter((r) => r.post) || [];

  return (
    <div style={{ display: "flex", gap: 24, padding: "24px", maxWidth: 1600, margin: "0 auto" }}>
      {/* Main */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ borderRadius: 12, overflow: "hidden", background: "#000" }}>
          <VideoPlayer playlist={embed.playlist} thumbnail={embed.thumbnail} />
        </div>

        <h1 style={{ fontSize: 18, fontWeight: 600, color: "#f1f1f1", fontFamily: "'Roboto', sans-serif", margin: "16px 0 8px", lineHeight: 1.4 }}>
          {rec?.text?.split("\n")[0] || "Video from Bluesky"}
        </h1>

        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 12, marginBottom: 12 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12, cursor: "pointer" }} onClick={() => onChannel(author.handle)}>
            <div style={{ width: 40, height: 40, borderRadius: "50%", overflow: "hidden", background: "#3f3f3f", flexShrink: 0 }}>
              {author.avatar && <img src={author.avatar} alt="" style={{ width: "100%", height: "100%", objectFit: "cover" }} />}
            </div>
            <div>
              <div style={{ color: "#f1f1f1", fontWeight: 500, fontSize: 14, fontFamily: "'Roboto', sans-serif" }}>{author.displayName || author.handle}</div>
              <div style={{ color: "#aaa", fontSize: 12 }}>@{author.handle}</div>
            </div>
            <button
              style={{ background: "#f1f1f1", border: "none", color: "#0f0f0f", padding: "8px 16px", borderRadius: 20, cursor: "pointer", fontWeight: 600, fontSize: 13, fontFamily: "'Roboto', sans-serif", marginLeft: 8 }}
              onClick={(e) => e.stopPropagation()}
            >
              Follow
            </button>
          </div>

          <div style={{ display: "flex", gap: 8 }}>
            <button style={{ background: "#272727", border: "none", color: "#f1f1f1", padding: "8px 16px", borderRadius: 20, cursor: "pointer", fontSize: 14, display: "flex", alignItems: "center", gap: 6, fontFamily: "'Roboto', sans-serif" }}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M1 21h4V9H1v12zm22-11c0-1.1-.9-2-2-2h-6.31l.95-4.57.03-.32c0-.41-.17-.79-.44-1.06L14.17 1 7.59 7.59C7.22 7.95 7 8.45 7 9v10c0 1.1.9 2 2 2h9c.83 0 1.54-.5 1.84-1.22l3.02-7.05c.09-.23.14-.47.14-.73v-2z" /></svg>
              {fmt(post.likeCount || 0)}
            </button>
            <button style={{ background: "#272727", border: "none", color: "#f1f1f1", padding: "8px 16px", borderRadius: 20, cursor: "pointer", fontSize: 14, display: "flex", alignItems: "center", gap: 6, fontFamily: "'Roboto', sans-serif" }}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M18 16.08c-.76 0-1.44.3-1.96.77L8.91 12.7c.05-.23.09-.46.09-.7s-.04-.47-.09-.7l7.05-4.11c.54.5 1.25.81 2.04.81 1.66 0 3-1.34 3-3s-1.34-3-3-3-3 1.34-3 3c0 .24.04.47.09.7L8.04 9.81C7.5 9.31 6.79 9 6 9c-1.66 0-3 1.34-3 3s1.34 3 3 3c.79 0 1.5-.31 2.04-.81l7.12 4.16c-.05.21-.08.43-.08.65 0 1.61 1.31 2.92 2.92 2.92 1.61 0 2.92-1.31 2.92-2.92s-1.31-2.92-2.92-2.92z" /></svg>
              Share
            </button>
          </div>
        </div>

        {/* Description */}
        <div style={{ background: "#212121", borderRadius: 12, padding: "12px 16px", marginBottom: 24 }}>
          <div style={{ fontSize: 13, color: "#f1f1f1", fontWeight: 500, marginBottom: 4, fontFamily: "'Roboto', sans-serif" }}>
            {fmt(post.likeCount || 0)} likes • {fmt(post.replyCount || 0)} comments • {fmt(post.repostCount || 0)} reposts • {ago(post.indexedAt)}
          </div>
          {rec?.text && (
            <div style={{ fontSize: 14, color: "#f1f1f1", marginTop: 8, whiteSpace: "pre-wrap", fontFamily: "'Roboto', sans-serif", lineHeight: 1.6 }}>
              {rec.text}
            </div>
          )}
          {embed.alt && embed.alt !== rec?.text && (
            <div style={{ fontSize: 13, color: "#aaa", marginTop: 8, fontStyle: "italic", fontFamily: "'Roboto', sans-serif" }}>{embed.alt}</div>
          )}
        </div>

        {/* Comments */}
        <div>
          <h3 style={{ color: "#f1f1f1", fontSize: 16, fontWeight: 600, marginBottom: 16, fontFamily: "'Roboto', sans-serif" }}>
            {fmt(post.replyCount || 0)} Comments
          </h3>
          {replies.length === 0 ? (
            <div style={{ color: "#aaa", fontSize: 14, fontFamily: "'Roboto', sans-serif" }}>No comments yet.</div>
          ) : (
            replies.map((r, i) => {
              const rp = r.post;
              return (
                <div key={i} style={{ display: "flex", gap: 12, marginBottom: 20 }}>
                  <div style={{ width: 32, height: 32, borderRadius: "50%", background: "#3f3f3f", flexShrink: 0, overflow: "hidden" }}>
                    {rp.author.avatar && <img src={rp.author.avatar} alt="" style={{ width: "100%", height: "100%", objectFit: "cover" }} />}
                  </div>
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 500, color: "#f1f1f1", fontFamily: "'Roboto', sans-serif" }}>
                      {rp.author.displayName || rp.author.handle}{" "}
                      <span style={{ color: "#aaa", fontWeight: 400 }}>{ago(rp.indexedAt)}</span>
                    </div>
                    <div style={{ fontSize: 14, color: "#f1f1f1", marginTop: 4, fontFamily: "'Roboto', sans-serif", lineHeight: 1.5 }}>{rp.record?.text}</div>
                    <div style={{ display: "flex", gap: 12, marginTop: 6, alignItems: "center" }}>
                      <button style={{ background: "none", border: "none", color: "#aaa", cursor: "pointer", fontSize: 12, display: "flex", alignItems: "center", gap: 4, padding: 0, fontFamily: "'Roboto', sans-serif" }}>
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M1 21h4V9H1v12zm22-11c0-1.1-.9-2-2-2h-6.31l.95-4.57.03-.32c0-.41-.17-.79-.44-1.06L14.17 1 7.59 7.59C7.22 7.95 7 8.45 7 9v10c0 1.1.9 2 2 2h9c.83 0 1.54-.5 1.84-1.22l3.02-7.05c.09-.23.14-.47.14-.73v-2z" /></svg>
                        {fmt(rp.likeCount || 0)}
                      </button>
                      <button style={{ background: "none", border: "none", color: "#aaa", cursor: "pointer", fontSize: 12, fontFamily: "'Roboto', sans-serif" }}>Reply</button>
                    </div>
                  </div>
                </div>
              );
            })
          )}
        </div>
      </div>

      {/* Related */}
      <div style={{ width: 402, flexShrink: 0 }}>
        <h3 style={{ color: "#f1f1f1", fontSize: 15, fontWeight: 600, marginBottom: 12, fontFamily: "'Roboto', sans-serif" }}>Related videos</h3>
        {related.length === 0 ? (
          <div style={{ color: "#aaa", fontSize: 14, fontFamily: "'Roboto', sans-serif" }}>No related videos found.</div>
        ) : (
          related.map((p, i) => <VideoCard key={p.uri || i} post={p} onWatch={onWatch} onChannel={onChannel} compact />)
        )}
      </div>
    </div>
  );
}

// ─── Channel Page ─────────────────────────────────────────────────────────────
function ChannelPage({ data, videos, loading, onWatch, onChannel }) {
  const [tab, setTab] = useState("Videos");
  const tabs = ["Videos", "About"];

  if (loading) return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "50vh", color: "#aaa", fontFamily: "'Roboto', sans-serif" }}>
      Loading channel...
    </div>
  );

  if (!data) return null;

  return (
    <div>
      {/* Banner */}
      <div style={{
        height: 160, background: data.banner ? "none" : "linear-gradient(135deg, #1a1a2e, #16213e, #0f3460)",
        backgroundImage: data.banner ? `url(${data.banner})` : undefined,
        backgroundSize: "cover", backgroundPosition: "center"
      }} />

      {/* Header */}
      <div style={{ padding: "0 24px", borderBottom: "1px solid #272727" }}>
        <div style={{ display: "flex", alignItems: "flex-end", gap: 24, padding: "0 0 24px", marginTop: -24 }}>
          <div style={{ width: 80, height: 80, borderRadius: "50%", border: "3px solid #0f0f0f", overflow: "hidden", background: "#3f3f3f", flexShrink: 0 }}>
            {data.avatar && <img src={data.avatar} alt="" style={{ width: "100%", height: "100%", objectFit: "cover" }} />}
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <h1 style={{ color: "#f1f1f1", fontSize: 24, fontWeight: 700, margin: "0 0 4px", fontFamily: "'Roboto', sans-serif" }}>{data.displayName || data.handle}</h1>
            <div style={{ color: "#aaa", fontSize: 14, fontFamily: "'Roboto', sans-serif" }}>
              @{data.handle} • {fmt(data.followersCount || 0)} followers • {videos.length} videos
            </div>
            {data.description && (
              <div style={{ color: "#aaa", fontSize: 13, marginTop: 6, maxWidth: 600, fontFamily: "'Roboto', sans-serif", whiteSpace: "pre-wrap" }}>{data.description?.slice(0, 200)}</div>
            )}
          </div>
          <button style={{
            flexShrink: 0, background: "#f1f1f1", border: "none", color: "#0f0f0f",
            padding: "10px 20px", borderRadius: 20, cursor: "pointer",
            fontWeight: 600, fontSize: 14, fontFamily: "'Roboto', sans-serif"
          }}>
            Follow
          </button>
        </div>

        {/* Tabs */}
        <div style={{ display: "flex", gap: 0 }}>
          {tabs.map((t) => (
            <button key={t} onClick={() => setTab(t)} style={{
              padding: "12px 20px", background: "none", border: "none",
              color: tab === t ? "#f1f1f1" : "#aaa", cursor: "pointer",
              fontSize: 14, fontWeight: tab === t ? 500 : 400,
              borderBottom: tab === t ? "3px solid #f1f1f1" : "3px solid transparent",
              fontFamily: "'Roboto', sans-serif", transition: "color 0.1s"
            }}>{t}</button>
          ))}
        </div>
      </div>

      <div style={{ padding: 24 }}>
        {tab === "Videos" && (
          videos.length === 0 ? (
            <div style={{ color: "#aaa", textAlign: "center", padding: 48, fontFamily: "'Roboto', sans-serif" }}>No videos found on this channel.</div>
          ) : (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: "24px 16px" }}>
              {videos.map((post, i) => <VideoCard key={post.uri || i} post={post} onWatch={onWatch} onChannel={onChannel} />)}
            </div>
          )
        )}
        {tab === "About" && (
          <div style={{ maxWidth: 700 }}>
            <div style={{ color: "#f1f1f1", fontFamily: "'Roboto', sans-serif" }}>
              <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 16 }}>About</h3>
              {data.description ? (
                <p style={{ fontSize: 14, color: "#aaa", lineHeight: 1.7, whiteSpace: "pre-wrap" }}>{data.description}</p>
              ) : (
                <p style={{ color: "#aaa", fontSize: 14 }}>No description provided.</p>
              )}
              <div style={{ marginTop: 24, display: "flex", flexDirection: "column", gap: 12 }}>
                {[
                  { label: "Followers", value: fmt(data.followersCount || 0) },
                  { label: "Following", value: fmt(data.followsCount || 0) },
                  { label: "Posts", value: fmt(data.postsCount || 0) },
                  { label: "Videos on SkyTube", value: fmt(videos.length) },
                ].map(({ label, value }) => (
                  <div key={label} style={{ display: "flex", gap: 24 }}>
                    <div style={{ color: "#aaa", fontSize: 14, width: 140 }}>{label}</div>
                    <div style={{ color: "#f1f1f1", fontSize: 14, fontWeight: 500 }}>{value}</div>
                  </div>
                ))}
              </div>
              <div style={{ marginTop: 24 }}>
                <a href={`https://bsky.app/profile/${data.handle}`} target="_blank" rel="noreferrer" style={{ color: "#3ea6ff", fontSize: 14 }}>
                  View on Bluesky →
                </a>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Search Page ──────────────────────────────────────────────────────────────
function SearchPage({ results, loading, query, onWatch, onChannel }) {
  const [filter, setFilter] = useState("All");

  if (loading) return (
    <div style={{ padding: 24 }}>
      <div style={{ color: "#aaa", fontSize: 14, fontFamily: "'Roboto', sans-serif", marginBottom: 24 }}>Searching for "{query}"...</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {Array(6).fill(0).map((_, i) => (
          <div key={i} style={{ display: "flex", gap: 12, padding: "8px 0" }}>
            <div style={{ width: 168, height: 94, borderRadius: 8, background: "#272727", flexShrink: 0 }} />
            <div style={{ flex: 1 }}>
              <div style={{ height: 14, background: "#272727", borderRadius: 4, marginBottom: 8, width: "80%" }} />
              <div style={{ height: 12, background: "#272727", borderRadius: 4, width: "50%" }} />
            </div>
          </div>
        ))}
      </div>
    </div>
  );

  const videos = results?.videos || [];
  const actors = results?.actors || [];

  const filterBtns = ["All", "Channels", "Videos"];

  return (
    <div style={{ padding: "16px 24px" }}>
      {/* Filter chips */}
      <div style={{ display: "flex", gap: 8, marginBottom: 20 }}>
        {filterBtns.map((f) => (
          <button key={f} onClick={() => setFilter(f)} style={{
            padding: "6px 12px", borderRadius: 8, border: "1px solid #3f3f3f",
            background: filter === f ? "#f1f1f1" : "none", color: filter === f ? "#0f0f0f" : "#f1f1f1",
            cursor: "pointer", fontSize: 14, fontFamily: "'Roboto', sans-serif"
          }}>{f}</button>
        ))}
      </div>

      {query && <div style={{ color: "#aaa", fontSize: 13, marginBottom: 16, fontFamily: "'Roboto', sans-serif" }}>Results for "{query}"</div>}

      {/* Channels */}
      {(filter === "All" || filter === "Channels") && actors.length > 0 && (
        <div style={{ marginBottom: 32 }}>
          <h3 style={{ color: "#f1f1f1", fontSize: 15, fontWeight: 600, marginBottom: 12, fontFamily: "'Roboto', sans-serif" }}>Channels</h3>
          {actors.slice(0, filter === "Channels" ? 20 : 4).map((a, i) => (
            <div key={i} onClick={() => onChannel(a.handle)}
              style={{ display: "flex", alignItems: "center", gap: 24, padding: "16px 0", cursor: "pointer", borderBottom: "1px solid #272727" }}
              onMouseEnter={(e) => (e.currentTarget.style.background = "rgba(255,255,255,0.03)")}
              onMouseLeave={(e) => (e.currentTarget.style.background = "none")}
            >
              <div style={{ width: 88, height: 88, borderRadius: "50%", background: "#3f3f3f", overflow: "hidden", flexShrink: 0 }}>
                {a.avatar && <img src={a.avatar} alt="" style={{ width: "100%", height: "100%", objectFit: "cover" }} />}
              </div>
              <div>
                <div style={{ color: "#f1f1f1", fontWeight: 500, fontSize: 15, fontFamily: "'Roboto', sans-serif" }}>{a.displayName || a.handle}</div>
                <div style={{ color: "#aaa", fontSize: 13, marginTop: 2, fontFamily: "'Roboto', sans-serif" }}>@{a.handle} • {fmt(a.followersCount || 0)} followers</div>
                {a.description && (
                  <div style={{ color: "#aaa", fontSize: 13, marginTop: 6, maxWidth: 500, fontFamily: "'Roboto', sans-serif" }}>
                    {a.description.slice(0, 120)}{a.description.length > 120 ? "..." : ""}
                  </div>
                )}
              </div>
              <button style={{ marginLeft: "auto", background: "#f1f1f1", border: "none", color: "#0f0f0f", padding: "8px 16px", borderRadius: 20, cursor: "pointer", fontWeight: 600, fontSize: 13, flexShrink: 0, fontFamily: "'Roboto', sans-serif" }}>
                Follow
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Videos */}
      {(filter === "All" || filter === "Videos") && (
        <div>
          <h3 style={{ color: "#f1f1f1", fontSize: 15, fontWeight: 600, marginBottom: 12, fontFamily: "'Roboto', sans-serif" }}>
            Videos {videos.length > 0 ? `(${videos.length})` : ""}
          </h3>
          {videos.length === 0 ? (
            <div style={{ color: "#aaa", fontSize: 14, fontFamily: "'Roboto', sans-serif", padding: "24px 0" }}>
              No videos found for "{query}". Try a different search.
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column" }}>
              {videos.map((post, i) => <VideoCard key={post.uri || i} post={post} onWatch={onWatch} onChannel={onChannel} compact />)}
            </div>
          )}
        </div>
      )}

      {!loading && videos.length === 0 && actors.length === 0 && (
        <div style={{ textAlign: "center", padding: "48px 0", color: "#aaa", fontFamily: "'Roboto', sans-serif" }}>
          <div style={{ fontSize: 48, marginBottom: 16 }}>🔍</div>
          <div style={{ fontSize: 16, marginBottom: 8 }}>No results found</div>
          <div style={{ fontSize: 14, color: "#555" }}>Try different keywords</div>
        </div>
      )}
    </div>
  );
}

// ─── Main App ─────────────────────────────────────────────────────────────────
export default function App() {
  const [session, setSession] = useState(null);
  const [page, setPage] = useState("home");
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [showLogin, setShowLogin] = useState(false);
  const [searchInput, setSearchInput] = useState("");

  const [homeVideos, setHomeVideos] = useState([]);
  const [homeLoading, setHomeLoading] = useState(true);
  const [currentVideo, setCurrentVideo] = useState(null);
  const [related, setRelated] = useState([]);
  const [thread, setThread] = useState(null);
  const [channelData, setChannelData] = useState(null);
  const [channelVideos, setChannelVideos] = useState([]);
  const [channelLoading, setChannelLoading] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState(null);
  const [searchLoading, setSearchLoading] = useState(false);

  // Load home videos on mount and when session changes
  const loadHome = useCallback(async (sess) => {
    setHomeLoading(true);
    const seen = new Set();
    const videos = [];

    const add = (posts) => {
      for (const p of posts) {
        if (p && isVid(p) && !seen.has(p.uri)) {
          videos.push(p);
          seen.add(p.uri);
        }
      }
    };

    try {
      // Authenticated timeline
      if (sess) {
        const r = await fetch(`${AUTH}/app.bsky.feed.getTimeline?limit=100`, {
          headers: { Authorization: `Bearer ${sess.accessJwt}` },
        });
        const d = await r.json();
        add((d.feed || []).map((i) => i.post));
      }

      // Supplement with searches for video content
      const terms = ["video", "watch", "clip", "vlog", "reel"];
      const results = await Promise.all(
        terms.map((t) =>
          fetch(`${PUB}/app.bsky.feed.searchPosts?q=${encodeURIComponent(t)}&limit=50&sort=latest`)
            .then((r) => r.json()).then((d) => d.posts || []).catch(() => [])
        )
      );
      for (const posts of results) add(posts);

      // Also try What's Hot feed
      const hotRes = await fetch(
        `${PUB}/app.bsky.feed.getFeed?feed=at://did:plc:z72i7hdynmk6r22z27h6tvur/app.bsky.feed.generator/whats-hot&limit=100`
      ).catch(() => null);
      if (hotRes?.ok) {
        const hotData = await hotRes.json();
        add((hotData.feed || []).map((i) => i.post));
      }
    } catch (e) {
      console.error("Error loading home:", e);
    }

    setHomeVideos(videos);
    setHomeLoading(false);
  }, []);

  useEffect(() => {
    if (page === "home") loadHome(session);
  }, [page, session, loadHome]);

  // Watch video
  const handleWatch = useCallback(async (post) => {
    setCurrentVideo(post);
    setThread(null);
    setRelated([]);
    setPage("watch");
    window.scrollTo(0, 0);

    try {
      const [threadRes, feedRes] = await Promise.all([
        fetch(`${PUB}/app.bsky.feed.getPostThread?uri=${encodeURIComponent(post.uri)}&depth=6`),
        fetch(`${PUB}/app.bsky.feed.getAuthorFeed?actor=${encodeURIComponent(post.author.did)}&limit=50`),
      ]);
      const threadData = await threadRes.json();
      const feedData = await feedRes.json();
      setThread(threadData.thread);
      setRelated(
        (feedData.feed || []).map((i) => i.post).filter((p) => isVid(p) && p.uri !== post.uri).slice(0, 15)
      );
    } catch (e) {
      console.error(e);
    }
  }, []);

  // Channel page
  const handleChannel = useCallback(async (actor) => {
    setChannelData(null);
    setChannelVideos([]);
    setChannelLoading(true);
    setPage("channel");
    window.scrollTo(0, 0);

    try {
      const [profileRes, feedRes] = await Promise.all([
        fetch(`${PUB}/app.bsky.actor.getProfile?actor=${encodeURIComponent(actor)}`),
        fetch(`${PUB}/app.bsky.feed.getAuthorFeed?actor=${encodeURIComponent(actor)}&limit=100&filter=posts_with_media`),
      ]);
      const profile = await profileRes.json();
      const feed = await feedRes.json();
      setChannelData(profile);
      setChannelVideos((feed.feed || []).map((i) => i.post).filter(isVid));
    } catch (e) {
      console.error(e);
    }
    setChannelLoading(false);
  }, []);

  // Search
  const handleSearch = useCallback(async (q) => {
    setSearchQuery(q);
    setSearchInput(q);
    setSearchResults(null);
    setSearchLoading(true);
    setPage("search");
    window.scrollTo(0, 0);

    try {
      const [postsRes, actorsRes] = await Promise.all([
        fetch(`${PUB}/app.bsky.feed.searchPosts?q=${encodeURIComponent(q)}&limit=50`),
        fetch(`${PUB}/app.bsky.actor.searchActors?q=${encodeURIComponent(q)}&limit=15`),
      ]);
      const postsData = await postsRes.json();
      const actorsData = await actorsRes.json();
      setSearchResults({
        videos: (postsData.posts || []).filter(isVid),
        actors: actorsData.actors || [],
      });
    } catch (e) {
      setSearchResults({ videos: [], actors: [] });
    }
    setSearchLoading(false);
  }, []);

  const marginLeft = sidebarOpen ? 240 : 72;

  return (
    <div style={{ fontFamily: "'Roboto', sans-serif", background: "#0f0f0f", minHeight: "100vh", color: "#f1f1f1" }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Roboto:wght@300;400;500;700&display=swap');
        *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
        ::-webkit-scrollbar { width: 8px; height: 8px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: #3f3f3f; border-radius: 4px; }
        ::-webkit-scrollbar-thumb:hover { background: #555; }
        body { background: #0f0f0f; overflow-x: hidden; }
        @keyframes shimmer { 0%,100%{opacity:1} 50%{opacity:0.4} }
        [style*="background: #272727"][style*="aspectRatio"] { animation: shimmer 1.5s ease-in-out infinite; }
        a { color: inherit; }
        button:focus { outline: none; }
        input:focus { outline: none; }
        video:focus { outline: none; }
      `}</style>

      <Header
        onHome={() => { setPage("home"); window.scrollTo(0, 0); }}
        onSearch={handleSearch}
        session={session}
        onLogin={() => setShowLogin(true)}
        onLogout={() => { setSession(null); }}
        input={searchInput}
        setInput={setSearchInput}
        toggleSidebar={() => setSidebarOpen((o) => !o)}
      />

      <Sidebar
        open={sidebarOpen}
        page={page}
        onHome={() => { setPage("home"); window.scrollTo(0, 0); }}
        onExplore={(q) => handleSearch(q || "video")}
        onSubscriptions={() => session ? handleSearch("video") : setShowLogin(true)}
        hasSession={!!session}
      />

      <main style={{
        marginLeft: marginLeft,
        marginTop: 56,
        minHeight: "calc(100vh - 56px)",
        transition: "margin-left 0.15s ease",
      }}>
        {page === "home" && (
          <HomePage
            videos={homeVideos}
            loading={homeLoading}
            onWatch={handleWatch}
            onChannel={handleChannel}
            onExplore={handleSearch}
          />
        )}
        {page === "watch" && currentVideo && (
          <WatchPage
            post={currentVideo}
            related={related}
            thread={thread}
            onWatch={handleWatch}
            onChannel={handleChannel}
          />
        )}
        {page === "channel" && (
          <ChannelPage
            data={channelData}
            videos={channelVideos}
            loading={channelLoading}
            onWatch={handleWatch}
            onChannel={handleChannel}
          />
        )}
        {page === "search" && (
          <SearchPage
            results={searchResults}
            loading={searchLoading}
            query={searchQuery}
            onWatch={handleWatch}
            onChannel={handleChannel}
          />
        )}
      </main>

      {showLogin && (
        <LoginModal
          onClose={() => setShowLogin(false)}
          onSuccess={(data) => {
            setSession(data);
            setShowLogin(false);
            loadHome(data);
          }}
        />
      )}
    </div>
  );
}
