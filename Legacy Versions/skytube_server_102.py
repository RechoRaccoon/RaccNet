"""
RaccTube local proxy server
Run:  python server.py
Then open:  http://localhost:8080
"""
import http.server
import urllib.request
import urllib.error
import urllib.parse
import json
import os
import sys
import subprocess
import tempfile
import shutil
import re
import threading
import socketserver
import time as _time
import email
import email.policy

PORT = 8080

# Simple RAM cache for public API calls (30-second TTL)
_cache      = {}
_cache_lock = threading.Lock()
CACHE_TTL   = 30

def _cache_get(url):
    with _cache_lock:
        entry = _cache.get(url)
        if entry and (_time.time() - entry[0]) < CACHE_TTL:
            return entry[1], entry[2]
    return None, None

def _cache_set(url, data, ct):
    with _cache_lock:
        _cache[url] = (_time.time(), data, ct)
        if len(_cache) > 400:
            oldest = sorted(_cache.items(), key=lambda x: x[1][0])[:80]
            for k, _ in oldest:
                del _cache[k]


HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>RaccTube</title>
<script src="https://cdn.jsdelivr.net/npm/htm@3.1.1/preact/standalone.umd.js"></script>
<link href="https://fonts.googleapis.com/css2?family=Roboto:wght@300;400;500;700&display=swap" rel="stylesheet"/>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{--accent:#00FF07;--accent-dim:rgba(0,255,7,0.12);--accent-dim-dark:rgba(0,255,7,0.08);--accent-solid-dim:#003300}
html,body{height:100%}
body{background:#0f0f0f;color:#f1f1f1;font-family:'Roboto',sans-serif;overflow-x:hidden}
::-webkit-scrollbar{width:8px;height:8px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:#3f3f3f;border-radius:0}
::-webkit-scrollbar-thumb:hover{background:#555}
*{scrollbar-width:thin;scrollbar-color:#3f3f3f transparent}
@keyframes shimmer{0%,100%{opacity:1}50%{opacity:.4}}
.shimmer{animation:shimmer 1.5s ease-in-out infinite}
.clamp2{overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;max-height:2.8em;line-height:1.4em}
button{-moz-appearance:none;cursor:pointer;font-family:'Roboto',sans-serif}
input{-moz-appearance:none;font-family:'Roboto',sans-serif}
a{color:inherit;text-decoration:none}
button:focus,input:focus,video:focus{outline:none}
#app{min-height:100vh}
</style>
</head>
<body>
<div id="app"></div>
<script>
// Apply saved theme immediately to avoid flash
(function(){
  try{
    var a=localStorage.getItem('racctube_accent');
    if(a){var s=document.createElement('style');s.id='raccnet-accent-style';s.textContent=':root{--accent:'+a+';}';document.head.appendChild(s);}

  }catch(e){}
})();
'use strict';
const { h, render, useState, useEffect, useRef, useCallback } = htmPreact;
const html = htmPreact.html;

// All API calls go through our local proxy — no CORS, no adblockers
const PUB_PROXY   = '/proxy/pub/xrpc';
const AUTH_PROXY  = '/proxy/auth/xrpc';
const VIDEO_PROXY = '/proxy/video/xrpc';
const CHAT_PROXY  = '/proxy/chat/xrpc';

// Load HLS.js eagerly so preview is instant on first hover
(function() {
  if (!window.Hls) {
    var s = document.createElement('script');
    s.src = 'https://cdn.jsdelivr.net/npm/hls.js@1.5.13/dist/hls.min.js';
    s.crossOrigin = 'anonymous';
    document.head.appendChild(s);
  }
})();

// ── Persistent session (localStorage) ────────────────────────────────────────
const SESSION_KEY = 'idkijab_session';
function saveSession(s) {
  try { localStorage.setItem(SESSION_KEY, JSON.stringify(s)); } catch(e) {}
}
function loadSession() {
  try { const s=localStorage.getItem(SESSION_KEY); return s?JSON.parse(s):null; } catch(e) { return null; }
}
function clearSession() {
  try { localStorage.removeItem(SESSION_KEY); } catch(e) {}
}

// ── Bluesky action helpers (like, repost, follow) ─────────────────────────────
// Each returns the URI of the created record (needed to undo), or null on failure.
async function bskyCreate(sess, collection, record) {
  if (!sess) return null;
  const res = await api(AUTH_PROXY+'/com.atproto.repo.createRecord', {
    method:'POST',
    headers:{'Content-Type':'application/json', 'Authorization':'Bearer '+sess.accessJwt},
    body: JSON.stringify({ repo:sess.did, collection, record })
  });
  if (!res.ok) return null;
  const d = await res.json();
  return d.uri || null;
}
async function bskyDelete(sess, collection, rkey) {
  if (!sess) return;
  await api(AUTH_PROXY+'/com.atproto.repo.deleteRecord', {
    method:'POST',
    headers:{'Content-Type':'application/json', 'Authorization':'Bearer '+sess.accessJwt},
    body: JSON.stringify({ repo:sess.did, collection, rkey })
  });
}
// Check if the viewer has liked/reposted/followed (from embedded viewer state)
function viewerLiked(post)    { return post && post.viewer && post.viewer.like; }
function viewerReposted(post) { return post && post.viewer && post.viewer.repost; }
function viewerFollows(profile) { return profile && profile.viewer && profile.viewer.following; }

async function api(url, opts) {
  opts = opts || {};
  const headers = Object.assign({'Accept':'application/json'}, opts.headers || {});
  const res = await fetch(url, Object.assign({}, opts, {headers}));
  return res;
}

const isVid = function(p) {
  if (!p || !p.embed) return false;
  const t = p.embed['$type'] || '';
  if (t === 'app.bsky.embed.video#view' || t === 'app.bsky.embed.video') return true;
  if (t === 'app.bsky.embed.recordWithMedia#view' || t === 'app.bsky.embed.recordWithMedia') {
    const m = p.embed.media;
    if (m && (m['$type'] === 'app.bsky.embed.video#view' || m['$type'] === 'app.bsky.embed.video')) return true;
  }
  return false;
};

// Detect video from raw record (searchPosts / unhydrated posts)
function isVidRaw(p) {
  if (!p) return false;
  if (isVid(p)) return true;
  if (p.embed) {
    const t = p.embed['$type'] || '';
    if (t === 'app.bsky.embed.video#view' || t === 'app.bsky.embed.video') return true;
  }
  const rec = p.record || p.value || {};
  const re2 = rec.embed || {};
  const rt = re2['$type'] || '';
  return rt === 'app.bsky.embed.video' || rt === 'app.bsky.embed.video#view';
}

function ago(d) {
  const s = Math.floor((Date.now() - new Date(d)) / 1000);
  if (s < 60) return 'just now';
  if (s < 3600) return Math.floor(s/60) + 'm ago';
  if (s < 86400) return Math.floor(s/3600) + 'h ago';
  if (s < 2592000) return Math.floor(s/86400) + 'd ago';
  if (s < 31536000) return Math.floor(s/2592000) + ' months ago';
  return Math.floor(s/31536000) + ' years ago';
}

function fmt(n) {
  if (!n) return '0';
  if (n >= 1e9) return (n/1e9).toFixed(1)+'B';
  if (n >= 1e6) return (n/1e6).toFixed(1)+'M';
  if (n >= 1e3) return (n/1e3).toFixed(1)+'K';
  return ''+n;
}

function VideoPlayer(props) {
  const ref = useRef(null);
  const hlsRef = useRef(null);
  const playlist = props.playlist;
  const thumbnail = props.thumbnail;

  useEffect(function() {
    if (!playlist || !ref.current) return;
    let active = true;
    function applyVolume() {
      if (!ref.current) return;
      var v = loadVolume();
      ref.current.volume = v;
      ref.current.muted = false;
    }
    function setup() {
      if (!active) return;
      if (hlsRef.current) { hlsRef.current.destroy(); hlsRef.current = null; }
      if (window.Hls && window.Hls.isSupported()) {
        const hls = new window.Hls({enableWorker:false, lowLatencyMode:false});
        hlsRef.current = hls;
        hls.loadSource(playlist);
        hls.attachMedia(ref.current);
        hls.on(window.Hls.Events.MANIFEST_PARSED, function() {
          if (ref.current) { applyVolume(); ref.current.play().catch(function(){}); }
        });
        hls.on(window.Hls.Events.ERROR, function(_, data) {
          if (data.fatal && ref.current) ref.current.src = playlist;
        });
      } else if (ref.current.canPlayType('application/vnd.apple.mpegurl')) {
        ref.current.src = playlist;
        ref.current.play().catch(function(){});
      } else {
        ref.current.src = playlist;
      }
    }
    if (window.Hls) {
      setup();
    } else {
      const s = document.createElement('script');
      s.src = 'https://cdn.jsdelivr.net/npm/hls.js@1.5.13/dist/hls.min.js';
      s.crossOrigin = 'anonymous';
      s.onload = setup;
      s.onerror = function() { if (ref.current) ref.current.src = playlist; };
      document.head.appendChild(s);
    }
    return function() { active = false; if (hlsRef.current) { hlsRef.current.destroy(); hlsRef.current = null; } };
  }, [playlist]);

  function onVolumeChange(e) {
    // Only save if not muted (muted = 0 in browser but user set a real level)
    if (!e.target.muted && e.target.volume > 0) {
      saveVolume(e.target.volume);
    }
  }

  return html`<video ref=${ref} controls poster=${thumbnail}
    onVolumeChange=${onVolumeChange}
    style=${{width:'100%',background:'#000',display:'block',maxHeight:'75vh',minHeight:'300px'}}/>`;
}

function Avatar(props) {
  const size = props.size || 36;
  const [err, setErr] = useState(false);
  const [hov, setHov] = useState(false);
  const clickable = !!props.onClick;
  const st = {
    width:size, height:size, borderRadius:'50%', flexShrink:0, overflow:'hidden',
    background:'#3f3f3f', cursor: clickable ? 'pointer' : 'default',
    display:'flex', alignItems:'center', justifyContent:'center',
    fontSize:size*0.4, color:'#aaa', fontWeight:600,
    outline: (clickable && hov) ? '2px solid var(--accent)' : '2px solid transparent',
    transition:'outline 0.15s',
    boxSizing:'border-box'
  };
  return html`<div style=${st} onClick=${props.onClick} title=${props.title||''}
    onMouseEnter=${function(){if(clickable) setHov(true);}}
    onMouseLeave=${function(){setHov(false);}}>
    ${props.src && !err
      ? html`<img src=${props.src} alt="" onError=${function(){setErr(true);}} style=${{width:'100%',height:'100%',objectFit:'cover',display:'block'}}/>`
      : '?'}
  </div>`;
}

function Thumb(props) {
  const [err, setErr] = useState(false);
  if (props.src && !err) {
    return html`<img src=${props.src} alt="" onError=${function(){setErr(true);}}
      style=${{width:'100%',height:'100%',objectFit:'cover',display:'block'}}/>`;
  }
  return html`<div style=${{width:'100%',height:'100%',display:'flex',alignItems:'center',justifyContent:'center'}}>
    <svg width="48" height="48" viewBox="0 0 24 24" fill="rgba(255,255,255,0.25)">
      <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 14.5v-9l6 4.5-6 4.5z"/>
    </svg>
  </div>`;
}

function SkeletonCard() {
  return html`<div>
    <div class="shimmer" style=${{width:'100%',paddingBottom:'56.25%',borderRadius:0,background:'#272727'}}/>
    <div style=${{display:'flex',gap:12,paddingTop:12}}>
      <div class="shimmer" style=${{width:36,height:36,borderRadius:'50%',background:'#272727',flexShrink:0}}/>
      <div style=${{flex:1}}>
        <div class="shimmer" style=${{height:14,background:'#272727',borderRadius:0,marginBottom:8,width:'90%'}}/>
        <div class="shimmer" style=${{height:12,background:'#272727',borderRadius:0,width:'60%'}}/>
      </div>
    </div>
  </div>`;
}

function VideoCard(props) {
  const post = props.post;
  // Accept both fully-hydrated (has playlist) and raw video posts (from searchPosts)
  if (!isVid(post) && !isVidRaw(post)) return null;
  const embed  = post.embed || {};
  const author = post.author;
  const rec    = post.record;
  const title = (rec && rec.text) || 'Untitled video';
  const [hovering, setHovering] = useState(false);
  const [previewReady, setPreviewReady] = useState(false);
  const videoRef = useRef(null);
  const hoverTimer = useRef(null);

  const hlsPreviewRef = useRef(null);

  function onEnter() {
    hoverTimer.current = setTimeout(function() {
      setHovering(true);
    }, 500);
  }
  function onLeave() {
    clearTimeout(hoverTimer.current);
    setHovering(false);
    setPreviewReady(false);
    if (videoRef.current) {
      videoRef.current.pause();
      videoRef.current.currentTime = 0;
    }
    if (hlsPreviewRef.current) {
      hlsPreviewRef.current.destroy();
      hlsPreviewRef.current = null;
    }
  }

  useEffect(function() {
    if (!hovering || !videoRef.current || !embed || !embed.playlist) return;
    const vid = videoRef.current;

    function startHls() {
      if (hlsPreviewRef.current) { hlsPreviewRef.current.destroy(); }
      if (window.Hls && window.Hls.isSupported()) {
        const hls = new window.Hls({enableWorker:false, lowLatencyMode:false, maxBufferLength:10});
        hlsPreviewRef.current = hls;
        hls.loadSource(embed.playlist);
        hls.attachMedia(vid);
        hls.on(window.Hls.Events.MANIFEST_PARSED, function() {
          vid.play().catch(function(){});
          setPreviewReady(true);
        });
        hls.on(window.Hls.Events.ERROR, function(_, data) {
          if (data.fatal) {
            vid.src = embed.playlist;
            vid.play().catch(function(){});
            setPreviewReady(true);
          }
        });
      } else {
        vid.src = embed.playlist;
        vid.play().catch(function(){});
        setPreviewReady(true);
      }
    }

    if (window.Hls) {
      startHls();
    } else {
      // HLS not loaded yet — wait for it
      var check = setInterval(function() {
        if (window.Hls) { clearInterval(check); startHls(); }
      }, 100);
      return function() { clearInterval(check); };
    }
  }, [hovering]);

  const [cardHov, setCardHov] = useState(false);
  return html`<div style=${{cursor:'pointer'}} onClick=${function(){props.onWatch(post);}}>
    <div style=${{width:'100%',paddingBottom:'56.25%',overflow:'hidden',background:'#1a1a1a',position:'relative',
      outline:cardHov?'2px solid var(--accent)':'2px solid transparent',transition:'outline 0.15s'}}
      onMouseEnter=${function(){setCardHov(true);if(embed.playlist)onEnter();}}
      onMouseLeave=${function(){setCardHov(false);if(embed.playlist)onLeave();}}>
      <div style=${{position:'absolute',top:0,left:0,right:0,bottom:0,
        opacity:(hovering&&previewReady)?0:1,transition:'opacity 0.3s'}}>
        <${Thumb} src=${embed.thumbnail||(embed.images&&embed.images[0]&&embed.images[0].thumb)||null}/>
      </div>
      <video ref=${videoRef} muted playsinline loop
        style=${{position:'absolute',top:0,left:0,width:'100%',height:'100%',objectFit:'cover',
          opacity:(hovering&&previewReady)?1:0,transition:'opacity 0.4s',
          display:hovering?'block':'none'}}/>
      ${hovering&&previewReady?html`<div style=${{position:'absolute',bottom:6,right:6,
        background:'rgba(0,0,0,0.7)',color:'var(--accent)',fontSize:10,padding:'2px 6px',fontWeight:600,letterSpacing:1}}>
        PREVIEW
      </div>`:null}
    </div>
    <div style=${{display:'flex',gap:12,paddingTop:12}}>
      <${Avatar} src=${author.avatar} onClick=${function(e){e.stopPropagation();props.onChannel(author.handle);}}/>
      <div style=${{flex:1,minWidth:0}}>
        <div class="clamp2" style=${{fontSize:14,fontWeight:500,color:'#f1f1f1'}}>${title}</div>
        <div onClick=${function(e){e.stopPropagation();props.onChannel(author.handle);}}
          style=${{fontSize:13,color:'#aaa',marginTop:4,cursor:'pointer'}}
          onMouseEnter=${function(e){e.currentTarget.style.color='#f1f1f1';}}
          onMouseLeave=${function(e){e.currentTarget.style.color='#aaa';}}>
          ${author.displayName || author.handle}
        </div>
        <div style=${{fontSize:13,color:'#aaa'}}>${fmt(post.likeCount||0)} likes · ${ago(post.indexedAt)}</div>
      </div>
    </div>
  </div>`;
}

function VideoCardCompact(props) {
  const post = props.post;
  if (!isVid(post) && !isVidRaw(post)) return null;
  const embed = post.embed || {}, author = post.author, rec = post.record;
  const title = (rec && rec.text) || 'Untitled video';
  return html`<div onClick=${function(){props.onWatch(post);}}
    style=${{display:'flex',gap:8,cursor:'pointer',padding:'8px 0'}}
    onMouseEnter=${function(e){e.currentTarget.style.opacity='0.8';}}
    onMouseLeave=${function(e){e.currentTarget.style.opacity='1';}}>
    <div style=${{width:168,flexShrink:0,borderRadius:0,overflow:'hidden',background:'#272727',aspectRatio:'16/9',position:'relative'}}>
      <div style=${{position:'absolute',top:0,left:0,right:0,bottom:0}}><${Thumb} src=${embed.thumbnail}/></div>
    </div>
    <div style=${{flex:1,minWidth:0}}>
      <div class="clamp2" style=${{fontSize:13,fontWeight:500,color:'#f1f1f1'}}>${title}</div>
      <div onClick=${function(e){e.stopPropagation();props.onChannel(author.handle);}}
        style=${{fontSize:12,color:'#aaa',marginTop:4,cursor:'pointer'}}
        onMouseEnter=${function(e){e.currentTarget.style.color='#f1f1f1';}}
        onMouseLeave=${function(e){e.currentTarget.style.color='#aaa';}}>
        ${author.displayName || author.handle}
      </div>
      <div style=${{fontSize:12,color:'#aaa'}}>${fmt(post.likeCount||0)} likes · ${ago(post.indexedAt)}</div>
    </div>
  </div>`;
}

function Header(props) {
  function submit(e) { e.preventDefault(); if (props.input.trim()) props.onSearch(props.input.trim()); }
  return html`<header style=${{position:'fixed',top:0,left:0,right:0,height:56,background:'#0f0f0f',
    display:'flex',alignItems:'center',padding:'0 16px',gap:16,zIndex:200,borderBottom:'1px solid var(--accent)'}}>
    <div style=${{display:'flex',alignItems:'center',gap:4,flexShrink:0}}>
      <button onClick=${props.onBack} title="Back"
        style=${{background:'none',border:'none',color:'#f1f1f1',padding:8,display:'flex',alignItems:'center',justifyContent:'center',cursor:'pointer'}}
        onMouseEnter=${function(e){e.currentTarget.style.background='#272727';}}
        onMouseLeave=${function(e){e.currentTarget.style.background='none';}}>
        <svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor"><path d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z"/></svg>
      </button>
      <button onClick=${props.onForward} title="Forward"
        style=${{background:'none',border:'none',color:'#f1f1f1',padding:8,display:'flex',alignItems:'center',justifyContent:'center',cursor:'pointer'}}
        onMouseEnter=${function(e){e.currentTarget.style.background='#272727';}}
        onMouseLeave=${function(e){e.currentTarget.style.background='none';}}>
        <svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor"><path d="M12 4l-1.41 1.41L16.17 11H4v2h12.17l-5.58 5.59L12 20l8-8z"/></svg>
      </button>
      <div onClick=${props.onHome} style=${{display:'flex',alignItems:'center',gap:8,cursor:'pointer',userSelect:'none'}}>
          <img src="data:image/png;base64,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" width="36" height="36" style="display:block;image-rendering:auto;border-radius:4px"/>
          <span style=${{fontSize:20,fontWeight:800,color:'var(--accent)',letterSpacing:-0.5}}>
            RaccTube
          </span>
        </div>
    </div>
    <div style=${{flex:1,display:'flex',justifyContent:'center'}}>
      <form onSubmit=${submit} style=${{display:'flex',width:'100%',maxWidth:600}}>
        <input value=${props.input} onInput=${function(e){props.setInput(e.target.value);}}
          placeholder="Search"
          style=${{flex:1,height:40,border:'1px solid var(--accent)',borderRight:'none',
            borderRadius:0,background:'#121212',color:'#f1f1f1',padding:'0 16px',fontSize:16}}
          onFocus=${function(e){e.target.style.borderColor='var(--accent)';}}
          onBlur=${function(e){e.target.style.borderColor='var(--accent)';}}/>
        <button type="submit"
          style=${{width:64,height:40,background:'var(--accent-solid-dim)',border:'1px solid var(--accent)',borderLeft:'none',
            borderRadius:0,color:'#f1f1f1',display:'flex',alignItems:'center',justifyContent:'center'}}
          onMouseEnter=${function(e){e.currentTarget.style.background='var(--accent-dim)';}}
          onMouseLeave=${function(e){e.currentTarget.style.background='var(--accent-solid-dim)';}}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
            <path d="M15.5 14h-.79l-.28-.27A6.471 6.471 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/>
          </svg>
        </button>
      </form>
    </div>
    <div style=${{flexShrink:0,display:'flex',alignItems:'center',gap:8}}>
      ${props.session?html`
        <button onClick=${props.onUpload} title="Upload video"
          style=${{background:'var(--accent)',border:'none',color:'#000',padding:'8px 16px',
            display:'flex',alignItems:'center',gap:6,fontWeight:700,fontSize:13,cursor:'pointer',borderRadius:0}}
          onMouseEnter=${function(e){e.currentTarget.style.background='var(--accent-dim)';e.currentTarget.style.color='var(--accent)';}}
          onMouseLeave=${function(e){e.currentTarget.style.background='var(--accent)';e.currentTarget.style.color='#000';}}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M9 16h6v-6h4l-7-7-7 7h4v6zm-4 2h14v2H5v-2z"/></svg>
          Upload
        </button>
      `:null}
      ${props.session ? html`
        <div style=${{display:'flex',alignItems:'center',gap:12}}>
          <${Avatar}
            src=${props.session.avatar}
            size=${32}
            onClick=${function(){props.onMyChannel&&props.onMyChannel();}}
            title="My Channel"/>

        </div>
      ` : html`
        <button onClick=${props.onLogin}
          style=${{display:'flex',alignItems:'center',gap:8,background:'none',border:'1px solid var(--accent)',
            color:'var(--accent)',padding:'6px 16px',borderRadius:0,fontSize:14,fontWeight:500}}
          onMouseEnter=${function(e){e.currentTarget.style.background='rgba(62,166,255,0.1)';}}
          onMouseLeave=${function(e){e.currentTarget.style.background='none';}}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
            <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 3c1.66 0 3 1.34 3 3s-1.34 3-3 3-3-1.34-3-3 1.34-3 3-3zm0 14.2c-2.5 0-4.71-1.28-6-3.22.03-1.99 4-3.08 6-3.08 1.99 0 5.97 1.09 6 3.08-1.29 1.94-3.5 3.22-6 3.22z"/>
          </svg>
          Sign in
        </button>
      `}
    </div>
  </header>`;
}

function SideItem(props) {
  return html`<button onClick=${props.onClick} title=${!props.open ? props.label : ''}
    style=${{display:'flex',alignItems:'center',gap:props.open?24:0,padding:props.open?'10px 12px':'18px 0',
      width:'100%',background:props.active?'var(--accent-dim)':'none',border:'none',
      borderLeft:props.active?'3px solid var(--accent)':'3px solid transparent',
      color:props.active?'var(--accent)':'#f1f1f1',
      borderRadius:0,justifyContent:props.open?'flex-start':'center',fontSize:14,
      fontWeight:props.active?600:400,transition:'background 0.1s'}}
    onMouseEnter=${function(e){if(!props.active)e.currentTarget.style.background='var(--accent-dim-dark)';}}
    onMouseLeave=${function(e){if(!props.active)e.currentTarget.style.background='none';}}>
    ${props.icon}
    ${props.open ? html`<span>${props.label}</span>` : null}
  </button>`;
}

function Sidebar(props) {
  const open = props.open;
  const H = html`<svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path d="M10 20v-6h4v6h5v-8h3L12 3 2 12h3v8z"/></svg>`;
  const S = html`<svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path d="M15.5 14h-.79l-.28-.27A6.471 6.471 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/></svg>`;
  const F = html`<svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path d="M10 18h4v-2h-4v2zM3 6v2h18V6H3zm3 7h12v-2H6v2z"/></svg>`;
  const U = html`<svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path d="M16 11c1.66 0 2.99-1.34 2.99-3S17.66 5 16 5c-1.66 0-3 1.34-3 3s1.34 3 3 3zm-8 0c1.66 0 2.99-1.34 2.99-3S9.66 5 8 5C6.34 5 5 6.34 5 8s1.34 3 3 3zm0 2c-2.33 0-7 1.17-7 3.5V19h14v-2.5c0-2.33-4.67-3.5-7-3.5zm8 0c-.29 0-.62.02-.97.05 1.16.84 1.97 1.97 1.97 3.45V19h6v-2.5c0-2.33-4.67-3.5-7-3.5z"/></svg>`;
  return html`<aside style=${{position:'fixed',top:56,left:0,bottom:0,width:open?240:72,background:'#0f0f0f',
    padding:open?'12px':'12px 4px',overflowY:'auto',overflowX:'hidden',zIndex:100,
    transition:'width 0.15s ease',boxSizing:'border-box',borderRight:'1px solid var(--accent)'}}>
    <${SideItem} open=${open} icon=${H} label="Home"          active=${props.page==='home'}   onClick=${props.onHome}/>
    <${SideItem} open=${open} icon=${S} label="Explore"       active=${props.page==='search'} onClick=${function(){props.onExplore('video');}}/>
    <${SideItem} open=${open} icon=${F} label="Feed"          active=${props.page==='feed'}   onClick=${props.onFeed}/>
    ${props.hasSession ? html`
      <div style=${{height:1,background:'var(--accent)',margin:'8px 0'}}/>
      <${SideItem} open=${open} icon=${U} label="Subscriptions" active=${props.page==='subs'} onClick=${props.onSubs}/>
    ` : null}
    ${open ? html`
      <div style=${{height:1,background:'#272727',margin:'12px 0'}}/>
      <div style=${{padding:'4px 12px'}}>
        <div style=${{color:'#aaa',fontSize:12,marginBottom:6}}>RaccTube</div>
        <a href="https://bsky.app" target="_blank" rel="noreferrer" style=${{color:'var(--accent)',fontSize:12}}>Bluesky AT Protocol</a>
        <div style=${{color:'var(--accent)',fontSize:11,marginTop:8}}>✓ Running via local proxy</div>
      </div>
    ` : null}
  </aside>`;
}

function LoginModal(props) {
  const [handle, setHandle] = useState('');
  const [pw, setPw] = useState('');
  const [err, setErr] = useState('');
  const [loading, setLoading] = useState(false);
  async function submit(e) {
    e.preventDefault(); setLoading(true); setErr('');
    try {
      const res = await api(AUTH_PROXY+'/com.atproto.server.createSession', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({identifier:handle.trim(), password:pw})
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.message || 'Login failed');
      props.onSuccess(data);
    } catch(e2) { setErr(e2.message||'Error'); }
    setLoading(false);
  }
  const iSt = {width:'100%',padding:'10px 14px',background:'#121212',border:'1px solid #3f3f3f',borderRadius:0,color:'#f1f1f1',fontSize:14,boxSizing:'border-box'};
  return html`<div onClick=${props.onClose}
    style=${{position:'fixed',top:0,left:0,right:0,bottom:0,background:'rgba(0,0,0,0.85)',zIndex:1000,display:'flex',alignItems:'center',justifyContent:'center'}}>
    <div onClick=${function(e){e.stopPropagation();}}
      style=${{background:'#212121',borderRadius:0,padding:32,width:420,maxWidth:'90vw',boxShadow:'0 8px 32px rgba(0,0,0,0.6)'}}>
      <div style=${{display:'flex',alignItems:'center',gap:10,marginBottom:6}}>
        <img src="data:image/png;base64,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" width="48" height="48" style="display:block;image-rendering:auto;border-radius:4px"/>
        <h2 style=${{color:'#f1f1f1',fontSize:20,fontWeight:600}}>Sign in to RaccTube</h2>
      </div>
      <p style=${{color:'#aaa',fontSize:13,marginBottom:24}}>Connect your Bluesky account for a personalized video feed.</p>
      <form onSubmit=${submit}>
        <div style=${{marginBottom:16}}>
          <label style=${{display:'block',color:'#aaa',fontSize:13,marginBottom:6}}>Handle or Email</label>
          <input value=${handle} onInput=${function(e){setHandle(e.target.value);}} placeholder="you.bsky.social" style=${iSt}
            onFocus=${function(e){e.target.style.borderColor='var(--accent)';}} onBlur=${function(e){e.target.style.borderColor='#3f3f3f';}}/>
        </div>
        <div style=${{marginBottom:8}}>
          <label style=${{display:'block',color:'#aaa',fontSize:13,marginBottom:6}}>App Password</label>
          <input type="password" value=${pw} onInput=${function(e){setPw(e.target.value);}} placeholder="xxxx-xxxx-xxxx-xxxx" style=${iSt}
            onFocus=${function(e){e.target.style.borderColor='var(--accent)';}} onBlur=${function(e){e.target.style.borderColor='#3f3f3f';}}/>
        </div>
        <p style=${{color:'#aaa',fontSize:12,marginBottom:20}}>Create an App Password at Bluesky Settings → Privacy & Security → App Passwords</p>
        ${err ? html`<div style=${{color:'#ff6666',fontSize:13,marginBottom:12}}>${err}</div>` : null}
        <button type="submit" disabled=${loading||!handle||!pw}
          style=${{width:'100%',padding:12,background:'#ff0000',color:'#fff',border:'none',borderRadius:0,fontSize:15,fontWeight:600,opacity:(loading||!handle||!pw)?0.6:1}}>
          ${loading ? 'Signing in...' : 'Sign In'}
        </button>
      </form>
    </div>
  </div>`;
}

const GRID_PAGE = 24;
const LIST_PAGE = 20;

// ── useScrollLoad: auto-loads more as user scrolls near the bottom ─────────
function useScrollLoad(hasMore, loadMore) {
  useEffect(function() {
    if (!hasMore) return;
    function check() {
      if (window.innerHeight + window.scrollY >= document.body.scrollHeight - 400) {
        loadMore();
      }
    }
    window.addEventListener('scroll', check, {passive: true});
    check(); // run once immediately for short pages
    return function() { window.removeEventListener('scroll', check); };
  }, [hasMore, loadMore]);
}

// ── VideoGrid ─────────────────────────────────────────────────────────────────
// ── Content filter helpers ────────────────────────────────────────────────────
const _ADULT_LBLS=['sexual','porn','nudity','graphic-media','adult','nsfw'];
function isAdultPost(post){
  if(!post) return false;
  var lbls=(post.labels||[]).concat((post.record&&post.record.labels&&post.record.labels.values)||[]);
  return lbls.some(function(l){return _ADULT_LBLS.indexOf(l.val||l.name||l)!==-1;});
}
function filterByContent(posts, filterMode){
  if(!filterMode||filterMode==='all') return posts;
  return posts.filter(function(p){
    var post=p&&p.post?p.post:p;
    var adult=isAdultPost(post);
    if(filterMode==='sfw') return !adult;
    if(filterMode==='nsfw') return adult;
    return true;
  });
}

function VideoGrid(props) {
  const [visible, setVisible] = useState(GRID_PAGE);
  const prevFirstUri = useRef('');
  const _cfv = loadFilter();
  const videos = filterByContent((props.videos||[]).map(function(v){return {post:v};}), _cfv).map(function(x){return x.post||x;});
  const shown   = videos.slice(0, visible);
  const hasMore = visible < videos.length;

  const firstUri = videos.length ? videos[0].uri : '';
  if (firstUri && firstUri !== prevFirstUri.current) {
    prevFirstUri.current = firstUri;
    if (visible !== GRID_PAGE) setVisible(GRID_PAGE);
  }

  const loadMore = useCallback(function() {
    setVisible(function(v) { return v + GRID_PAGE; });
  }, []);

  useScrollLoad(hasMore && !props.loading, loadMore);

  if (props.loading && !videos.length) {
    return html`<div style=${{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(280px,1fr))',gap:'24px 16px'}}>
      ${[0,1,2,3,4,5,6,7,8,9,10,11].map(function(i){return html`<${SkeletonCard} key=${i}/>`;})}</div>`;
  }
  if (!videos.length) {
    return html`<div style=${{display:'flex',flexDirection:'column',alignItems:'center',justifyContent:'center',height:'40vh',gap:16,color:'#aaa'}}>
      <svg width="64" height="64" viewBox="0 0 24 24" fill="#3f3f3f"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 14.5v-9l6 4.5-6 4.5z"/></svg>
      <p style=${{fontSize:16}}>No videos found.</p>
    </div>`;
  }
  return html`<div>
    <div style=${{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(280px,1fr))',gap:'24px 16px'}}>
      ${shown.map(function(p,i){return html`<${VideoCard} key=${p.uri||i} post=${p} onWatch=${props.onWatch} onChannel=${props.onChannel}/>`;})}
    </div>
  </div>`;
}

// ── VideoListCompact ──────────────────────────────────────────────────────────
function VideoListCompact(props) {
  const [visible, setVisible] = useState(LIST_PAGE);
  const prevFirstUri = useRef('');
  const videos  = props.videos || [];
  const shown   = videos.slice(0, visible);
  const hasMore = visible < videos.length;

  const firstUri = videos.length ? videos[0].uri : '';
  if (firstUri && firstUri !== prevFirstUri.current) {
    prevFirstUri.current = firstUri;
    if (visible !== LIST_PAGE) setVisible(LIST_PAGE);
  }

  const loadMore = useCallback(function() {
    setVisible(function(v) { return v + LIST_PAGE; });
  }, []);

  useScrollLoad(hasMore, loadMore);

  // Filter to only actual video posts before rendering so no null cards appear
  const validShown = shown.filter(function(p){ return isVid(p)||isVidRaw(p); });
  if (!validShown.length && !videos.length) return props.empty || null;
  if (!validShown.length) return null;
  return html`<div>
    ${validShown.map(function(p,i){return html`<${VideoCardCompact} key=${p.uri||i} post=${p} onWatch=${props.onWatch} onChannel=${props.onChannel}/>`;})}
  </div>`;
}


// ── Persistence helpers ───────────────────────────────────────────────────────
// ── Watch History (localStorage, permanent) ──────────────────────────────────
const HISTORY_KEY = 'idkijab_history';
const VOLUME_KEY  = 'racctube_volume';
const ACCENT_KEY  = 'racctube_accent';
const FILTER_KEY  = 'racctube_filter'; // 'all' | 'sfw' | 'nsfw'
function loadAccent(){ try{return localStorage.getItem(ACCENT_KEY)||'#00FF07';}catch(e){return '#00FF07';} }
function saveAccent(v){ try{localStorage.setItem(ACCENT_KEY,v);}catch(e){} }
function loadFilter(){ try{return localStorage.getItem(FILTER_KEY)||'all';}catch(e){return 'all';} }
function saveFilter(v){ try{localStorage.setItem(FILTER_KEY,v);}catch(e){} }
function hexToRgb(hex){
  var r=parseInt(hex.slice(1,3),16),g=parseInt(hex.slice(3,5),16),b=parseInt(hex.slice(5,7),16);
  return isNaN(r)?null:{r:r,g:g,b:b};
}
function applyAccent(color){
  document.documentElement.style.setProperty('--accent', color);
  // Derive dim version (15% opacity overlay, used for backgrounds)
  var rgb=hexToRgb(color);
  var dim=rgb?'rgba('+rgb.r+','+rgb.g+','+rgb.b+',0.12)':'var(--accent-dim)';
  var dimDark=rgb?'rgba('+rgb.r+','+rgb.g+','+rgb.b+',0.08)':'var(--accent-dim-dark)';
  var dimSolid=rgb?'rgb('+Math.round(rgb.r*0.12)+','+Math.round(rgb.g*0.12)+','+Math.round(rgb.b*0.12)+')':'var(--accent-solid-dim)';
  document.documentElement.style.setProperty('--accent-dim', dim);
  document.documentElement.style.setProperty('--accent-dim-dark', dimDark);
  document.documentElement.style.setProperty('--accent-solid-dim', dimSolid);
  var s=document.getElementById('raccnet-accent-style');
  if(!s){s=document.createElement('style');s.id='raccnet-accent-style';document.head.appendChild(s);}
  s.textContent=':root{--accent:'+color+';--accent-dim:'+dim+';--accent-dim-dark:'+dimDark+';--accent-solid-dim:'+dimSolid+';}';
}

function loadVolume(){ try{ var v=localStorage.getItem(VOLUME_KEY); return v!==null?parseFloat(v):1; }catch(e){return 1;} }
function saveVolume(v){ try{ localStorage.setItem(VOLUME_KEY,String(v)); }catch(e){} }
function loadHistory(){
  try{var h=localStorage.getItem(HISTORY_KEY);return h?JSON.parse(h):[];}catch(e){return[];}
}
function saveHistory(items){
  try{localStorage.setItem(HISTORY_KEY,JSON.stringify(items.slice(0,500)));}catch(e){}
}
function addToHistory(post){
  if(!post||!post.uri) return;
  var h=loadHistory();
  h=h.filter(function(p){return p.uri!==post.uri;});
  h.unshift({uri:post.uri,cid:post.cid,indexedAt:post.indexedAt,
    likeCount:post.likeCount,repostCount:post.repostCount,
    record:{text:(post.record&&post.record.text)||''},
    embed:{$type:(post.embed&&post.embed['$type'])||'',
      thumbnail:(post.embed&&post.embed.thumbnail)||null,
      playlist:(post.embed&&post.embed.playlist)||null},
    author:{did:(post.author&&post.author.did)||'',
      handle:(post.author&&post.author.handle)||'',
      displayName:(post.author&&post.author.displayName)||'',
      avatar:(post.author&&post.author.avatar)||null}});
  saveHistory(h);
}

const PAGE_KEY       = 'idkijab_lastpage';
const CHAN_KEY        = 'idkijab_lastchan';    // last channel handle
const FEED_KEY       = 'idkijab_lastfeed';    // last feed URI
const CHANTAB_KEY    = 'idkijab_chantab';     // last channel tab
const FEEDTAB_KEY    = 'idkijab_feedtab';     // last feed tab

function saveLastPage(p){ try{localStorage.setItem(PAGE_KEY,p);}catch(e){} }
function loadLastPage(){ try{return localStorage.getItem(PAGE_KEY)||'subs';}catch(e){return 'subs';} }
function saveLastChan(h){ try{localStorage.setItem(CHAN_KEY,h||'');}catch(e){} }
function loadLastChan(){ try{return localStorage.getItem(CHAN_KEY)||'';}catch(e){return '';} }
function saveLastFeed(u){ try{localStorage.setItem(FEED_KEY,u||'');}catch(e){} }
function loadLastFeed(){ try{return localStorage.getItem(FEED_KEY)||'';}catch(e){return '';} }
function saveChanTab(t){ try{localStorage.setItem(CHANTAB_KEY,t||'Videos');}catch(e){} }
function loadChanTab(){ try{return localStorage.getItem(CHANTAB_KEY)||'Videos';}catch(e){return 'Videos';} }
function saveFeedTab(t){ try{localStorage.setItem(FEEDTAB_KEY,t||'Videos');}catch(e){} }
function loadFeedTab(){ try{return localStorage.getItem(FEEDTAB_KEY)||'Videos';}catch(e){return 'Videos';} }

// ── Default (logged-out) feed list ────────────────────────────────────────────
const DEFAULT_FEEDS = [
  {uri:'at://did:plc:z72i7hdynmk6r22z27h6tvur/app.bsky.feed.generator/whats-hot',    displayName:"What\u2019s Hot"},
  {uri:'at://did:plc:z72i7hdynmk6r22z27h6tvur/app.bsky.feed.generator/with-friends', displayName:'Popular with Friends'},
  {uri:'at://did:plc:tenurhgjptubkk5zf5xxn4wv/app.bsky.feed.generator/discover',     displayName:'Discover'},
];

// ── Subscriptions Page ────────────────────────────────────────────────────────
// ── FollowStrip — scrollable row of channel avatars with arrow buttons ────────
function FollowStrip(props) {
  const scrollRef = useRef(null);
  function scrollBy(dx) {
    if (scrollRef.current) scrollRef.current.scrollBy({left:dx, behavior:'smooth'});
  }
  const btnSt = {
    background:'rgba(0,0,0,0.7)', border:'1px solid #333', color:'#f1f1f1',
    width:40, height:40, cursor:'pointer', display:'flex', alignItems:'center',
    justifyContent:'center', flexShrink:0, zIndex:1
  };
  return html`<div style=${{position:'relative',marginBottom:28}}>
    <div style=${{display:'flex',alignItems:'center',gap:0}}>
      <button style=${btnSt} onClick=${function(){scrollBy(-400);}}>
        <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M15.41 7.41L14 6l-6 6 6 6 1.41-1.41L10.83 12z"/></svg>
      </button>
      <div ref=${scrollRef}
        style=${{display:'flex',gap:20,overflowX:'auto',flex:1,
          scrollbarWidth:'none',msOverflowStyle:'none',padding:'8px 12px'}}>
        ${(props.actors||[]).map(function(actor,i){
          return html`<div key=${actor.did||i}
            onClick=${function(){props.onChannel(actor.handle);}}
            style=${{display:'flex',flexDirection:'column',alignItems:'center',gap:10,
              cursor:'pointer',flexShrink:0,width:130}}
            onMouseEnter=${function(e){e.currentTarget.querySelector('div').style.boxShadow='0 0 0 4px var(--accent), 0 0 12px rgba(var(--accent-rgb,0,255,7),0.5)';}}
            onMouseLeave=${function(e){e.currentTarget.querySelector('div').style.boxShadow='0 0 0 4px var(--accent)';;}}>
            <div style=${{width:110,height:110,borderRadius:'50%',overflow:'hidden',background:'#272727',
              boxShadow:'0 0 0 4px var(--accent)',
              boxSizing:'border-box',flexShrink:0}}>
              ${actor.avatar
                ? html`<img src=${actor.avatar} alt="" style=${{width:'100%',height:'100%',objectFit:'cover',display:'block'}}/>`
                : null}
            </div>
            <span style=${{fontSize:13,color:'#ccc',textAlign:'center',width:'100%',
              overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>
              ${actor.displayName||actor.handle}
            </span>
          </div>`;
        })}
      </div>
      <button style=${btnSt} onClick=${function(){scrollBy(400);}}>
        <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M10 6L8.59 7.41 13.17 12l-4.58 4.59L10 18l6-6z"/></svg>
      </button>
    </div>
  </div>`;
}


// ── FriendsFeed — videos shared via DM ───────────────────────────────────────
function FriendsFeed(props) {
  const sess = props.session;
  const [items,    setItems]    = useState([]);  // video items
  const [postItems,setPostItems]= useState([]);  // non-video post items (with sender)
  const [senders,  setSenders]  = useState([]);
  const [subTab,   setSubTab]   = useState('Videos');
  const [loading,  setLoading]  = useState(false);
  const [loaded,   setLoaded]   = useState(false);
  const [err,      setErr]      = useState('');

  function chatUrl(path) {
    var pdsHost = sess&&sess.pdsDid ? sess.pdsDid.replace('did:web:','') : 'bsky.social';
    return CHAT_PROXY+path+'?_pds='+encodeURIComponent(pdsHost);
  }

  useEffect(function(){
    if(!sess||loaded) return;
    var cancelled=false;
    async function load(){
      setLoading(true); setErr('');
      try{
        // 0. Get blocked accounts so we can filter them out
        var blockedDids=new Set();
        try{
          var blR=await api(AUTH_PROXY+'/app.bsky.graph.getBlocks?limit=100',
            {headers:{Authorization:'Bearer '+sess.accessJwt}});
          if(blR.ok){var bld=await blR.json();(bld.blocks||[]).forEach(function(b){blockedDids.add(b.did);});}
        }catch(e2){}
        // 1. Get all convos
        var cr=await api(chatUrl('/chat.bsky.convo.listConvos'),
          {headers:{Authorization:'Bearer '+sess.accessJwt}});
        if(!cr.ok){setErr('Could not load conversations. Check DM permissions.');setLoading(false);return;}
        var cd=await cr.json();
        var convos=(cd.convos||[]).filter(function(c){
          // Skip convos with blocked accounts
          return !(c.members||[]).some(function(mb){return mb.did!==sess.did&&blockedDids.has(mb.did);});
        });
        var found=[];
        var senderMap={};
        // 2. For each convo, get recent messages
        for(var i=0;i<convos.length&&!cancelled;i++){
          // Paginate through ALL messages in this convo
          var msgs=[];
          var cursor2='';
          for(var page=0;page<20;page++){
            var pageUrl=chatUrl('/chat.bsky.convo.getMessages')+'&convoId='+encodeURIComponent(convos[i].id)+'&limit=100'+(cursor2?'&cursor='+encodeURIComponent(cursor2):'');
            var mr=await api(pageUrl,{headers:{Authorization:'Bearer '+sess.accessJwt}});
            if(!mr.ok) break;
            var pageData=await mr.json();
            msgs=msgs.concat(pageData.messages||[]);
            if(!pageData.cursor) break;
            cursor2=pageData.cursor;
          }
          // 3. Collect ALL messages received (not sent by us, not deleted)
          var convoOther=(convos[i].members||[]).find(function(mb){return mb.did!==sess.did;}) || {};
          for(var j=0;j<msgs.length;j++){
            var m=msgs[j];
            var mt=m['$type']||'';
            if(mt==='chat.bsky.convo.defs#deletedMessageView') continue;
            if(m.sender&&m.sender.did===sess.did) continue;
            var embed=m.embed;
            var et=embed?embed['$type']||'':'';
            // Record embed = shared Bluesky post → hydrate later
            if(et==='app.bsky.embed.record'||et==='app.bsky.embed.record#view'){
              var rec=embed.record||{};
              if(rec.uri) found.push({sender:convoOther,msgText:m.text||'',postUri:rec.uri,postCid:rec.cid,sentAt:m.sentAt||'',_type:'record'});
            } else {
              // Plain text, image, or other embed — build a synthetic post object
              var synPost={
                uri:'dm-'+m.id,
                cid:m.id,
                indexedAt:m.sentAt||'',
                likeCount:0,repostCount:0,replyCount:0,
                author:convoOther,
                record:{text:m.text||'', '$type':'app.bsky.feed.post', createdAt:m.sentAt||''},
                embed:embed||null,
                labels:[]
              };
              found.push({sender:convoOther,msgText:'',postUri:null,sentAt:m.sentAt||'',_type:'dm',post:synPost});
            }
          }
        }
        // 4. Hydrate record-type post URIs
        var toHydrate=found.filter(function(x){return x._type==='record'&&x.postUri;});
        if(toHydrate.length>0&&!cancelled){
          var uris=toHydrate.map(function(x){return x.postUri;});
          var hydrated={};
          for(var k=0;k<uris.length;k+=25){
            var batch=uris.slice(k,k+25);
            var qstr=batch.map(function(u){return 'uris='+encodeURIComponent(u);}).join('&');
            var pr=await api(PUB_PROXY+'/app.bsky.feed.getPosts?'+qstr);
            if(pr.ok){(await pr.json()).posts.forEach(function(p){hydrated[p.uri]=p;});}
          }
          toHydrate.forEach(function(x){x.post=hydrated[x.postUri]||null;});
        }
        // 5. Build final list — include all items that have a post object
        var allItems2=found.filter(function(x){return !!x.post;});
        allItems2.sort(function(a,b){return b.sentAt.localeCompare(a.sentAt);});
        var vidOnly=allItems2.filter(function(x){return isVid(x.post)||isVidRaw(x.post);});
        var postOnly=allItems2.filter(function(x){return !isVid(x.post)&&!isVidRaw(x.post);});
        if(!cancelled){
          var seenSenders=new Set();
          var senders=[];
          allItems2.forEach(function(x){
            if(x.sender&&x.sender.did&&!seenSenders.has(x.sender.did)){
              seenSenders.add(x.sender.did); senders.push(x.sender);
            }
          });
          setItems(vidOnly); setPostItems(postOnly); setSenders(senders);
        }
      }catch(e){if(!cancelled)setErr(e.message||'Failed to load');}
      if(!cancelled){setLoading(false);setLoaded(true);}
    }
    load();
    return function(){cancelled=true;};
  },[sess&&sess.did]);

  if(!sess) return html`<div style=${{padding:24,color:'#aaa'}}>Sign in to see videos shared with you.</div>`;
  if(loading) return html`<div style=${{padding:24,textAlign:'center',color:'#aaa'}}>Loading shared videos…</div>`;
  if(err) return html`<div style=${{padding:24,color:'#ff6666',fontSize:14}}>${err}</div>`;
  if(!items.length&&!postItems.length) return html`<div style=${{display:'flex',flexDirection:'column',alignItems:'center',justifyContent:'center',height:'40vh',gap:16,color:'#aaa'}}>
    <svg width="64" height="64" viewBox="0 0 24 24" fill="#3f3f3f"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2z"/></svg>
    <p style=${{fontSize:16}}>No messages shared with you yet.</p>
    <p style=${{fontSize:13,color:'#555'}}>When a friend DMs you something, it will appear here.</p>
  </div>`;

  const tabSt2=function(a){return {padding:'6px 14px',background:a?'var(--accent)':'none',color:a?'#000':'#aaa',
    border:'none',fontSize:13,fontWeight:600,cursor:'pointer',borderRadius:0};};
  return html`<div style=${{padding:24}}>
    <div style=${{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:20}}>
      <h2 style=${{color:'#f1f1f1',fontSize:20,fontWeight:700,margin:0}}>From Friends</h2>
      <div style=${{display:'flex',gap:4}}>
        <button style=${tabSt2(subTab==='Videos')} onClick=${function(){setSubTab('Videos');}}>Videos</button>
        <button style=${tabSt2(subTab==='Posts')}  onClick=${function(){setSubTab('Posts');}}>Posts</button>
      </div>
    </div>
    ${senders.length>0?html`<${FollowStrip} actors=${senders} onChannel=${props.onChannel}/>`:null}
    ${subTab==='Videos'?html`<div style=${{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(280px,1fr))',gap:'24px 16px'}}>
      ${items.length?items.map(function(item,i){
        return html`<${FriendCard} key=${i} item=${item} onWatch=${props.onWatch} onChannel=${props.onChannel}/>`;
      }):html`<div style=${{padding:'32px 0',color:'#555',fontSize:14,gridColumn:'1/-1'}}>No videos shared with you yet.</div>`}
    </div>`:null}
    ${subTab==='Posts'?html`<${ChannelPostsFeed}
      posts=${postItems.map(function(x){return {post:x.post};})}
      loading=${false}
      session=${props.session}
      onChannel=${props.onChannel}
      onWatch=${props.onWatch}
      hideFilter=${true}
    />`:null}
  </div>`;
}


function FriendCard(props) {
  const item=props.item, post=item.post||{}, sender=item.sender||{};
  const msgText=(item.msgText||'').trim();
  const MAX=60;
  const needsMore=msgText.length>MAX;
  const short=msgText.slice(0,MAX)+(needsMore?'...':'');
  const [open,setOpen]=useState(false);
  const author=post.author||{};
  const rec=post.record||{};
  const embed=post.embed||{};
  const title=(rec.text&&rec.text.split('\n')[0])||'Video';
  const thumb=embed.thumbnail||(embed.images&&embed.images[0]&&embed.images[0].thumb)||null;
  const [thumbHov,setThumbHov]=useState(false);
  return html`<div style=${{cursor:'pointer'}}>
    <div style=${{display:'flex',alignItems:'center',gap:8,marginBottom:6,height:34,overflow:'hidden'}}>
      <${Avatar} src=${sender.avatar} size=${26}
        onClick=${function(e){e.stopPropagation();props.onChannel&&props.onChannel(sender.handle);}}/>
      <span style=${{color:'#e0e0e0',fontSize:12,fontWeight:600,flexShrink:0}}>${sender.displayName||sender.handle||'Unknown'}</span>
      ${msgText?html`<span style=${{color:'#888',fontSize:12,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',flex:1,minWidth:0}}>${open?'':short}</span>`:html`<span style=${{flex:1}}></span>`}
      ${needsMore?html`<span onClick=${function(e){e.stopPropagation();setOpen(function(v){return !v;});}} style=${{color:'var(--accent)',fontSize:11,fontWeight:600,cursor:'pointer',flexShrink:0,marginLeft:4}}>${open?'Close':'Expand'}</span>`:null}
      <span style=${{color:'#444',fontSize:11,flexShrink:0,marginLeft:4}}>${ago(item.sentAt)}</span>
    </div>
    <div onClick=${function(){if(!open)props.onWatch&&props.onWatch(post);}}
      onMouseEnter=${function(){setThumbHov(true);}}
      onMouseLeave=${function(){setThumbHov(false);}}
      style=${{width:'100%',paddingBottom:'56.25%',overflow:'hidden',background:'#1a1a1a',position:'relative',
        outline:thumbHov?'2px solid var(--accent)':'2px solid transparent',transition:'outline 0.15s'}}>
      <div style=${{position:'absolute',top:0,left:0,right:0,bottom:0}}>
        <${Thumb} src=${thumb}/>
      </div>
      ${open?html`<div style=${{position:'absolute',top:0,left:0,right:0,bottom:0,
        background:'rgba(0,0,0,0.88)',overflowY:'auto',padding:14,zIndex:2,boxSizing:'border-box'}}
        onClick=${function(e){e.stopPropagation();}}>
        <p style=${{color:'#f1f1f1',fontSize:13,lineHeight:1.65,margin:0,textAlign:'left',wordBreak:'break-word'}}>${msgText}</p>
      </div>`:null}
    </div>
    <div style=${{display:'flex',gap:12,paddingTop:12}} onClick=${function(){props.onWatch&&props.onWatch(post);}}>
      <${Avatar} src=${author.avatar} size=${36}
        onClick=${function(e){e.stopPropagation();props.onChannel&&props.onChannel(author.handle);}}/>
      <div style=${{flex:1,minWidth:0}}>
        <div class="clamp2" style=${{fontSize:14,fontWeight:500,color:'#f1f1f1'}}>${title}</div>
        <div style=${{fontSize:13,color:'#aaa',marginTop:4}}>${author.displayName||author.handle}</div>
        <div style=${{fontSize:13,color:'#aaa'}}>${fmt(post.likeCount||0)} likes · ${ago(post.indexedAt)}</div>
      </div>
    </div>
  </div>`;
}


function SubsPage(props) {
  const [tab,         setTab]         = useState('Videos');
  const [subsPosts,   setSubsPosts]   = useState([]);
  const [postsLoading,setPostsLoading]= useState(false);
  const [postsLoaded, setPostsLoaded] = useState(false);

  async function openTab(t) {
    setTab(t);
    if (t === 'Posts' && !postsLoaded && props.session) {
      setPostsLoading(true);
      try {
        var sess = props.session;
        var r = await api(AUTH_PROXY+'/app.bsky.feed.getTimeline?limit=100',
          {headers:{Authorization:'Bearer '+sess.accessJwt}});
        if (r.ok) {
          var d = await r.json();
          setSubsPosts(d.feed||[]);
          setPostsLoaded(true);
        }
      } catch(e){ console.error(e); }
      setPostsLoading(false);
    }
  }

  const tabSt = function(a){ return {
    padding:'10px 20px',background:'none',border:'none',cursor:'pointer',fontSize:14,
    color:a?'#f1f1f1':'#aaa',fontWeight:a?500:400,
    borderBottom:'3px solid '+(a?'var(--accent)':'transparent')
  };};

  return html`<div style=${{padding:24}}>
    <div style=${{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:20,flexWrap:'wrap',gap:12}}>
      <h2 style=${{color:'#f1f1f1',fontSize:20,fontWeight:700,margin:0}}>Subscriptions</h2>
      <div style=${{display:'flex'}}>
        <button onClick=${function(){openTab('Videos');}} style=${tabSt(tab==='Videos')}>Videos</button>
        <button onClick=${function(){openTab('Posts');}}  style=${tabSt(tab==='Posts')}>Posts</button>
      </div>
    </div>
    ${props.followStrip&&props.followStrip.length>0?html`
      <${FollowStrip} actors=${props.followStrip} onChannel=${props.onChannel}/>
    `:null}
    ${tab==='Videos'?html`<div>
      ${props.loading?html`<div style=${{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(280px,1fr))',gap:'24px 16px'}}>
        ${[0,1,2,3,4,5,6,7].map(function(i){return html`<${SkeletonCard} key=${i}/>`;})}
      </div>`:null}
      ${!props.loading&&(!props.videos||!props.videos.length)?html`<div style=${{display:'flex',flexDirection:'column',alignItems:'center',justifyContent:'center',height:'40vh',gap:16,color:'#aaa'}}>
        <svg width="64" height="64" viewBox="0 0 24 24" fill="#3f3f3f"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 14.5v-9l6 4.5-6 4.5z"/></svg>
        <p style=${{fontSize:16}}>No videos from people you follow.</p>
        <p style=${{fontSize:13,color:'#555'}}>Follow people on Bluesky who post videos and they'll appear here.</p>
      </div>`:null}
      ${!props.loading&&props.videos&&props.videos.length?html`<${VideoGrid} videos=${props.videos} loading=${false} onWatch=${props.onWatch} onChannel=${props.onChannel}/>`:null}
    </div>`:null}
    ${tab==='Posts'?html`<${ChannelPostsFeed} posts=${subsPosts} loading=${postsLoading} session=${props.session} onChannel=${props.onChannel} onWatch=${props.onWatch}/>`:null}
  </div>`;
}

// ── Feed Page ─────────────────────────────────────────────────────────────────
function FeedPage(props) {
  const [tab,          setTab]          = useState('Videos');
  const [feedPosts,    setFeedPosts]    = useState([]);
  const [postsLoading, setPostsLoading] = useState(false);
  const [loadedUri,    setLoadedUri]    = useState(null); // track which URI was loaded

  async function loadPosts(uri, sess) {
    if (!uri) return;
    setPostsLoading(true); setFeedPosts([]);
    try {
      const authOpts = sess ? {headers:{Authorization:'Bearer '+sess.accessJwt}} : {};
      const endpoint = sess ? AUTH_PROXY : PUB_PROXY;
      const r = await api(endpoint+'/app.bsky.feed.getFeed?feed='+encodeURIComponent(uri)+'&limit=100', authOpts);
      if (r.ok) { const d = await r.json(); setFeedPosts(d.feed||[]); }
    } catch(e) { console.error(e); }
    setPostsLoading(false); setLoadedUri(uri);
  }

  async function openTab(t) {
    setTab(t);
    if (t === 'Posts' && props.feedUri && props.feedUri !== loadedUri) {
      await loadPosts(props.feedUri, props.session);
    }
  }

  // When feedUri changes while on Posts tab, reload automatically
  useEffect(function() {
    if (tab === 'Posts' && props.feedUri && props.feedUri !== loadedUri) {
      loadPosts(props.feedUri, props.session);
    }
  }, [props.feedUri]);

  const tabSt = function(active) { return {
    padding:'10px 20px',background:'none',border:'none',
    color:active?'#f1f1f1':'#aaa',fontSize:14,fontWeight:active?500:400,
    borderBottom:'3px solid '+(active?'var(--accent)':'transparent'),cursor:'pointer'
  };};

  return html`<div style=${{padding:24}}>
    <div style=${{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:20,flexWrap:'wrap',gap:12}}>
      <h2 style=${{color:'#f1f1f1',fontSize:20,fontWeight:700,margin:0}}>${props.feedName||'Feed'}</h2>
      <div style=${{display:'flex'}}>
        <button onClick=${function(){openTab('Videos');}} style=${tabSt(tab==='Videos')}>Videos</button>
        <button onClick=${function(){openTab('Posts');}}  style=${tabSt(tab==='Posts')}>Posts</button>
      </div>
    </div>
    ${tab==='Videos'?html`<${VideoGrid} videos=${props.videos} loading=${props.loading} onWatch=${props.onWatch} onChannel=${props.onChannel}/>`:null}
    ${tab==='Posts'?html`<${ChannelPostsFeed} posts=${feedPosts} loading=${postsLoading} session=${props.session} onChannel=${props.onChannel} onWatch=${props.onWatch}/>`:null}
  </div>`;
}


// ── Sidebar ───────────────────────────────────────────────────────────────────
// ── SettingsPage ──────────────────────────────────────────────────────────────
function SettingsPage(props) {
  const sess = props.session;
  const [accent,   setAccent]   = useState(function(){return loadAccent();});
  const [accentIn, setAccentIn] = useState(function(){return loadAccent();});
  const [filter,   setFilter]   = useState(function(){return loadFilter();});

  function applyAndSaveAccent(c){
    setAccent(c); setAccentIn(c);
    saveAccent(c); applyAccent(c);
  }
  function applyAndSaveFilter(f){
    setFilter(f); saveFilter(f);
  }

  const section = function(title, children){
    return html`<div style=${{background:'#141414',border:'1px solid #1e1e1e',padding:20,marginBottom:16}}>
      <div style=${{color:'#f1f1f1',fontSize:15,fontWeight:600,marginBottom:14}}>${title}</div>
      ${children}
    </div>`;
  };

  return html`<div style=${{padding:24,maxWidth:620}}>
    <h2 style=${{color:'#f1f1f1',fontSize:22,fontWeight:700,marginBottom:24}}>Settings</h2>

    ${sess?section('Account', html`<div>
      <div style=${{display:'flex',alignItems:'center',gap:14,marginBottom:16}}>
        <${Avatar} src=${sess.avatar} size=${52} onClick=${props.onMyChannel}/>
        <div>
          <div style=${{color:'#f1f1f1',fontSize:16,fontWeight:600}}>${sess.displayName||sess.handle}</div>
          <div style=${{color:'#555',fontSize:13}}>@${sess.handle}</div>
        </div>
      </div>
      <button onClick=${props.onLogout}
        style=${{background:'none',border:'1px solid #ff4444',color:'#ff4444',
          padding:'10px 20px',fontSize:13,fontWeight:600,cursor:'pointer',borderRadius:0}}
        onMouseEnter=${function(e){e.currentTarget.style.background='rgba(255,68,68,0.1)';}}
        onMouseLeave=${function(e){e.currentTarget.style.background='none';}}>
        Sign out of @${sess.handle}
      </button>
    </div>`):null}

    ${section('Accent Color', html`<div>
      <div style=${{color:'#aaa',fontSize:13,marginBottom:12}}>Changes highlights, borders, and active indicators across the site.</div>
      <div style=${{display:'flex',alignItems:'center',gap:12,flexWrap:'wrap'}}>
        <input type="color" value=${accent}
          onInput=${function(e){applyAndSaveAccent(e.target.value);}}
          style=${{width:44,height:44,border:'none',background:'none',cursor:'pointer',padding:0}}/>
        <input type="text" value=${accentIn}
          onInput=${function(e){setAccentIn(e.target.value);}}
          onBlur=${function(e){
            var v=e.target.value.trim();
            if(/^#[0-9a-fA-F]{3,8}$/.test(v)||/^rgb/.test(v)){applyAndSaveAccent(v);}
            else{setAccentIn(accent);}
          }}
          placeholder="#00FF07"
          style=${{background:'#111',border:'1px solid #333',color:'#f1f1f1',padding:'8px 12px',
            fontSize:14,width:120,borderRadius:0,fontFamily:'monospace'}}/>
        <button onClick=${function(){applyAndSaveAccent('#00FF07');}}
          style=${{background:'none',border:'1px solid #333',color:'#aaa',padding:'8px 14px',fontSize:13,cursor:'pointer',borderRadius:0}}
          onMouseEnter=${function(e){e.currentTarget.style.color='#f1f1f1';}}
          onMouseLeave=${function(e){e.currentTarget.style.color='#aaa';}}>Reset to default</button>
      </div>
    </div>`)}



    ${section('Content Filter', html`<div>
      <div style=${{color:'#aaa',fontSize:13,marginBottom:12}}>Control which content appears across all feeds and tabs.</div>
      ${[['all','Show all content (default)'],['sfw','Hide adult/explicit content'],['nsfw','Show only adult/explicit content']].map(function(opt){
        var isChecked=filter===opt[0];
        return html`<label key=${opt[0]} style=${{display:'flex',alignItems:'center',gap:10,padding:'8px 0',cursor:'pointer'}}>
          <div onClick=${function(){applyAndSaveFilter(opt[0]);}}
            style=${{width:18,height:18,borderRadius:'50%',border:'2px solid '+(isChecked?'var(--accent)':'#555'),
              background:isChecked?'var(--accent)':'none',cursor:'pointer',flexShrink:0,
              display:'flex',alignItems:'center',justifyContent:'center'}}>
            ${isChecked?html`<div style=${{width:8,height:8,borderRadius:'50%',background:'#000'}}></div>`:null}
          </div>
          <span style=${{color:isChecked?'var(--accent)':'#f1f1f1',fontSize:14,fontWeight:isChecked?600:400}}>${opt[1]}</span>
        </label>`;
      })}
    </div>`)}

    ${section('About RaccTube', html`<div style=${{color:'#555',fontSize:13,lineHeight:1.7}}>
      RaccTube is a Bluesky video platform built on the AT Protocol.
    </div>`)}
  </div>`;
}


// ── HistoryPage ──────────────────────────────────────────────────────────────
function HistoryPage(props) {
  const [history, setHistory] = useState(function(){return loadHistory();});
  function clearHistory(){
    saveHistory([]); setHistory([]);
  }
  return html`<div style=${{padding:24}}>
    <div style=${{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:20}}>
      <h2 style=${{color:'#f1f1f1',fontSize:20,fontWeight:700,margin:0}}>Watch History</h2>
      ${history.length>0?html`<button onClick=${clearHistory}
        style=${{background:'none',border:'1px solid #3f3f3f',color:'#aaa',padding:'6px 14px',
          fontSize:13,cursor:'pointer',borderRadius:0}}
        onMouseEnter=${function(e){e.currentTarget.style.color='#f1f1f1';e.currentTarget.style.borderColor='#f1f1f1';}}
        onMouseLeave=${function(e){e.currentTarget.style.color='#aaa';e.currentTarget.style.borderColor='#3f3f3f';}}>
        Clear History
      </button>`:null}
    </div>
    ${!history.length?html`<div style=${{display:'flex',flexDirection:'column',alignItems:'center',justifyContent:'center',height:'50vh',gap:16,color:'#aaa'}}>
      <svg width="64" height="64" viewBox="0 0 24 24" fill="#3f3f3f"><path d="M13 3a9 9 0 0 0-9 9H1l3.89 3.89.07.14L9 12H6c0-3.87 3.13-7 7-7s7 3.13 7 7-3.13 7-7 7c-1.93 0-3.68-.79-4.94-2.06l-1.42 1.42A8.954 8.954 0 0 0 13 21a9 9 0 0 0 0-18zm-1 5v5l4.28 2.54.72-1.21-3.5-2.08V8H12z"/></svg>
      <p style=${{fontSize:16}}>No watch history yet.</p>
      <p style=${{fontSize:13,color:'#555'}}>Videos you watch will appear here and are saved locally on your device.</p>
    </div>`:html`<${VideoGrid} videos=${history} loading=${false} onWatch=${props.onWatch} onChannel=${props.onChannel}/>`}
  </div>`;
}


function Sidebar(props) {
  const open = props.open;
  const SearchIco = html`<svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path d="M15.5 14h-.79l-.28-.27A6.471 6.471 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/></svg>`;
  const SubsIco   = html`<svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path d="M16 11c1.66 0 2.99-1.34 2.99-3S17.66 5 16 5c-1.66 0-3 1.34-3 3s1.34 3 3 3zm-8 0c1.66 0 2.99-1.34 2.99-3S9.66 5 8 5C6.34 5 5 6.34 5 8s1.34 3 3 3zm0 2c-2.33 0-7 1.17-7 3.5V19h14v-2.5c0-2.33-4.67-3.5-7-3.5zm8 0c-.29 0-.62.02-.97.05 1.16.84 1.97 1.97 1.97 3.45V19h6v-2.5c0-2.33-4.67-3.5-7-3.5z"/></svg>`;
  const FeedIco   = html`<svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path d="M6.18 15.64a2.18 2.18 0 0 1 2.18 2.18C8.36 19.01 7.38 20 6.18 20C4.98 20 4 19.01 4 17.82a2.18 2.18 0 0 1 2.18-2.18M4 4.44A15.56 15.56 0 0 1 19.56 20h-2.83A12.73 12.73 0 0 0 4 7.27V4.44m0 5.66a9.9 9.9 0 0 1 9.9 9.9h-2.83A7.07 7.07 0 0 0 4 12.93V10.1z"/></svg>`;

  const feeds = props.feeds || [];

  return html`<aside style=${{position:'fixed',top:56,left:0,bottom:0,width:open?240:72,background:'#0f0f0f',
    padding:open?'12px':'12px 4px',overflowY:'auto',overflowX:'hidden',zIndex:100,
    transition:'width 0.15s ease',boxSizing:'border-box'}}>

    <${SideItem} open=${open} icon=${SearchIco} label="Search"
      active=${props.page==='search'} onClick=${props.onSearch}/>

    ${props.hasSession?html`<${SideItem} open=${open}
      icon=${html`<svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm-9 9H7V9h4v2zm6 0h-4V9h4v2z"/></svg>`}
      label="From Friends"
      active=${props.page==='friends'}
      onClick=${props.onFriends}/>`:null}
    <${SideItem} open=${open} icon=${SubsIco}   label="Subscriptions"
      active=${props.page==='subs'}
      onClick=${function(){props.hasSession?props.onSubs():props.onLogin();}}/>
    <${SideItem} open=${open}
      icon=${html`<svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path d="M13 3a9 9 0 0 0-9 9H1l3.89 3.89.07.14L9 12H6c0-3.87 3.13-7 7-7s7 3.13 7 7-3.13 7-7 7c-1.93 0-3.68-.79-4.94-2.06l-1.42 1.42A8.954 8.954 0 0 0 13 21a9 9 0 0 0 0-18zm-1 5v5l4.28 2.54.72-1.21-3.5-2.08V8H12z"/></svg>`}
      label="History"
      active=${props.page==='history'}
      onClick=${props.onHistory}/>



    ${feeds.length>0?html`
      <div style=${{height:1,background:'var(--accent)',margin:'10px 0 6px'}}/>
      ${open?html`<div style=${{color:'#666',fontSize:11,textTransform:'uppercase',letterSpacing:1,padding:'0 12px 6px'}}>Feeds</div>`:null}
      ${feeds.map(function(feed){
        const active = props.page==='feed' && props.activeFeed===feed.uri;
        const feedIcon = feed.avatar
          ? html`<img src=${feed.avatar} alt="" style=${{width:24,height:24,borderRadius:0,objectFit:'cover',flexShrink:0}}/>`
          : FeedIco;
        return html`<${SideItem} key=${feed.uri} open=${open} icon=${feedIcon}
          label=${feed.displayName}
          active=${active}
          onClick=${function(){props.onFeedSelect(feed);}}/>`
      })}
    `:null}
    <div style=${{height:1,background:'var(--accent)',margin:'10px 0 6px'}}/>
    <${SideItem} open=${open}
      icon=${html`<svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path d="M19.14,12.94c0.04-0.3,0.06-0.61,0.06-0.94c0-0.32-0.02-0.64-0.07-0.94l2.03-1.58c0.18-0.14,0.23-0.41,0.12-0.61 l-1.92-3.32c-0.12-0.22-0.37-0.29-0.59-0.22l-2.39,0.96c-0.5-0.38-1.03-0.7-1.62-0.94L14.4,2.81c-0.04-0.24-0.24-0.41-0.48-0.41 h-3.84c-0.24,0-0.43,0.17-0.47,0.41L9.25,5.35C8.66,5.59,8.12,5.92,7.63,6.29L5.24,5.33c-0.22-0.08-0.47,0-0.59,0.22L2.74,8.87 C2.62,9.08,2.66,9.34,2.86,9.48l2.03,1.58C4.84,11.36,4.8,11.69,4.8,12s0.02,0.64,0.07,0.94l-2.03,1.58 c-0.18,0.14-0.23,0.41-0.12,0.61l1.92,3.32c0.12,0.22,0.37,0.29,0.59,0.22l2.39-0.96c0.5,0.38,1.03,0.7,1.62,0.94l0.36,2.54 c0.05,0.24,0.24,0.41,0.48,0.41h3.84c0.24,0,0.44-0.17,0.47-0.41l0.36-2.54c0.59-0.24,1.13-0.56,1.62-0.94l2.39,0.96 c0.22,0.08,0.47,0,0.59-0.22l1.92-3.32c0.12-0.22,0.07-0.47-0.12-0.61L19.14,12.94z M12,15.6c-1.98,0-3.6-1.62-3.6-3.6 s1.62-3.6,3.6-3.6s3.6,1.62,3.6,3.6S13.98,15.6,12,15.6z"/></svg>`}
      label="Settings"
      active=${props.page==='settings'}
      onClick=${props.onSettings}/>

  </aside>`;
}

// ── Bluesky action helpers ────────────────────────────────────────────────────
async function bskyCreate(sess, collection, record) {
  if (!sess) return null;
  const res = await api(AUTH_PROXY+'/com.atproto.repo.createRecord', {
    method:'POST',
    headers:{'Content-Type':'application/json','Authorization':'Bearer '+sess.accessJwt},
    body:JSON.stringify({repo:sess.did, collection, record})
  });
  if (!res.ok) return null;
  const d = await res.json();
  return d.uri || null;
}
async function bskyDelete(sess, collection, rkey) {
  if (!sess) return;
  await api(AUTH_PROXY+'/com.atproto.repo.deleteRecord', {
    method:'POST',
    headers:{'Content-Type':'application/json','Authorization':'Bearer '+sess.accessJwt},
    body:JSON.stringify({repo:sess.did, collection, rkey})
  });
}
function viewerLiked(post)      { return post && post.viewer && post.viewer.like; }
function viewerReposted(post)   { return post && post.viewer && post.viewer.repost; }
function viewerFollows(profile) { return profile && profile.viewer && profile.viewer.following; }

// ── Subscribe button ──────────────────────────────────────────────────────────
function SubscribeButton(props) {
  const sess = props.session;
  const [subbed,  setSubbed]  = useState(!!viewerFollows({viewer:props.viewer}));
  const [subUri,  setSubUri]  = useState(viewerFollows({viewer:props.viewer})||null);
  const [loading, setLoading] = useState(false);
  useEffect(function(){
    setSubbed(!!viewerFollows({viewer:props.viewer}));
    setSubUri(viewerFollows({viewer:props.viewer})||null);
  },[props.did, props.viewer]);
  // Fresh sync from API when session available and we have a DID
  useEffect(function(){
    if(!sess||!props.did) return;
    var cancelled=false;
    api(AUTH_PROXY+'/app.bsky.actor.getProfile?actor='+encodeURIComponent(props.did),
      {headers:{Authorization:'Bearer '+sess.accessJwt}})
      .then(function(r){ return r.ok?r.json():null; })
      .then(function(d){
        if(d&&!cancelled){
          setSubbed(!!viewerFollows(d));
          setSubUri(viewerFollows(d)||null);
        }
      }).catch(function(){});
    return function(){cancelled=true;};
  },[props.did, sess&&sess.accessJwt]);
  async function toggle(e) {
    e.stopPropagation();
    if (!sess || loading) return;
    setLoading(true);
    if (subbed) {
      setSubbed(false);
      const rkey = subUri && subUri.split('/').pop();
      setSubUri(null);
      if (rkey) await bskyDelete(sess, 'app.bsky.graph.follow', rkey);
    } else {
      setSubbed(true);
      const uri = await bskyCreate(sess, 'app.bsky.graph.follow', {
        '$type':'app.bsky.graph.follow', subject:props.did, createdAt:new Date().toISOString()
      });
      setSubUri(uri);
    }
    setLoading(false);
  }
  const pad = props.small ? '7px 14px' : '10px 20px';
  const fsz = props.small ? 13 : 14;
  return html`<button onClick=${toggle} disabled=${loading}
    style=${{flexShrink:0, background:subbed?'#1a1a1a':'var(--accent)',
      border:subbed?'1px solid #555':'none', color:subbed?'#f1f1f1':'#000',
      padding:pad, borderRadius:0, fontWeight:600, fontSize:fsz,
      transition:'all 0.15s', opacity:loading?0.6:1, cursor:sess?'pointer':'default'}}>
    ${loading ? '...' : (subbed ? 'Subscribed' : 'Subscribe')}
  </button>`;
}

// -- CommentBox component
function CommentBox(props) {
  const sess=props.session;
  const [text,setText]=useState('');
  const [posting,setPosting]=useState(false);
  const [done,setDone]=useState(false);
  const [err,setErr]=useState('');
  if(!sess) return html`<div style=${{color:'#555',fontSize:13,padding:'8px 0',marginBottom:10}}>Sign in to comment.</div>`;
  async function submit(e){
    e.preventDefault();
    if(!text.trim()||posting) return;
    setPosting(true); setErr('');
    try{
      const rec={'$type':'app.bsky.feed.post',text:text.trim(),
        reply:{root:{uri:props.postUri,cid:props.postCid},parent:{uri:props.postUri,cid:props.postCid}},
        createdAt:new Date().toISOString(),langs:['en']};
      const res=await api(AUTH_PROXY+'/com.atproto.repo.createRecord',{
        method:'POST',
        headers:{'Content-Type':'application/json','Authorization':'Bearer '+sess.accessJwt},
        body:JSON.stringify({repo:sess.did,collection:'app.bsky.feed.post',record:rec})});
      if(!res.ok) throw new Error(await res.text());
      setText(''); setDone(true);
      setTimeout(function(){setDone(false);},3000);
      if(props.onPosted) props.onPosted();
    }catch(e2){setErr(e2.message||'Failed');}
    setPosting(false);
  }
  return html`<div style=${{marginBottom:18}}>
    <form onSubmit=${submit} style=${{display:'flex',gap:10,alignItems:'flex-start'}}>
      <${Avatar} src=${sess.avatar} size=${36}/>
      <div style=${{flex:1}}>
        <textarea value=${text} onInput=${function(e){setText(e.target.value);}}
          placeholder='Add a comment…' rows='2' maxlength='300'
          style=${{width:'100%',background:'#1a1a1a',border:'1px solid #333',color:'#f1f1f1',
            padding:'10px 12px',fontSize:14,resize:'vertical',boxSizing:'border-box',borderRadius:0,
            fontFamily:"'Roboto',sans-serif"}}
          onFocus=${function(e){e.target.style.borderColor='var(--accent)';}}
          onBlur=${function(e){e.target.style.borderColor='#333';}}
          disabled=${posting}/>
        <div style=${{display:'flex',justifyContent:'flex-end',gap:10,marginTop:6,alignItems:'center'}}>
          ${err?html`<span style=${{color:'#ff6666',fontSize:12,flex:1}}>${err}</span>`:null}
          ${done?html`<span style=${{color:'var(--accent)',fontSize:12}}>Posted!</span>`:null}
          <button type='submit' disabled=${posting||!text.trim()}
            style=${{background:posting||!text.trim()?'#1a1a1a':'var(--accent)',
              color:posting||!text.trim()?'#555':'#000',
              border:'none',padding:'8px 20px',fontSize:13,fontWeight:700,
              cursor:posting||!text.trim()?'not-allowed':'pointer',borderRadius:0}}>
            ${posting?'Posting…':'Post'}
          </button>
        </div>
      </div>
    </form>
  </div>`;
}


// ── ShareModal ───────────────────────────────────────────────────────────────
function ShareModal(props) {
  const sess     = props.session;
  const post     = props.post;
  const author   = post.author||{};
  const postUrl  = 'https://bsky.app/profile/'+author.handle+'/post/'+post.uri.split('/').pop();
  const [convos,    setConvos]    = useState([]);
  const [loading,   setLoading]   = useState(true);
  const [search,    setSearch]    = useState('');
  const [searchRes, setSearchRes] = useState([]);
  const [searching, setSearching] = useState(false);
  const [msg,       setMsg]       = useState('');
  const [sending,   setSending]   = useState(null); // convo id being sent to
  const [sent,      setSent]      = useState({});
  const [err,       setErr]       = useState('');
  const [dmErr,     setDmErr]     = useState(false);

  // api.bsky.chat requires a service auth token, not a plain accessJwt
  // Use regular accessJwt — the proxy routes to user's PDS with atproto-proxy header
  function getChatToken() { return Promise.resolve(sess.accessJwt); }
  // Build a chat URL that includes the user's PDS host so the proxy routes correctly
  function chatUrl(path) {
    var pdsHost = sess.pdsDid ? sess.pdsDid.replace('did:web:','') : 'bsky.social';
    return CHAT_PROXY+path+'?_pds='+encodeURIComponent(pdsHost);
  }

  useEffect(function(){
    if(!sess) return;
    var cancelled=false;
    (async function(){
      try{
        var token=await getChatToken();
        // Fetch blocked list to filter from share modal
        var shareBlockedDids=new Set();
        try{
          var sbR=await api(AUTH_PROXY+'/app.bsky.graph.getBlocks?limit=100',{headers:{Authorization:'Bearer '+sess.accessJwt}});
          if(sbR.ok){var sbd=await sbR.json();(sbd.blocks||[]).forEach(function(b){shareBlockedDids.add(b.did);});}
        }catch(e3){}
        var r=await api(chatUrl('/chat.bsky.convo.listConvos'),{headers:{Authorization:'Bearer '+token}});
        if(r.status===401||r.status===403){if(!cancelled)setDmErr(true);if(!cancelled)setLoading(false);return;}
        if(r.ok){
          var d=await r.json();
          if(d&&d.convos&&!cancelled){
            var sorted=d.convos.filter(function(c){
              return !(c.members||[]).some(function(mb){return mb.did!==sess.did&&shareBlockedDids.has(mb.did);});
            }).slice().sort(function(a,b){
              var at=a.lastMessage&&a.lastMessage.sentAt||''; var bt=b.lastMessage&&b.lastMessage.sentAt||'';
              return bt.localeCompare(at);
            });
            setConvos(sorted);
          }
        }
      }catch(e){if(!cancelled)setErr('Failed to load DMs: '+e.message);}
      if(!cancelled)setLoading(false);
    })();
    return function(){cancelled=true;};
  },[]);

  var searchTimer=null;
  function onSearchInput(e){
    var val=e.target.value; setSearch(val);
    clearTimeout(searchTimer);
    if(!val.trim()){setSearchRes([]);return;}
    searchTimer=setTimeout(async function(){
      setSearching(true);
      try{
        var r=await api(PUB_PROXY+'/app.bsky.actor.searchActors?q='+encodeURIComponent(val)+'&limit=8');
        if(r.ok){var d=await r.json(); setSearchRes((d.actors||[]).filter(function(a){return !shareBlockedDids.has(a.did);}));}
      }catch(e2){}
      setSearching(false);
    },350);
  }

  async function sendTo(convoId){
    if(!sess||sending) return;
    setSending(convoId); setErr('');
    try{
      var token=await getChatToken();
      // Include the post as an embed so Bluesky renders it as a card, not a plain link
      var dmText = msg.trim() || '';
      var dmMessage = {
        text: dmText,
        embed: {'$type':'app.bsky.embed.record', record:{uri:post.uri,cid:post.cid}}
      };
      var r=await api(chatUrl('/chat.bsky.convo.sendMessage'),{
        method:'POST',
        headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},
        body:JSON.stringify({convoId:convoId,message:dmMessage})
      });
      if(!r.ok) throw new Error(await r.text());
      setSent(function(s){return Object.assign({},s,{[convoId]:true});});
    }catch(e2){setErr(e2.message||'Send failed');}
    setSending(null);
  }

  async function sendToActor(actorDid){
    if(!sess||sending) return;
    setSending('new_'+actorDid); setErr('');
    try{
      var token=await getChatToken();
      var cr=await api(chatUrl('/chat.bsky.convo.getConvoForMembers')+'&members[]='+encodeURIComponent(sess.did)+'&members[]='+encodeURIComponent(actorDid),
        {headers:{Authorization:'Bearer '+token}});
      if(!cr.ok) throw new Error(await cr.text());
      var cd=await cr.json();
      await sendTo(cd.convo.id);
    }catch(e2){setErr(e2.message||'Failed');setSending(null);}
  }

  function ConvoRow(rp){
    var c=rp.convo;
    var other=(c.members||[]).find(function(m){return m.did!==sess.did;})||c.members[0]||{};
    var isSent=sent[c.id];
    var isSending=sending===c.id;
    return html`<div style=${{display:'flex',alignItems:'center',gap:12,padding:'10px 0',borderBottom:'1px solid #1e1e1e'}}>
      <${Avatar} src=${other.avatar} size=${36}/>
      <div style=${{flex:1,minWidth:0}}>
        <div style=${{color:'#f1f1f1',fontSize:14,fontWeight:500}}>${other.displayName||other.handle}</div>
        <div style=${{color:'#555',fontSize:12}}>@${other.handle||''}</div>
      </div>
      <button onClick=${function(){sendTo(c.id);}} disabled=${isSending||isSent}
        style=${{background:isSent?'var(--accent-solid-dim)':isSending?'#1a1a1a':'var(--accent)',color:isSent?'var(--accent)':isSending?'#555':'#000',
          border:'none',padding:'7px 16px',fontSize:13,fontWeight:600,cursor:isSending||isSent?'default':'pointer',borderRadius:0}}>
        ${isSent?'Sent':isSending?'Sending…':'Send'}
      </button>
    </div>`;
  }

  return html`<div onClick=${props.onClose}
    style=${{position:'fixed',top:0,left:0,right:0,bottom:0,background:'rgba(0,0,0,0.88)',zIndex:3000,display:'flex',alignItems:'center',justifyContent:'center',padding:16}}>
    <div onClick=${function(e){e.stopPropagation();}}
      style=${{background:'#1a1a1a',border:'1px solid #272727',padding:24,width:480,maxWidth:'100%',maxHeight:'80vh',overflowY:'auto',boxShadow:'0 20px 60px rgba(0,0,0,0.8)'}}>
      <div style=${{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:16}}>
        <h2 style=${{color:'#f1f1f1',fontSize:16,fontWeight:700,margin:0}}>Share via DM</h2>
        <button onClick=${props.onClose} style=${{background:'none',border:'none',color:'#666',fontSize:20,cursor:'pointer'}} onMouseEnter=${function(e){e.currentTarget.style.color='#f1f1f1';}} onMouseLeave=${function(e){e.currentTarget.style.color='#666';}}>✕</button>
      </div>
      ${!sess?html`<div style=${{color:'#aaa',fontSize:14}}>Sign in to share via DM.</div>`:null}
      ${dmErr?html`<div style=${{background:'#1a0a00',border:'1px solid #884400',padding:'10px 14px',color:'#ffaa66',fontSize:13,marginBottom:16,lineHeight:1.5}}>
        ⚠️ DM access denied (401/403 from your PDS). If using an App Password, make sure it has Direct Messages enabled (Bluesky Settings → Privacy & Security → App Passwords).
      </div>`:null}
      ${sess&&!dmErr?html`<div>
        <input value=${search} onInput=${onSearchInput} placeholder="Search for a user to send to…"
          style=${{width:'100%',padding:'9px 12px',background:'#111',border:'1px solid #333',color:'#f1f1f1',fontSize:14,marginBottom:12,boxSizing:'border-box',borderRadius:0}}
          onFocus=${function(e){e.target.style.borderColor='var(--accent)';}} onBlur=${function(e){e.target.style.borderColor='#333';}}/>
        ${err?html`<div style=${{color:'#ff6666',fontSize:12,marginBottom:8}}>${err}</div>`:null}
        ${search.trim()&&searchRes.length?html`<div style=${{marginBottom:12,maxHeight:180,overflowY:'auto',border:'1px solid #222',background:'#111'}}>
          ${searchRes.map(function(a){return html`<div key=${a.did} style=${{display:'flex',alignItems:'center',gap:10,padding:'8px 12px',borderBottom:'1px solid #1e1e1e'}}>
            <${Avatar} src=${a.avatar} size=${32}/>
            <div style=${{flex:1,minWidth:0,fontSize:13,color:'#f1f1f1'}}>${a.displayName||a.handle}<span style=${{color:'#555',marginLeft:8}}>@${a.handle}</span></div>
            <button onClick=${function(){sendToActor(a.did);}} disabled=${sending==='new_'+a.did||sent['new_'+a.did]}
              style=${{background:sent['new_'+a.did]?'var(--accent-solid-dim)':'var(--accent)',color:sent['new_'+a.did]?'var(--accent)':'#000',border:'none',padding:'6px 12px',fontSize:12,fontWeight:600,cursor:'pointer',borderRadius:0}}>
              ${sent['new_'+a.did]?'Sent':sending==='new_'+a.did?'…':'Send'}
            </button>
          </div>`;}) }
        </div>`:null}
        <div style=${{marginBottom:12}}>
          <textarea value=${msg} onInput=${function(e){setMsg(e.target.value);}} placeholder="Add a message (optional)…" rows="2" maxlength="280"
            style=${{width:'100%',background:'#111',border:'1px solid #333',color:'#f1f1f1',padding:'9px 12px',fontSize:13,resize:'vertical',boxSizing:'border-box',borderRadius:0}}
            onFocus=${function(e){e.target.style.borderColor='var(--accent)';}} onBlur=${function(e){e.target.style.borderColor='#333';}}/>
        </div>
        <div style=${{color:'#444',fontSize:12,marginBottom:8}}>Recent conversations</div>
        ${loading?html`<div style=${{color:'#555',fontSize:13,textAlign:'center',padding:'16px 0'}}>Loading conversations…</div>`:null}
        ${!loading&&!convos.length&&!dmErr?html`<div style=${{color:'#555',fontSize:13}}>No conversations yet.</div>`:null}
        ${convos.map(function(c){return html`<${ConvoRow} key=${c.id} convo=${c}/>`;})}
      </div>`:null}
    </div>
  </div>`;
}


// ── Watch page ────────────────────────────────────────────────────────────────
function WatchPage(props) {
  const post   = props.post;
  const embed  = post.embed;
  const author = post.author;
  const rec    = post.record;
  const sess   = props.session;
  const replies = ((props.thread && props.thread.replies) || []).filter(function(r){return r.post;}).slice(0,20);
  const postId  = post.uri.split('/').pop();

  const [showShare,  setShowShare]  = useState(false);
  const [liked,      setLiked]      = useState(!!viewerLiked(post));
  const [likeUri,    setLikeUri]    = useState(viewerLiked(post)||null);
  const [likeCount,  setLikeCount]  = useState(post.likeCount||0);
  const [reposted,   setReposted]   = useState(!!viewerReposted(post));
  const [repostUri,  setRepostUri]  = useState(viewerReposted(post)||null);
  const [repostCount,setRepostCount]= useState(post.repostCount||0);

  useEffect(function(){
    setLiked(!!viewerLiked(post));      setLikeUri(viewerLiked(post)||null);    setLikeCount(post.likeCount||0);
    setReposted(!!viewerReposted(post));setRepostUri(viewerReposted(post)||null);setRepostCount(post.repostCount||0);
  },[post.uri]);
  useEffect(function(){
    if(!sess||!post.uri) return;
    var cancelled=false;
    api(AUTH_PROXY+'/app.bsky.feed.getPosts?uris='+encodeURIComponent(post.uri),
      {headers:{Authorization:'Bearer '+sess.accessJwt}})
      .then(function(r){return r.ok?r.json():null;})
      .then(function(d){
        if(d&&!cancelled){
          var p=(d.posts||[])[0];
          if(p){
            setLiked(!!viewerLiked(p)); setLikeUri(viewerLiked(p)||null); setLikeCount(p.likeCount||0);
            setReposted(!!viewerReposted(p)); setRepostUri(viewerReposted(p)||null); setRepostCount(p.repostCount||0);
          }
        }
      }).catch(function(){});
    return function(){cancelled=true;};
  },[post.uri, sess&&sess.accessJwt]);

  async function toggleLike() {
    if (!sess) return;
    if (liked) {
      setLiked(false); setLikeCount(function(n){return n-1;});
      const rkey = likeUri && likeUri.split('/').pop(); setLikeUri(null);
      if (rkey) await bskyDelete(sess,'app.bsky.feed.like',rkey);
    } else {
      setLiked(true); setLikeCount(function(n){return n+1;});
      const uri = await bskyCreate(sess,'app.bsky.feed.like',{'$type':'app.bsky.feed.like',subject:{uri:post.uri,cid:post.cid},createdAt:new Date().toISOString()});
      setLikeUri(uri);
    }
  }
  async function toggleRepost() {
    if (!sess) return;
    if (reposted) {
      setReposted(false); setRepostCount(function(n){return n-1;});
      const rkey = repostUri && repostUri.split('/').pop(); setRepostUri(null);
      if (rkey) await bskyDelete(sess,'app.bsky.feed.repost',rkey);
    } else {
      setReposted(true); setRepostCount(function(n){return n+1;});
      const uri = await bskyCreate(sess,'app.bsky.feed.repost',{'$type':'app.bsky.feed.repost',subject:{uri:post.uri,cid:post.cid},createdAt:new Date().toISOString()});
      setRepostUri(uri);
    }
  }

  const bSt = function(active, col) { return {background:active?(col||'#333'):'#1a1a1a',border:'1px solid #333',color:'#f1f1f1',padding:'8px 16px',borderRadius:0,fontSize:14,display:'flex',alignItems:'center',gap:6,cursor:sess?'pointer':'default',transition:'background 0.15s'}; };
  const ADULT_LABELS=['sexual','porn','nudity','graphic-media','adult'];
  const isAdult=!!(post.labels&&post.labels.some(function(l){return ADULT_LABELS.indexOf(l.val)!==-1;}));

  return html`<div style=${{display:'flex',gap:24,padding:24,maxWidth:1600,margin:'0 auto'}}>${showShare?html`<${ShareModal} post=${post} session=${sess} onClose=${function(){setShowShare(false);}}/>`  :null}
    <div style=${{flex:1,minWidth:0}}>
      <div style=${{overflow:'hidden',background:'#000'}}>
        <${VideoPlayer} playlist=${embed.playlist} thumbnail=${embed.thumbnail}/>
      </div>
      <h1 style=${{fontSize:18,fontWeight:600,color:'#f1f1f1',margin:'16px 0 8px',lineHeight:1.4}}>
        ${(rec&&rec.text&&rec.text.split('\n')[0])||'Video from Bluesky'}
      </h1>
      <div style=${{display:'flex',alignItems:'center',justifyContent:'space-between',flexWrap:'wrap',gap:12,marginBottom:16}}>
        <div style=${{display:'flex',alignItems:'center',gap:12}}>
          <div style=${{display:'flex',alignItems:'center',gap:12,cursor:'pointer'}} onClick=${function(){props.onChannel(author.handle);}}>
            <${Avatar} src=${author.avatar} size=${40} onClick=${function(){props.onChannel(author.handle);}}/>
            <div>
              <div style=${{color:'#f1f1f1',fontWeight:500,fontSize:14}}>${author.displayName||author.handle}</div>
              <div style=${{color:'#aaa',fontSize:12}}>@${author.handle}</div>
            </div>
          </div>
          ${sess&&sess.did!==author.did?html`<${SubscribeButton} did=${author.did} viewer=${author.viewer} session=${sess}/>`:null}
        </div>
        <div style=${{display:'flex',gap:8,flexWrap:'wrap'}}>
          <button onClick=${toggleLike} style=${bSt(liked,'var(--accent-solid-dim)')}><svg width="16" height="16" viewBox="0 0 24 24" fill=${liked?'var(--accent)':'currentColor'}><path d="M1 21h4V9H1v12zm22-11c0-1.1-.9-2-2-2h-6.31l.95-4.57.03-.32c0-.41-.17-.79-.44-1.06L14.17 1 7.59 7.59C7.22 7.95 7 8.45 7 9v10c0 1.1.9 2 2 2h9c.83 0 1.54-.5 1.84-1.22l3.02-7.05c.09-.23.14-.47.14-.73v-2z"/></svg>${fmt(likeCount)}</button>
          <button title="Dislike (coming soon)" style=${bSt(false)}><svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M15 3H6c-.83 0-1.54.5-1.84 1.22l-3.02 7.05c-.09.23-.14.47-.14.73v2c0 1.1.9 2 2 2h6.31l-.95 4.57-.03.32c0 .41.17.79.44 1.06L10.83 23l6.59-6.59c.36-.36.58-.86.58-1.41V5c0-1.1-.9-2-2-2zm4 0v12h4V3h-4z"/></svg>Dislike</button>
          <button onClick=${toggleRepost} style=${bSt(reposted,'var(--accent-solid-dim)')}><svg width="16" height="16" viewBox="0 0 24 24" fill=${reposted?'var(--accent)':'currentColor'}><path d="M7 7h10v3l4-4-4-4v3H5v6h2V7zm10 10H7v-3l-4 4 4 4v-3h12v-6h-2v4z"/></svg>${fmt(repostCount)}</button>
          <button onClick=${function(){setShowShare(true);}} style=${bSt(false)} title="Share via DM"><svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M18 16.08c-.76 0-1.44.3-1.96.77L8.91 12.7c.05-.23.09-.46.09-.7s-.04-.47-.09-.7l7.05-4.11c.54.5 1.25.81 2.04.81 1.66 0 3-1.34 3-3s-1.34-3-3-3-3 1.34-3 3c0 .24.04.47.09.7L8.04 9.81C7.5 9.31 6.79 9 6 9c-1.66 0-3 1.34-3 3s1.34 3 3 3c.79 0 1.5-.31 2.04-.81l7.12 4.16c-.05.21-.08.43-.08.65 0 1.61 1.31 2.92 2.92 2.92 1.61 0 2.92-1.31 2.92-2.92s-1.31-2.92-2.92-2.92z"/></svg>Share</button>
          ${isAdult?html`<button title="Came to this (coming soon)" style=${{background:'rgba(255,0,120,0.08)',border:'1px solid #ff0077',color:'#ff55aa',display:'flex',alignItems:'center',gap:6,fontSize:14,padding:'8px 14px',borderRadius:0,cursor:'pointer'}}>🔥 Came to this</button>`:null}
        </div>
      </div>
      <div style=${{background:'#1a1a1a',border:'1px solid #272727',padding:'12px 16px',marginBottom:24}}>
        <div style=${{fontSize:13,color:'#f1f1f1',fontWeight:500,marginBottom:4}}>
          ${fmt(likeCount)} likes · ${fmt(post.replyCount||0)} comments · ${fmt(repostCount)} reposts · ${ago(post.indexedAt)}
        </div>
        ${rec&&rec.text?html`<div style=${{fontSize:14,color:'#f1f1f1',marginTop:8,whiteSpace:'pre-wrap',lineHeight:1.6}}>${rec.text}</div>`:null}
        <a href=${'https://bsky.app/profile/'+author.handle+'/post/'+postId} target="_blank" rel="noreferrer"
          style=${{display:'inline-block',marginTop:12,color:'var(--accent)',fontSize:13}}>View on Bluesky →</a>
      </div>
      ${!sess?html`<div style=${{color:'#aaa',fontSize:13,marginBottom:16,padding:'8px 12px',background:'#1a1a1a',border:'1px solid #272727'}}>
        <a onClick=${function(){props.onLogin&&props.onLogin();}} style=${{color:'var(--accent)',cursor:'pointer'}}>Sign in</a> to like, repost, and subscribe.
      </div>`:null}
      <h3 style=${{color:'#f1f1f1',fontSize:16,fontWeight:600,marginBottom:16}}>${fmt(post.replyCount||0)} Comments</h3>
      <${CommentBox} postUri=${post.uri} postCid=${post.cid} session=${sess} onPosted=${function(){}}/>
      ${replies.length===0?html`<div style=${{color:'#aaa',fontSize:14}}>No comments yet.</div>`:
        replies.map(function(r,i){const rp=r.post;return html`<div key=${i} style=${{display:'flex',gap:12,marginBottom:20}}>
          <${Avatar} src=${rp.author.avatar} size=${32}/>
          <div>
            <div style=${{fontSize:13,fontWeight:500,color:'#f1f1f1'}}>${rp.author.displayName||rp.author.handle}<span style=${{color:'#aaa',fontWeight:400}}> ${ago(rp.indexedAt)}</span></div>
            <div style=${{fontSize:14,color:'#f1f1f1',marginTop:4,lineHeight:1.5}}>${rp.record&&rp.record.text}</div>
          </div>
        </div>`;})}
    </div>
    <div style=${{width:402,flexShrink:0}}>
      <h3 style=${{color:'#f1f1f1',fontSize:15,fontWeight:600,marginBottom:12}}>More from this channel</h3>
      ${props.related.length===0?html`<div style=${{color:'#aaa',fontSize:14}}>No related videos.</div>`:
        html`<${VideoListCompact} videos=${props.related} onWatch=${props.onWatch} onChannel=${props.onChannel}/>`
      }
    </div>
  </div>`;
}

// ── VideoListCompact — paginated compact video list ──────────────────────────


// ── Search page ───────────────────────────────────────────────────────────────
function SearchPage(props) {
  const [filter, setFilter] = useState('All');
  const results = props.results;
  const videos  = (results && results.videos)     || [];
  const actors  = (results && results.actors)     || [];
  const total   = (results && results.totalPosts) || 0;
  const err     = results && results.error;

  if (props.loading) return html`<div style=${{padding:24}}>
    <div style=${{color:'#aaa',fontSize:14,marginBottom:24}}>Searching for "${props.query}"…</div>
    <div style=${{display:'flex',flexDirection:'column',gap:12}}>
      ${[0,1,2,3,4,5].map(function(i){return html`<div key=${i} style=${{display:'flex',gap:12}}>
        <div class="shimmer" style=${{width:168,height:94,background:'#272727',flexShrink:0}}/>
        <div style=${{flex:1}}>
          <div class="shimmer" style=${{height:14,background:'#272727',marginBottom:8,width:'80%'}}/>
          <div class="shimmer" style=${{height:12,background:'#272727',width:'50%'}}/>
        </div>
      </div>`;})}
    </div>
  </div>`;

  return html`<div style=${{padding:'16px 24px'}}>
    <div style=${{display:'flex',gap:8,marginBottom:20}}>
      ${['All','Channels','Videos'].map(function(f){return html`<button key=${f} onClick=${function(){setFilter(f);}}
        style=${{padding:'6px 12px',border:'1px solid #3f3f3f',
          background:filter===f?'var(--accent)':'none',color:filter===f?'#000':'#f1f1f1',fontSize:14,borderRadius:0,cursor:'pointer',fontWeight:filter===f?600:400}}>${f}</button>`;
      })}
    </div>
    <div style=${{color:'#aaa',fontSize:13,marginBottom:16}}>
      Results for "${props.query}"
      ${!err&&results?html`<span style=${{marginLeft:12,color:'#555'}}>
        ${actors.length} channel${actors.length!==1?'s':''} · ${videos.length} video${videos.length!==1?'s':''}
      </span>`:null}
    </div>
    ${err?html`<div style=${{background:'#1a0000',border:'1px solid #882222',padding:'12px 16px',marginBottom:20,color:'#ff9999',fontSize:13,lineHeight:1.5}}>⚠️ ${err}</div>`:null}
    ${!err&&results&&actors.length===0&&videos.length===0?html`<div style=${{background:'#0d1a0d',border:'1px solid var(--accent)',padding:'12px 16px',marginBottom:20,color:'#aaa',fontSize:14,lineHeight:1.6}}>
      💡 Try your exact handle like <code style=${{color:'var(--accent)',background:'#0a1a0a',padding:'1px 5px'}}>yourname.bsky.social</code>
    </div>`:null}
    ${(filter==='All'||filter==='Channels')&&actors.length>0?html`<div style=${{marginBottom:32}}>
      <h3 style=${{color:'#f1f1f1',fontSize:15,fontWeight:600,marginBottom:12}}>Channels</h3>
      ${actors.slice(0,filter==='Channels'?20:5).map(function(a,i){return html`<div key=${i}
        onClick=${function(){props.onChannel(a.handle);}}
        style=${{display:'flex',alignItems:'center',gap:24,padding:'16px 0',cursor:'pointer',borderBottom:'1px solid #272727'}}
        onMouseEnter=${function(e){e.currentTarget.style.background='rgba(255,255,255,0.03)';}}
        onMouseLeave=${function(e){e.currentTarget.style.background='none';}}>
        <${Avatar} src=${a.avatar} size=${80}/>
        <div style=${{flex:1,minWidth:0}}>
          <div style=${{color:'#f1f1f1',fontWeight:500,fontSize:15}}>${a.displayName||a.handle}</div>
          <div style=${{color:'#aaa',fontSize:13,marginTop:2}}>@${a.handle} · ${fmt(a.followersCount||0)} followers</div>
          ${a.description?html`<div style=${{color:'#aaa',fontSize:13,marginTop:6,maxWidth:500}}>${a.description.slice(0,120)}${a.description.length>120?'…':''}</div>`:null}
        </div>
        <div style=${{display:'flex',flexDirection:'column',gap:8,flexShrink:0}}>
          <button onClick=${function(e){e.stopPropagation();props.onChannel(a.handle);}}
            style=${{background:'var(--accent)',border:'none',color:'#000',padding:'8px 16px',fontWeight:600,fontSize:13,cursor:'pointer',borderRadius:0}}>View Channel</button>
        </div>
      </div>`;})}
    </div>`:null}
    ${(filter==='All'||filter==='Videos')?html`<div>
      <h3 style=${{color:'#f1f1f1',fontSize:15,fontWeight:600,marginBottom:12}}>Videos${videos.length?' ('+videos.length+')':''}</h3>
      ${videos.length===0?html`<div style=${{color:'#aaa',fontSize:14,padding:'24px 0'}}>No videos found for "${props.query}".</div>`:
        html`<${VideoGrid} videos=${videos} onWatch=${props.onWatch} onChannel=${props.onChannel}/>`
      }
    </div>`:null}
    ${!props.loading&&!err&&results&&videos.length===0&&actors.length===0?html`<div style=${{textAlign:'center',padding:'48px 0',color:'#aaa'}}>
      <div style=${{fontSize:48,marginBottom:16}}>🔍</div>
      <div style=${{fontSize:16,marginBottom:8}}>No results found</div>
    </div>`:null}
  </div>`;
}

// ── Upload Modal ──────────────────────────────────────────────────────────────
function UploadModal(props) {
  const sess = props.session;
  const [title,     setTitle]     = useState('');
  const [desc,      setDesc]      = useState('');
  const [videoFile, setVideoFile] = useState(null);
  const [thumbFile, setThumbFile] = useState(null);
  const [thumbUrl,  setThumbUrl]  = useState(null);
  const [status,    setStatus]    = useState('idle');
  const [progress,  setProgress]  = useState('');
  const [error,     setError]     = useState('');

  function onVideoChange(e){ const f=e.target.files&&e.target.files[0]; if(f){setVideoFile(f);setError('');} }
  function onThumbChange(e){ const f=e.target.files&&e.target.files[0]; if(f){setThumbFile(f);setThumbUrl(URL.createObjectURL(f));setError('');} }

  async function upload() {
    if (!videoFile || !title.trim()) return;
    setError(''); setStatus('stitching');
    try {
      let finalFile = videoFile;
      if (thumbFile) {
        setProgress('Adding thumbnail as first frame…');
        const form = new FormData();
        form.append('video', videoFile, videoFile.name);
        form.append('thumbnail', thumbFile, thumbFile.name);
        const sr = await fetch('/process-video', {method:'POST', body:form});
        if (!sr.ok) { const e=await sr.json().catch(function(){return {};}); throw new Error(e.error||'Stitch failed'); }
        finalFile = new File([await sr.arrayBuffer()], videoFile.name, {type:videoFile.type||'video/mp4'});
      }
      setStatus('uploading'); setProgress('Authenticating with video service…');
      let pdsDid = sess.pdsDid;
      if (!pdsDid) {
        try {
          const dR = await api(PUB_PROXY+'/com.atproto.identity.resolveHandle?handle='+encodeURIComponent(sess.handle));
          if (dR.ok) {
            const dd = await dR.json();
            const plcR = await fetch('https://plc.directory/'+encodeURIComponent(dd.did));
            if (plcR.ok) {
              const plc = await plcR.json();
              const pds = (plc.service||[]).find(function(s){return s.id==='#atproto_pds';});
              if (pds) pdsDid = 'did:web:'+pds.serviceEndpoint.replace(/^https?:\/\//,'').replace(/\/$/,'');
            }
          }
        } catch(e){}
      }
      const saR = await api(AUTH_PROXY+'/com.atproto.server.getServiceAuth?aud='+encodeURIComponent(pdsDid||'did:web:bsky.social')+'&lxm=com.atproto.repo.uploadBlob',
        {headers:{Authorization:'Bearer '+sess.accessJwt}});
      if (!saR.ok) throw new Error('Auth failed: '+(await saR.text()));
      const svcToken = (await saR.json()).token;
      setProgress('Uploading video…');
      const vR = await fetch(VIDEO_PROXY+'/app.bsky.video.uploadVideo?did='+encodeURIComponent(sess.did)+'&name='+encodeURIComponent(finalFile.name),
        {method:'POST', headers:{'Content-Type':finalFile.type||'video/mp4','Authorization':'Bearer '+svcToken}, body:finalFile});
      if (!vR.ok) throw new Error('Upload failed: '+(await vR.text()));
      const jobId = (await vR.json()).jobId;
      if (!jobId) throw new Error('No job ID returned');
      setStatus('processing');
      let videoRef = null;
      for (let i=0; i<120; i++) {
        setProgress('Processing… '+Math.min(i*2,99)+'%');
        await new Promise(function(r){setTimeout(r,2000);});
        const sR = await fetch(VIDEO_PROXY+'/app.bsky.video.getJobStatus?jobId='+encodeURIComponent(jobId),{headers:{Authorization:'Bearer '+svcToken}});
        if (!sR.ok) continue;
        const job = (await sR.json()).jobStatus;
        if (job && job.blob) { videoRef = job.blob; break; }
        if (job && job.state==='JOB_STATE_FAILED') throw new Error('Processing failed: '+(job.error||'unknown'));
      }
      if (!videoRef) throw new Error('Processing timed out');
      setStatus('posting'); setProgress('Creating post…');
      const postText = title.trim()+(desc.trim()?'\n\n'+desc.trim():'');
      const pR = await api(AUTH_PROXY+'/com.atproto.repo.createRecord', {
        method:'POST', headers:{'Content-Type':'application/json','Authorization':'Bearer '+sess.accessJwt},
        body:JSON.stringify({repo:sess.did, collection:'app.bsky.feed.post', record:{
          '$type':'app.bsky.feed.post', text:postText,
          embed:{'$type':'app.bsky.embed.video', video:videoRef, alt:title.trim()},
          createdAt:new Date().toISOString(), langs:['en']
        }})
      });
      if (!pR.ok) throw new Error('Post failed: '+(await pR.text()));
      setStatus('done'); setProgress('');
      setTimeout(function(){props.onDone();}, 1500);
    } catch(e) { console.error('Upload:',e); setError(e.message||'Upload failed'); setStatus('error'); setProgress(''); }
  }

  const busy = ['stitching','uploading','processing','posting'].indexOf(status)!==-1;
  const done = status==='done';
  const iSt  = {width:'100%',padding:'10px 14px',background:'#121212',border:'1px solid #3f3f3f',color:'#f1f1f1',fontSize:14,boxSizing:'border-box',borderRadius:0};
  const STEPS = [{id:'stitching',label:'Adding thumbnail'},{id:'uploading',label:'Uploading video'},{id:'processing',label:'Processing'},{id:'posting',label:'Creating post'},{id:'done',label:'Done!'}];
  const order = ['stitching','uploading','processing','posting','done'];

  return html`<div onClick=${props.onClose}
    style=${{position:'fixed',top:0,left:0,right:0,bottom:0,background:'rgba(0,0,0,0.9)',zIndex:2000,display:'flex',alignItems:'center',justifyContent:'center',padding:16}}>
    <div onClick=${function(e){e.stopPropagation();}}
      style=${{background:'#1a1a1a',border:'1px solid #272727',padding:32,width:540,maxWidth:'100%',maxHeight:'92vh',overflowY:'auto',boxShadow:'0 20px 60px rgba(0,0,0,0.8)'}}>
      <div style=${{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:28}}>
        <h2 style=${{color:'#f1f1f1',fontSize:18,fontWeight:700}}>Upload Video</h2>
        ${!busy?html`<button onClick=${props.onClose} style=${{background:'none',border:'none',color:'#666',fontSize:22,padding:'0 4px',cursor:'pointer'}}>✕</button>`:null}
      </div>
      ${done?html`<div style=${{textAlign:'center',padding:'40px 0'}}>
        <div style=${{fontSize:64,marginBottom:16}}>🎉</div>
        <div style=${{fontSize:20,fontWeight:700,color:'#f1f1f1',marginBottom:8}}>Uploaded!</div>
      </div>`:html`
        <div style=${{marginBottom:20}}>
          <label style=${{display:'block',color:'#aaa',fontSize:13,fontWeight:500,marginBottom:8}}>Video File *</label>
          <label style=${{display:'flex',alignItems:'center',gap:14,padding:'14px 18px',
            border:'2px dashed '+(videoFile?'var(--accent)':'#3f3f3f'),cursor:busy?'default':'pointer',
            background:videoFile?'var(--accent-dim-dark)':'#111',transition:'border-color 0.15s'}}>
            <svg width="28" height="28" viewBox="0 0 24 24" fill=${videoFile?'var(--accent)':'#555'}><path d="M9 16h6v-6h4l-7-7-7 7h4v6zm-4 2h14v2H5v-2z"/></svg>
            <div style=${{flex:1,minWidth:0}}>
              <div style=${{fontSize:14,color:videoFile?'#f1f1f1':'#888'}}>${videoFile?videoFile.name:'Click to choose a video file'}</div>
              ${videoFile?html`<div style=${{fontSize:12,color:'#555',marginTop:2}}>${(videoFile.size/1024/1024).toFixed(1)} MB</div>`:null}
            </div>
            <input type="file" accept="video/*" style=${{display:'none'}} onInput=${onVideoChange} disabled=${busy}/>
          </label>
        </div>
        <div style=${{marginBottom:20}}>
          <label style=${{display:'block',color:'#aaa',fontSize:13,fontWeight:500,marginBottom:8}}>Thumbnail Image <span style=${{color:'#555'}}>(becomes first frame)</span></label>
          <label style=${{display:'flex',alignItems:'center',gap:14,padding:'14px 18px',
            border:'2px dashed '+(thumbFile?'var(--accent)':'#3f3f3f'),cursor:busy?'default':'pointer',
            background:thumbFile?'var(--accent-dim-dark)':'#111',transition:'border-color 0.15s'}}>
            <svg width="28" height="28" viewBox="0 0 24 24" fill=${thumbFile?'var(--accent)':'#555'}><path d="M21 19V5c0-1.1-.9-2-2-2H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2zM8.5 13.5l2.5 3.01L14.5 12l4.5 6H5l3.5-4.5z"/></svg>
            <div style=${{flex:1,minWidth:0}}>
              <div style=${{fontSize:14,color:thumbFile?'#f1f1f1':'#888'}}>${thumbFile?thumbFile.name:'Click to choose thumbnail'}</div>
            </div>
            <input type="file" accept="image/*" style=${{display:'none'}} onInput=${onThumbChange} disabled=${busy}/>
          </label>
          ${thumbUrl?html`<img src=${thumbUrl} style=${{width:'100%',maxHeight:160,objectFit:'cover',display:'block',marginTop:8,border:'1px solid #272727'}}/>`:null}
        </div>
        <div style=${{marginBottom:16}}>
          <label style=${{display:'block',color:'#aaa',fontSize:13,fontWeight:500,marginBottom:8}}>Title *</label>
          <input value=${title} onInput=${function(e){setTitle(e.target.value);}} placeholder="Video title" maxlength="200" style=${iSt} disabled=${busy}
            onFocus=${function(e){e.target.style.borderColor='var(--accent)';}} onBlur=${function(e){e.target.style.borderColor='#3f3f3f';}}/>
          <div style=${{textAlign:'right',fontSize:11,color:'#555',marginTop:3}}>${title.length}/200</div>
        </div>
        <div style=${{marginBottom:20}}>
          <label style=${{display:'block',color:'#aaa',fontSize:13,fontWeight:500,marginBottom:8}}>Description</label>
          <textarea value=${desc} onInput=${function(e){setDesc(e.target.value);}} placeholder="Describe your video" rows="3" maxlength="2000"
            style=${Object.assign({},iSt,{resize:'vertical'})} disabled=${busy}
            onFocus=${function(e){e.target.style.borderColor='var(--accent)';}} onBlur=${function(e){e.target.style.borderColor='#3f3f3f';}}/>
        </div>
        ${busy||status==='error'?html`<div style=${{marginBottom:20,background:'#111',border:'1px solid #272727',padding:'14px 16px'}}>
          ${STEPS.map(function(step,i){
            const cur=order.indexOf(status); const si=order.indexOf(step.id);
            const active=status===step.id; const complete=cur>si; const pending=cur<si;
            return html`<div key=${step.id} style=${{display:'flex',alignItems:'center',gap:10,padding:'5px 0',opacity:pending?0.3:1}}>
              <div style=${{width:22,height:22,flexShrink:0,display:'flex',alignItems:'center',justifyContent:'center',
                background:complete?'var(--accent-solid-dim)':active?'var(--accent)':'#2a2a2a',fontSize:10,fontWeight:700,color:active?'#000':'#fff'}}>
                ${complete?'✓':(i+1)}
              </div>
              <span style=${{fontSize:13,color:active?'#f1f1f1':'#888',fontWeight:active?600:400,flex:1}}>${step.label}</span>
              ${active?html`<span style=${{fontSize:11,color:'#888'}}>${progress}</span>`:null}
            </div>`;
          })}
        </div>`:null}
        ${error?html`<div style=${{background:'#1a0000',border:'1px solid #882222',padding:'10px 14px',marginBottom:16,color:'#ff9999',fontSize:13,lineHeight:1.5}}>
          ⚠️ ${error}
          ${error.indexOf('FFmpeg')!==-1?html`<div style=${{marginTop:8,fontSize:12,color:'#cc6666'}}>Run: <code>winget install ffmpeg</code> then restart the server.</div>`:null}
        </div>`:null}
        <button onClick=${upload} disabled=${busy||!videoFile||!title.trim()}
          style=${{width:'100%',padding:14,background:busy?'var(--accent-solid-dim)':'var(--accent)',color:busy?'var(--accent)':'#000',
            border:'none',fontSize:15,fontWeight:700,borderRadius:0,
            opacity:(busy||!videoFile||!title.trim())?0.55:1,cursor:(busy||!videoFile||!title.trim())?'not-allowed':'pointer'}}>
          ${busy?(progress||'Working…'):(thumbFile?'Stitch & Upload':'Upload Video')}
        </button>
      `}
    </div>
  </div>`;
}


// ── Edit Profile Modal ────────────────────────────────────────────────────────
function EditProfileModal(props) {
  const sess = props.session;
  const d    = props.data; // current profile data
  const [displayName, setDisplayName] = useState(d.displayName || '');
  const [bio,         setBio]         = useState(d.description || '');
  const [avatarFile,  setAvatarFile]  = useState(null);
  const [avatarUrl,   setAvatarUrl]   = useState(d.avatar || null);
  const [bannerFile,  setBannerFile]  = useState(null);
  const [bannerUrl,   setBannerUrl]   = useState(d.banner || null);
  const [saving,      setSaving]      = useState(false);
  const [err,         setErr]         = useState('');

  function onAvatarChange(e) {
    const f = e.target.files && e.target.files[0];
    if (f) { setAvatarFile(f); setAvatarUrl(URL.createObjectURL(f)); }
  }
  function onBannerChange(e) {
    const f = e.target.files && e.target.files[0];
    if (f) { setBannerFile(f); setBannerUrl(URL.createObjectURL(f)); }
  }

  async function uploadBlob(file) {
    const res = await fetch(AUTH_PROXY+'/com.atproto.repo.uploadBlob', {
      method: 'POST',
      headers: { 'Content-Type': file.type || 'image/jpeg', 'Authorization': 'Bearer '+sess.accessJwt },
      body: file
    });
    if (!res.ok) throw new Error('Image upload failed');
    return (await res.json()).blob;
  }

  async function save() {
    setSaving(true); setErr('');
    try {
      // Get current profile record so we preserve existing fields
      const recRes = await api(PUB_PROXY+'/app.bsky.actor.getProfile?actor='+encodeURIComponent(sess.did));
      const profile = recRes.ok ? await recRes.json() : {};

      const record = {
        '$type': 'app.bsky.actor.profile',
        displayName: displayName.trim() || undefined,
        description: bio.trim() || undefined,
      };

      // Upload new avatar blob if changed
      if (avatarFile) {
        record.avatar = await uploadBlob(avatarFile);
      } else if (profile.avatar) {
        // Keep existing — we need the blob ref from the raw record
        const rawRes = await api(AUTH_PROXY+'/com.atproto.repo.getRecord?repo='+encodeURIComponent(sess.did)+'&collection=app.bsky.actor.profile&rkey=self',
          {headers:{Authorization:'Bearer '+sess.accessJwt}});
        if (rawRes.ok) {
          const raw = await rawRes.json();
          if (raw.value && raw.value.avatar) record.avatar = raw.value.avatar;
        }
      }

      // Upload new banner blob if changed
      if (bannerFile) {
        record.banner = await uploadBlob(bannerFile);
      } else if (profile.banner) {
        if (!record.avatar) {
          // fetch raw record once for both
          const rawRes = await api(AUTH_PROXY+'/com.atproto.repo.getRecord?repo='+encodeURIComponent(sess.did)+'&collection=app.bsky.actor.profile&rkey=self',
            {headers:{Authorization:'Bearer '+sess.accessJwt}});
          if (rawRes.ok) {
            const raw = await rawRes.json();
            if (raw.value && raw.value.banner) record.banner = raw.value.banner;
          }
        }
      }

      // Write the profile record
      const putRes = await api(AUTH_PROXY+'/com.atproto.repo.putRecord', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer '+sess.accessJwt },
        body: JSON.stringify({
          repo: sess.did,
          collection: 'app.bsky.actor.profile',
          rkey: 'self',
          record: record
        })
      });
      if (!putRes.ok) throw new Error('Profile update failed: '+(await putRes.text()));

      props.onSaved({ displayName: displayName.trim(), description: bio.trim(), avatar: avatarUrl, banner: bannerUrl });
    } catch(e) {
      setErr(e.message || 'Save failed');
    }
    setSaving(false);
  }

  const iSt = {width:'100%',padding:'10px 14px',background:'#121212',border:'1px solid #3f3f3f',
    color:'#f1f1f1',fontSize:14,boxSizing:'border-box',borderRadius:0};
  const fileLabelSt = function(chosen) { return {
    display:'flex',alignItems:'center',gap:12,padding:'12px 16px',
    border:'2px dashed '+(chosen?'var(--accent)':'#3f3f3f'),cursor:'pointer',
    background:chosen?'var(--accent-dim-dark)':'#111',transition:'border-color 0.15s'
  };};

  return html`<div onClick=${props.onClose}
    style=${{position:'fixed',top:0,left:0,right:0,bottom:0,background:'rgba(0,0,0,0.88)',zIndex:2000,
      display:'flex',alignItems:'center',justifyContent:'center',padding:16}}>
    <div onClick=${function(e){e.stopPropagation();}}
      style=${{background:'#1a1a1a',border:'1px solid #272727',padding:32,width:520,maxWidth:'100%',
        maxHeight:'90vh',overflowY:'auto',boxShadow:'0 20px 60px rgba(0,0,0,0.8)'}}>

      <div style=${{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:24}}>
        <h2 style=${{color:'#f1f1f1',fontSize:18,fontWeight:700}}>Edit Profile</h2>
        ${!saving?html`<button onClick=${props.onClose}
          style=${{background:'none',border:'none',color:'#666',fontSize:22,padding:'0 4px',cursor:'pointer'}}
          onMouseEnter=${function(e){e.currentTarget.style.color='#f1f1f1';}}
          onMouseLeave=${function(e){e.currentTarget.style.color='#666';}}>✕</button>`:null}
      </div>
      <div style=${{marginBottom:20}}>
        <label style=${{display:'block',color:'#aaa',fontSize:13,fontWeight:500,marginBottom:8}}>Banner Image</label>
        ${bannerUrl?html`<img src=${bannerUrl} style=${{width:'100%',height:120,objectFit:'cover',display:'block',marginBottom:8,border:'1px solid #272727'}}/>`:null}
        <label style=${fileLabelSt(!!bannerFile)}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill=${bannerFile?'var(--accent)':'#555'}><path d="M21 19V5c0-1.1-.9-2-2-2H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2zM8.5 13.5l2.5 3.01L14.5 12l4.5 6H5l3.5-4.5z"/></svg>
          <span style=${{fontSize:13,color:bannerFile?'#f1f1f1':'#888'}}>${bannerFile?bannerFile.name:'Click to choose banner image'}</span>
          <input type="file" accept="image/*" style=${{display:'none'}} onInput=${onBannerChange} disabled=${saving}/>
        </label>
      </div>
      <div style=${{marginBottom:20}}>
        <label style=${{display:'block',color:'#aaa',fontSize:13,fontWeight:500,marginBottom:8}}>Profile Picture</label>
        <div style=${{display:'flex',alignItems:'center',gap:16,marginBottom:8}}>
          ${avatarUrl?html`<img src=${avatarUrl} style=${{width:60,height:60,borderRadius:'50%',objectFit:'cover',border:'2px solid #272727'}}/>`:null}
          <label style=${Object.assign({},fileLabelSt(!!avatarFile),{flex:1})}>
            <svg width="20" height="20" viewBox="0 0 24 24" fill=${avatarFile?'var(--accent)':'#555'}><path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z"/></svg>
            <span style=${{fontSize:13,color:avatarFile?'#f1f1f1':'#888'}}>${avatarFile?avatarFile.name:'Click to choose profile picture'}</span>
            <input type="file" accept="image/*" style=${{display:'none'}} onInput=${onAvatarChange} disabled=${saving}/>
          </label>
        </div>
      </div>
      <div style=${{marginBottom:16}}>
        <label style=${{display:'block',color:'#aaa',fontSize:13,fontWeight:500,marginBottom:8}}>Display Name</label>
        <input value=${displayName} onInput=${function(e){setDisplayName(e.target.value);}} maxlength="64"
          placeholder="Your display name" style=${iSt} disabled=${saving}
          onFocus=${function(e){e.target.style.borderColor='var(--accent)';}}
          onBlur=${function(e){e.target.style.borderColor='#3f3f3f';}}/>
      </div>
      <div style=${{marginBottom:24}}>
        <label style=${{display:'block',color:'#aaa',fontSize:13,fontWeight:500,marginBottom:8}}>Bio</label>
        <textarea value=${bio} onInput=${function(e){setBio(e.target.value);}} maxlength="256"
          placeholder="Tell people about yourself" rows="4"
          style=${Object.assign({},iSt,{resize:'vertical'})} disabled=${saving}
          onFocus=${function(e){e.target.style.borderColor='var(--accent)';}}
          onBlur=${function(e){e.target.style.borderColor='#3f3f3f';}}/>
        <div style=${{textAlign:'right',fontSize:11,color:'#555',marginTop:3}}>${bio.length}/256</div>
      </div>

      ${err?html`<div style=${{background:'#1a0000',border:'1px solid #882222',padding:'10px 14px',
        marginBottom:16,color:'#ff9999',fontSize:13}}>⚠️ ${err}</div>`:null}

      <button onClick=${save} disabled=${saving}
        style=${{width:'100%',padding:14,background:saving?'var(--accent-solid-dim)':'var(--accent)',color:saving?'var(--accent)':'#000',
          border:'none',fontSize:15,fontWeight:700,borderRadius:0,
          opacity:saving?0.7:1,cursor:saving?'not-allowed':'pointer'}}>
        ${saving?'Saving…':'Save Profile'}
      </button>
    </div>
  </div>`;
}


// ── PostCard — a single standalone post card used in ChannelPostsFeed ────────
function PostCard(props) {
  const item   = props.item;  // full feed item (has reason for reposts)
  const post   = item.post || item;
  const reason = item.reason;
  const isRepost = reason && reason['$type'] === 'app.bsky.feed.defs#reasonRepost';
  const reposter = isRepost ? (reason.by || {}) : null;

  const rec    = post.record || {};
  const author = post.author || {};
  const embed  = post.embed;
  const text   = rec.text || '';
  const sess   = props.session;
  const [showShare, setShowShare] = useState(false);
  const ADULT_LABELS2=['sexual','porn','nudity','graphic-media','adult'];
  const isAdult=!!(post.labels&&post.labels.some(function(l){return ADULT_LABELS2.indexOf(l.val)!==-1;}));

  // Like state
  const [liked,      setLiked]      = useState(!!viewerLiked(post));
  const [likeUri,    setLikeUri]    = useState(viewerLiked(post)||null);
  const [likeCount,  setLikeCount]  = useState(post.likeCount||0);
  const [reposted,   setReposted]   = useState(!!viewerReposted(post));
  const [repostUri,  setRepostUri]  = useState(viewerReposted(post)||null);
  const [repostCount,setRepostCount]= useState(post.repostCount||0);

  useEffect(function(){
    setLiked(!!viewerLiked(post)); setLikeUri(viewerLiked(post)||null); setLikeCount(post.likeCount||0);
    setReposted(!!viewerReposted(post)); setRepostUri(viewerReposted(post)||null); setRepostCount(post.repostCount||0);
  },[post.uri]);
  // Fresh sync: re-fetch post with auth so viewer.like/repost are populated
  useEffect(function(){
    if(!sess||!post.uri) return;
    var cancelled=false;
    api(AUTH_PROXY+'/app.bsky.feed.getPosts?uris='+encodeURIComponent(post.uri),
      {headers:{Authorization:'Bearer '+sess.accessJwt}})
      .then(function(r){return r.ok?r.json():null;})
      .then(function(d){
        if(d&&!cancelled){
          var p=(d.posts||[])[0];
          if(p){
            setLiked(!!viewerLiked(p)); setLikeUri(viewerLiked(p)||null); setLikeCount(p.likeCount||0);
            setReposted(!!viewerReposted(p)); setRepostUri(viewerReposted(p)||null); setRepostCount(p.repostCount||0);
          }
        }
      }).catch(function(){});
    return function(){cancelled=true;};
  },[post.uri, sess&&sess.accessJwt]);

  async function toggleRepost(e) {
    e.stopPropagation();
    if(!sess) return;
    if(reposted){
      setReposted(false); setRepostCount(function(n){return n-1;});
      const rkey=repostUri&&repostUri.split('/').pop(); setRepostUri(null);
      if(rkey) await bskyDelete(sess,'app.bsky.feed.repost',rkey);
    } else {
      setReposted(true); setRepostCount(function(n){return n+1;});
      const uri=await bskyCreate(sess,'app.bsky.feed.repost',{
        '$type':'app.bsky.feed.repost',subject:{uri:post.uri,cid:post.cid},createdAt:new Date().toISOString()
      });
      setRepostUri(uri);
    }
  }

  async function toggleLike(e) {
    e.stopPropagation();
    if (!sess) return;
    if (liked) {
      setLiked(false); setLikeCount(function(n){return n-1;});
      const rkey = likeUri && likeUri.split('/').pop(); setLikeUri(null);
      if (rkey) await bskyDelete(sess,'app.bsky.feed.like',rkey);
    } else {
      setLiked(true); setLikeCount(function(n){return n+1;});
      const uri = await bskyCreate(sess,'app.bsky.feed.like',{
        '$type':'app.bsky.feed.like', subject:{uri:post.uri,cid:post.cid}, createdAt:new Date().toISOString()
      });
      setLikeUri(uri);
    }
  }

  var imgs = null;
  if (embed) {
    var et = embed['$type']||'';
    if (et==='app.bsky.embed.images#view' && embed.images) imgs = embed.images;
    else if ((et==='app.bsky.embed.recordWithMedia#view'||et==='app.bsky.embed.recordWithMedia') && embed.media && embed.media.images) imgs = embed.media.images;
  }

  return html`<div style=${{background:'#141414',borderBottom:'2px solid #1a1a1a',transition:'background 0.1s'}} onMouseEnter=${function(e){e.currentTarget.style.background='#181818';}} onMouseLeave=${function(e){e.currentTarget.style.background='#141414';}}>${showShare?html`<${ShareModal} post=${post} session=${sess} onClose=${function(){setShowShare(false);}}/>`  :null}${isRepost?html`<div style=${{padding:'10px 24px 0',display:'flex',alignItems:'center',gap:8,color:'#666',fontSize:12}}><svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M7 7h10v3l4-4-4-4v3H5v6h2V7zm10 10H7v-3l-4 4 4 4v-3h12v-6h-2v4z"/></svg><span>${reposter&&(reposter.displayName||reposter.handle)||'Someone'} reposted</span></div>`:null}<div style=${{padding:'18px 24px'}}>
      <div style=${{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:14}}
        onClick=${function(e){e.stopPropagation();props.onChannel&&props.onChannel(author.handle);}}>
        <div style=${{display:'flex',alignItems:'center',gap:12,cursor:'pointer',flex:1,minWidth:0}}>
          <${Avatar} src=${author.avatar} size=${44}
            onClick=${function(e){e.stopPropagation();props.onChannel&&props.onChannel(author.handle);}}/>
          <div style=${{flex:1,minWidth:0}}>
            <div style=${{color:'#f1f1f1',fontWeight:600,fontSize:15}}>${author.displayName||author.handle}</div>
            <div style=${{color:'#555',fontSize:12}}>@${author.handle} · ${ago(post.indexedAt)}</div>
          </div>
        </div>
      </div>
      <div onClick=${function(){props.onOpenPost&&props.onOpenPost(item);}} style=${{cursor:'pointer'}}>
        ${text?html`<div style=${{fontSize:16,color:'#e0e0e0',lineHeight:1.7,whiteSpace:'pre-wrap',
          marginBottom:12,wordBreak:'break-word'}}>${text}</div>`:null}
        ${imgs&&imgs.length?html`<div style=${{display:'grid',
          gridTemplateColumns:imgs.length===1?'1fr':imgs.length===2?'1fr 1fr':'1fr 1fr',gap:3,marginBottom:12}}>
          ${imgs.slice(0,4).map(function(img,ii){return html`<img key=${ii}
            src=${img.thumb||img.fullsize} alt=${img.alt||''}
            style=${{width:'100%',aspectRatio:imgs.length===1?'16/9':'1/1',objectFit:'cover',display:'block'}}
/>`;})}
        </div>`:null}
      </div>
      <div style=${{display:'flex',alignItems:'center',justifyContent:'space-between',marginTop:14}} onClick=${function(e){e.stopPropagation();}}>
        <div style=${{display:'flex',gap:8,flexWrap:'wrap'}}>
          <button onClick=${toggleLike} style=${{background:liked?'var(--accent-dim)':'#1a1a1a',border:'1px solid '+(liked?'var(--accent)':'#333'),color:liked?'var(--accent)':'#aaa',cursor:sess?'pointer':'default',display:'flex',alignItems:'center',gap:6,fontSize:15,padding:'10px 16px',borderRadius:0}}><svg width="18" height="18" viewBox="0 0 24 24" fill=${liked?'var(--accent)':'currentColor'}><path d="M1 21h4V9H1v12zm22-11c0-1.1-.9-2-2-2h-6.31l.95-4.57.03-.32c0-.41-.17-.79-.44-1.06L14.17 1 7.59 7.59C7.22 7.95 7 8.45 7 9v10c0 1.1.9 2 2 2h9c.83 0 1.54-.5 1.84-1.22l3.02-7.05c.09-.23.14-.47.14-.73v-2z"/></svg><span>${fmt(likeCount)}</span></button>
          <button title="Dislike (coming soon)" style=${{background:'#1a1a1a',border:'1px solid #333',color:'#aaa',display:'flex',alignItems:'center',gap:6,fontSize:15,padding:'10px 16px',borderRadius:0,cursor:'pointer'}}><svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M15 3H6c-.83 0-1.54.5-1.84 1.22l-3.02 7.05c-.09.23-.14.47-.14.73v2c0 1.1.9 2 2 2h6.31l-.95 4.57-.03.32c0 .41.17.79.44 1.06L10.83 23l6.59-6.59c.36-.36.58-.86.58-1.41V5c0-1.1-.9-2-2-2zm4 0v12h4V3h-4z"/></svg>Dislike</button>
          <button onClick=${toggleRepost} style=${{background:reposted?'var(--accent-dim)':'#1a1a1a',border:'1px solid '+(reposted?'var(--accent)':'#333'),color:reposted?'var(--accent)':'#aaa',cursor:sess?'pointer':'default',display:'flex',alignItems:'center',gap:6,fontSize:15,padding:'10px 16px',borderRadius:0}}><svg width="18" height="18" viewBox="0 0 24 24" fill=${reposted?'var(--accent)':'currentColor'}><path d="M7 7h10v3l4-4-4-4v3H5v6h2V7zm10 10H7v-3l-4 4 4 4v-3h12v-6h-2v4z"/></svg><span>${fmt(repostCount)}</span></button>
          ${isAdult?html`<button title="Came to this (coming soon)" style=${{background:'rgba(255,0,120,0.08)',border:'1px solid #ff0077',color:'#ff55aa',display:'flex',alignItems:'center',gap:6,fontSize:15,padding:'10px 16px',borderRadius:0,cursor:'pointer'}}>🔥 Came to this</button>`:null}
        </div>
        <div style=${{display:'flex',gap:8,alignItems:'center'}}>
          ${sess&&sess.did!==author.did?html`<div onClick=${function(e){e.stopPropagation();}}><${SubscribeButton} did=${author.did} viewer=${author.viewer} session=${sess} small=${false}/></div>`:null}
          <button onClick=${function(e){e.stopPropagation();setShowShare(true);}} style=${{background:'#1a1a1a',border:'1px solid #333',color:'#aaa',display:'flex',alignItems:'center',gap:6,fontSize:14,padding:'8px 12px',borderRadius:0,cursor:'pointer'}} title="Share via DM"><svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M18 16.08c-.76 0-1.44.3-1.96.77L8.91 12.7c.05-.23.09-.46.09-.7s-.04-.47-.09-.7l7.05-4.11c.54.5 1.25.81 2.04.81 1.66 0 3-1.34 3-3s-1.34-3-3-3-3 1.34-3 3c0 .24.04.47.09.7L8.04 9.81C7.5 9.31 6.79 9 6 9c-1.66 0-3 1.34-3 3s1.34 3 3 3c.79 0 1.5-.31 2.04-.81l7.12 4.16c-.05.21-.08.43-.08.65 0 1.61 1.31 2.92 2.92 2.92 1.61 0 2.92-1.31 2.92-2.92s-1.31-2.92-2.92-2.92z"/></svg></button>
        </div>
      </div>
    </div>
  </div>`;
}

// ── PostDetailPage — standalone post view with comments ─────────────────────
function PostDetailPage(props) {
  const item   = props.item;
  const post   = item.post || item;
  const author = post.author || {};
  const rec    = post.record || {};
  const embed  = post.embed;
  const thread = props.thread || null;
  const replies = thread && thread.replies
    ? thread.replies.filter(function(r){return r.post;}).slice(0,50)
    : [];

  // Extract images from embed
  var imgs = null;
  if (embed) {
    var et = embed['$type']||'';
    if (et==='app.bsky.embed.images#view') imgs = embed.images;
    else if ((et==='app.bsky.embed.recordWithMedia#view'||et==='app.bsky.embed.recordWithMedia')
             && embed.media && embed.media.images) imgs = embed.media.images;
  }

  return html`<div style=${{padding:16}}>
    <button onClick=${props.onBack}
      style=${{background:'none',border:'1px solid #3f3f3f',color:'#f1f1f1',padding:'7px 16px',
        marginBottom:16,cursor:'pointer',fontSize:13,display:'flex',alignItems:'center',gap:6,borderRadius:0}}
      onMouseEnter=${function(e){e.currentTarget.style.background='#272727';}}
      onMouseLeave=${function(e){e.currentTarget.style.background='none';}}>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z"/></svg>
      Back
    </button>
    <div style=${{display:'flex',gap:24,alignItems:'flex-start',maxWidth:1400,margin:'0 auto'}}>
      <div style=${{flex:'0 0 60%',minWidth:0,background:'#141414',border:'1px solid #222',padding:28}}>
        <div style=${{display:'flex',alignItems:'center',gap:14,marginBottom:18,cursor:'pointer'}}
          onClick=${function(){props.onChannel(author.handle);}}>
          <${Avatar} src=${author.avatar} size=${56}/>
          <div style=${{flex:1}}>
            <div style=${{color:'#f1f1f1',fontWeight:700,fontSize:18}}>${author.displayName||author.handle}</div>
            <div style=${{color:'#555',fontSize:13}}>@${author.handle} · ${ago(post.indexedAt)}</div>
          </div>
        </div>
        ${rec.text?html`<div style=${{fontSize:17,color:'#e0e0e0',lineHeight:1.75,
          whiteSpace:'pre-wrap',marginBottom:20,wordBreak:'break-word'}}>${rec.text}</div>`:null}
        ${imgs&&imgs.length?html`<div style=${{display:'grid',
          gridTemplateColumns:imgs.length===1?'1fr':imgs.length===2?'1fr 1fr':'1fr 1fr',
          gap:4,marginBottom:20}}>
          ${imgs.map(function(img,ii){return html`<img key=${ii}
            src=${img.fullsize||img.thumb} alt=${img.alt||''}
            style=${{width:'100%',aspectRatio:imgs.length===1?'auto':'1/1',
              maxHeight:600,objectFit:'contain',display:'block',background:'#0a0a0a'}}
          />`;}) }
        </div>`:null}
        <div style=${{display:'flex',gap:24,color:'#555',fontSize:13,
          paddingTop:16,borderTop:'1px solid #1e1e1e'}}>
          <span style=${{color:'#f1f1f1'}}>♥ ${fmt(post.likeCount||0)}</span>
          <span>↩ ${fmt(post.repostCount||0)}</span>
          <span>💬 ${fmt(post.replyCount||0)}</span>
        </div>
      </div>
      <div style=${{flex:'0 0 38%',minWidth:0,maxHeight:'85vh',overflowY:'auto',
        background:'#111',border:'1px solid #1e1e1e',padding:20}}>
        <h3 style=${{color:'#f1f1f1',fontSize:15,fontWeight:600,marginBottom:16,
          paddingBottom:10,borderBottom:'1px solid #1e1e1e'}}>
          ${fmt(post.replyCount||0)} Replies
        </h3>
        <${CommentBox} postUri=${post.uri} postCid=${post.cid} session=${props.session} onPosted=${function(){}}/>
        ${!thread?html`<div style=${{color:'#555',fontSize:13,textAlign:'center',paddingTop:24}}>Loading…</div>`:
          replies.length===0?html`<div style=${{color:'#555',fontSize:14}}>No replies yet.</div>`:
          replies.map(function(r,i){
            const rp=r.post; const rc=rp.record||{};
            return html`<div key=${i} style=${{display:'flex',gap:10,padding:'12px 0',borderBottom:'1px solid #1a1a1a'}}>
              <${Avatar} src=${rp.author.avatar} size=${34}
                onClick=${function(){props.onChannel(rp.author.handle);}}/>
              <div style=${{flex:1,minWidth:0}}>
                <div style=${{fontSize:13,fontWeight:600,color:'#f1f1f1',marginBottom:3}}>
                  ${rp.author.displayName||rp.author.handle}
                  <span style=${{color:'#555',fontWeight:400,fontSize:11,marginLeft:6}}>${ago(rp.indexedAt)}</span>
                </div>
                <div style=${{fontSize:13,color:'#ccc',lineHeight:1.55,whiteSpace:'pre-wrap',wordBreak:'break-word'}}>${rc.text}</div>
              </div>
            </div>`;
          })
        }
      </div>

    </div>
  </div>`;
}

// ── ChannelPostsFeed ─────────────────────────────────────────────────────────
function ChannelPostsFeed(props) {
  const [visible,     setVisible]     = useState(10);
  const [filterOwn,   setFilterOwn]   = useState(false);
  const [openedItem,  setOpenedItem]  = useState(null);
  const [postThread,  setPostThread]  = useState(null);
  const sess = props.session;

  // Close any open post when the posts list changes (new channel loaded)
  const prevFirstUri = useRef(null);
  const firstPostUri = props.posts && props.posts.length > 0
    ? ((props.posts[0].post || props.posts[0]).uri) : null;
  if (firstPostUri && firstPostUri !== prevFirstUri.current) {
    prevFirstUri.current = firstPostUri;
    if (openedItem !== null) { setOpenedItem(null); setPostThread(null); }
    if (visible !== 10) setVisible(10);
    if (filterOwn) setFilterOwn(false);
  }

  // Feed items (full item with reason)
  const _cf2 = loadFilter();
  const allItems = filterByContent(props.posts||[], _cf2);
  const filtered = filterOwn
    ? allItems.filter(function(item){ return !item.reason; })
    : allItems;
  const shown   = filtered.slice(0, visible);
  const hasMore = visible < filtered.length;

  const loadMore = useCallback(function(){ setVisible(function(v){return v+10;}); }, []);
  useScrollLoad(hasMore, loadMore);

  async function openPost(item) {
    setOpenedItem(item);
    setPostThread(null);
    const post = item.post || item;
    try {
      const r = await api(PUB_PROXY+'/app.bsky.feed.getPostThread?uri='+encodeURIComponent(post.uri)+'&depth=6');
      if (r.ok) { const d=await r.json(); setPostThread(d.thread); }
    } catch(e){}
  }

  if (openedItem) {
    return html`<${PostDetailPage}
      item=${openedItem}
      thread=${postThread}
      session=${sess}
      onBack=${function(){setOpenedItem(null);setPostThread(null);}}
      onChannel=${props.onChannel}
    />`;
  }

  if (props.loading) return html`<div style=${{padding:'32px 0',textAlign:'center',color:'#aaa'}}>Loading posts…</div>`;
  if (!allItems.length) return html`<div style=${{padding:'32px 0',textAlign:'center',color:'#aaa'}}>No posts found.</div>`;

  return html`<div>
    ${!props.hideFilter?html`<div style=${{display:'flex',gap:8,marginBottom:16}}>
      <button onClick=${function(){setFilterOwn(false);setVisible(10);}}
        style=${{padding:'6px 14px',border:'none',background:!filterOwn?'var(--accent)':'#1a1a1a',
          color:!filterOwn?'#000':'#aaa',fontSize:13,fontWeight:!filterOwn?600:400,
          cursor:'pointer',borderRadius:0}}>Posts + Reposts</button>
      <button onClick=${function(){setFilterOwn(true);setVisible(10);}}
        style=${{padding:'6px 14px',border:'none',background:filterOwn?'var(--accent)':'#1a1a1a',
          color:filterOwn?'#000':'#aaa',fontSize:13,fontWeight:filterOwn?600:400,
          cursor:'pointer',borderRadius:0}}>Posts Only</button>
    </div>`:null}
    <div style=${{maxWidth:680,margin:'0 auto'}}>
      ${shown.map(function(item,i){
        return html`<${PostCard} key=${(item.post||item).uri||i}
          item=${item}
          session=${sess}
          onChannel=${props.onChannel}
          onOpenPost=${openPost}
        />`;
      })}
    </div>
  </div>`;
}



// ── LikedTab — Videos and Posts the user has liked ───────────────────────────
function LikedTab(props) {
  const [sub, setSub] = useState('Liked Posts');
  if(props.loading) return html`<div style=${{padding:'32px 0',textAlign:'center',color:'#aaa'}}>Loading liked content…</div>`;
  if(!props.posts||!props.posts.length) return html`<div style=${{padding:'32px 0',textAlign:'center',color:'#aaa'}}>No liked content found.</div>`;
  const tabSt = function(a){ return {
    padding:'10px 20px',background:'none',border:'none',cursor:'pointer',fontSize:14,
    color:a?'#f1f1f1':'#aaa',fontWeight:a?500:400,
    borderBottom:'3px solid '+(a?'var(--accent)':'transparent')
  };};
  return html`<div>
    <div style=${{display:'flex',justifyContent:'flex-end',borderBottom:'1px solid #272727',marginBottom:20}}>
      <button style=${tabSt(sub==='Liked Videos')} onClick=${function(){setSub('Liked Videos');}}>Liked Videos (${(props.videos||[]).length})</button>
      <button style=${tabSt(sub==='Liked Posts')}  onClick=${function(){setSub('Liked Posts');}}>Liked Posts (${(props.posts||[]).length})</button>
    </div>
    ${sub==='Liked Videos'?html`<${VideoGrid} videos=${props.videos||[]} loading=${false} onWatch=${props.onWatch} onChannel=${props.onChannel}/>`:null}
    ${sub==='Liked Posts'?html`<${ChannelPostsFeed} posts=${props.posts||[]} loading=${false} session=${props.session} onChannel=${props.onChannel} onWatch=${props.onWatch} hideFilter=${true}/>`:null}
  </div>`;
}


// ── ChannelDMsTab — full DM chat with a channel ──────────────────────────────
function ChannelDMsTab(props) {
  const sess      = props.session;
  const channelDid= props.channelDid;
  const [msgs,    setMsgs]    = useState([]);
  const [loading, setLoading] = useState(true);
  const [err,     setErr]     = useState('');
  const [convoId, setConvoId] = useState(null);
  const [text,    setText]    = useState('');
  const [sending, setSending] = useState(false);
  const scrollRef = useRef(null);

  function chatUrl(path) {
    var pdsHost = sess&&sess.pdsDid ? sess.pdsDid.replace('did:web:','') : 'bsky.social';
    return CHAT_PROXY+path+'?_pds='+encodeURIComponent(pdsHost);
  }

  // Scroll to bottom whenever msgs change
  useEffect(function(){
    if(scrollRef.current){
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  },[msgs]);

  useEffect(function(){
    if(!sess||!channelDid) return;
    var cancelled=false;
    async function load(){
      setLoading(true); setErr(''); setMsgs([]);
      try{
        // Find existing convo via listConvos (don't use getConvoForMembers — creates empty convo)
        var lr=await api(chatUrl('/chat.bsky.convo.listConvos'),
          {headers:{Authorization:'Bearer '+sess.accessJwt}});
        if(!lr.ok){
          if(lr.status===401||lr.status===403){setErr('DM access denied — check your App Password has Direct Messages enabled.');setLoading(false);return;}
          throw new Error(await lr.text());
        }
        var convos=(await lr.json()).convos||[];
        var found=convos.find(function(c){
          return (c.members||[]).some(function(mb){return mb.did===channelDid;});
        });
        if(!found){if(!cancelled){setLoading(false);setErr('no_convo');} return;}
        var cid=found.id;
        if(!cancelled) setConvoId(cid);
        // Paginate all messages
        var allMsgs=[];
        var cur='';
        for(var pg=0;pg<20;pg++){
          var pageUrl=chatUrl('/chat.bsky.convo.getMessages')+'&convoId='+encodeURIComponent(cid)+'&limit=100'+(cur?'&cursor='+encodeURIComponent(cur):'');
          var mr=await api(pageUrl,{headers:{Authorization:'Bearer '+sess.accessJwt}});
          if(!mr.ok) break;
          var pd=await mr.json();
          allMsgs=allMsgs.concat(pd.messages||[]);
          if(!pd.cursor) break;
          cur=pd.cursor;
        }
        var withContent=allMsgs.filter(function(m){
          return (m['$type']||'')!=='chat.bsky.convo.defs#deletedMessageView';
        });
        withContent.reverse(); // oldest first
        // Hydrate embedded post URIs
        var hydrated={};
        var uris=withContent.map(function(m){
          return m.embed&&(m.embed['$type']==='app.bsky.embed.record'||m.embed['$type']==='app.bsky.embed.record#view')
            ?m.embed.record&&m.embed.record.uri:null;
        }).filter(Boolean);
        if(uris.length){
          for(var k=0;k<uris.length;k+=25){
            var qstr=uris.slice(k,k+25).map(function(u){return 'uris='+encodeURIComponent(u);}).join('&');
            var pr=await api(PUB_PROXY+'/app.bsky.feed.getPosts?'+qstr);
            if(pr.ok){(await pr.json()).posts.forEach(function(p){hydrated[p.uri]=p;});}
          }
        }
        if(!cancelled) setMsgs(withContent.map(function(m){
          var embPost=null;
          if(m.embed){
            var et=m.embed['$type']||'';
            if(et==='app.bsky.embed.record'||et==='app.bsky.embed.record#view'){
              embPost=hydrated[(m.embed.record&&m.embed.record.uri)]||null;
            }
          }
          return Object.assign({},m,{_post:embPost,_isMine:m.sender&&m.sender.did===sess.did});
        }));
      }catch(e2){if(!cancelled)setErr(e2.message||'Failed');}
      if(!cancelled) setLoading(false);
    }
    load();
    return function(){cancelled=true;};
  },[channelDid, sess&&sess.did]);

  async function sendMsg(){
    if(!text.trim()||!convoId||sending) return;
    setSending(true);
    try{
      var r=await api(chatUrl('/chat.bsky.convo.sendMessage'),{
        method:'POST',
        headers:{Authorization:'Bearer '+sess.accessJwt,'Content-Type':'application/json'},
        body:JSON.stringify({convoId:convoId,message:{text:text.trim()}})
      });
      if(!r.ok) throw new Error(await r.text());
      var sent=await r.json();
      setText('');
      setMsgs(function(prev){
        return prev.concat([Object.assign({},sent,{_isMine:true,_post:null})]);
      });
    }catch(e){alert('Send failed: '+e.message);}
    setSending(false);
  }

  function onKey(e){
    if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendMsg();}
  }

  if(!sess) return html`<div style=${{padding:'32px 0',textAlign:'center',color:'#aaa'}}>Sign in to see DMs.</div>`;
  if(loading) return html`<div style=${{padding:'32px 0',textAlign:'center',color:'#aaa'}}>Loading DMs…</div>`;
  if(err==='no_convo') return html`<div style=${{padding:'32px 0',textAlign:'center',color:'#aaa'}}>
    <div style=${{fontSize:32,marginBottom:12}}>💬</div>
    <div>No DM history with this channel yet.</div>
    <div style=${{fontSize:12,marginTop:8,color:'#555'}}>Use the Share button on a video or post to start a conversation.</div>
  </div>`;
  if(err) return html`<div style=${{padding:'32px 0',color:'#ff6666',fontSize:14}}>${err}</div>`;

  return html`<div style=${{display:'flex',flexDirection:'column',height:'calc(100vh - 380px)',minHeight:400}}>
    <div ref=${scrollRef} style=${{flex:1,overflowY:'auto',padding:'8px 0'}}>
      ${!msgs.length?html`<div style=${{padding:'32px 0',textAlign:'center',color:'#555'}}>No messages yet.</div>`:null}
      ${msgs.map(function(m,i){
        var isMine=m._isMine;
        var post=m._post;
        var et=m.embed?m.embed['$type']||'':'';
        var isRecordEmbed=et==='app.bsky.embed.record'||et==='app.bsky.embed.record#view';
        var isImgEmbed=et==='app.bsky.embed.images'||et==='app.bsky.embed.images#view';
        var isVideo=post&&(isVid(post)||isVidRaw(post));
        var hasText=!!(m.text&&m.text.trim());
        // Chat image embeds may have {images:[{image:{thumb,fullsize},alt}]} or {images:[{thumb,fullsize,alt}]}
        var imgs=[];
        if(isImgEmbed&&m.embed.images){
          imgs=m.embed.images.map(function(img){
            if(img.thumb||img.fullsize) return img; // already flat
            if(img.image) return {thumb:img.image.thumb,fullsize:img.image.fullsize,alt:img.alt||''};
            return null;
          }).filter(Boolean);
        }
        return html`<div key=${i} style=${{display:'flex',flexDirection:'column',
          alignItems:isMine?'flex-end':'flex-start',padding:'4px 16px',marginBottom:2}}>
          <div style=${{fontSize:10,color:'#444',marginBottom:2}}>${isMine?'You':'Channel'} · ${ago(m.sentAt)}</div>
          ${hasText?html`<div style=${{background:isMine?'var(--accent-dim)':'#1e1e1e',color:'#f1f1f1',
            padding:'9px 13px',fontSize:14,lineHeight:1.5,maxWidth:'72%',wordBreak:'break-word',
            border:'1px solid '+(isMine?'var(--accent)':'#2a2a2a'),borderRadius:2}}>
            ${m.text}
          </div>`:null}
          ${imgs.length?html`<div style=${{marginTop:hasText?6:0,display:'grid',
            gridTemplateColumns:imgs.length===1?'1fr':imgs.length===2?'1fr 1fr':'1fr 1fr',
            gap:3,maxWidth:360}}>
            ${imgs.map(function(img,ii){return html`<img key=${ii} src=${img.thumb||img.fullsize||''} alt=${img.alt||''}
              style=${{width:'100%',aspectRatio:imgs.length===1?'16/9':'1/1',objectFit:'cover',display:'block',cursor:'pointer'}}
              onClick=${function(){window.open(img.fullsize||img.thumb,'_blank');}}/>`;})}
          </div>`:null}
          ${isRecordEmbed&&post?html`<div style=${{marginTop:hasText||imgs.length?6:0,maxWidth:400,width:'100%'}}>
            ${isVideo
              ? html`<div style=${{cursor:'pointer'}} onClick=${function(){props.onWatch&&props.onWatch(post);}}>
                  <div style=${{width:'100%',paddingBottom:'56.25%',position:'relative',overflow:'hidden',background:'#1a1a1a',outline:'2px solid transparent',transition:'outline 0.15s'}}
                    onMouseEnter=${function(e){e.currentTarget.style.outline='2px solid var(--accent)';}}
                    onMouseLeave=${function(e){e.currentTarget.style.outline='2px solid transparent';}}>
                    <div style=${{position:'absolute',top:0,left:0,right:0,bottom:0}}>
                      <${Thumb} src=${post.embed&&(post.embed.thumbnail||(post.embed.images&&post.embed.images[0]&&post.embed.images[0].thumb))||null}/>
                    </div>
                  </div>
                  <div style=${{display:'flex',gap:10,paddingTop:8,alignItems:'flex-start'}}>
                    <${Avatar} src=${post.author&&post.author.avatar} size=${32}
                      onClick=${function(e){e.stopPropagation();props.onChannel&&post.author&&props.onChannel(post.author.handle);}}/>
                    <div style=${{flex:1,minWidth:0}}>
                      <div class="clamp2" style=${{fontSize:13,fontWeight:500,color:'#f1f1f1',lineHeight:1.3}}>${(post.record&&post.record.text&&post.record.text.split('\n')[0])||'Video'}</div>
                      <div style=${{fontSize:12,color:'#aaa',marginTop:2}}>${post.author&&(post.author.displayName||post.author.handle)}</div>
                    </div>
                  </div>
                </div>`
              : html`<div style=${{background:'#141414',border:'1px solid #2a2a2a',padding:14,cursor:'pointer',borderRadius:2}}
                  onClick=${function(){props.onChannel&&post.author&&props.onChannel(post.author.handle);}}>
                  <div style=${{display:'flex',gap:8,alignItems:'center',marginBottom:8}}>
                    <${Avatar} src=${post.author&&post.author.avatar} size=${28}/>
                    <span style=${{color:'#f1f1f1',fontSize:13,fontWeight:600}}>${post.author&&(post.author.displayName||post.author.handle)}</span>
                  </div>
                  ${post.record&&post.record.text?html`<div style=${{fontSize:13,color:'#ccc',lineHeight:1.5,whiteSpace:'pre-wrap',marginBottom:8}}>${post.record.text.slice(0,200)}</div>`:null}
                  ${(function(){
                    var pe=post.embed;
                    if(!pe) return null;
                    var pet=pe['$type']||'';
                    var pimgs=null;
                    if(pet==='app.bsky.embed.images#view'||pet==='app.bsky.embed.images') pimgs=pe.images||[];
                    else if((pet==='app.bsky.embed.recordWithMedia#view'||pet==='app.bsky.embed.recordWithMedia')&&pe.media) pimgs=pe.media.images||[];
                    if(!pimgs||!pimgs.length) return null;
                    return html`<div style=${{display:'grid',gridTemplateColumns:pimgs.length===1?'1fr':'1fr 1fr',gap:2}}>
                      ${pimgs.slice(0,4).map(function(img,ii){
                        var src2=img.thumb||img.fullsize||(img.image&&(img.image.thumb||img.image.fullsize))||'';
                        var full=img.fullsize||(img.image&&img.image.fullsize)||src2;
                        return html`<img key=${ii} src=${src2} alt=${img.alt||''} style=${{width:'100%',aspectRatio:pimgs.length===1?'16/9':'1/1',objectFit:'cover',display:'block',cursor:'pointer'}} onClick=${function(e){e.stopPropagation();window.open(full,'_blank');}}/>`;})}
                    </div>`;
                  })()}
                </div>`}
          </div>`:null}
          ${isRecordEmbed&&!post&&m.embed?html`<a href=${'https://bsky.app/profile/'+(m.embed.record&&m.embed.record.uri||'').split('/')[2]+'/post/'+(m.embed.record&&m.embed.record.uri||'').split('/').pop()}
            target="_blank" rel="noreferrer" style=${{color:'var(--accent)',fontSize:12,marginTop:4}}>View post on Bluesky →</a>`:null}
        </div>`;
      })}
    </div>
    <div style=${{borderTop:'1px solid var(--accent)',padding:'8px 12px',display:'flex',gap:8,alignItems:'center',background:'#0f0f0f'}}>
      <textarea value=${text} onInput=${function(e){setText(e.target.value);}} onKeyDown=${onKey}
        placeholder="Send a message…"
        rows="1"
        style=${{flex:1,background:'#1a1a1a',border:'1px solid #333',color:'#f1f1f1',padding:'7px 12px',
          fontSize:14,resize:'none',borderRadius:2,fontFamily:'inherit',lineHeight:1.4}}
        onFocus=${function(e){e.target.style.borderColor='var(--accent)';}}
        onBlur=${function(e){e.target.style.borderColor='#333';}}/>
      <button onClick=${sendMsg} disabled=${sending||!text.trim()}
        style=${{background:sending||!text.trim()?'#1a1a1a':'var(--accent)',
          color:sending||!text.trim()?'#444':'#000',
          border:'none',padding:'7px 16px',fontSize:13,fontWeight:700,
          cursor:sending||!text.trim()?'default':'pointer',borderRadius:2,flexShrink:0,alignSelf:'stretch'}}>
        ${sending?'…':'Send'}
      </button>
    </div>
  </div>`;
}

// ── Channel Page ──────────────────────────────────────────────────────────────
function ChannelPage(props) {
  const [tab,         setTab]         = useState('Content');
  const [contentSub,  setContentSub]  = useState('Videos');
  const [showEdit,    setShowEdit]    = useState(false);
  const [profileData, setProfileData] = useState(null);
  // When props.data changes (new channel navigated to), reset all local state
  const prevPropsDidRef = useRef(null);
  const incomingDid = props.data && props.data.did;
  const [allPosts,    setAllPosts]    = useState([]);
  const [postsLoading,setPostsLoading]= useState(false);
  const [loadedDid,   setLoadedDid]   = useState(null);
  const [likedPosts,  setLikedPosts]  = useState([]);
  const [likedVids,   setLikedVids]   = useState([]);
  const [likedLoading,setLikedLoading]= useState(false);
  const [likedLoaded, setLikedLoaded] = useState(false);

  // Reset all state when navigating to a different channel (safe in useEffect)
  useEffect(function() {
    if (!incomingDid) return;
    if (prevPropsDidRef.current && prevPropsDidRef.current !== incomingDid) {
      setProfileData(null);
      setTab('Content');
      setContentSub('Videos');
      setShowEdit(false);
      setLikedLoaded(false); setLikedPosts([]); setLikedVids([]);
      setAllPosts([]); setLoadedDid(null);
      setDmTabKey(function(k){return k+1;});
      setHasDMs(false);
    }
    prevPropsDidRef.current = incomingDid;
  }, [incomingDid]);
  const [likedTab,    setLikedTab]    = useState('Videos');
  const [dmTabKey,    setDmTabKey]    = useState(0);
  const [mutualCount, setMutualCount] = useState(null);
  const [hasDMs,      setHasDMs]      = useState(false);
  const d    = profileData || props.data;
  const sess = props.session;
  const isOwn = sess && d && sess.did === d.did;

  // Reset posts when we navigate to a different channel
  useEffect(function() {
    if (d && d.did && d.did !== loadedDid) {
      setAllPosts([]);
      setLoadedDid(null);
      if (tab === 'Content' && contentSub === 'Posts') loadPosts(d.did);
    }
  }, [d && d.did]);

  // Check if DM convo exists with this channel
  useEffect(function(){
    if(!sess||!d||!d.did||isOwn) return;
    var cancelled=false;
    function chatUrl2(path){
      var pdsHost=sess.pdsDid?sess.pdsDid.replace('did:web:',''):'bsky.social';
      return CHAT_PROXY+path+'?_pds='+encodeURIComponent(pdsHost);
    }
    api(chatUrl2('/chat.bsky.convo.listConvos'),{headers:{Authorization:'Bearer '+sess.accessJwt}})
      .then(function(r){return r.ok?r.json():null;})
      .then(function(data){
        if(!data||cancelled) return;
        var found=(data.convos||[]).some(function(c){
          return (c.members||[]).some(function(mb){return mb.did===d.did;});
        });
        if(!cancelled) setHasDMs(found);
      }).catch(function(){});
    return function(){cancelled=true;};
  },[d&&d.did, sess&&sess.did]);

  // Compute mutual follows (Friends)
  useEffect(function() {
    if (!d || !d.did) return;
    setMutualCount(null);
    var cancelled = false;
    async function loadMutuals() {
      try {
        // Get who d follows, then count how many also follow d back
        var fr = await api(PUB_PROXY+'/app.bsky.graph.getFollows?actor='+encodeURIComponent(d.did)+'&limit=100');
        if (!fr.ok || cancelled) return;
        var follows = (await fr.json()).follows || [];
        // Check if each follows d back via d.viewer.followedBy or just count
        // Simpler: use the followers list and intersect
        var followDids = new Set(follows.map(function(f){return f.did;}));
        var flr = await api(PUB_PROXY+'/app.bsky.graph.getFollowers?actor='+encodeURIComponent(d.did)+'&limit=100');
        if (!flr.ok || cancelled) return;
        var followers = (await flr.json()).followers || [];
        var mutual = followers.filter(function(f){return followDids.has(f.did);}).length;
        if (!cancelled) setMutualCount(mutual);
      } catch(e) {}
    }
    loadMutuals();
    return function(){cancelled=true;};
  }, [d && d.did]);

  async function loadPosts(did) {
    if (!did) return;
    setPostsLoading(true);
    try {
      var r = await api(PUB_PROXY+'/app.bsky.feed.getAuthorFeed?actor='+encodeURIComponent(did)+'&limit=100');
      if (r.ok) {
        var data = await r.json();
        setAllPosts(data.feed||[]);
        setLoadedDid(did);
      }
    } catch(e){ console.error(e); }
    setPostsLoading(false);
  }

  async function loadLikes(did) {
    if(!did) return;
    setLikedLoading(true);
    setLikedPosts([]); setLikedVids([]);
    try {
      // Use public API — works for any account's public likes
      var url = PUB_PROXY+'/app.bsky.feed.getActorLikes?actor='+encodeURIComponent(did)+'&limit=100';
      var r   = await api(url);
      if(!r.ok){
        console.error('getActorLikes failed', r.status, await r.text().catch(function(){return '';}));
        setLikedLoading(false); return;
      }
      var data  = await r.json();
      var items = data.feed||[];
      setLikedPosts(items);
      var vids = items.map(function(i){return i.post||i;}).filter(function(p){
        return p && (isVid(p)||isVidRaw(p));
      });
      setLikedVids(vids);
      setLikedLoaded(true);
    } catch(e){ console.error('loadLikes:', e); }
    setLikedLoading(false);
  }

  async function openTab(t) {
    setTab(t);
    if (t === 'Liked' && !likedLoaded && d && d.did) {
      await loadLikes(d.did);
    }
  }
  async function openContentSub(s) {
    setContentSub(s);
    if (s === 'Posts' && d && d.did && d.did !== loadedDid) {
      await loadPosts(d.did);
    }
  }
  if (props.loading) return html`<div style=${{display:'flex',alignItems:'center',justifyContent:'center',height:'50vh',color:'#aaa'}}>Loading channel...</div>`;
  if (!props.data) return null;
  return html`<div>
    ${showEdit&&isOwn?html`<${EditProfileModal}
      session=${sess}
      data=${d}
      onClose=${function(){setShowEdit(false);}}
      onSaved=${function(updated){
        setProfileData(Object.assign({},d,updated));
        setShowEdit(false);
      }}
    />`:null}

    <div style=${{width:'100%',background:d.banner?'none':'linear-gradient(135deg,#1a1a2e,#0f3460)',
      backgroundImage:d.banner?('url('+d.banner+')'):'none',
      backgroundSize:'cover',backgroundPosition:'center',
      minHeight:240,maxHeight:320,overflow:'hidden'}}>
      ${d.banner?html`<img src=${d.banner} alt="" style=${{width:'100%',maxHeight:320,objectFit:'cover',display:'block'}}/>`:html`<div style=${{height:240}}/>`}
    </div>
    <div style=${{padding:'0 24px',borderBottom:'1px solid var(--accent)'}}>
      <div style=${{display:'flex',alignItems:'center',gap:20,padding:'20px 0 20px'}}>
        <${Avatar} src=${d.avatar} size=${80}/>
        <div style=${{flex:1,minWidth:0}}>
          <h1 style=${{color:'#f1f1f1',fontSize:24,fontWeight:700,marginBottom:4,display:'flex',alignItems:'center',gap:10}}>
            ${d.displayName||d.handle}
            ${!isOwn&&sess&&d.viewer&&d.viewer.following&&d.viewer.followedBy?html`<span style=${{fontSize:13,fontWeight:600,color:'var(--accent)',border:'1px solid var(--accent)',padding:'2px 8px',letterSpacing:0.5}}>Friends</span>`:null}
          </h1>
          <div style=${{color:'#aaa',fontSize:14}}>@${d.handle} · ${fmt(d.followersCount||0)} followers · ${fmt(d.followsCount||0)} following · ${props.videos.length} videos</div>
          ${d.description?html`<div style=${{color:'#aaa',fontSize:13,marginTop:6,maxWidth:600,whiteSpace:'pre-wrap'}}>${d.description.slice(0,200)}${d.description.length>200?'...':''}</div>`:null}
        </div>
        ${isOwn
          ? html`<button onClick=${function(){setShowEdit(true);}}
              style=${{background:'#1a1a1a',border:'1px solid var(--accent)',color:'var(--accent)',padding:'10px 20px',
                fontWeight:600,fontSize:14,cursor:'pointer',borderRadius:0,transition:'background 0.15s'}}
              onMouseEnter=${function(e){e.currentTarget.style.background='var(--accent-dim)';}}
              onMouseLeave=${function(e){e.currentTarget.style.background='#1a1a1a';}}>
              ✏ Edit Profile
            </button>`
          : html`<${SubscribeButton} did=${d.did} viewer=${d.viewer} session=${props.session}/>`
        }
      </div>
      <div style=${{display:'flex',alignItems:'center',justifyContent:'space-between'}}>
        <div style=${{display:'flex'}}>
          ${(['Content','Liked'].concat(sess&&!isOwn&&hasDMs?['DMs']:[])).map(function(t){return html`<button key=${t} onClick=${function(){openTab(t);}}
            style=${{padding:'12px 20px',background:'none',border:'none',color:tab===t?'#f1f1f1':'#aaa',
              fontSize:14,fontWeight:tab===t?500:400,borderBottom:'3px solid '+(tab===t?'var(--accent)':'transparent'),cursor:'pointer'}}>${t}</button>`;
          })}
        </div>
        ${tab==='Content'?html`<div style=${{display:'flex',gap:4,marginRight:8}}>
          <button onClick=${function(){openContentSub('Videos');}} style=${{padding:'6px 14px',background:contentSub==='Videos'?'var(--accent)':'none',color:contentSub==='Videos'?'#000':'#aaa',border:'none',fontSize:13,fontWeight:600,cursor:'pointer',borderRadius:0}}>Videos</button>
          <button onClick=${function(){openContentSub('Posts');}}  style=${{padding:'6px 14px',background:contentSub==='Posts'?'var(--accent)':'none',color:contentSub==='Posts'?'#000':'#aaa',border:'none',fontSize:13,fontWeight:600,cursor:'pointer',borderRadius:0}}>Posts</button>
        </div>`:null}
      </div>
    </div>
    <div style=${{padding:24}}>
      ${tab==='Content'&&contentSub==='Videos'?html`<${VideoGrid} videos=${props.videos} loading=${false} onWatch=${props.onWatch} onChannel=${props.onChannel}/>`:null}
      ${tab==='Content'&&contentSub==='Posts'?html`<${ChannelPostsFeed} posts=${allPosts} loading=${postsLoading} session=${props.session} onChannel=${props.onChannel} onWatch=${props.onWatch}/>`:null}
      ${tab==='Liked'?html`<${LikedTab} videos=${likedVids} posts=${likedPosts} loading=${likedLoading} session=${props.session} onWatch=${props.onWatch} onChannel=${props.onChannel}/>`:null}
      ${tab==='DMs'&&sess&&d?html`<${ChannelDMsTab} key=${dmTabKey} session=${sess} channelDid=${d.did} onWatch=${props.onWatch} onChannel=${props.onChannel}/>`:null}

    </div>
  </div>`;
}


// ── App ───────────────────────────────────────────────────────────────────────
function App() {
  // Apply saved settings on mount
  useEffect(function(){
    applyAccent(loadAccent());
  },[]);
  const [session,       setSession]       = useState(function(){return loadSession();});
  const [page,          setPage]          = useState(function(){return loadLastPage();});
  const [prevPage,      setPrevPage]      = useState(null);
  const navHistoryRef = useRef(['search']); // stack of pages visited
  const navFutureRef  = useRef([]);         // forward stack
  const [sidebarOpen,   setSidebarOpen]   = useState(true);
  const [showLogin,     setShowLogin]     = useState(false);
  const [showUpload,    setShowUpload]    = useState(false);
  const [searchInput,   setSearchInput]   = useState('');
  const [feeds,         setFeeds]         = useState(null);
  const [activeFeed,    setActiveFeed]    = useState(null);
  const [feedVideos,    setFeedVideos]    = useState([]);
  const [feedLoading,   setFeedLoading]   = useState(false);
  const [currentVideo,  setCurrentVideo]  = useState(null);
  const [related,       setRelated]       = useState([]);
  const [thread,        setThread]        = useState(null);
  const [channelData,   setChannelData]   = useState(null);
  const [channelVideos, setChannelVideos] = useState([]);
  const [channelLoading,setChannelLoading]= useState(false);
  const [channelCursor, setChannelCursor] = useState(null); // {actor, cursor, seen}
  const [channelFromPage,setChannelFromPage]=useState(null);
  const pageRef = useRef('search'); // always holds latest page value without causing dep issues
  const [searchQuery,   setSearchQuery]   = useState('');
  const [searchResults, setSearchResults] = useState(null);
  const [searchLoading, setSearchLoading] = useState(false);
  const [subsVideos,    setSubsVideos]    = useState([]);
  const [subsLoading,   setSubsLoading]   = useState(false);
  const [followStrip,   setFollowStrip]   = useState([]);
  const [friendsKey,    setFriendsKey]    = useState(0); // increment to force refresh

  // Save page to localStorage whenever it changes (except channel/watch)
  function navTo(p) {
    if(p!=='channel'&&p!=='watch') pageRef.current = p;
    // Push current page to history, clear forward stack
    navHistoryRef.current.push(p);
    if(navHistoryRef.current.length > 50) navHistoryRef.current.shift();
    navFutureRef.current = [];
    setPage(p);
    if(p!=='channel'&&p!=='watch') saveLastPage(p);
    window.scrollTo(0,0);
  }
  function navBack() {
    const hist = navHistoryRef.current;
    if(hist.length < 2) return;
    const current = hist.pop();
    navFutureRef.current.push(current);
    const prev = hist[hist.length-1];
    setPage(prev);
    if(prev!=='channel'&&prev!=='watch') saveLastPage(prev);
    window.scrollTo(0,0);
  }
  function navForward() {
    const fut = navFutureRef.current;
    if(!fut.length) return;
    const next = fut.pop();
    navHistoryRef.current.push(next);
    setPage(next);
    if(next!=='channel'&&next!=='watch') saveLastPage(next);
    window.scrollTo(0,0);
  }

  // ── Load saved feeds ────────────────────────────────────────────────────────
  const loadSavedFeeds = useCallback(async function(sess){
    if(!sess){ setFeeds(DEFAULT_FEEDS); return; }
    try{
      const r=await api(AUTH_PROXY+'/app.bsky.actor.getPreferences',{headers:{Authorization:'Bearer '+sess.accessJwt}});
      if(!r.ok){ setFeeds(DEFAULT_FEEDS); return; }
      const d=await r.json();
      const prefs=d.preferences||[];
      let saved=[];
      const v2=prefs.find(function(p){return p['$type']==='app.bsky.actor.defs#savedFeedsPrefV2';});
      const v1=prefs.find(function(p){return p['$type']==='app.bsky.actor.defs#savedFeedsPref';});
      if(v2&&v2.items){
        saved=v2.items.filter(function(i){return i.type==='feed';})
          .map(function(i){return {uri:i.value,displayName:i.value.split('/').pop()};});
      } else if(v1&&v1.saved){
        saved=v1.saved.map(function(u){return {uri:u,displayName:u.split('/').pop()};});
      }
      if(saved.length>0){
        try{
          const qs=saved.map(function(f){return 'feeds='+encodeURIComponent(f.uri);}).join('&');
          const gR=await api(PUB_PROXY+'/app.bsky.feed.getFeedGenerators?'+qs);
          if(gR.ok){
            const gd=await gR.json();
            const byUri={};
            (gd.feeds||[]).forEach(function(g){byUri[g.uri]=g;});
            saved=saved.map(function(f){
              const g=byUri[f.uri];
              return {uri:f.uri,displayName:g?g.displayName:f.displayName,avatar:g?g.avatar:null};
            });
          }
        }catch(e){}
      }
      setFeeds(saved.length>0?saved:DEFAULT_FEEDS);
    }catch(e){ setFeeds(DEFAULT_FEEDS); }
  },[]);

  // ── Load a specific feed's videos ───────────────────────────────────────────
  const loadFeedVideos = useCallback(async function(feedUri, sess){
    setFeedLoading(true); setFeedVideos([]);
    const seen=new Set(); const videos=[];
    function add(posts){(posts||[]).forEach(function(p){if(p&&isVid(p)&&!seen.has(p.uri)){videos.push(p);seen.add(p.uri);}});}
    try{
      const authOpts = sess?{headers:{Authorization:'Bearer '+sess.accessJwt}}:{};
      const endpoint = sess?AUTH_PROXY:PUB_PROXY;
      let r=null, authFailed=false;
      try { r = await api(endpoint+'/app.bsky.feed.getFeed?feed='+encodeURIComponent(feedUri)+'&limit=100',authOpts); } catch(e){ r=null; }
      if(r&&r.ok){ const d=await r.json(); add((d.feed||[]).map(function(i){return i.post;})); }
      else if(r&&!r.ok) authFailed = r.status!==404;
      // Only fall back to public if the auth request didn't give a hard error
      if(videos.length<10 && !authFailed){
        let r2=null;
        try { r2=await api(PUB_PROXY+'/app.bsky.feed.getFeed?feed='+encodeURIComponent(feedUri)+'&limit=100'); } catch(e){}
        if(r2&&r2.ok){ const d=await r2.json(); add((d.feed||[]).map(function(i){return i.post;})); }
      }
    }catch(e){ console.error('loadFeedVideos:',e); }
    setFeedVideos(videos); setFeedLoading(false);
  },[]);

  const handleFeedSelect = useCallback(function(feed){
    setActiveFeed(feed.uri);
    saveLastPage('feed');
    saveLastFeed(feed.uri);
    setPage('feed');
    window.scrollTo(0,0);
    loadFeedVideos(feed.uri, session);
  },[session, loadFeedVideos]);

  // ── Subscriptions ───────────────────────────────────────────────────────────
  const handleSubs = useCallback(async function(){
    setSubsLoading(true); navTo('subs');
    const seen=new Set(); const videos=[];
    const strip=[];
    function add(posts){(posts||[]).forEach(function(p){if(p&&isVid(p)&&!seen.has(p.uri)){videos.push(p);seen.add(p.uri);}});}
    try{
      if(!session) return;
      const fR=await api(AUTH_PROXY+'/app.bsky.graph.getFollows?actor='+encodeURIComponent(session.did)+'&limit=100',
        {headers:{Authorization:'Bearer '+session.accessJwt}});
      if(fR.ok){
        const fd=await fR.json();
        const follows=(fd.follows||[]).slice(0,40);
        // Fetch each follow's latest posts in batches
        const followData=[];
        for(let i=0;i<follows.length;i+=5){
          const batch=follows.slice(i,i+5);
          const results=await Promise.all(batch.map(function(actor){
            return api(PUB_PROXY+'/app.bsky.feed.getAuthorFeed?actor='+encodeURIComponent(actor.did)+'&limit=20&filter=posts_with_media')
              .then(function(r){return r.ok?r.json():{feed:[]};})
              .then(function(d){
                const posts=(d.feed||[]).map(function(i){return i.post;});
                add(posts);
                const latestVid=posts.find(isVid);
                return {actor:actor, latestVideoAt: latestVid?new Date(latestVid.indexedAt).getTime():0};
              })
              .catch(function(){return {actor:actor,latestVideoAt:0};});
          }));
          followData.push.apply(followData,results);
        }
        // Sort follow strip by most recent video
        followData.sort(function(a,b){return b.latestVideoAt-a.latestVideoAt;});
        followData.forEach(function(fd){
          strip.push(Object.assign({},fd.actor,{hasNewVideo:fd.latestVideoAt>0}));
        });
      }
    }catch(e){ console.error('subs:',e); }
    videos.sort(function(a,b){return new Date(b.indexedAt)-new Date(a.indexedAt);});
    setFollowStrip(strip);
    setSubsVideos(videos);
    setSubsLoading(false);
  },[session]);

  // ── Watch ───────────────────────────────────────────────────────────────────
  const handleWatch = useCallback(async function(post){
    const watchUri = post.uri;
    addToHistory(post);
    setCurrentVideo(post); setThread(null); setRelated([]);
    setPage('watch'); window.scrollTo(0,0);
    try{
      const [tR, fR] = await Promise.all([
        api(PUB_PROXY+'/app.bsky.feed.getPostThread?uri='+encodeURIComponent(post.uri)+'&depth=6'),
        api(PUB_PROXY+'/app.bsky.feed.getAuthorFeed?actor='+encodeURIComponent(post.author.did)+'&limit=50&filter=posts_with_media')
      ]);
      if(tR.ok){const d=await tR.json();setThread(d.thread);}
      // Only set related if we're still watching the same video (no race condition)
      if(fR.ok){
        const d=await fR.json();
        const vids=(d.feed||[]).map(function(i){return i.post;}).filter(function(p){return isVid(p)&&p.uri!==watchUri;}).slice(0,15);
        setRelated(vids);
      }
    }catch(e){ console.error(e); }
  },[]);

  // ── Channel ─────────────────────────────────────────────────────────────────
  const handleChannel = useCallback(async function(actor){
    if(!actor) return;
    setChannelFromPage(pageRef.current);
    saveLastChan(actor); saveLastPage('channel');
    setChannelData(null); setChannelVideos([]); setChannelLoading(true);
    setPage('channel'); window.scrollTo(0,0);
    try{
      // Use auth endpoint so viewer.following is populated when logged in
      const sess = loadSession();
      const profileUrl = (sess
        ? AUTH_PROXY+'/app.bsky.actor.getProfile?actor='+encodeURIComponent(actor)
        : PUB_PROXY+'/app.bsky.actor.getProfile?actor='+encodeURIComponent(actor));
      const profileOpts = sess ? {headers:{Authorization:'Bearer '+sess.accessJwt}} : {};
      const pR = await api(profileUrl, profileOpts);
      if(pR.ok){ const d=await pR.json(); setChannelData(d); }

      // Paginate all videos with a hard cap to prevent infinite loops
      const seen = new Set();
      const vids = [];
      let cursor = null;
      let pageCount = 0;
      const MAX_PAGES = 20;
      do {
        const url = PUB_PROXY+'/app.bsky.feed.getAuthorFeed?actor='+encodeURIComponent(actor)
          +'&limit=100&filter=posts_with_media'+(cursor?'&cursor='+encodeURIComponent(cursor):'');
        let fR;
        try { fR = await api(url); } catch(e){ break; }
        if(!fR.ok) break;
        let fd;
        try { fd = await fR.json(); } catch(e){ break; }
        (fd.feed||[]).forEach(function(item){
          const p = item.post;
          if(p && isVid(p) && !seen.has(p.uri)){ vids.push(p); seen.add(p.uri); }
        });
        cursor = fd.cursor || null;
        pageCount++;
        setChannelVideos(vids.slice());
      } while(cursor && pageCount < MAX_PAGES);
      setChannelCursor(null);

    }catch(e){ console.error('handleChannel error:',e); }
    setChannelLoading(false);
  },[]); // no [page] dep — pageRef keeps it fresh without recreating the callback

  // ── Search ──────────────────────────────────────────────────────────────────
  const handleSearch = useCallback(async function(q){
    const raw=(q||'').trim(); if(!raw) return;
    const stripped=raw.startsWith('@')?raw.slice(1):raw;
    setSearchQuery(raw); setSearchInput(raw);
    setSearchResults(null); setSearchLoading(true);
    navTo('search');
    try{
      // Extract required hashtags (words starting with #)
      const requiredTags=(raw.match(/#[\w]+/g)||[]).map(function(t){return t.toLowerCase();});
      // Build search query: use the raw query (Bluesky indexes hashtags natively)
      const searchQ=raw;

      const aR=await api(PUB_PROXY+'/app.bsky.actor.searchActors?q='+encodeURIComponent(stripped)+'&limit=20');
      // Fetch multiple pages to get more results, always sort=latest
      const postFetches=[
        api(PUB_PROXY+'/app.bsky.feed.searchPosts?q='+encodeURIComponent(searchQ)+'&limit=100&sort=latest'),
      ];
      // If searching hashtags, also search by just the tag for better coverage
      requiredTags.forEach(function(tag){
        postFetches.push(api(PUB_PROXY+'/app.bsky.feed.searchPosts?q='+encodeURIComponent(tag)+'&limit=100&sort=latest'));
      });

      let actors=aR.ok?((await aR.json()).actors||[]):[];
      const looksLikeHandle=!stripped.includes(' ')&&!raw.startsWith('#');
      if(looksLikeHandle){
        const dR=await api(PUB_PROXY+'/app.bsky.actor.getProfile?actor='+encodeURIComponent(stripped)).catch(function(){return null;});
        if(dR&&dR.ok){const p=await dR.json();if(p&&p.handle)actors=[p].concat(actors.filter(function(a){return a.did!==p.did;}));}
      }

      // Merge all post results, dedupe, sort by date newest first
      const seen=new Set(); const allPosts=[];
      const responses=await Promise.all(postFetches);
      responses.forEach(function(r2){
        // r2 is already a Response — we awaited Promise.all on the fetch promises
      });
      // Actually collect posts properly
      const allRaw=[];
      for(let i=0;i<responses.length;i++){
        if(responses[i]&&responses[i].ok){
          const d=await responses[i].json();
          (d.posts||[]).forEach(function(p){ if(!seen.has(p.uri)){seen.add(p.uri);allRaw.push(p);} });
        }
      }
      // Sort newest first
      allRaw.sort(function(a,b){ return new Date(b.indexedAt||0)-new Date(a.indexedAt||0); });

      // Filter: if required hashtags specified, post text/facets MUST contain them all
      function postHasTag(p, tag){
        const text=((p.record&&p.record.text)||'').toLowerCase();
        // Check text
        if(text.includes(tag)) return true;
        // Check facets for tags
        const facets=(p.record&&p.record.facets)||[];
        for(var f=0;f<facets.length;f++){
          const features=facets[f].features||[];
          for(var ff=0;ff<features.length;ff++){
            const feat=features[ff];
            if(feat['$type']==='app.bsky.richtext.facet#tag'&&('#'+feat.tag.toLowerCase())===tag) return true;
          }
        }
        return false;
      }

      function isVideoPost(p){
        if(!p) return false;
        // Check hydrated top-level embed first
        if(p.embed){
          const t=p.embed['$type']||'';
          if(t==='app.bsky.embed.video#view'||t==='app.bsky.embed.video') return true;
          if(t==='app.bsky.embed.recordWithMedia#view'||t==='app.bsky.embed.recordWithMedia'){
            const m=p.embed.media;
            if(m&&(m['$type']==='app.bsky.embed.video#view'||m['$type']==='app.bsky.embed.video')) return true;
          }
        }
        // ALWAYS check record.embed — searchPosts stores video info here even when p.embed is null
        const re2=(p.record&&p.record.embed)||{};
        const rt=re2['$type']||'';
        return rt==='app.bsky.embed.video'||rt==='app.bsky.embed.video#view';
      }

      let filtered=allRaw;
      if(requiredTags.length>0){
        filtered=allRaw.filter(function(p){
          return requiredTags.every(function(tag){return postHasTag(p,tag);});
        });
      }

      const videos=filtered.filter(isVideoPost);

      setSearchResults({videos:videos,actors:actors,totalPosts:filtered.length,allPosts:filtered,error:null});
    }catch(e){
      setSearchResults({videos:[],actors:[],totalPosts:0,allPosts:[],error:e.message||String(e)});
    }
    setSearchLoading(false);
  },[]);

  // ── Login ───────────────────────────────────────────────────────────────────
  const handleLoginSuccess = useCallback(async function(data){
    try{
      const pr=await api(AUTH_PROXY+'/app.bsky.actor.getProfile?actor='+encodeURIComponent(data.handle),
        {headers:{Authorization:'Bearer '+data.accessJwt}});
      if(pr.ok){const pd=await pr.json(); data.avatar=pd.avatar; data.displayName=pd.displayName;}
    }catch(e){}
    try{
      if(data.didDoc&&data.didDoc.service){
        const pds=data.didDoc.service.find(function(s){return s.id==='#atproto_pds'||(s.type&&s.type.indexOf('AtprotoPersonalDataServer')!==-1);});
        if(pds&&pds.serviceEndpoint){
          data.pdsDid='did:web:'+pds.serviceEndpoint.replace(/^https?:\/\//,'').replace(/\/$/,'');
        }
      }
    }catch(e){}
    saveSession(data);
    setSession(data);
    setShowLogin(false);
    loadSavedFeeds(data);
  },[loadSavedFeeds]);

  // ── Init ────────────────────────────────────────────────────────────────────
  useEffect(function(){
    loadSavedFeeds(session);
    // Validate saved session
    const saved=loadSession();
    if(saved){
      api(AUTH_PROXY+'/app.bsky.actor.getProfile?actor='+encodeURIComponent(saved.did),
        {headers:{Authorization:'Bearer '+saved.accessJwt}})
        .then(function(r){ if(!r.ok){ clearSession(); setSession(null); } })
        .catch(function(){});
    }
    // Restore last location
    const lastPage = loadLastPage();
    if(lastPage==='channel'){
      const lastChan = loadLastChan();
      if(lastChan) handleChannel(lastChan);
    } else if(lastPage==='feed'){
      const lastFeed = loadLastFeed();
      if(lastFeed){
        setActiveFeed(lastFeed);
        loadFeedVideos(lastFeed, session);
      }
    } else if(lastPage==='subs' && saved){
      handleSubs();
    }
  },[]);

  const mL=sidebarOpen?240:72;
  const currentFeedName = activeFeed&&feeds?(feeds.find(function(f){return f.uri===activeFeed;})||{}).displayName:'Feed';

  return html`<div style=${{minHeight:'100vh',background:'#0f0f0f',color:'#f1f1f1'}}>
    <${Header}
      onHome=${function(){navTo('search');}}
      onSearch=${handleSearch}
      onBack=${navBack}
      onForward=${navForward}
      session=${session}
      onLogin=${function(){setShowLogin(true);}}
      onLogout=${function(){clearSession();setSession(null);setFeeds(DEFAULT_FEEDS);}}
      onMyChannel=${function(){if(session&&session.handle) handleChannel(session.handle);}}
      onUpload=${function(){setShowUpload(true);}}
      input=${searchInput}
      setInput=${setSearchInput}
      toggleSidebar=${function(){setSidebarOpen(function(o){return!o;});}}/>

    <${Sidebar}
      open=${sidebarOpen}
      page=${page}
      activeFeed=${activeFeed}
      feeds=${feeds||[]}
      hasSession=${!!session}
      onSearch=${function(){navTo('search');}}
      onSubs=${function(){handleSubs();}}
      onFriends=${function(){navTo('friends');}}
      onHistory=${function(){navTo('history');}}
      onSettings=${function(){navTo('settings');}}
      onFeedSelect=${handleFeedSelect}
      onLogin=${function(){setShowLogin(true);}}/>

    <main style=${{marginLeft:mL,marginTop:56,minHeight:'calc(100vh - 56px)',transition:'margin-left 0.15s ease'}}>
      ${page==='history'?html`<${HistoryPage} onWatch=${handleWatch} onChannel=${handleChannel}/>`:null}
      ${page==='settings'?html`<${SettingsPage} session=${session} onLogout=${function(){clearSession();setSession(null);setFeeds(DEFAULT_FEEDS);navTo('search');}} onMyChannel=${function(){if(session&&session.handle) handleChannel(session.handle);}}/>`:null}
      ${page==='friends'?html`<${FriendsFeed} session=${session} key=${friendsKey} onWatch=${handleWatch} onChannel=${handleChannel}/>`:null}
      ${page==='subs'?html`<${SubsPage} videos=${subsVideos} loading=${subsLoading} followStrip=${followStrip} session=${session} onWatch=${handleWatch} onChannel=${handleChannel}/>`:null}
      ${page==='search'?html`<${SearchPage} results=${searchResults} loading=${searchLoading} query=${searchQuery} session=${session} onWatch=${handleWatch} onChannel=${handleChannel}/>`:null}
      ${page==='feed'?html`<${FeedPage} videos=${feedVideos} loading=${feedLoading} feedName=${currentFeedName} feedUri=${activeFeed} session=${session} onWatch=${handleWatch} onChannel=${handleChannel}/>`:null}
      ${page==='watch'&&currentVideo?html`<${WatchPage} post=${currentVideo} related=${related} thread=${thread} session=${session} onLogin=${function(){setShowLogin(true);}} onWatch=${handleWatch} onChannel=${handleChannel}/>`:null}
      ${page==='channel'?html`<${ChannelPage} data=${channelData} videos=${channelVideos} loading=${channelLoading} session=${session} onBack=${function(){navTo(channelFromPage||'search');}} onWatch=${handleWatch} onChannel=${handleChannel}/>`:null}
    </main>

    ${showLogin?html`<${LoginModal} onClose=${function(){setShowLogin(false);}} onSuccess=${handleLoginSuccess}/>`:null}
    ${showUpload&&session?html`<${UploadModal} session=${session} onClose=${function(){setShowUpload(false);}} onDone=${function(){setShowUpload(false);navTo('search');}}/> `:null}
  </div>`;
}


render(html`<${App}/>`, document.getElementById('app'));
</script>
</body>
</html>
"""

# Pre-encode HTML once
_HTML_BYTES = HTML.encode("utf-8")


# ── Request handler ────────────────────────────────────────────────────────────
class Handler(http.server.BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        # Only log errors
        if args and len(args) >= 2 and not str(args[1]).startswith(("2","3")):
            print(f"  {self.address_string()} {fmt % args}")

    def send_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, Accept")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_cors()
        self.end_headers()

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._serve_html()
        elif self.path.startswith("/proxy/pub/"):
            self._proxy("GET", "https://public.api.bsky.app/" + self.path[len("/proxy/pub/"):], cacheable=True)
        elif self.path.startswith("/proxy/auth/"):
            self._proxy("GET", "https://bsky.social/" + self.path[len("/proxy/auth/"):])
        elif self.path.startswith("/proxy/chat/"):
            # Extract _pds param to route to user's actual PDS
            import urllib.parse as _up
            _parts = _up.urlsplit(self.path)
            _qs = _up.parse_qs(_parts.query)
            _pds_host = (_qs.get('_pds',['bsky.social'])[0]).strip('/')
            # Rebuild path without _pds param
            _new_qs = '&'.join(f'{k}={val}' for k,vals in _qs.items() if k != '_pds' for val in vals)
            _clean = _parts.path + ('?' + _new_qs if _new_qs else '')
            _target = f"https://{_pds_host}/" + _clean[len("/proxy/chat/"):]
            self._proxy("GET", _target, extra_headers={"atproto-proxy": "did:web:api.bsky.chat#bsky_chat"})
        elif self.path.startswith("/proxy/video/"):
            self._proxy("GET", "https://video.bsky.app/" + self.path[len("/proxy/video/"):])
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path.startswith("/proxy/auth/"):
            length = int(self.headers.get("Content-Length", 0))
            body   = self.rfile.read(length) if length else b""
            self._proxy("POST", "https://bsky.social/" + self.path[len("/proxy/auth/"):], body=body)
        elif self.path.startswith("/proxy/chat/"):
            import urllib.parse as _up
            _parts = _up.urlsplit(self.path)
            _qs = _up.parse_qs(_parts.query)
            _pds_host = (_qs.get('_pds',['bsky.social'])[0]).strip('/')
            _new_qs = '&'.join(f'{k}={val}' for k,vals in _qs.items() if k != '_pds' for val in vals)
            _clean = _parts.path + ('?' + _new_qs if _new_qs else '')
            _target = f"https://{_pds_host}/" + _clean[len("/proxy/chat/"):]
            length = int(self.headers.get("Content-Length", 0))
            body   = self.rfile.read(length) if length else b""
            self._proxy("POST", _target, body=body, extra_headers={"atproto-proxy": "did:web:api.bsky.chat#bsky_chat"})
        elif self.path.startswith("/proxy/video/"):
            length = int(self.headers.get("Content-Length", 0))
            body   = self.rfile.read(length) if length else b""
            self._proxy("POST", "https://video.bsky.app/" + self.path[len("/proxy/video/"):], body=body, timeout=300)
        elif self.path == "/process-video":
            self._process_video()
        else:
            self.send_response(404)
            self.end_headers()

    def _serve_html(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(_HTML_BYTES)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(_HTML_BYTES)

    def _proxy(self, method, url, body=None, timeout=30, cacheable=False, extra_headers=None):
        if cacheable and method == "GET":
            cached_data, cached_ct = _cache_get(url)
            if cached_data is not None:
                self.send_response(200)
                self.send_header("Content-Type", cached_ct)
                self.send_header("Content-Length", str(len(cached_data)))
                self.send_cors()
                self.end_headers()
                self.wfile.write(cached_data)
                return

        fwd = {}
        for h in ("Authorization", "Content-Type", "Accept"):
            v = self.headers.get(h)
            if v:
                fwd[h] = v
        if "Accept" not in fwd:
            fwd["Accept"] = "application/json"
        if extra_headers:
            fwd.update(extra_headers)

        try:
            req = urllib.request.Request(url, data=body, headers=fwd, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
                ct   = resp.headers.get("Content-Type", "application/json")
                if cacheable and method == "GET":
                    _cache_set(url, data, ct)
                self.send_response(resp.status)
                self.send_header("Content-Type", ct)
                self.send_header("Content-Length", str(len(data)))
                self.send_cors()
                self.end_headers()
                self.wfile.write(data)
        except urllib.error.HTTPError as e:
            data = e.read()
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_cors()
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            msg = json.dumps({"error": str(e)}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(msg)))
            self.send_cors()
            self.end_headers()
            self.wfile.write(msg)

    def _process_video(self):
        tmpdir = tempfile.mkdtemp(prefix="idkijab_")
        try:
            ctype  = self.headers.get("Content-Type", "")
            length = int(self.headers.get("Content-Length", 0))
            body   = self.rfile.read(length)

            msg_text = ("Content-Type: " + ctype + "\r\n\r\n").encode() + body
            msg = email.message_from_bytes(msg_text, policy=email.policy.compat32)

            fields = {}
            if msg.is_multipart():
                for part in msg.get_payload():
                    disp = part.get("Content-Disposition", "")
                    m = re.search(r'name="([^"]+)"', disp)
                    if m:
                        fields[m.group(1)] = part.get_payload(decode=True)

            if "video" not in fields:
                self._json_error(400, "Missing video field"); return
            if "thumbnail" not in fields:
                self._json_error(400, "Missing thumbnail field"); return

            video_path = os.path.join(tmpdir, "input.mp4")
            thumb_path = os.path.join(tmpdir, "thumb.jpg")
            out_path   = os.path.join(tmpdir, "output.mp4")

            with open(video_path, "wb") as f: f.write(fields["video"])
            with open(thumb_path, "wb") as f: f.write(fields["thumbnail"])

            if not shutil.which("ffmpeg"):
                self._json_error(500, "FFmpeg not installed. Run: winget install ffmpeg"); return

            w, h = 1280, 720
            try:
                probe = subprocess.run(
                    ["ffprobe","-v","error","-select_streams","v:0",
                     "-show_entries","stream=width,height","-of","csv=p=0", video_path],
                    capture_output=True, text=True, timeout=30)
                if probe.returncode == 0 and probe.stdout.strip():
                    parts = probe.stdout.strip().split(",")
                    w, h = int(parts[0]), int(parts[1])
            except Exception:
                pass

            w = w if w % 2 == 0 else w - 1
            h = h if h % 2 == 0 else h - 1

            norm_thumb = os.path.join(tmpdir, "thumb_norm.jpg")
            subprocess.run(
                ["ffmpeg","-y","-i",thumb_path,"-vf",f"scale={w}:{h},setsar=1","-frames:v","1",norm_thumb],
                capture_output=True, timeout=60)
            if not os.path.exists(norm_thumb):
                norm_thumb = thumb_path

            cmd = [
                "ffmpeg", "-y",
                "-loop","1","-framerate","30","-t","0.0334","-i", norm_thumb,
                "-i", video_path,
                "-filter_complex",
                (f"[0:v]scale={w}:{h}:force_original_aspect_ratio=disable,"
                 f"setsar=1,fps=30,format=yuv420p[th];"
                 f"[1:v]scale={w}:{h}:force_original_aspect_ratio=disable,"
                 f"setsar=1,format=yuv420p[vid];"
                 f"[th][vid]concat=n=2:v=1:a=0[v]"),
                "-map","[v]","-map","1:a?",
                "-c:v","libx264","-preset","fast","-crf","23",
                "-c:a","aac","-b:a","128k",
                "-movflags","+faststart",
                out_path
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=300)
            if result.returncode != 0:
                err = result.stderr.decode("utf-8", errors="replace")[-600:]
                self._json_error(500, "FFmpeg failed: " + err); return

            with open(out_path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(data)))
            self.send_cors()
            self.end_headers()
            self.wfile.write(data)

        except subprocess.TimeoutExpired:
            self._json_error(500, "FFmpeg timed out")
        except Exception as e:
            self._json_error(500, str(e))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def _json_error(self, code, message):
        msg = json.dumps({"error": message}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(msg)))
        self.send_cors()
        self.end_headers()
        self.wfile.write(msg)


# ── Threaded server (each request gets its own thread) ─────────────────────────
class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads    = True
    allow_reuse_address = True


# ── Start ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    server = ThreadedHTTPServer(("localhost", PORT), Handler)
    print(f"""
╔══════════════════════════════════════════╗
║      RaccTube Server      ║
╠══════════════════════════════════════════╣
║  Open in Firefox:                        ║
║  http://localhost:{PORT}                    ║
║  Press Ctrl+C to stop                    ║
╚══════════════════════════════════════════╝
""")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
        server.server_close()
