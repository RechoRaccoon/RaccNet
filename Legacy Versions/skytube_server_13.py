"""
Racc.net local proxy server
Run:  python raccnet_server.py
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
import gzip
import hashlib
import threading
import socketserver
import time as _time
from functools import lru_cache

PORT = 8080

# ── Server-side response cache (pub API only — auth responses never cached) ───
_cache = {}          # url -> (timestamp, data, content_type)
_cache_lock = threading.Lock()
CACHE_TTL = 30       # seconds — short enough to feel live, long enough to cut repeat hits

def _cache_get(url):
    with _cache_lock:
        entry = _cache.get(url)
        if entry and (_time.time() - entry[0]) < CACHE_TTL:
            return entry[1], entry[2]
    return None, None

def _cache_set(url, data, ct):
    with _cache_lock:
        _cache[url] = (_time.time(), data, ct)
        # Evict oldest entries if cache grows large
        if len(_cache) > 500:
            oldest = sorted(_cache.items(), key=lambda x: x[1][0])[:100]
            for k, _ in oldest:
                del _cache[k]

# Pre-compress and cache the HTML on startup so each request is instant
_HTML_BYTES      = None   # raw utf-8
_HTML_GZ_BYTES   = None   # gzip compressed
_HTML_ETAG       = None   # hex digest for If-None-Match

# ── Embedded HTML (the full Racc.net app, pointing at /proxy/ instead of bsky.app) ──
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Racc.net</title>
<script src="https://cdn.jsdelivr.net/npm/htm@3.1.1/preact/standalone.umd.js"></script>
<link href="https://fonts.googleapis.com/css2?family=Roboto:wght@300;400;500;700&display=swap" rel="stylesheet"/>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%}
body{background:#0f0f0f;color:#f1f1f1;font-family:'Roboto',sans-serif;overflow-x:hidden}
::-webkit-scrollbar{width:8px;height:8px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:#3f3f3f;border-radius:4px}
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
'use strict';
const { h, render, useState, useEffect, useRef, useCallback } = htmPreact;
const html = htmPreact.html;

// All API calls go through our local proxy — no CORS, no adblockers
const PUB_PROXY   = '/proxy/pub/xrpc';
const AUTH_PROXY  = '/proxy/auth/xrpc';
const VIDEO_PROXY = '/proxy/video/xrpc';

// ── Persistent session (localStorage) ────────────────────────────────────────
const SESSION_KEY = 'raccnet_session';
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

const isVid = function(p) { return p && p.embed && p.embed['$type'] === 'app.bsky.embed.video#view'; };

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
    function setup() {
      if (!active) return;
      if (hlsRef.current) { hlsRef.current.destroy(); hlsRef.current = null; }
      if (window.Hls && window.Hls.isSupported()) {
        const hls = new window.Hls({enableWorker:false, lowLatencyMode:false});
        hlsRef.current = hls;
        hls.loadSource(playlist);
        hls.attachMedia(ref.current);
        hls.on(window.Hls.Events.MANIFEST_PARSED, function() {
          if (ref.current) ref.current.play().catch(function(){});
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

  return html`<video ref=${ref} controls poster=${thumbnail}
    style=${{width:'100%',background:'#000',display:'block',maxHeight:'75vh',minHeight:'300px'}}/>`;
}

function Avatar(props) {
  const size = props.size || 36;
  const [err, setErr] = useState(false);
  const st = {width:size,height:size,borderRadius:'50%',flexShrink:0,overflow:'hidden',background:'#3f3f3f',
    cursor:props.onClick?'pointer':'default',display:'flex',alignItems:'center',justifyContent:'center',
    fontSize:size*0.4,color:'#aaa',fontWeight:600};
  return html`<div style=${st} onClick=${props.onClick}>
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
    <div class="shimmer" style=${{width:'100%',paddingBottom:'56.25%',borderRadius:12,background:'#272727'}}/>
    <div style=${{display:'flex',gap:12,paddingTop:12}}>
      <div class="shimmer" style=${{width:36,height:36,borderRadius:'50%',background:'#272727',flexShrink:0}}/>
      <div style=${{flex:1}}>
        <div class="shimmer" style=${{height:14,background:'#272727',borderRadius:4,marginBottom:8,width:'90%'}}/>
        <div class="shimmer" style=${{height:12,background:'#272727',borderRadius:4,width:'60%'}}/>
      </div>
    </div>
  </div>`;
}

function VideoCard(props) {
  const post = props.post;
  if (!isVid(post)) return null;
  const embed = post.embed, author = post.author, rec = post.record;
  const title = (rec && rec.text) || 'Untitled video';
  return html`<div style=${{cursor:'pointer'}} onClick=${function(){props.onWatch(post);}}>
    <div style=${{width:'100%',paddingBottom:'56.25%',borderRadius:12,overflow:'hidden',background:'#1a1a1a',position:'relative'}}>
      <div style=${{position:'absolute',top:0,left:0,right:0,bottom:0}}><${Thumb} src=${embed.thumbnail}/></div>
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
  if (!isVid(post)) return null;
  const embed = post.embed, author = post.author, rec = post.record;
  const title = (rec && rec.text) || 'Untitled video';
  return html`<div onClick=${function(){props.onWatch(post);}}
    style=${{display:'flex',gap:8,cursor:'pointer',padding:'8px 0'}}
    onMouseEnter=${function(e){e.currentTarget.style.opacity='0.8';}}
    onMouseLeave=${function(e){e.currentTarget.style.opacity='1';}}>
    <div style=${{width:168,flexShrink:0,borderRadius:8,overflow:'hidden',background:'#272727',aspectRatio:'16/9',position:'relative'}}>
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
    display:'flex',alignItems:'center',padding:'0 16px',gap:16,zIndex:200,borderBottom:'1px solid #272727'}}>
    <div style=${{display:'flex',alignItems:'center',gap:12,flexShrink:0}}>
      <button onClick=${props.toggleSidebar}
        style=${{background:'none',border:'none',color:'#f1f1f1',padding:8,borderRadius:'50%',display:'flex',alignItems:'center',justifyContent:'center'}}
        onMouseEnter=${function(e){e.currentTarget.style.background='#272727';}}
        onMouseLeave=${function(e){e.currentTarget.style.background='none';}}>
        <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path d="M3 18h18v-2H3v2zm0-5h18v-2H3v2zm0-7v2h18V6H3z"/></svg>
      </button>
      <div <div onClick=${props.onHome} style=${{display:'flex',alignItems:'center',gap:8,cursor:'pointer',userSelect:'none'}}>
          <svg width="36" height="36" viewBox="0 0 64 64" fill="none">
            <ellipse cx="14" cy="14" rx="9" ry="11" fill="#4ade80"/>
            <ellipse cx="50" cy="14" rx="9" ry="11" fill="#4ade80"/>
            <ellipse cx="14" cy="14" rx="5" ry="7" fill="#86efac"/>
            <ellipse cx="50" cy="14" rx="5" ry="7" fill="#86efac"/>
            <ellipse cx="32" cy="34" rx="24" ry="22" fill="#4ade80"/>
            <ellipse cx="22" cy="33" rx="9" ry="8" fill="#166534"/>
            <ellipse cx="42" cy="33" rx="9" ry="8" fill="#166534"/>
            <ellipse cx="22" cy="33" rx="5" ry="5" fill="#f1f1f1"/>
            <ellipse cx="42" cy="33" rx="5" ry="5" fill="#f1f1f1"/>
            <ellipse cx="23" cy="33" rx="3" ry="3" fill="#1a1a1a"/>
            <ellipse cx="43" cy="33" rx="3" ry="3" fill="#1a1a1a"/>
            <ellipse cx="24" cy="32" rx="1" ry="1" fill="#fff"/>
            <ellipse cx="44" cy="32" rx="1" ry="1" fill="#fff"/>
            <ellipse cx="32" cy="41" rx="4" ry="3" fill="#166534"/>
            <rect x="28" y="22" width="8" height="18" rx="4" fill="#166534"/>
            <ellipse cx="14" cy="40" rx="6" ry="4" fill="#86efac" opacity="0.6"/>
            <ellipse cx="50" cy="40" rx="6" ry="4" fill="#86efac" opacity="0.6"/>
          </svg>
          <span style=${{fontSize:20,fontWeight:800,color:'#4ade80',letterSpacing:-0.5}}>
            Racc<span style=${{color:'#86efac',fontWeight:400}}>.net</span>
          </span>
        </div>
    </div>
    <div style=${{flex:1,display:'flex',justifyContent:'center'}}>
      <form onSubmit=${submit} style=${{display:'flex',width:'100%',maxWidth:600}}>
        <input value=${props.input} onInput=${function(e){props.setInput(e.target.value);}}
          placeholder="Search"
          style=${{flex:1,height:40,border:'1px solid #3f3f3f',borderRight:'none',
            borderRadius:'40px 0 0 40px',background:'#121212',color:'#f1f1f1',padding:'0 16px',fontSize:16}}
          onFocus=${function(e){e.target.style.borderColor='#1c62b9';}}
          onBlur=${function(e){e.target.style.borderColor='#3f3f3f';}}/>
        <button type="submit"
          style=${{width:64,height:40,background:'#272727',border:'1px solid #3f3f3f',borderLeft:'none',
            borderRadius:'0 40px 40px 0',color:'#f1f1f1',display:'flex',alignItems:'center',justifyContent:'center'}}
          onMouseEnter=${function(e){e.currentTarget.style.background='#3f3f3f';}}
          onMouseLeave=${function(e){e.currentTarget.style.background='#272727';}}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
            <path d="M15.5 14h-.79l-.28-.27A6.471 6.471 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/>
          </svg>
        </button>
      </form>
    </div>
    <div style=${{flexShrink:0,display:'flex',alignItems:'center',gap:8}}>
      ${props.session?html`
        <button onClick=${props.onUpload} title="Upload video"
          style=${{background:'none',border:'none',color:'#f1f1f1',padding:8,borderRadius:'50%',display:'flex',alignItems:'center',justifyContent:'center'}}
          onMouseEnter=${function(e){e.currentTarget.style.background='#272727';}}
          onMouseLeave=${function(e){e.currentTarget.style.background='none';}}>
          <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor">
            <path d="M9 16h6v-6h4l-7-7-7 7h4v6zm-4 2h14v2H5v-2z"/>
          </svg>
        </button>
      `:null}
      ${props.session ? html`
        <div style=${{display:'flex',alignItems:'center',gap:12}}>
          <${Avatar} src=${props.session.avatar} size=${32}/>
          <button onClick=${props.onLogout}
            style=${{background:'none',border:'1px solid #3f3f3f',color:'#f1f1f1',padding:'6px 12px',borderRadius:4,fontSize:13}}
            onMouseEnter=${function(e){e.currentTarget.style.background='#272727';}}
            onMouseLeave=${function(e){e.currentTarget.style.background='none';}}>Sign out</button>
        </div>
      ` : html`
        <button onClick=${props.onLogin}
          style=${{display:'flex',alignItems:'center',gap:8,background:'none',border:'1px solid #3f3f3f',
            color:'#3ea6ff',padding:'6px 16px',borderRadius:20,fontSize:14,fontWeight:500}}
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
      width:'100%',background:props.active?'#272727':'none',border:'none',color:'#f1f1f1',
      borderRadius:10,justifyContent:props.open?'flex-start':'center',fontSize:14,
      fontWeight:props.active?500:400,transition:'background 0.1s'}}
    onMouseEnter=${function(e){if(!props.active)e.currentTarget.style.background='#272727';}}
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
    transition:'width 0.15s ease',boxSizing:'border-box'}}>
    <${SideItem} open=${open} icon=${H} label="Home"          active=${props.page==='home'}   onClick=${props.onHome}/>
    <${SideItem} open=${open} icon=${S} label="Explore"       active=${props.page==='search'} onClick=${function(){props.onExplore('video');}}/>
    <${SideItem} open=${open} icon=${F} label="Feed"          active=${props.page==='feed'}   onClick=${props.onFeed}/>
    ${props.hasSession ? html`
      <div style=${{height:1,background:'#272727',margin:'8px 0'}}/>
      <${SideItem} open=${open} icon=${U} label="Subscriptions" active=${props.page==='subs'} onClick=${props.onSubs}/>
    ` : null}
    ${open ? html`
      <div style=${{height:1,background:'#272727',margin:'12px 0'}}/>
      <div style=${{padding:'4px 12px'}}>
        <div style=${{color:'#aaa',fontSize:12,marginBottom:6}}>Racc.net</div>
        <a href="https://bsky.app" target="_blank" rel="noreferrer" style=${{color:'#4ade80',fontSize:12}}>Bluesky AT Protocol</a>
        <div style=${{color:'#4ade80',fontSize:11,marginTop:8}}>✓ Running via local proxy</div>
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
  const iSt = {width:'100%',padding:'10px 14px',background:'#121212',border:'1px solid #3f3f3f',borderRadius:6,color:'#f1f1f1',fontSize:14,boxSizing:'border-box'};
  return html`<div onClick=${props.onClose}
    style=${{position:'fixed',top:0,left:0,right:0,bottom:0,background:'rgba(0,0,0,0.85)',zIndex:1000,display:'flex',alignItems:'center',justifyContent:'center'}}>
    <div onClick=${function(e){e.stopPropagation();}}
      style=${{background:'#212121',borderRadius:12,padding:32,width:420,maxWidth:'90vw',boxShadow:'0 8px 32px rgba(0,0,0,0.6)'}}>
      <div style=${{display:'flex',alignItems:'center',gap:10,marginBottom:6}}>
        <svg width="32" height="32" viewBox="0 0 64 64" fill="none">
          <ellipse cx="14" cy="14" rx="9" ry="11" fill="#4ade80"/>
          <ellipse cx="50" cy="14" rx="9" ry="11" fill="#4ade80"/>
          <ellipse cx="14" cy="14" rx="5" ry="7" fill="#86efac"/>
          <ellipse cx="50" cy="14" rx="5" ry="7" fill="#86efac"/>
          <ellipse cx="32" cy="34" rx="24" ry="22" fill="#4ade80"/>
          <ellipse cx="22" cy="33" rx="9" ry="8" fill="#166534"/>
          <ellipse cx="42" cy="33" rx="9" ry="8" fill="#166534"/>
          <ellipse cx="22" cy="33" rx="5" ry="5" fill="#f1f1f1"/>
          <ellipse cx="42" cy="33" rx="5" ry="5" fill="#f1f1f1"/>
          <ellipse cx="23" cy="33" rx="3" ry="3" fill="#1a1a1a"/>
          <ellipse cx="43" cy="33" rx="3" ry="3" fill="#1a1a1a"/>
          <ellipse cx="32" cy="41" rx="4" ry="3" fill="#166534"/>
          <rect x="28" y="22" width="8" height="18" rx="4" fill="#166534"/>
        </svg>
        <h2 style=${{color:'#f1f1f1',fontSize:20,fontWeight:600}}>Sign in to Racc.net</h2>
      </div>
      <p style=${{color:'#aaa',fontSize:13,marginBottom:24}}>Connect your Bluesky account for a personalized video feed.</p>
      <form onSubmit=${submit}>
        <div style=${{marginBottom:16}}>
          <label style=${{display:'block',color:'#aaa',fontSize:13,marginBottom:6}}>Handle or Email</label>
          <input value=${handle} onInput=${function(e){setHandle(e.target.value);}} placeholder="you.bsky.social" style=${iSt}
            onFocus=${function(e){e.target.style.borderColor='#1c62b9';}} onBlur=${function(e){e.target.style.borderColor='#3f3f3f';}}/>
        </div>
        <div style=${{marginBottom:8}}>
          <label style=${{display:'block',color:'#aaa',fontSize:13,marginBottom:6}}>App Password</label>
          <input type="password" value=${pw} onInput=${function(e){setPw(e.target.value);}} placeholder="xxxx-xxxx-xxxx-xxxx" style=${iSt}
            onFocus=${function(e){e.target.style.borderColor='#1c62b9';}} onBlur=${function(e){e.target.style.borderColor='#3f3f3f';}}/>
        </div>
        <p style=${{color:'#aaa',fontSize:12,marginBottom:20}}>Create an App Password at Bluesky Settings → Privacy & Security → App Passwords</p>
        ${err ? html`<div style=${{color:'#ff6666',fontSize:13,marginBottom:12}}>${err}</div>` : null}
        <button type="submit" disabled=${loading||!handle||!pw}
          style=${{width:'100%',padding:12,background:'#ff0000',color:'#fff',border:'none',borderRadius:6,fontSize:15,fontWeight:600,opacity:(loading||!handle||!pw)?0.6:1}}>
          ${loading ? 'Signing in...' : 'Sign In'}
        </button>
      </form>
    </div>
  </div>`;
}

function VideoGrid(props) {
  if (props.loading) return html`<div style=${{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(280px,1fr))',gap:'24px 16px'}}>
    ${[0,1,2,3,4,5,6,7,8,9,10,11].map(function(i){return html`<${SkeletonCard} key=${i}/>`;})}
  </div>`;
  if (!props.videos||!props.videos.length) return html`<div style=${{display:'flex',flexDirection:'column',alignItems:'center',justifyContent:'center',height:'40vh',gap:16,color:'#aaa'}}>
    <svg width="64" height="64" viewBox="0 0 24 24" fill="#3f3f3f"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 14.5v-9l6 4.5-6 4.5z"/></svg>
    <p style=${{fontSize:16}}>No videos found.</p>
  </div>`;
  return html`<div style=${{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(280px,1fr))',gap:'24px 16px'}}>
    ${props.videos.map(function(p,i){return html`<${VideoCard} key=${p.uri||i} post=${p} onWatch=${props.onWatch} onChannel=${props.onChannel}/>`;})}
  </div>`;
}

// Default feeds shown when logged out
const DEFAULT_FEEDS = [
  {uri:'all',          displayName:'All'},
  {uri:'at://did:plc:z72i7hdynmk6r22z27h6tvur/app.bsky.feed.generator/whats-hot',       displayName:'What\u2019s Hot'},
  {uri:'at://did:plc:z72i7hdynmk6r22z27h6tvur/app.bsky.feed.generator/with-friends',    displayName:'Popular with Friends'},
  {uri:'at://did:plc:tenurhgjptubkk5zf5xxn4wv/app.bsky.feed.generator/discover',        displayName:'Discover'},
];

function HomePage(props) {
  const feeds   = props.feeds || DEFAULT_FEEDS;
  const active  = props.activeFeed || 'all';
  const onFeedSelect = props.onFeedSelect;

  return html`<div style=${{padding:24}}>
    <div style=${{display:'flex',gap:8,overflowX:'auto',marginBottom:24,paddingBottom:4,
      scrollbarWidth:'none',msOverflowStyle:'none'}}>
      ${feeds.map(function(feed){
        const isActive = active === feed.uri;
        return html`<button key=${feed.uri}
          onClick=${function(){ onFeedSelect(feed); }}
          style=${{flexShrink:0,padding:'6px 14px',borderRadius:8,border:'none',
            background:isActive?'#f1f1f1':'#272727',color:isActive?'#0f0f0f':'#f1f1f1',
            fontSize:14,fontWeight:isActive?500:400,whiteSpace:'nowrap',
            transition:'background 0.15s,color 0.15s'}}>
          ${feed.displayName}
        </button>`;
      })}
    </div>
    <${VideoGrid} videos=${props.videos} loading=${props.loading} onWatch=${props.onWatch} onChannel=${props.onChannel}/>
  </div>`;
}

// ── Reusable Subscribe button ─────────────────────────────────────────────────
function SubscribeButton(props) {
  const sess = props.session;
  const [subbed,  setSubbed]   = useState(!!viewerFollows({viewer:props.viewer}));
  const [subUri,  setSubUri]   = useState(viewerFollows({viewer:props.viewer})||null);
  const [loading, setLoading]  = useState(false);

  useEffect(function(){
    setSubbed(!!viewerFollows({viewer:props.viewer}));
    setSubUri(viewerFollows({viewer:props.viewer})||null);
  },[props.did, props.viewer]);

  async function toggle(e) {
    e.stopPropagation();
    if(!sess||loading) return;
    setLoading(true);
    if(subbed){
      setSubbed(false);
      const rkey=subUri&&subUri.split('/').pop();
      setSubUri(null);
      if(rkey) await bskyDelete(sess,'app.bsky.graph.follow',rkey);
    } else {
      setSubbed(true);
      const uri=await bskyCreate(sess,'app.bsky.graph.follow',{
        '$type':'app.bsky.graph.follow',
        subject:props.did,
        createdAt:new Date().toISOString()
      });
      setSubUri(uri);
    }
    setLoading(false);
  }

  const pad = props.small ? '7px 14px' : '10px 20px';
  const fsz = props.small ? 13 : 14;
  return html`<button onClick=${toggle} disabled=${loading}
    style=${{flexShrink:0,
      background:subbed?'#272727':'#f1f1f1',
      border:subbed?'1px solid #555':'none',
      color:subbed?'#f1f1f1':'#0f0f0f',
      padding:pad, borderRadius:20, fontWeight:600, fontSize:fsz,
      transition:'all 0.15s', opacity:loading?0.6:1,
      cursor:sess?'pointer':'default'}}>
    ${loading?'...':(subbed?'Subscribed':'Subscribe')}
  </button>`;
}

function WatchPage(props) {
  const post=props.post, embed=post.embed, author=post.author, rec=post.record;
  const sess=props.session;
  const replies=((props.thread&&props.thread.replies)||[]).filter(function(r){return r.post;}).slice(0,20);
  const postId=post.uri.split('\n')[0]||post.uri; // keep full URI for actions

  // ── Local interaction state (optimistic UI) ────────────────────────────────
  const [liked,    setLiked]    = useState(!!viewerLiked(post));
  const [likeUri,  setLikeUri]  = useState(viewerLiked(post)||null);
  const [likeCount,setLikeCount]= useState(post.likeCount||0);
  const [reposted, setReposted] = useState(!!viewerReposted(post));
  const [repostUri,setRepostUri]= useState(viewerReposted(post)||null);
  const [repostCount,setRepostCount]= useState(post.repostCount||0);
  const [subbed,   setSubbed]   = useState(!!viewerFollows(author));
  const [subUri,   setSubUri]   = useState(viewerFollows(author)||null);
  const [subLoading,setSubLoading]=useState(false);

  // Reset state when post changes
  useEffect(function(){
    setLiked(!!viewerLiked(post)); setLikeUri(viewerLiked(post)||null); setLikeCount(post.likeCount||0);
    setReposted(!!viewerReposted(post)); setRepostUri(viewerReposted(post)||null); setRepostCount(post.repostCount||0);
    setSubbed(!!viewerFollows(author)); setSubUri(viewerFollows(author)||null);
  },[post.uri]);

  async function toggleLike() {
    if(!sess) return;
    if(liked){
      setLiked(false); setLikeCount(function(n){return n-1;});
      const rkey=likeUri&&likeUri.split('/').pop();
      setLikeUri(null);
      if(rkey) await bskyDelete(sess,'app.bsky.feed.like',rkey);
    } else {
      setLiked(true); setLikeCount(function(n){return n+1;});
      const uri=await bskyCreate(sess,'app.bsky.feed.like',{
        '$type':'app.bsky.feed.like',
        subject:{uri:post.uri, cid:post.cid},
        createdAt:new Date().toISOString()
      });
      setLikeUri(uri);
    }
  }

  async function toggleRepost() {
    if(!sess) return;
    if(reposted){
      setReposted(false); setRepostCount(function(n){return n-1;});
      const rkey=repostUri&&repostUri.split('/').pop();
      setRepostUri(null);
      if(rkey) await bskyDelete(sess,'app.bsky.feed.repost',rkey);
    } else {
      setReposted(true); setRepostCount(function(n){return n+1;});
      const uri=await bskyCreate(sess,'app.bsky.feed.repost',{
        '$type':'app.bsky.feed.repost',
        subject:{uri:post.uri, cid:post.cid},
        createdAt:new Date().toISOString()
      });
      setRepostUri(uri);
    }
  }

  async function toggleSub() {
    if(!sess||subLoading) return;
    setSubLoading(true);
    if(subbed){
      setSubbed(false);
      const rkey=subUri&&subUri.split('/').pop();
      setSubUri(null);
      if(rkey) await bskyDelete(sess,'app.bsky.graph.follow',rkey);
    } else {
      setSubbed(true);
      const uri=await bskyCreate(sess,'app.bsky.graph.follow',{
        '$type':'app.bsky.graph.follow',
        subject:author.did,
        createdAt:new Date().toISOString()
      });
      setSubUri(uri);
    }
    setSubLoading(false);
  }

  const bSt=function(active,activeColor){return {
    background:active?(activeColor||'#ff0000'):'#272727',
    border:'none',color:'#f1f1f1',padding:'8px 16px',borderRadius:20,
    fontSize:14,display:'flex',alignItems:'center',gap:6,
    transition:'background 0.15s',cursor:sess?'pointer':'default',opacity:sess?1:0.5
  };};

  return html`<div style=${{display:'flex',gap:24,padding:24,maxWidth:1600,margin:'0 auto'}}>
    <div style=${{flex:1,minWidth:0}}>
      <div style=${{borderRadius:12,overflow:'hidden',background:'#000'}}>
        <${VideoPlayer} playlist=${embed.playlist} thumbnail=${embed.thumbnail}/>
      </div>
      <h1 style=${{fontSize:18,fontWeight:600,color:'#f1f1f1',margin:'16px 0 8px',lineHeight:1.4}}>
        ${(rec&&rec.text&&rec.text.split('\n')[0])||'Video from Bluesky'}
      </h1>
      <div style=${{display:'flex',alignItems:'center',justifyContent:'space-between',flexWrap:'wrap',gap:12,marginBottom:16}}>
        <div style=${{display:'flex',alignItems:'center',gap:12}}>
          <div style=${{display:'flex',alignItems:'center',gap:12,cursor:'pointer'}} onClick=${function(){props.onChannel(author.handle);}}>
            <${Avatar} src=${author.avatar} size=${40}/>
            <div>
              <div style=${{color:'#f1f1f1',fontWeight:500,fontSize:14}}>${author.displayName||author.handle}</div>
              <div style=${{color:'#aaa',fontSize:12}}>@${author.handle}</div>
            </div>
          </div>
          <button onClick=${toggleSub} disabled=${subLoading}
            style=${{background:subbed?'#272727':'#f1f1f1',border:subbed?'1px solid #555':'none',
              color:subbed?'#f1f1f1':'#0f0f0f',padding:'8px 18px',borderRadius:20,fontWeight:600,
              fontSize:13,marginLeft:8,transition:'all 0.15s',cursor:'pointer',opacity:subLoading?0.6:1}}>
            ${subLoading?'...':(subbed?'Subscribed':'Subscribe')}
          </button>
        </div>
        <div style=${{display:'flex',gap:8}}>
          <button onClick=${toggleLike} style=${bSt(liked,'#ff4444')}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill=${liked?'#ff8888':'currentColor'}>
              <path d="M1 21h4V9H1v12zm22-11c0-1.1-.9-2-2-2h-6.31l.95-4.57.03-.32c0-.41-.17-.79-.44-1.06L14.17 1 7.59 7.59C7.22 7.95 7 8.45 7 9v10c0 1.1.9 2 2 2h9c.83 0 1.54-.5 1.84-1.22l3.02-7.05c.09-.23.14-.47.14-.73v-2z"/>
            </svg>
            ${likeCount>0?fmt(likeCount):'Like'}
          </button>
          <button onClick=${toggleRepost} style=${bSt(reposted,'#2a7a2a')}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill=${reposted?'#88ff88':'currentColor'}>
              <path d="M7 7h10v3l4-4-4-4v3H5v6h2V7zm10 10H7v-3l-4 4 4 4v-3h12v-6h-2v4z"/>
            </svg>
            ${repostCount>0?fmt(repostCount):'Repost'}
          </button>
          <button onClick=${function(){navigator.clipboard&&navigator.clipboard.writeText('https://bsky.app/profile/'+author.handle+'/post/'+post.uri.split('/').pop());}} style=${bSt(false)}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M18 16.08c-.76 0-1.44.3-1.96.77L8.91 12.7c.05-.23.09-.46.09-.7s-.04-.47-.09-.7l7.05-4.11c.54.5 1.25.81 2.04.81 1.66 0 3-1.34 3-3s-1.34-3-3-3-3 1.34-3 3c0 .24.04.47.09.7L8.04 9.81C7.5 9.31 6.79 9 6 9c-1.66 0-3 1.34-3 3s1.34 3 3 3c.79 0 1.5-.31 2.04-.81l7.12 4.16c-.05.21-.08.43-.08.65 0 1.61 1.31 2.92 2.92 2.92 1.61 0 2.92-1.31 2.92-2.92s-1.31-2.92-2.92-2.92z"/></svg>
            Share
          </button>
        </div>
      </div>
      <div style=${{background:'#212121',borderRadius:12,padding:'12px 16px',marginBottom:24}}>
        <div style=${{fontSize:13,color:'#f1f1f1',fontWeight:500,marginBottom:4}}>
          ${fmt(likeCount)} likes · ${fmt(post.replyCount||0)} comments · ${fmt(repostCount)} reposts · ${ago(post.indexedAt)}
        </div>
        ${rec&&rec.text?html`<div style=${{fontSize:14,color:'#f1f1f1',marginTop:8,whiteSpace:'pre-wrap',lineHeight:1.6}}>${rec.text}</div>`:null}
        <a href=${'https://bsky.app/profile/'+author.handle+'/post/'+post.uri.split('/').pop()} target="_blank" rel="noreferrer"
          style=${{display:'inline-block',marginTop:12,color:'#3ea6ff',fontSize:13}}>View on Bluesky →</a>
      </div>
      ${!sess?html`<div style=${{color:'#aaa',fontSize:13,marginBottom:16,padding:'8px 12px',background:'#1a1a1a',borderRadius:8}}>
        <a onClick=${function(){props.onLogin&&props.onLogin();}} style=${{color:'#3ea6ff',cursor:'pointer'}}>Sign in</a> to like, repost, and subscribe.
      </div>`:null}
      <div>
        <h3 style=${{color:'#f1f1f1',fontSize:16,fontWeight:600,marginBottom:16}}>${fmt(post.replyCount||0)} Comments</h3>
        ${replies.length===0?html`<div style=${{color:'#aaa',fontSize:14}}>No comments yet.</div>`:
          replies.map(function(r,i){const rp=r.post;return html`<div key=${i} style=${{display:'flex',gap:12,marginBottom:20}}>
            <${Avatar} src=${rp.author.avatar} size=${32}/>
            <div>
              <div style=${{fontSize:13,fontWeight:500,color:'#f1f1f1'}}>${rp.author.displayName||rp.author.handle}<span style=${{color:'#aaa',fontWeight:400}}> ${ago(rp.indexedAt)}</span></div>
              <div style=${{fontSize:14,color:'#f1f1f1',marginTop:4,lineHeight:1.5}}>${rp.record&&rp.record.text}</div>
            </div>
          </div>`;})
        }
      </div>
    </div>
    <div style=${{width:402,flexShrink:0}}>
      <h3 style=${{color:'#f1f1f1',fontSize:15,fontWeight:600,marginBottom:12}}>More from this channel</h3>
      ${props.related.length===0?html`<div style=${{color:'#aaa',fontSize:14}}>No related videos.</div>`:
        props.related.map(function(p,i){return html`<${VideoCardCompact} key=${p.uri||i} post=${p} onWatch=${props.onWatch} onChannel=${props.onChannel}/>`;})}
    </div>
  </div>`;
}

function ChannelPage(props) {
  const [tab,setTab]=useState('Videos');
  if(props.loading)return html`<div style=${{display:'flex',alignItems:'center',justifyContent:'center',height:'50vh',color:'#aaa'}}>Loading channel...</div>`;
  if(!props.data)return null;
  const d=props.data;
  return html`<div>
    <div style=${{height:160,background:d.banner?('url('+d.banner+') center/cover'):'linear-gradient(135deg,#1a1a2e,#0f3460)'}}/>
    <div style=${{padding:'0 24px',borderBottom:'1px solid #272727'}}>
      <div style=${{display:'flex',alignItems:'flex-end',gap:24,paddingBottom:24,marginTop:-24}}>
        <${Avatar} src=${d.avatar} size=${80}/>
        <div style=${{flex:1,minWidth:0}}>
          <h1 style=${{color:'#f1f1f1',fontSize:24,fontWeight:700,marginBottom:4}}>${d.displayName||d.handle}</h1>
          <div style=${{color:'#aaa',fontSize:14}}>@${d.handle} · ${fmt(d.followersCount||0)} followers · ${props.videos.length} videos</div>
          ${d.description?html`<div style=${{color:'#aaa',fontSize:13,marginTop:6,maxWidth:600,whiteSpace:'pre-wrap'}}>${d.description.slice(0,200)}${d.description.length>200?'...':''}</div>`:null}
        </div>
        <${SubscribeButton} did=${d.did} viewer=${d.viewer} session=${props.session}/>
      </div>
      <div style=${{display:'flex'}}>${['Videos','About'].map(function(t){return html`<button key=${t} onClick=${function(){setTab(t);}}
        style=${{padding:'12px 20px',background:'none',border:'none',color:tab===t?'#f1f1f1':'#aaa',
          fontSize:14,fontWeight:tab===t?500:400,borderBottom:'3px solid '+(tab===t?'#f1f1f1':'transparent')}}>${t}</button>`;})}</div>
    </div>
    <div style=${{padding:24}}>
      ${tab==='Videos'?html`<${VideoGrid} videos=${props.videos} loading=${false} onWatch=${props.onWatch} onChannel=${props.onChannel}/>`:null}
      ${tab==='About'?html`<div style=${{maxWidth:700}}>
        <h3 style=${{color:'#f1f1f1',fontSize:16,fontWeight:600,marginBottom:16}}>About</h3>
        ${d.description?html`<p style=${{fontSize:14,color:'#aaa',lineHeight:1.7,whiteSpace:'pre-wrap'}}>${d.description}</p>`:html`<p style=${{color:'#aaa',fontSize:14}}>No description.</p>`}
        <div style=${{marginTop:24,display:'flex',flexDirection:'column',gap:12}}>
          ${[['Followers',fmt(d.followersCount||0)],['Following',fmt(d.followsCount||0)],['Posts',fmt(d.postsCount||0)],['Videos',fmt(props.videos.length)]].map(function(pair){return html`<div key=${pair[0]} style=${{display:'flex',gap:24}}>
            <div style=${{color:'#aaa',fontSize:14,width:140}}>${pair[0]}</div>
            <div style=${{color:'#f1f1f1',fontSize:14,fontWeight:500}}>${pair[1]}</div>
          </div>`;})}
        </div>
        <a href=${'https://bsky.app/profile/'+d.handle} target="_blank" rel="noreferrer"
          style=${{display:'inline-block',marginTop:24,color:'#3ea6ff',fontSize:14}}>View on Bluesky →</a>
      </div>`:null}
    </div>
  </div>`;
}

function SearchPage(props) {
  const [filter,setFilter]=useState('All');
  const results=props.results;
  const videos=(results&&results.videos)||[];
  const actors=(results&&results.actors)||[];
  const total=(results&&results.totalPosts)||0;
  const err=results&&results.error;

  if(props.loading)return html`<div style=${{padding:24}}>
    <div style=${{color:'#aaa',fontSize:14,marginBottom:24}}>Searching for "${props.query}"...</div>
    <div style=${{display:'flex',flexDirection:'column',gap:12}}>
      ${[0,1,2,3,4,5].map(function(i){return html`<div key=${i} style=${{display:'flex',gap:12}}>
        <div class="shimmer" style=${{width:168,height:94,borderRadius:8,background:'#272727',flexShrink:0}}/>
        <div style=${{flex:1}}>
          <div class="shimmer" style=${{height:14,background:'#272727',borderRadius:4,marginBottom:8,width:'80%'}}/>
          <div class="shimmer" style=${{height:12,background:'#272727',borderRadius:4,width:'50%'}}/>
        </div>
      </div>`;})}
    </div>
  </div>`;

  return html`<div style=${{padding:'16px 24px'}}>
    <div style=${{display:'flex',gap:8,marginBottom:20}}>
      ${['All','Channels','Videos'].map(function(f){return html`<button key=${f} onClick=${function(){setFilter(f);}}
        style=${{padding:'6px 12px',borderRadius:8,border:'1px solid #3f3f3f',
          background:filter===f?'#f1f1f1':'none',color:filter===f?'#0f0f0f':'#f1f1f1',fontSize:14}}>${f}</button>`;
      })}
    </div>
    <div style=${{color:'#aaa',fontSize:13,marginBottom:16}}>
      Results for "${props.query}"
      ${!err&&results?html`<span style=${{marginLeft:12,color:'#555'}}>
        ${actors.length} channel${actors.length!==1?'s':''} · ${videos.length} video${videos.length!==1?'s':''}
        ${total>videos.length?' ('+total+' posts, '+(total-videos.length)+' non-video)':''}
      </span>`:null}
    </div>
    ${err?html`<div style=${{background:'#3f0000',border:'1px solid #ff4444',borderRadius:8,padding:'12px 16px',marginBottom:20,color:'#ff8888',fontSize:14}}>
      Error: ${err}
    </div>`:null}
    ${!err&&results&&actors.length===0&&videos.length===0?html`<div style=${{background:'#0d1f2d',border:'1px solid #1c62b9',borderRadius:8,padding:'12px 16px',marginBottom:20,color:'#aaa',fontSize:14,lineHeight:1.6}}>
      💡 Try your exact handle like <code style=${{color:'#3ea6ff',background:'#0a1a2a',padding:'1px 5px',borderRadius:3}}>yourname.bsky.social</code>
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
          ${a.description?html`<div style=${{color:'#aaa',fontSize:13,marginTop:6,maxWidth:500}}>${a.description.slice(0,120)}${a.description.length>120?'...':''}</div>`:null}
        </div>
        <div style=${{display:'flex',flexDirection:'column',gap:8,alignItems:'flex-end',flexShrink:0}}>
          <${SubscribeButton} did=${a.did} viewer=${a.viewer} session=${props.session} small=${true}/>
          <button onClick=${function(e){e.stopPropagation();props.onChannel(a.handle);}} style=${{background:'none',border:'1px solid #3f3f3f',color:'#3ea6ff',padding:'6px 12px',borderRadius:20,fontSize:12}}>View Channel</button>
        </div>
      </div>`;})}
    </div>`:null}
    ${(filter==='All'||filter==='Videos')?html`<div>
      <h3 style=${{color:'#f1f1f1',fontSize:15,fontWeight:600,marginBottom:12}}>Videos${videos.length?' ('+videos.length+')':''}</h3>
      ${videos.length===0?html`<div style=${{color:'#aaa',fontSize:14,padding:'24px 0'}}>No videos found for "${props.query}".${total>0?html`<span style=${{color:'#555'}}> (${total} non-video posts found)</span>`:null}</div>`:
        videos.map(function(p,i){return html`<${VideoCardCompact} key=${p.uri||i} post=${p} onWatch=${props.onWatch} onChannel=${props.onChannel}/>`;})
      }
    </div>`:null}
    ${!props.loading&&!err&&results&&videos.length===0&&actors.length===0?html`<div style=${{textAlign:'center',padding:'48px 0',color:'#aaa'}}>
      <div style=${{fontSize:48,marginBottom:16}}>🔍</div>
      <div style=${{fontSize:16,marginBottom:8}}>No results found</div>
      <div style=${{fontSize:14,color:'#555'}}>Try <span style=${{color:'#3ea6ff'}}>yourname.bsky.social</span></div>
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
  const [status,    setStatus]    = useState('idle'); // idle|stitching|uploading|processing|posting|done|error
  const [progress,  setProgress]  = useState('');
  const [error,     setError]     = useState('');

  function onVideoChange(e) {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    setVideoFile(file);
    setError('');
  }

  function onThumbChange(e) {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    setThumbFile(file);
    setThumbUrl(URL.createObjectURL(file));
    setError('');
  }

  async function upload() {
    if (!videoFile || !title.trim()) return;
    setError(''); setStatus('stitching');

    try {
      // ── Step 1: Send video + thumbnail to Python server to stitch ──────────
      // Python uses FFmpeg to prepend the thumbnail as the first frame.
      // The returned body is the modified video file bytes.
      let finalVideoFile = videoFile;
      if (thumbFile) {
        setProgress('Adding thumbnail as first frame…');
        const form = new FormData();
        form.append('video', videoFile, videoFile.name);
        form.append('thumbnail', thumbFile, thumbFile.name);
        const stitchRes = await fetch('/process-video', { method:'POST', body: form });
        if (!stitchRes.ok) {
          const err = await stitchRes.json().catch(function(){return {};});
          throw new Error(err.error || 'Could not stitch thumbnail into video');
        }
        const stitchedBytes = await stitchRes.arrayBuffer();
        finalVideoFile = new File([stitchedBytes], videoFile.name, { type: videoFile.type || 'video/mp4' });
      }

      // ── Step 2: Get a service auth token for video.bsky.app ───────────────────
      // The audience must be the user's OWN PDS DID (e.g. did:web:rooter.us-west.host.bsky.network)
      // NOT did:web:video.bsky.app — Bluesky mints the token on the PDS, then video.bsky.app accepts it
      setStatus('uploading');
      setProgress('Authenticating with video service…');
      const pdsDid = sess.pdsDid || ('did:web:' + AUTH_PROXY.replace('/proxy/auth/xrpc','').replace(/^.*:\/\//, ''));
      // fallback: try to resolve PDS from DID doc now if not stored
      let resolvedPdsDid = pdsDid;
      if (!sess.pdsDid) {
        try {
          const ddRes = await fetch(PUB_PROXY+'/com.atproto.identity.resolveHandle?handle='+encodeURIComponent(sess.handle));
          if (ddRes.ok) {
            const ddData = await ddRes.json();
            const plcRes = await fetch('https://plc.directory/'+encodeURIComponent(ddData.did));
            if (plcRes.ok) {
              const plcData = await plcRes.json();
              const pdsSvc = (plcData.service||[]).find(function(s){return s.id==='#atproto_pds';});
              if (pdsSvc && pdsSvc.serviceEndpoint) {
                const host = pdsSvc.serviceEndpoint.replace(/^https?:\/\//, '').replace(/\/$/, '');
                resolvedPdsDid = 'did:web:' + host;
              }
            }
          }
        } catch(e) { console.warn('PDS DID resolve failed, using fallback:', e); }
      } else {
        resolvedPdsDid = sess.pdsDid;
      }
      const saRes = await fetch(
        AUTH_PROXY+'/com.atproto.server.getServiceAuth?aud='+encodeURIComponent(resolvedPdsDid)+'&lxm=com.atproto.repo.uploadBlob',
        { headers:{'Authorization':'Bearer '+sess.accessJwt} }
      );
      if (!saRes.ok) throw new Error('Could not get video service token: '+(await saRes.text()));
      const serviceToken = (await saRes.json()).token;
      if (!serviceToken) throw new Error('Video service did not return a token');

      // ── Step 3: Upload video using the service token ─────────────────────────
      setProgress('Uploading video… (may take a while for large files)');
      const vRes = await fetch(
        VIDEO_PROXY+'/app.bsky.video.uploadVideo?did='+encodeURIComponent(sess.did)+'&name='+encodeURIComponent(finalVideoFile.name),
        { method:'POST', headers:{'Content-Type': finalVideoFile.type||'video/mp4', 'Authorization':'Bearer '+serviceToken}, body: finalVideoFile }
      );
      if (!vRes.ok) throw new Error('Video upload failed: '+(await vRes.text()));
      const vData = await vRes.json();
      const jobId = vData.jobId;
      if (!jobId) throw new Error('No job ID returned from video service');

      // ── Step 4: Poll until Bluesky finishes processing ───────────────────────
      setStatus('processing');
      let videoRef = null;
      for (let i = 0; i < 120; i++) {
        setProgress('Processing video… '+Math.min(i*2, 99)+'%');
        await new Promise(function(r){setTimeout(r,2000);});
        const sRes = await fetch(VIDEO_PROXY+'/app.bsky.video.getJobStatus?jobId='+encodeURIComponent(jobId),
          {headers:{'Authorization':'Bearer '+serviceToken}});
        if (!sRes.ok) continue;
        const job = (await sRes.json()).jobStatus;
        if (job && job.blob) { videoRef = job.blob; break; }
        if (job && job.state === 'JOB_STATE_FAILED') throw new Error('Video processing failed: '+(job.error||'unknown'));
      }
      if (!videoRef) throw new Error('Video processing timed out — try again.');

      // ── Step 4: Create the Bluesky post ─────────────────────────────────────
      setStatus('posting'); setProgress('Creating post…');
      const postText = title.trim() + (desc.trim() ? '\n\n'+desc.trim() : '');
      const postRes = await fetch(AUTH_PROXY+'/com.atproto.repo.createRecord', {
        method:'POST',
        headers:{'Content-Type':'application/json','Authorization':'Bearer '+sess.accessJwt},
        body: JSON.stringify({
          repo: sess.did,
          collection: 'app.bsky.feed.post',
          record: {
            '$type': 'app.bsky.feed.post',
            text: postText,
            embed: { '$type':'app.bsky.embed.video', video:videoRef, alt:title.trim() },
            createdAt: new Date().toISOString(),
            langs: ['en']
          }
        })
      });
      if (!postRes.ok) throw new Error('Post creation failed: '+(await postRes.text()));

      setStatus('done'); setProgress('');
      setTimeout(function(){ props.onDone(); }, 1800);

    } catch(err) {
      console.error('Upload error:', err);
      setError(err.message || 'Upload failed');
      setStatus('error'); setProgress('');
    }
  }

  const busy = ['stitching','uploading','processing','posting'].indexOf(status) !== -1;
  const done = status === 'done';
  const iSt  = {width:'100%',padding:'10px 14px',background:'#121212',border:'1px solid #3f3f3f',
    borderRadius:8,color:'#f1f1f1',fontSize:14,boxSizing:'border-box'};

  function FilePicker(fpProps) {
    const chosen = fpProps.chosen;
    const accept = fpProps.accept;
    const onChange = fpProps.onChange;
    const label   = fpProps.label;
    const hint    = fpProps.hint;
    const icon    = fpProps.icon;
    return html`<div style=${{marginBottom:20}}>
      <div style=${{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:8}}>
        <label style=${{color:'#aaa',fontSize:13,fontWeight:500}}>${label}</label>
        ${hint?html`<span style=${{color:'#555',fontSize:11}}>${hint}</span>`:null}
      </div>
      <label style=${{display:'flex',alignItems:'center',gap:14,padding:'14px 18px',
        border:'2px dashed '+(chosen?'#ff0000':'#3f3f3f'),borderRadius:12,cursor:busy?'default':'pointer',
        background:chosen?'rgba(255,0,0,0.05)':'#181818',transition:'border-color 0.15s'}}>
        <svg width="28" height="28" viewBox="0 0 24 24" fill=${chosen?'#ff4444':'#555'}>${icon}</svg>
        <div style=${{flex:1,minWidth:0}}>
          <div style=${{fontSize:14,color:chosen?'#f1f1f1':'#888',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>
            ${chosen?chosen.name:'Click to choose…'}
          </div>
          ${chosen?html`<div style=${{fontSize:12,color:'#555',marginTop:2}}>${(chosen.size/1024/1024).toFixed(1)} MB</div>`:null}
        </div>
        <input type="file" accept=${accept} style=${{display:'none'}} onInput=${onChange} disabled=${busy}/>
      </label>
    </div>`;
  }

  const STEPS = [
    {id:'stitching',  label:'Adding thumbnail as first frame'},
    {id:'uploading',  label:'Authenticating + uploading video'},
    {id:'processing', label:'Bluesky processing video'},
    {id:'posting',    label:'Creating post'},
    {id:'done',       label:'Done!'},
  ];

  return html`<div onClick=${props.onClose}
    style=${{position:'fixed',top:0,left:0,right:0,bottom:0,background:'rgba(0,0,0,0.9)',zIndex:2000,
      display:'flex',alignItems:'center',justifyContent:'center',padding:16}}>
    <div onClick=${function(e){e.stopPropagation();}}
      style=${{background:'#1e1e1e',borderRadius:16,padding:32,width:560,maxWidth:'100%',
        maxHeight:'92vh',overflowY:'auto',boxShadow:'0 20px 60px rgba(0,0,0,0.8)'}}>

      <div style=${{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:28}}>
        <div style=${{display:'flex',alignItems:'center',gap:10}}>
          <svg width="28" height="28" viewBox="0 0 64 64" fill="none">
            <ellipse cx="14" cy="14" rx="9" ry="11" fill="#4ade80"/>
            <ellipse cx="50" cy="14" rx="9" ry="11" fill="#4ade80"/>
            <ellipse cx="14" cy="14" rx="5" ry="7" fill="#86efac"/>
            <ellipse cx="50" cy="14" rx="5" ry="7" fill="#86efac"/>
            <ellipse cx="32" cy="34" rx="24" ry="22" fill="#4ade80"/>
            <ellipse cx="22" cy="33" rx="9" ry="8" fill="#166534"/>
            <ellipse cx="42" cy="33" rx="9" ry="8" fill="#166534"/>
            <ellipse cx="22" cy="33" rx="5" ry="5" fill="#f1f1f1"/>
            <ellipse cx="42" cy="33" rx="5" ry="5" fill="#f1f1f1"/>
            <ellipse cx="23" cy="33" rx="3" ry="3" fill="#1a1a1a"/>
            <ellipse cx="43" cy="33" rx="3" ry="3" fill="#1a1a1a"/>
            <ellipse cx="32" cy="41" rx="4" ry="3" fill="#166534"/>
            <rect x="28" y="22" width="8" height="18" rx="4" fill="#166534"/>
          </svg>
          <h2 style=${{color:'#f1f1f1',fontSize:18,fontWeight:700}}>Upload Video</h2>
        </div>
        ${!busy?html`<button onClick=${props.onClose}
          style=${{background:'none',border:'none',color:'#666',fontSize:22,padding:'0 4px',cursor:'pointer'}}
          onMouseEnter=${function(e){e.currentTarget.style.color='#f1f1f1';}}
          onMouseLeave=${function(e){e.currentTarget.style.color='#666';}}>✕</button>`:null}
      </div>

      ${done?html`
        <div style=${{textAlign:'center',padding:'40px 0'}}>
          <div style=${{fontSize:64,marginBottom:16}}>🎉</div>
          <div style=${{fontSize:20,fontWeight:700,color:'#f1f1f1',marginBottom:8}}>Video uploaded!</div>
          <div style=${{fontSize:14,color:'#aaa'}}>Your video is now live on Bluesky.</div>
        </div>
      `:html`

        <${FilePicker}
          label="Video File *"
          hint=""
          accept="video/*"
          chosen=${videoFile}
          onChange=${onVideoChange}
          icon=${html`<path d="M17 10.5V7c0-.55-.45-1-1-1H4c-.55 0-1 .45-1 1v10c0 .55.45 1 1 1h12c.55 0 1-.45 1-1v-3.5l4 4v-11l-4 4z"/>`}/>

        <${FilePicker}
          label="Thumbnail Image"
          hint="Will be inserted as the very first frame of your video"
          accept="image/*"
          chosen=${thumbFile}
          onChange=${onThumbChange}
          icon=${html`<path d="M21 19V5c0-1.1-.9-2-2-2H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2zM8.5 13.5l2.5 3.01L14.5 12l4.5 6H5l3.5-4.5z"/>`}/>

        ${thumbUrl?html`
          <div style=${{marginBottom:20,borderRadius:10,overflow:'hidden',maxHeight:180,position:'relative'}}>
            <img src=${thumbUrl} style=${{width:'100%',objectFit:'cover',display:'block',maxHeight:180}}/>
            <div style=${{position:'absolute',bottom:0,left:0,right:0,padding:'6px 10px',
              background:'linear-gradient(transparent,rgba(0,0,0,0.7))',fontSize:11,color:'#ccc'}}>
              This image will appear as the first frame — every app sees it as the thumbnail
            </div>
          </div>
        `:null}

        <div style=${{marginBottom:16}}>
          <label style=${{display:'block',color:'#aaa',fontSize:13,fontWeight:500,marginBottom:8}}>Title *</label>
          <input value=${title} onInput=${function(e){setTitle(e.target.value);}}
            placeholder="Give your video a title" maxlength="200"
            style=${Object.assign({},iSt,{resize:'none'})} disabled=${busy}
            onFocus=${function(e){e.target.style.borderColor='#ff0000';}}
            onBlur=${function(e){e.target.style.borderColor='#3f3f3f';}}/>
          <div style=${{textAlign:'right',fontSize:11,color:'#555',marginTop:3}}>${title.length}/200</div>
        </div>

        <div style=${{marginBottom:20}}>
          <label style=${{display:'block',color:'#aaa',fontSize:13,fontWeight:500,marginBottom:8}}>Description</label>
          <textarea value=${desc} onInput=${function(e){setDesc(e.target.value);}}
            placeholder="Describe your video (optional)" rows="3" maxlength="2000"
            style=${Object.assign({},iSt,{resize:'vertical'})} disabled=${busy}
            onFocus=${function(e){e.target.style.borderColor='#ff0000';}}
            onBlur=${function(e){e.target.style.borderColor='#3f3f3f';}}/>
          <div style=${{textAlign:'right',fontSize:11,color:'#555',marginTop:3}}>${desc.length}/2000</div>
        </div>

        ${title.trim()?html`
          <div style=${{marginBottom:20,background:'#161616',borderRadius:10,padding:'12px 14px',border:'1px solid #2a2a2a'}}>
            <div style=${{fontSize:10,color:'#555',marginBottom:6,textTransform:'uppercase',letterSpacing:1}}>Post preview</div>
            <div style=${{fontSize:13,color:'#f1f1f1',lineHeight:1.6,whiteSpace:'pre-wrap'}}>${title.trim()}${desc.trim()?'\n\n'+desc.trim():''}</div>
          </div>
        `:null}

        ${busy||status==='error'?html`
          <div style=${{marginBottom:20,background:'#161616',borderRadius:10,padding:'14px 16px'}}>
            ${STEPS.map(function(step,i){
              const order = ['stitching','uploading','processing','posting','done'];
              const cur   = order.indexOf(status);
              const si    = order.indexOf(step.id);
              const active   = status === step.id;
              const complete = cur > si;
              const pending  = cur < si;
              return html`<div key=${step.id}
                style=${{display:'flex',alignItems:'center',gap:10,padding:'5px 0',opacity:pending?0.3:1,transition:'opacity 0.4s'}}>
                <div style=${{width:22,height:22,borderRadius:'50%',flexShrink:0,display:'flex',alignItems:'center',justifyContent:'center',
                  background:complete?'#1a6b1a':active?'#cc0000':'#2a2a2a',fontSize:10,fontWeight:700,color:'#fff'}}>
                  ${complete?'✓':(i+1)}
                </div>
                <span style=${{fontSize:13,color:active?'#f1f1f1':'#888',fontWeight:active?600:400,flex:1}}>${step.label}</span>
                ${active?html`<span style=${{fontSize:11,color:'#888'}}>${progress}</span>`:null}
              </div>`;
            })}
          </div>
        `:null}

        ${error?html`
          <div style=${{background:'#2a0000',border:'1px solid #882222',borderRadius:8,
            padding:'10px 14px',marginBottom:16,color:'#ff9999',fontSize:13,lineHeight:1.5}}>
            ⚠️ ${error}
            ${error.indexOf('FFmpeg')===-1?'':html`<div style=${{marginTop:8,fontSize:12,color:'#cc6666'}}>
              FFmpeg is not installed. Run: <code style=${{background:'#1a0000',padding:'1px 5px',borderRadius:3}}>winget install ffmpeg</code>
              in your terminal, then restart the server.
            </div>`}
          </div>
        `:null}

        <button onClick=${upload} disabled=${busy||!videoFile||!title.trim()}
          style=${{width:'100%',padding:14,background:busy?'#0f3320':'#16a34a',color:'#fff',
            border:'none',borderRadius:10,fontSize:15,fontWeight:700,marginTop:4,
            opacity:(busy||!videoFile||!title.trim())?0.55:1,
            transition:'opacity 0.15s,background 0.15s',
            cursor:(busy||!videoFile||!title.trim())?'not-allowed':'pointer'}}>
          ${busy?(progress||'Working…'):(thumbFile?'Stitch Thumbnail & Upload':'Upload Video')}
        </button>

        <p style=${{color:'#444',fontSize:11,textAlign:'center',marginTop:12,lineHeight:1.5}}>
          ${thumbFile
            ? 'Your thumbnail will be inserted as the very first frame of the video so every AT Protocol app displays it correctly.'
            : 'No thumbnail selected — the video\'s natural first frame will be used.'}
        </p>
      `}
    </div>
  </div>`;
}


function SubsPage(props) {
  return html`<div style=${{padding:24}}>
    <h2 style=${{color:'#f1f1f1',fontSize:20,fontWeight:700,marginBottom:20}}>Subscriptions</h2>
    ${props.loading?html`<div style=${{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(280px,1fr))',gap:'24px 16px'}}>
      ${[0,1,2,3,4,5,6,7].map(function(i){return html`<${SkeletonCard} key=${i}/>`;})}
    </div>`:null}
    ${!props.loading&&props.videos.length===0?html`<div style=${{display:'flex',flexDirection:'column',alignItems:'center',justifyContent:'center',height:'40vh',gap:16,color:'#aaa'}}>
      <svg width="64" height="64" viewBox="0 0 24 24" fill="#3f3f3f"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 14.5v-9l6 4.5-6 4.5z"/></svg>
      <p style=${{fontSize:16}}>No videos from people you follow yet.</p>
      <p style=${{fontSize:13,color:'#555'}}>Follow some people on Bluesky who post videos and they'll show up here.</p>
    </div>`:null}
    ${!props.loading&&props.videos.length>0?html`<div style=${{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(280px,1fr))',gap:'24px 16px'}}>
      ${props.videos.map(function(p,i){return html`<${VideoCard} key=${p.uri||i} post=${p} onWatch=${props.onWatch} onChannel=${props.onChannel}/>`;  })}
    </div>`:null}
  </div>`;
}

function App() {
  const [session,setSession]=useState(function(){return loadSession();});
  const [page,setPage]=useState('home');
  const [sidebarOpen,setSidebarOpen]=useState(true);
  const [showLogin,setShowLogin]=useState(false);
  const [showUpload,setShowUpload]=useState(false);
  const [searchInput,setSearchInput]=useState('');
  const [homeVideos,setHomeVideos]=useState([]);
  const [homeLoading,setHomeLoading]=useState(true);
  const [feeds,setFeeds]=useState(null);          // null = not loaded yet
  const [activeFeed,setActiveFeed]=useState('all');
  const [currentVideo,setCurrentVideo]=useState(null);
  const [related,setRelated]=useState([]);
  const [thread,setThread]=useState(null);
  const [channelData,setChannelData]=useState(null);
  const [channelVideos,setChannelVideos]=useState([]);
  const [channelLoading,setChannelLoading]=useState(false);
  const [searchQuery,setSearchQuery]=useState('');
  const [subsVideos,setSubsVideos]=useState([]);
  const [subsLoading,setSubsLoading]=useState(false);
  const [searchResults,setSearchResults]=useState(null);
  const [searchLoading,setSearchLoading]=useState(false);

  // Load saved feeds from Bluesky preferences, then load videos for the active feed
  const loadSavedFeeds=useCallback(async function(sess){
    if(!sess){setFeeds(DEFAULT_FEEDS);return;}
    try{
      const r=await api(AUTH_PROXY+'/app.bsky.actor.getPreferences',{headers:{Authorization:'Bearer '+sess.accessJwt}});
      if(!r.ok){setFeeds(DEFAULT_FEEDS);return;}
      const d=await r.json();
      const prefs=d.preferences||[];
      // Try savedFeedsPrefV2 first, then fall back to savedFeedsPref
      let saved=[];
      const v2=prefs.find(function(p){return p['$type']==='app.bsky.actor.defs#savedFeedsPrefV2';});
      const v1=prefs.find(function(p){return p['$type']==='app.bsky.actor.defs#savedFeedsPref';});
      if(v2&&v2.items){
        saved=v2.items
          .filter(function(item){return item.type==='feed';})
          .map(function(item){return {uri:item.value, displayName:item.value.split('/').pop()};});
      } else if(v1&&v1.saved){
        saved=v1.saved.map(function(uri){return {uri:uri, displayName:uri.split('/').pop()};});
      }
      // Hydrate display names by fetching feed generator info in bulk
      if(saved.length>0){
        try{
          const uris=saved.map(function(f){return 'feeds='+encodeURIComponent(f.uri);}).join('&');
          const gR=await api(PUB_PROXY+'/app.bsky.feed.getFeedGenerators?'+uris);
          if(gR.ok){
            const gd=await gR.json();
            const byUri={};
            (gd.feeds||[]).forEach(function(g){byUri[g.uri]=g;});
            saved=saved.map(function(f){
              const g=byUri[f.uri];
              return {uri:f.uri, displayName:g?g.displayName:f.displayName, avatar:g?g.avatar:null};
            });
          }
        }catch(e){console.warn('Could not hydrate feed names:',e);}
      }
      // Prepend "All" chip
      const allChip={uri:'all',displayName:'All'};
      setFeeds([allChip].concat(saved.length>0?saved:DEFAULT_FEEDS.slice(1)));
    }catch(e){
      console.error('loadSavedFeeds:',e);
      setFeeds(DEFAULT_FEEDS);
    }
  },[]);

  const loadFeedVideos=useCallback(async function(feedUri,sess){
    setHomeLoading(true);
    const seen=new Set(),videos=[];
    function add(posts){(posts||[]).forEach(function(p){if(p&&isVid(p)&&!seen.has(p.uri)){videos.push(p);seen.add(p.uri);}});}
    try{
      if(feedUri==='all'){
        // "All" = timeline (if logged in) + popular video search
        if(sess){
          const r=await api(AUTH_PROXY+'/app.bsky.feed.getTimeline?limit=100',{headers:{Authorization:'Bearer '+sess.accessJwt}});
          if(r.ok){const d=await r.json();add((d.feed||[]).map(function(i){return i.post;}));}
        }
        const terms=['video','watch','clip','vlog','reel'];
        const batches=await Promise.all(terms.map(function(t){
          return api(PUB_PROXY+'/app.bsky.feed.searchPosts?q='+encodeURIComponent(t)+'&limit=50&sort=latest')
            .then(function(r){return r.ok?r.json():{posts:[]};}).then(function(d){return d.posts||[];}).catch(function(){return[];});
        }));
        batches.forEach(add);
        const hotR=await api(PUB_PROXY+'/app.bsky.feed.getFeed?feed=at://did:plc:z72i7hdynmk6r22z27h6tvur/app.bsky.feed.generator/whats-hot&limit=100').catch(function(){return null;});
        if(hotR&&hotR.ok){const d=await hotR.json();add((d.feed||[]).map(function(i){return i.post;}));}
      } else {
        // Specific saved feed
        // Use auth endpoint when logged in for personalised feeds
        const feedUrl='/app.bsky.feed.getFeed?feed='+encodeURIComponent(feedUri)+'&limit=100';
        const authOpts=sess?{headers:{Authorization:'Bearer '+sess.accessJwt}}:{};
        const r=await api((sess?AUTH_PROXY:PUB_PROXY)+feedUrl,authOpts);
        if(r.ok){const d=await r.json();add((d.feed||[]).map(function(i){return i.post;}));}
        // Supplement with public endpoint in case auth feed is sparse
        if(videos.length<10){
          const r2=await api(PUB_PROXY+feedUrl).catch(function(){return null;});
          if(r2&&r2.ok){const d=await r2.json();add((d.feed||[]).map(function(i){return i.post;}));}
        }
      }
    }catch(e){console.error('loadFeedVideos:',e);}
    setHomeVideos(videos);setHomeLoading(false);
  },[]);

  const handleFeedSelect=useCallback(function(feed){
    setActiveFeed(feed.uri);
    loadFeedVideos(feed.uri,session);
  },[session,loadFeedVideos]);

  // Initial load
  // On first home load (including page refresh with saved session)
  useEffect(function(){
    if(page==='home'){
      loadSavedFeeds(session);
      loadFeedVideos('all',session);
    }
  },[page]);

  // Refresh session token in background if we loaded from localStorage
  useEffect(function(){
    var saved=loadSession();
    if(!saved) return;
    // Re-validate by fetching profile; if 401, clear the stale session
    api(AUTH_PROXY+'/app.bsky.actor.getProfile?actor='+encodeURIComponent(saved.did),
      {headers:{Authorization:'Bearer '+saved.accessJwt}})
      .then(function(r){
        if(!r.ok){ clearSession(); setSession(null); }
      }).catch(function(){});
  },[]);

  // When session changes (login/logout), refresh feeds and reload
  useEffect(function(){
    if(page==='home'){
      setActiveFeed('all');
      loadSavedFeeds(session);
      loadFeedVideos('all',session);
    }
  },[session]);

  const handleWatch=useCallback(async function(post){
    setCurrentVideo(post);setThread(null);setRelated([]);setPage('watch');window.scrollTo(0,0);
    try{
      const tR=await api(PUB_PROXY+'/app.bsky.feed.getPostThread?uri='+encodeURIComponent(post.uri)+'&depth=6');
      const fR=await api(PUB_PROXY+'/app.bsky.feed.getAuthorFeed?actor='+encodeURIComponent(post.author.did)+'&limit=50');
      if(tR.ok){const d=await tR.json();setThread(d.thread);}
      if(fR.ok){const d=await fR.json();setRelated((d.feed||[]).map(function(i){return i.post;}).filter(function(p){return isVid(p)&&p.uri!==post.uri;}).slice(0,15));}
    }catch(e){console.error(e);}
  },[]);

  const handleChannel=useCallback(async function(actor){
    setChannelData(null);setChannelVideos([]);setChannelLoading(true);setPage('channel');window.scrollTo(0,0);
    try{
      const pR=await api(PUB_PROXY+'/app.bsky.actor.getProfile?actor='+encodeURIComponent(actor));
      const fR=await api(PUB_PROXY+'/app.bsky.feed.getAuthorFeed?actor='+encodeURIComponent(actor)+'&limit=100&filter=posts_with_media');
      if(pR.ok){const d=await pR.json();setChannelData(d);}
      if(fR.ok){const d=await fR.json();setChannelVideos((d.feed||[]).map(function(i){return i.post;}).filter(isVid));}
    }catch(e){console.error(e);}
    setChannelLoading(false);
  },[]);

  const handleSearch=useCallback(async function(q){
    const raw=(q||'').trim();if(!raw)return;
    const stripped=raw.startsWith('@')?raw.slice(1):raw;
    const looksLikeHandle=!stripped.includes(' ');
    setSearchQuery(raw);setSearchInput(raw);setSearchResults(null);setSearchLoading(true);setPage('search');window.scrollTo(0,0);
    try{
      const aR=await api(PUB_PROXY+'/app.bsky.actor.searchActors?q='+encodeURIComponent(stripped)+'&limit=20');
      const pR=await api(PUB_PROXY+'/app.bsky.feed.searchPosts?q='+encodeURIComponent(raw)+'&limit=100&sort=latest');
      let actors=aR.ok?((await aR.json()).actors||[]):[];
      const allPosts=pR.ok?((await pR.json()).posts||[]):[];
      if(looksLikeHandle){
        const dR=await api(PUB_PROXY+'/app.bsky.actor.getProfile?actor='+encodeURIComponent(stripped)).catch(function(){return null;});
        if(dR&&dR.ok){const p=await dR.json();if(p&&p.handle)actors=[p].concat(actors.filter(function(a){return a.did!==p.did;}));}
      }
      function hasVideo(p){
        if(!p||!p.embed)return false;
        const t=p.embed['$type']||'';
        if(t==='app.bsky.embed.video#view'||t==='app.bsky.embed.video')return true;
        if(t==='app.bsky.embed.recordWithMedia#view'){const m=p.embed.media;if(m&&(m['$type']==='app.bsky.embed.video#view'||m['$type']==='app.bsky.embed.video'))return true;}
        return false;
      }
      setSearchResults({videos:allPosts.filter(hasVideo),actors:actors,totalPosts:allPosts.length,error:null});
    }catch(e){
      console.error('Search:',e);
      setSearchResults({videos:[],actors:[],totalPosts:0,error:e.message||String(e)});
    }
    setSearchLoading(false);
  },[]);

  const handleSubs=useCallback(async function(){
    if(!session)return;
    setSubsLoading(true);
    setPage('subs');
    window.scrollTo(0,0);
    const seen=new Set(),videos=[];
    function add(posts){(posts||[]).forEach(function(p){if(p&&isVid(p)&&!seen.has(p.uri)){videos.push(p);seen.add(p.uri);}});}
    try{
      // Fetch who the user follows
      const fR=await api(AUTH_PROXY+'/app.bsky.graph.getFollows?actor='+encodeURIComponent(session.did)+'&limit=100',
        {headers:{Authorization:'Bearer '+session.accessJwt}});
      if(fR.ok){
        const fd=await fR.json();
        const follows=(fd.follows||[]).slice(0,30); // cap at 30 to avoid too many requests
        // Fetch recent videos from each followed account (in parallel batches of 5)
        for(let i=0;i<follows.length;i+=5){
          const batch=follows.slice(i,i+5);
          await Promise.all(batch.map(function(actor){
            return api(PUB_PROXY+'/app.bsky.feed.getAuthorFeed?actor='+encodeURIComponent(actor.did)+'&limit=20&filter=posts_with_media')
              .then(function(r){return r.ok?r.json():{feed:[]};})
              .then(function(d){add((d.feed||[]).map(function(i){return i.post;}));})
              .catch(function(){});
          }));
        }
      }
    }catch(e){console.error('handleSubs:',e);}
    // Sort by date, newest first
    videos.sort(function(a,b){return new Date(b.indexedAt)-new Date(a.indexedAt);});
    setSubsVideos(videos);
    setSubsLoading(false);
  },[session]);

  // Called after successful login — fetches full profile (for avatar), then reloads
  const handleLoginSuccess=useCallback(async function(data){
    // createSession doesn't include avatar — fetch profile separately
    try{
      const pr=await api(AUTH_PROXY+'/app.bsky.actor.getProfile?actor='+encodeURIComponent(data.handle),
        {headers:{Authorization:'Bearer '+data.accessJwt}});
      if(pr.ok){const pd=await pr.json(); data.avatar=pd.avatar; data.displayName=pd.displayName;}
    }catch(e){console.warn('Could not fetch profile avatar:',e);}
    // Extract the user's PDS DID from their DID document so we can use it for
    // video service auth. The didDoc comes back with createSession.
    try{
      if(data.didDoc && data.didDoc.service){
        const pds = data.didDoc.service.find(function(s){
          return s.id==='#atproto_pds' || (s.type&&s.type.indexOf('AtprotoPersonalDataServer')!==-1);
        });
        if(pds && pds.serviceEndpoint){
          // Convert https://rooter.us-west.host.bsky.network -> did:web:rooter.us-west.host.bsky.network
          const host = pds.serviceEndpoint.replace(/^https?:\/\//, '').replace(/\/$/, '');
          data.pdsDid = 'did:web:' + host;
        }
      }
    }catch(e){console.warn('Could not extract PDS DID:',e);}
    saveSession(data);
    setSession(data);
    setShowLogin(false);
    setActiveFeed('all');
    loadSavedFeeds(data);
    loadFeedVideos('all',data);
  },[loadSavedFeeds,loadFeedVideos]);

  const mL=sidebarOpen?240:72;
  return html`<div style=${{minHeight:'100vh',background:'#0f0f0f',color:'#f1f1f1'}}>
    <${Header} onHome=${function(){setPage('home');window.scrollTo(0,0);}} onSearch=${handleSearch}
      session=${session} onLogin=${function(){setShowLogin(true);}} onLogout=${function(){clearSession();setSession(null);setActiveFeed('all');setFeeds(DEFAULT_FEEDS);loadFeedVideos('all',null);}}
      onUpload=${function(){setShowUpload(true);}}
      input=${searchInput} setInput=${setSearchInput} toggleSidebar=${function(){setSidebarOpen(function(o){return!o;});}}/>
    <${Sidebar} open=${sidebarOpen} page=${page}
      onHome=${function(){setPage('home');window.scrollTo(0,0);}}
      onExplore=${handleSearch} onFeed=${function(){handleSearch('video');}}
      onSubs=${function(){session?handleSubs():setShowLogin(true);}}
      hasSession=${!!session}/>
    <main style=${{marginLeft:mL,marginTop:56,minHeight:'calc(100vh - 56px)',transition:'margin-left 0.15s ease'}}>
      ${page==='home'?html`<${HomePage} videos=${homeVideos} loading=${homeLoading} onWatch=${handleWatch} onChannel=${handleChannel} onExplore=${handleSearch} feeds=${feeds||DEFAULT_FEEDS} activeFeed=${activeFeed} onFeedSelect=${handleFeedSelect}/>`:null}
      ${page==='watch'&&currentVideo?html`<${WatchPage} post=${currentVideo} related=${related} thread=${thread} session=${session} onLogin=${function(){setShowLogin(true);}} onWatch=${handleWatch} onChannel=${handleChannel}/>`:null}
      ${page==='channel'?html`<${ChannelPage} data=${channelData} videos=${channelVideos} loading=${channelLoading} session=${session} onWatch=${handleWatch} onChannel=${handleChannel}/>`:null}
      ${page==='search'?html`<${SearchPage} results=${searchResults} loading=${searchLoading} query=${searchQuery} session=${session} onWatch=${handleWatch} onChannel=${handleChannel}/>`:null}
      ${page==='subs'?html`<${SubsPage} videos=${subsVideos} loading=${subsLoading} onWatch=${handleWatch} onChannel=${handleChannel}/>`:null}
    </main>
    ${showLogin?html`<${LoginModal} onClose=${function(){setShowLogin(false);}} onSuccess=${handleLoginSuccess}/> `:null}
    ${showUpload&&session?html`<${UploadModal} session=${session} onClose=${function(){setShowUpload(false);}} onDone=${function(){setShowUpload(false);setPage('home');loadFeedVideos(activeFeed,session);}}/> `:null}
  </div>`;
}

render(html`<${App}/>`, document.getElementById('app'));
</script>
</body>
</html>"""

# ── Request handler ───────────────────────────────────────────────────────────
class Handler(http.server.BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        # Only print non-200/304 responses to keep the terminal clean
        if args and len(args) >= 2:
            code = str(args[1]) if len(args) > 1 else ''
            if code.startswith('2') or code in ('304',''):
                return
        print(f"  {self.address_string()} {fmt % args}")

    def send_cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, Accept')

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_cors()
        self.end_headers()

    def do_GET(self):
        if self.path == '/' or self.path == '/index.html':
            self._serve_html()
        elif self.path.startswith('/proxy/pub/'):
            self._proxy('GET', 'https://public.api.bsky.app/' + self.path[len('/proxy/pub/'):], cacheable=True)
        elif self.path.startswith('/proxy/auth/'):
            self._proxy('GET', 'https://bsky.social/' + self.path[len('/proxy/auth/'):])
        elif self.path.startswith('/proxy/video/'):
            self._proxy('GET', 'https://video.bsky.app/' + self.path[len('/proxy/video/'):])
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path.startswith('/proxy/auth/'):
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length) if length else b''
            self._proxy('POST', 'https://bsky.social/' + self.path[len('/proxy/auth/'):], body=body)
        elif self.path.startswith('/proxy/video/'):
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length) if length else b''
            self._proxy('POST', 'https://video.bsky.app/' + self.path[len('/proxy/video/'):], body=body, timeout=300)
        elif self.path == '/process-video':
            self._process_video()
        else:
            self.send_response(404)
            self.end_headers()

    def _process_video(self):
        """
        POST /process-video  (multipart/form-data)
        Fields: video=<video file>  thumbnail=<image file>
        Returns the video with the thumbnail prepended as the first frame.
        """
        import email, email.parser, email.policy
        tmpdir = tempfile.mkdtemp(prefix="raccnet_")
        try:
            content_type = self.headers.get("Content-Type", "")
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)

            # Parse multipart without the cgi module (works on all Python versions)
            # Reconstruct a full MIME message so email library can parse it
            msg_text = (
                "Content-Type: " + content_type + "\r\n\r\n"
            ).encode() + body
            msg = email.message_from_bytes(msg_text, policy=email.policy.compat32)

            fields = {}
            if msg.is_multipart():
                for part in msg.get_payload():
                    disp = part.get("Content-Disposition", "")
                    name_match = __import__("re").search(r'name="([^"]+)"', disp)
                    if name_match:
                        fields[name_match.group(1)] = part.get_payload(decode=True)

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
                self._json_error(500, "FFmpeg is not installed. Run: winget install ffmpeg  then restart the server."); return

            # Get video dimensions with ffprobe
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

            # libx264 requires width and height to be divisible by 2
            w = w if w % 2 == 0 else w - 1
            h = h if h % 2 == 0 else h - 1

            # Step A: normalise the thumbnail to a known-good JPEG at the right size.
            # This cures "Invalid argument / Could not open encoder before EOF" which
            # happens when the thumbnail blob was a PNG, WEBP, or had odd dimensions.
            norm_thumb = os.path.join(tmpdir, "thumb_norm.jpg")
            norm_cmd = [
                "ffmpeg", "-y",
                "-i", thumb_path,
                "-vf", "scale={w}:{h},setsar=1".format(w=w, h=h),
                "-frames:v", "1",
                norm_thumb
            ]
            norm_result = subprocess.run(norm_cmd, capture_output=True, timeout=60)
            if norm_result.returncode != 0:
                # If normalise failed, just copy the original and let FFmpeg try
                norm_thumb = thumb_path

            # Step B: prepend the normalised thumbnail (1/30 s) then the original video
            cmd = [
                "ffmpeg", "-y",
                # Input 0: thumbnail as a 1-frame still
                "-loop", "1", "-framerate", "30", "-t", "0.0334", "-i", norm_thumb,
                # Input 1: original video
                "-i", video_path,
                "-filter_complex",
                (
                    "[0:v]scale={w}:{h}:force_original_aspect_ratio=disable,"
                    "setsar=1,fps=30,format=yuv420p[th];"
                    "[1:v]scale={w}:{h}:force_original_aspect_ratio=disable,"
                    "setsar=1,format=yuv420p[vid];"
                    "[th][vid]concat=n=2:v=1:a=0[v]"
                ).format(w=w, h=h),
                "-map", "[v]",
                "-map", "1:a?",
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart",
                out_path
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=300)
            if result.returncode != 0:
                err = result.stderr.decode("utf-8", errors="replace")[-800:]
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
        msg = json.dumps({'error': message}).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(msg)))
        self.send_cors()
        self.end_headers()
        self.wfile.write(msg)

    def _serve_html(self):
        # ETag check — return 304 if browser already has this version
        if self.headers.get('If-None-Match') == _HTML_ETAG:
            self.send_response(304)
            self.end_headers()
            return
        accept_gz = 'gzip' in self.headers.get('Accept-Encoding', '')
        body = _HTML_GZ_BYTES if accept_gz else _HTML_BYTES
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('ETag', _HTML_ETAG)
        self.send_header('Cache-Control', 'no-cache')  # revalidate but cache
        if accept_gz:
            self.send_header('Content-Encoding', 'gzip')
        self.end_headers()
        self.wfile.write(body)

    def _proxy(self, method, url, body=None, timeout=30, cacheable=False):
        """
        Proxy a request to an upstream API.
        cacheable=True: cache GET responses in RAM for CACHE_TTL seconds.
        Sends gzip-encoded responses to the browser when it supports it.
        """
        # Check RAM cache for GET requests on public endpoints
        if cacheable and method == 'GET':
            cached_data, cached_ct = _cache_get(url)
            if cached_data is not None:
                self._send_data(200, cached_ct, cached_data)
                return

        fwd_headers = {}
        for h in ('Authorization', 'Content-Type', 'Accept'):
            v = self.headers.get(h)
            if v:
                fwd_headers[h] = v
        if 'Accept' not in fwd_headers:
            fwd_headers['Accept'] = 'application/json'
        # Ask upstream to gzip the response to reduce transfer size
        fwd_headers['Accept-Encoding'] = 'gzip'

        try:
            req = urllib.request.Request(url, data=body, headers=fwd_headers, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                ct  = resp.headers.get('Content-Type', 'application/json')
                enc = resp.headers.get('Content-Encoding', '')
                # Decompress if upstream sent gzip (so we can cache plaintext
                # and re-compress for the browser ourselves)
                if enc == 'gzip':
                    try: data = gzip.decompress(raw)
                    except Exception: data = raw
                else:
                    data = raw
                if cacheable and method == 'GET':
                    _cache_set(url, data, ct)
                self._send_data(resp.status, ct, data)
        except urllib.error.HTTPError as e:
            data = e.read()
            self._send_data(e.code, 'application/json', data)
        except Exception as e:
            msg = json.dumps({'error': str(e)}).encode()
            self._send_data(502, 'application/json', msg)

    def _send_data(self, status, content_type, data):
        """Send a response, gzip-compressing it if the browser supports it."""
        accept_gz = 'gzip' in self.headers.get('Accept-Encoding', '')
        is_compressible = any(t in content_type for t in ('json','text','javascript'))
        if accept_gz and is_compressible and len(data) > 512:
            body = gzip.compress(data, compresslevel=6)
            gz   = True
        else:
            body = data
            gz   = False
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        if gz:
            self.send_header('Content-Encoding', 'gzip')
        self.send_cors()
        self.end_headers()
        self.wfile.write(body)


# ── Start server ──────────────────────────────────────────────────────────────
class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """Handle each request in its own thread so one slow upload never blocks browsing."""
    daemon_threads = True
    allow_reuse_address = True

if __name__ == '__main__':
    # Pre-compress HTML once at boot — every request after is just a memcpy
    _HTML_BYTES    = HTML.encode('utf-8')
    _HTML_GZ_BYTES = gzip.compress(_HTML_BYTES, compresslevel=9)
    _HTML_ETAG     = '"' + hashlib.md5(_HTML_BYTES).hexdigest() + '"'

    server = ThreadedHTTPServer(('localhost', PORT), Handler)
    print(f"""
╔══════════════════════════════════════════╗
║           Racc.net Local Server           ║
╠══════════════════════════════════════════╣
║  Open in Firefox:  http://localhost:{PORT}  ║
║  Each request runs in its own thread     ║
║  Press Ctrl+C to stop                    ║
╚══════════════════════════════════════════╝
""")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
        server.server_close()
