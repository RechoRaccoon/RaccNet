"""
RaccNet local proxy server
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
<title>RaccNet</title>
<script src="https://cdn.jsdelivr.net/npm/htm@3.1.1/preact/standalone.umd.js"></script>
<link href="https://fonts.googleapis.com/css2?family=Roboto:wght@300;400;500;700&display=swap" rel="stylesheet"/>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{--accent:#00FF07;--accent-dim:rgba(0,255,7,0.12);--accent-dim-dark:rgba(0,255,7,0.08);--accent-solid-dim:#003300}
html,body{height:100%}
body{background:var(--page-bg,#0f0f0f);color:#f1f1f1;font-family:'Roboto',sans-serif;overflow-x:hidden}
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
    var a=localStorage.getItem('raccnet_accent');
    if(a){var s=document.createElement('style');s.id='raccnet-accent-style';s.textContent=':root{--accent:'+a+';}';document.head.appendChild(s);}
    var bg=localStorage.getItem('raccnet_bg');
    if(bg){
      var de=document.documentElement;
      de.style.backgroundImage='url('+bg+')';
      de.style.backgroundSize='cover';
      de.style.backgroundPosition='center';
      de.style.backgroundAttachment='fixed';
      de.style.setProperty('--page-bg','transparent');
    }
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
          <img src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAC4AAAAkCAYAAAD2IghRAAABCGlDQ1BJQ0MgUHJvZmlsZQAAeJxjYGA8wQAELAYMDLl5JUVB7k4KEZFRCuwPGBiBEAwSk4sLGHADoKpv1yBqL+viUYcLcKakFicD6Q9ArFIEtBxopAiQLZIOYWuA2EkQtg2IXV5SUAJkB4DYRSFBzkB2CpCtkY7ETkJiJxcUgdT3ANk2uTmlyQh3M/Ck5oUGA2kOIJZhKGYIYnBncAL5H6IkfxEDg8VXBgbmCQixpJkMDNtbGRgkbiHEVBYwMPC3MDBsO48QQ4RJQWJRIliIBYiZ0tIYGD4tZ2DgjWRgEL7AwMAVDQsIHG5TALvNnSEfCNMZchhSgSKeDHkMyQx6QJYRgwGDIYMZAKbWPz9HbOBQAAALQUlEQVR42r2YWayex1nHf8/MvOu3n/0cO95jJ3FcorRNSWkqpVFa1KallUCqBBLiFhBCgLhDCr3johIXSCC4QNxwA0gQqkYiUhoSUqlLGmd14iV27GP7rN8551vfbWa4+I6PkygJtkM6V6/eZeZ5n/nP//k/fwE8dzCmmw2WptrEkSErSoZ5wVKnwUq3x6W1LpW1e+/uW5jj6MH9PP+TX6BEcP6OlnzfMLf7QRgYPnvsIAdmO4iAc47COvIKRkXBkaU5lmY6nLu2jtGKLC946DMn+Y1vfIV+lvPyK2/w/zHkVjN+48W7l+Y4PD+FUoowDPCA94pAK6xzZKUlL0vmW3WMAus8lXXE7RomrPFvTz/HMM/xnzDrcjtQERG893Qadb5471ECrXDeEwYGvMVaj4hie5SRRgatBIVHBIqixHtYGY05ffYy1rpPFLi6nZe994RBwBdOHKIRB3hX4X2F9x4RQQTAEWiF9x6tNGu9Ed3hmFHf8dpv9xnd38Bbf5srf8LARYTvPvE4jSQkr0ocDu89SgA8anKBUVBUFiOORqTJi4rOdMrJH06zf7qNrhtwv8TAG406rXqKrxzDYcZgXOCcY1SUiKgJNJQQBppxlpN5xb0n72O60+H1y1ep7YA9sw438C2fMqvcoLClqRmSUwlXLuzg9gXMXk9IE83mzpBer0+zFpMmEaHRBFrxzW98jYXFBax7jmazQTbsMW9TPn/gID8+887NH7iDoYEn/09s72ZntDMm+G6d07+zzsnnplBjhzFCktZ49CtfJssLVtc2J7DRghXFdneTz33uAY4eO8Lqyjpb21t0GjG1OGJlq3eHVeQ2oCJKyHxBfCXgWLZAsOZwxrPdHxEGhnuO382XvvgQnWYdROGc541XX2d6aooXX/wZ//n0C7z6xnkq61jvjXl3dRPnPuWMT+Ayob65fpvDzJFf3MZoodXpENfq1Oop169eY3V1ldBolMCxI4cYDEr+efM5/uiP/5IH7n6A5//7GdI4JYkj1rb7WOc+3cC99whweW2FQIRF3WBU5PzWb36HUydPcO3dZdK0xoED+7iyfBVEKIucV5av0Tx1F7XHavzZr/0pP3rmB6x3N+mNM9a3e59+xm/QoTiojQ375ptYBxcvL/POhTVeN+s8c/5n9M/1SRNFUVVUo5KxVAz2C5tbq5xdfY2cAf6dAZvZiO3B6I51y20H7oGDS1O0kpDCed46e56/+Ku/5cTXH+S0fYPRpkJt92iqNnG7wyPffgi5arn+Dxe4Nl6m6WL6VzaJk5BRVjDMckTk06HD98NF2O4NWY0ikihiaWEem23w2MKjfO/+71H7seXhx79E++FZ3lq9zuuzOd2ZBuZ8g8VrLYbLG5zf7nF1rUtWFHvzfqQekd0r798nUMxHfqEErP8QavRcXO1yz12LGC30c8+br/8PL6y9QPv7GUECc3++wLX6iLfeWMesCDYSRqOM5UGPuamUQGlqaUxWFB8tltTufTdZVdilfTW5dxMq+gMzfAz0AqMJjcYhtNOE7naXzkKNcqeku7pD0Sg5OxizvxbhVkace7dLd6PHAR8QhQFJZLi6uUOWFx9bPJo+wdwVolsh1aBEpjSM/G64wpMIN7WDAnMgJP5WHTljP5SuvPesbveZaTWY7dTprm6w8/YW09N1tqqMt0+vsF3k9FeGXL60RWEddZPQzD0vX77GxcurbA/H7y/5MqkVgqBQzH9rnoVfnyf8/SZhO2L6Skr0LzNUxmFfyicZFyB4PIUthx974kdqLPzJEod/NEV30KNydnKARCYL+MlBne/UWVnrUsw02Gwr7NUNalox/OwiUanYwCKLDQIMGZ6hc8jnD8B8g3JjgB0Xk+BFJju8u8uzx6aQZ9usPZExXqw49HcNooWQra8WLG00KX+ao2VePZn+9Sz77pnh+Ok5ol+pUfyhoX+yJOxoWs9FbOa9D2WYlW6PTW/pPPEAVStmam1EPzWEGyN22gabGCTz5GVFEMXYuZQwDGjunyHe36Z/bgVXOvAek0ZM3X8X+6Ip5u5tce13Rzg8Uy+EdP4rxLY9ndMBtacUj37tCcy+g4sUj0eM/ylDm5TBD0LK1OOoSF5VpO0mTd2gDBWiFW6QYwc5pXcIsP/L9xBP17Dn19k+2GLzYJNkpyTMHWNxiBKCJMQYA96TFzk4RzxdZ/oLR1l99gzpYpvOI8eZdzFTtk+54zjw9zWStzVTT4X4GtR7Iap0nPrmY/zH0z/EHNfHyH5vk6wVIjg6/6gZ/AEEPaF1OsJHlkO/+gDDlqboDgmNYfD8BVavr2HaKc0TC5T9MW6hzpYRgtLjZ+uUtkL6A7wGrTVFVeKcw+FwClyWM3VyH+OLmzROLqFqAVnmcWIxQ8XB79dAedy0IMA4GxO2Ul5+7TXOnXkTc/i+E6y++grFRomvCfv/JqT18xAPRD3N+ryw04JqOCKpx3itCO9b5Gil8admMbWYfDBGKYU4oRKPG48pigKvQIugEIwxjMcjlNZUVQXeE8UB0w8fRYwChGImYnCsRXJph6JWUoRC0ctZX++y0etTVhXWuUkFbzQafqZVJwkC6nFCvZ6ghwpVefpTcPV4wtgXJCpCKcEVlnxUkF4a0HhwH2qpSbY9AOeJGilVWYID6ytQGm8dSgQvYCsL3uPHJSYO0UlEtjWgrCriNMFbi48UPiuRwkMcsPnSJbo/v3iT3pXCOYfp9/v0+/29B2kcIUY49OBx7JE2lSrRucekAcPBAOMmFWB8sE6tGeGySRekLBhtMMZQlhWu8CgRxBgcFltajDEIinJo0VojAjrQlLbEiiUIDFQeqwOIPUEQ0FjosCWX9mjY7dKzea9O8N4zynIAejMhJgadQVJvUFTFLhsKaT2lHJd453DW4yuH0oayKBALYgQTGLRM5L71Clc6lANbVjjnJnARj8MTaIMrKorA4wTEeaI4ohzmbPzi4kQSfEDPmA/qBFECztN7bZmlr5/C65CyKIl0iAkUYgSlFfkoo8wKdC3EVpbQBAy3+lTDDBMFgEywaBTeOarCUuUlrqoIkwQqAe1xzqIETGjw3lPlBSaJKLpDlp86Tb4z4ma9/xiR5Xe7ksG7G4zObpAemcaEBuUnh8xZh/Mer6HKKkwSTCqeKMI4xHuHLS2+dDhnQSYNNKIQJUSNFKpJY+JFCJII2V3TW0dneprR8hYX//0lylGOKNmL6bYMoXS+Rfu+fdSOTKPTiCrLGY5GKAuxiSGawCFMIkQJyugJ3xcVIhOl5JxFK40TT5HnjDcG5Nd3qB+ewYQGtEYHClOPya/1uPSvP6UcF3sG1O07WSJ7WxQ0EmrHZmjds0jQTrFVhVKGvD8iDiLCRkRWZqjAUG800SJU3iJu91BZR1Va8uGYYpTTffECoytdlFGIUpgkIF1oM7i8Ocn0xwR9axbcjUOxO0mURDQOz2LqEUHlkU5C7cQipSvxeLQxmDBAK4W1DmM0glAWBVVRYQtLkRVkqz02nn0L/2E95y0Yg+YWuof3mDdCPs7J31zee3zgq59BJyHj3hgVGEQJzjvEepQotGjKsqCsKozWOLEIQjLTJJ5pMF7bmWh/f2OD/S1ZFrfuZPmbPyFKEK1AwDRjFBCEIVorKmux1YTylFIUZbE3gXMOBJQWdKAJZ+rvm9s7f8s+yx1Zj955vHWIF4IgxHqLZsI6omTC06XFV3bSTGlNvdaclPWyABF0YDBp+MvxDt8raQHqcYOarlG5Co8QRwlhEGKUAaOoxO0dMr/r5FprUUqBkokev0ML8ROZvZ3pWeq2hhcIwxDrLEYUSisEj7UW6x1VVTIc9nHOo7RCKSFQhnCk73jt/wUUOYx5Z8vu4gAAAABJRU5ErkJggg==" width="46" height="36" style="display:block;image-rendering:auto"/>
          <span style=${{fontSize:20,fontWeight:800,color:'var(--accent)',letterSpacing:-0.5}}>
            RaccNet
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
        <div style=${{color:'#aaa',fontSize:12,marginBottom:6}}>RaccNet</div>
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
        <img src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACgAAAAgCAYAAABgrToAAAABCGlDQ1BJQ0MgUHJvZmlsZQAAeJxjYGA8wQAELAYMDLl5JUVB7k4KEZFRCuwPGBiBEAwSk4sLGHADoKpv1yBqL+viUYcLcKakFicD6Q9ArFIEtBxopAiQLZIOYWuA2EkQtg2IXV5SUAJkB4DYRSFBzkB2CpCtkY7ETkJiJxcUgdT3ANk2uTmlyQh3M/Ck5oUGA2kOIJZhKGYIYnBncAL5H6IkfxEDg8VXBgbmCQixpJkMDNtbGRgkbiHEVBYwMPC3MDBsO48QQ4RJQWJRIliIBYiZ0tIYGD4tZ2DgjWRgEL7AwMAVDQsIHG5TALvNnSEfCNMZchhSgSKeDHkMyQx6QJYRgwGDIYMZAKbWPz9HbOBQAAAI8ElEQVR42rWYSYxlVRnHf2e6w3v1ql7Vq6oeqye7GJVRxgBiRJCFQTe6cmNM3LgwLo0xJK7cGuPGhUaNxjgkCBpCIgKJAoJoy0x30w30UNU1vPm9e++ZXLyypTEtXQTP9p57zv/7/t//G44AIttYi81p9s7PkRpNdzQmNQoJHF9ZpzMYnd93z5238tJrx1hZW0cIQYzbuub8EpcCUABaKW5Y3sfBHS2IERcClYfSeZxzCODMZhcBVM7zhc/dTykiP/jxrxgNhh8YpLhUDzbyjH0Lsyw0G0zXa4Q4AS1jxMdId1TQatTIjSJG6BcjZhuzvPz6aY6cfQtnA5HtA5SXZIUQ9McFZ9o95hoNJCCJGAkQUCKSGc2orOiMCs71evgicjScwt8iUV59IHCXBPDf1Oxf2s3d11+FEoEQHBBAgJRiEgISiIF6ahiWFdYGTD0hvbJFvTk14UqI/w9AgMv272dxZppBf8S4sjjnCSGilURIgVGSUVkxKEoO7t5JOxTkRyU7n3aM6wVEEP8PimOMCARLV+/gXOxxrmGRwpAYw1q7x0anR4yBxCgUcN/993LvZ+5j/+49qNmUrAhcm+/cOmv7FCvgwf9pgZLEGKkfrPHaN3ssvJwxs5GACSzt38flVyzzzumzWGvRWpHmOVrAHbffjFSKd06dZq6Rk2jNaqe/bZbfXyRbZm++0eXKsI/6OQgysNnuMt2Y4pabbmTv7h0QwXvPsdePEQU88vhTvPjqO2y0e/QKy7lOf5Jm4odNcZicePr1FfIfRRKvkCpy3XXX0VqYZ31lnTzJyPOEPDEcWtrNn174O8cPFHznez/g4EeuoD8csW/HAlqpbUfh+wPcEkq3HPLnh/9GFg0oyY03XkssK5448iyzO+aZnZ5FasXxk29yutOnszyivWeDuz75CUIIrHZ6OO+3TfH7xuC7lVwn4dDOOQprefa5I1z9ift5YfYtXnjyKNKOYBRJZMaupRaDEwMe/8OjbOzdpHk8EnWg0x9TWrctgHo7m2u5oTuaXNKYneHrX/4Gtqv4CT9l7rl59t++i8G0ILupxdrPj1A8e450ZHm1s8Zqu0/l3LbrmryoX8V7Uo0QvLPWpjsuydME6TxPPPMLug+dRj60zjWfvZpdXzvM8daA50+coNqnGfuC7voqo8qz3i8orUNcLNDke8CJd1Ms3rMhXjwe5xt1TKIxXtLV63RX1hj/cwwHIidESTpwcGbMa2+uI0/3WKxlZIlhvT9kVJQXPTiLBtkyBBdBR4TfKj6Id5VJASKXZPfU8Y8VVIW9wO3/Lns3LO/n0M45hu0Rh5cPcabf5c2NNuHaJrPTKWsrA4bA0kag99YaKxsd1tp9wrspFJMSSYSFTy8wf2WT3p0evjXCf0nTESPG396YeFDtNKS35YjjATmnWfz+Xg68OEv7TI9A3BLJxBYhBM1aytpGl+HhFmu9TWY6BdXl84QkYd17aNWRUTGoLO7wPP5gC9se4vrFf9Xj+aVZ1JNznH2gIOlLDjzSpPtFR1PWqP1Vo/RN6YP1ny2w/PICu/J5xt819G605Imh8ceEzap3AeVCCta6A6o9Tabv/SjpxoiQamxhKVWkaiRQeAICPT9FzDUzi01qB1r03lgllBapFXPXH+BAaxc79zQ4+5UxyRnJ3h/W0GcFU6c0c49m3PDxO9GH6kuM1gP2tMV9PmP0gCRgmfqLJpmdZnBIY40gVJ74dodur4/Uij13XY6xjnKxzqBVAykx4wChgEShpEQA3jnG/QGmkTF/8yHOPfU683dfQXPvAruOdGAYufKrTfITiuSURDYF4SXL1Z+6m1///mHUvdd86sHaL4dkzpC/qBAzAl0K9vy4hq0pxB1LyJmM6QOLmDQhnh1QO7zA3PX7cJXDzmQQI0oKSuXxzqKUQkpJaUu8d0itCc6Tz06h8oR8dxOXCHIvyTZK8uMSUQRcPbDe6bJe9PnHK69w6u23EbfeeltU4x5GKlKZoEpJEIESx9tXJAxrkAWN1orxoKL+0gbZtbuQy3OM232SJCVIiD7gvSNKkEGglMI6i/d+okgpSLKUUXuIUgqhBUpL0rMjYmGxiWLlmWO0VzYvTNTPPPM0UggSo5mZmiLLDXNzs/jL5xk1PMoGSBTjcUEInt7+GulCPqHcBYSBPE2x1m71pAIk+OiRUqG1wfdKdG5QWiEEOOHJVIoIMNhdgxBJdEJ4I4cVkHLSQcUY0VJKQggUlaXYbAMwXm4xtcOg+54kzymqghghSVOCMkQp8FWFCBMrgwsoqYgGpJCT1GQjIQRklNgY8cETLCijwHlsdCAFoookaYLrjalWeyAgxHBemDqEcIFCiTA8tkrrY3swjSlcZclUQkgCWmvG/RG2qJC5JvqIt47euU200QgpEVIilcRZhystrrToJCFqQ5SBGDxKTwRUlRUyMxTtAad+9w+q3vi/yt2FzcLWBzcoSdKUdL6OSjVGGQiRECPOWhQKlShiiOjEgBR4H/ClxZUVrrKw1aapVCORSK1QtQSdJiijEQhqjSnCWsHJ3z6PHZaTHBm3MXamzTrNq3ZTX15ETyWMR2N86chMRlQRrTQ6mwBMspTgwlZ1iMQQEFJRViWjjQHDo6vkO6ZRiUEaSTo3RRhWnPzN89hRedG5+eIAhTjfTavM0Di8SOuaJWKqET5SjUrqrWmCCtjgqU9NkRiD9Q7JJMhdZanGFeW4pP38SQavnj1/vKmlBOvx1l1w1/YGd7FV4LZ+NsagGylJELTuvIz0UIvxaEiSpiitttQHiUmwtsJZRygdVWGx3TGrj72ML+3kynhpTwfy/drp824XAmst480B3U4fNZ0hBEg9Gcp9CMQQ0EoRvMc7RwgeqRSCSNLISefqW54S/2nn4ofwsnDBzCgEQkp0akCAUZoQI947nHWThB08aZJhdIJ1FUiBSjR6Ov9Pqxc/rKnuva1/jNTzKVKZYr1DS02WZpM8KAU+BqQQoATGGKy1E6OUJDrHdpfmA6zW7CK5yyi0R0SFFIDW2OBxwSGEpirGxBARSiIRGKFQw+0/ffwLBcRekj+VriwAAAAASUVORK5CYII=" width="40" height="32" style="display:block;image-rendering:auto"/>
        <h2 style=${{color:'#f1f1f1',fontSize:20,fontWeight:600}}>Sign in to RaccNet</h2>
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
    if(visible >= videos.length && props.onLoadMore) {
      props.onLoadMore();
    } else {
      setVisible(function(v) { return v + GRID_PAGE; });
    }
  }, [visible, videos.length]);

  const hasMoreOrServer = hasMore || !!props.hasMore;
  useScrollLoad(hasMoreOrServer && !props.loading, loadMore);

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
const VOLUME_KEY  = 'raccnet_volume';
const ACCENT_KEY  = 'raccnet_accent';
const BG_KEY      = 'raccnet_bg';
const FILTER_KEY  = 'raccnet_filter'; // 'all' | 'sfw' | 'nsfw'
function loadAccent(){ try{return localStorage.getItem(ACCENT_KEY)||'#00FF07';}catch(e){return '#00FF07';} }
function saveAccent(v){ try{localStorage.setItem(ACCENT_KEY,v);}catch(e){} }
function loadBg(){ try{return localStorage.getItem(BG_KEY)||'';}catch(e){return '';} }
function saveBg(v){ try{localStorage.setItem(BG_KEY,v);}catch(e){} }
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
function applyBg(url){
  var html=document.documentElement;
  if(url){
    html.style.backgroundImage='url('+url+')';
    html.style.backgroundSize='cover';
    html.style.backgroundPosition='center';
    html.style.backgroundAttachment='fixed';
    // Override the body background CSS rule
    html.style.setProperty('--page-bg','transparent');
  } else {
    html.style.backgroundImage='none';
    html.style.removeProperty('--page-bg');
  }
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
  const bgRef = useRef(null);

  function applyAndSaveAccent(c){
    setAccent(c); setAccentIn(c);
    saveAccent(c); applyAccent(c);
  }
  function applyAndSaveFilter(f){
    setFilter(f); saveFilter(f);
  }
  function onBgFile(e){
    var file=e.target.files&&e.target.files[0];
    if(!file) return;
    var reader=new FileReader();
    reader.onload=function(ev){
      var dataUrl=ev.target.result;
      try{ saveBg(dataUrl); }catch(err){ console.warn('BG too large for localStorage, using session only'); }
      applyBg(dataUrl);
    };
    reader.readAsDataURL(file);
  }
  function clearBg(){
    saveBg(''); applyBg('');
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

    ${section('Background Image', html`<div>
      <div style=${{color:'#aaa',fontSize:13,marginBottom:12}}>Set a custom background image. The image is stored locally on your device.</div>
      <div style=${{display:'flex',gap:10,flexWrap:'wrap'}}>
        <button onClick=${function(){bgRef.current&&bgRef.current.click();}}
          style=${{background:'var(--accent)',color:'#000',border:'none',padding:'9px 18px',fontSize:13,fontWeight:700,cursor:'pointer',borderRadius:0}}>
          Choose Image
        </button>
        <button onClick=${clearBg}
          style=${{background:'none',border:'1px solid #333',color:'#aaa',padding:'9px 18px',fontSize:13,cursor:'pointer',borderRadius:0}}
          onMouseEnter=${function(e){e.currentTarget.style.color='#f1f1f1';}}
          onMouseLeave=${function(e){e.currentTarget.style.color='#aaa';}}>
          Clear Background
        </button>
        <input ref=${bgRef} type="file" accept="image/*" onChange=${onBgFile} style=${{display:'none'}}/>
      </div>
      <div style=${{marginTop:12,display:'flex',alignItems:'center',gap:12}}>
        <span style=${{color:'#aaa',fontSize:13,width:90}}>Opacity</span>
        <input type="range" min="0" max="1" step="0.05"
          defaultValue="1"
          onInput=${function(e){
            document.documentElement.style.opacity='1'; // html el always full
            // Apply via a pseudo-overlay — adjust body background opacity
            var v=parseFloat(e.target.value);
            document.body.style.background='rgba(15,15,15,'+(1-v)+')';
          }}
          style=${{flex:1,accentColor:'var(--accent)'}}/>
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

    ${section('About RaccNet', html`<div style=${{color:'#555',fontSize:13,lineHeight:1.7}}>
      RaccNet is a Bluesky video platform built on the AT Protocol.
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
      ${tab==='Content'&&contentSub==='Videos'?html`<${VideoGrid} videos=${props.videos} loading=${false} onWatch=${props.onWatch} onChannel=${props.onChannel} onLoadMore=${props.onLoadMore} hasMore=${props.hasMore}/>`:null}
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
    var bg=loadBg(); if(bg) applyBg(bg);
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

      // Load first page immediately, store cursor for lazy loading
      const seen = new Set();
      const vids = [];
      const url = PUB_PROXY+'/app.bsky.feed.getAuthorFeed?actor='+encodeURIComponent(actor)
        +'&limit=30&filter=posts_with_media';
      let fR;
      try { fR = await api(url); } catch(e){ fR = null; }
      if(fR && fR.ok){
        let fd;
        try { fd = await fR.json(); } catch(e){ fd = {}; }
        (fd.feed||[]).forEach(function(item){
          const p = item.post;
          if(p && isVid(p) && !seen.has(p.uri)){ vids.push(p); seen.add(p.uri); }
        });
        setChannelVideos(vids.slice());
        // Store cursor on channelData for lazy loading more
        if(fd.cursor) setChannelCursor({actor:actor, cursor:fd.cursor, seen:seen});
        else setChannelCursor(null);
      }

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
      ${page==='channel'?html`<${ChannelPage} data=${channelData} videos=${channelVideos} loading=${channelLoading} session=${session}
        onBack=${function(){navTo(channelFromPage||'search');}} onWatch=${handleWatch} onChannel=${handleChannel}
        onLoadMore=${async function(){
          if(!channelCursor) return;
          var cc=channelCursor;
          var url=PUB_PROXY+'/app.bsky.feed.getAuthorFeed?actor='+encodeURIComponent(cc.actor)+'&limit=5&filter=posts_with_media&cursor='+encodeURIComponent(cc.cursor);
          var r=await api(url).catch(function(){return null;});
          if(!r||!r.ok) return;
          var fd=await r.json();
          var newVids=[];
          (fd.feed||[]).forEach(function(item){
            var p=item.post;
            if(p&&isVid(p)&&!cc.seen.has(p.uri)){newVids.push(p);cc.seen.add(p.uri);}
          });
          setChannelVideos(function(prev){return prev.concat(newVids);});
          if(fd.cursor) setChannelCursor({actor:cc.actor,cursor:fd.cursor,seen:cc.seen});
          else setChannelCursor(null);
        }}
        hasMore=${!!channelCursor}
      />`:null}
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
║      RaccNet Server      ║
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
