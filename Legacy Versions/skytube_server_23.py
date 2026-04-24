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
<title>Racc.net</title>
<script src="https://cdn.jsdelivr.net/npm/htm@3.1.1/preact/standalone.umd.js"></script>
<link href="https://fonts.googleapis.com/css2?family=Roboto:wght@300;400;500;700&display=swap" rel="stylesheet"/>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
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
  if (!isVid(post)) return null;
  const embed = post.embed, author = post.author, rec = post.record;
  const title = (rec && rec.text) || 'Untitled video';
  return html`<div style=${{cursor:'pointer'}} onClick=${function(){props.onWatch(post);}}>
    <div style=${{width:'100%',paddingBottom:'56.25%',borderRadius:0,overflow:'hidden',background:'#1a1a1a',position:'relative'}}>
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
    display:'flex',alignItems:'center',padding:'0 16px',gap:16,zIndex:200,borderBottom:'1px solid #272727'}}>
    <div style=${{display:'flex',alignItems:'center',gap:12,flexShrink:0}}>
      <button onClick=${props.toggleSidebar}
        style=${{background:'none',border:'none',color:'#f1f1f1',padding:8,borderRadius:'50%',display:'flex',alignItems:'center',justifyContent:'center'}}
        onMouseEnter=${function(e){e.currentTarget.style.background='#272727';}}
        onMouseLeave=${function(e){e.currentTarget.style.background='none';}}>
        <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path d="M3 18h18v-2H3v2zm0-5h18v-2H3v2zm0-7v2h18V6H3z"/></svg>
      </button>
      <div onClick=${props.onHome} style=${{display:'flex',alignItems:'center',gap:8,cursor:'pointer',userSelect:'none'}}>
          <img src="data:image/png;base64,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" width="46" height="36" style="display:block;image-rendering:auto"/>
          <span style=${{fontSize:20,fontWeight:800,color:'#00FF07',letterSpacing:-0.5}}>
            Racc<span style=${{color:'#00cc06',fontWeight:400}}>.net</span>
          </span>
        </div>
    </div>
    <div style=${{flex:1,display:'flex',justifyContent:'center'}}>
      <form onSubmit=${submit} style=${{display:'flex',width:'100%',maxWidth:600}}>
        <input value=${props.input} onInput=${function(e){props.setInput(e.target.value);}}
          placeholder="Search"
          style=${{flex:1,height:40,border:'1px solid #3f3f3f',borderRight:'none',
            borderRadius:0,background:'#121212',color:'#f1f1f1',padding:'0 16px',fontSize:16}}
          onFocus=${function(e){e.target.style.borderColor='#00FF07';}}
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
          <div onClick=${function(){props.onMyChannel&&props.onMyChannel();}} style=${{cursor:'pointer'}}>
            <${Avatar} src=${props.session.avatar} size=${32}/>
          </div>
          <button onClick=${props.onLogout}
            style=${{background:'none',border:'1px solid #3f3f3f',color:'#f1f1f1',padding:'6px 12px',borderRadius:0,fontSize:13}}
            onMouseEnter=${function(e){e.currentTarget.style.background='#272727';}}
            onMouseLeave=${function(e){e.currentTarget.style.background='none';}}>Sign out</button>
        </div>
      ` : html`
        <button onClick=${props.onLogin}
          style=${{display:'flex',alignItems:'center',gap:8,background:'none',border:'1px solid #00FF07',
            color:'#00FF07',padding:'6px 16px',borderRadius:0,fontSize:14,fontWeight:500}}
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
      borderRadius:0,justifyContent:props.open?'flex-start':'center',fontSize:14,
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
        <a href="https://bsky.app" target="_blank" rel="noreferrer" style=${{color:'#00FF07',fontSize:12}}>Bluesky AT Protocol</a>
        <div style=${{color:'#00FF07',fontSize:11,marginTop:8}}>✓ Running via local proxy</div>
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
        <img src="data:image/png;base64,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" width="40" height="32" style="display:block;image-rendering:auto"/>
        <h2 style=${{color:'#f1f1f1',fontSize:20,fontWeight:600}}>Sign in to Racc.net</h2>
      </div>
      <p style=${{color:'#aaa',fontSize:13,marginBottom:24}}>Connect your Bluesky account for a personalized video feed.</p>
      <form onSubmit=${submit}>
        <div style=${{marginBottom:16}}>
          <label style=${{display:'block',color:'#aaa',fontSize:13,marginBottom:6}}>Handle or Email</label>
          <input value=${handle} onInput=${function(e){setHandle(e.target.value);}} placeholder="you.bsky.social" style=${iSt}
            onFocus=${function(e){e.target.style.borderColor='#00FF07';}} onBlur=${function(e){e.target.style.borderColor='#3f3f3f';}}/>
        </div>
        <div style=${{marginBottom:8}}>
          <label style=${{display:'block',color:'#aaa',fontSize:13,marginBottom:6}}>App Password</label>
          <input type="password" value=${pw} onInput=${function(e){setPw(e.target.value);}} placeholder="xxxx-xxxx-xxxx-xxxx" style=${iSt}
            onFocus=${function(e){e.target.style.borderColor='#00FF07';}} onBlur=${function(e){e.target.style.borderColor='#3f3f3f';}}/>
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

// ── Persistence helpers ───────────────────────────────────────────────────────
const PAGE_KEY = 'raccnet_lastpage';
function saveLastPage(p){ try{localStorage.setItem(PAGE_KEY,p);}catch(e){} }
function loadLastPage(){ try{return localStorage.getItem(PAGE_KEY)||'trending';}catch(e){return 'trending';} }

// ── Default (logged-out) feed list ────────────────────────────────────────────
const DEFAULT_FEEDS = [
  {uri:'at://did:plc:z72i7hdynmk6r22z27h6tvur/app.bsky.feed.generator/whats-hot',    displayName:"What\u2019s Hot"},
  {uri:'at://did:plc:z72i7hdynmk6r22z27h6tvur/app.bsky.feed.generator/with-friends', displayName:'Popular with Friends'},
  {uri:'at://did:plc:tenurhgjptubkk5zf5xxn4wv/app.bsky.feed.generator/discover',     displayName:'Discover'},
];

// ── Trending Page ─────────────────────────────────────────────────────────────
function TrendingPage(props) {
  const [period, setPeriod] = useState('24h');
  const [videos, setVideos] = useState([]);
  const [loading, setLoading] = useState(true);

  const PERIODS = [
    {id:'24h',   label:'24 Hours'},
    {id:'week',  label:'This Week'},
    {id:'month', label:'This Month'},
    {id:'year',  label:'This Year'},
  ];

  useEffect(function(){
    let cancelled = false;
    async function load() {
      setLoading(true); setVideos([]);
      const now = Date.now();
      const cutoffs = { '24h': 86400000, 'week': 604800000, 'month': 2592000000, 'year': 31536000000 };
      const cutoff = now - cutoffs[period];
      const seen = new Set(); const found = [];
      function add(posts){
        (posts||[]).forEach(function(p){
          if(!p||!isVid(p)||seen.has(p.uri)) return;
          const t = new Date(p.indexedAt).getTime();
          if(t >= cutoff){ found.push(p); seen.add(p.uri); }
        });
      }
      try {
        // Fetch from multiple sources to get enough video posts
        const terms = ['video','vlog','watch','clip'];
        const batches = await Promise.all(terms.map(function(t){
          return api(PUB_PROXY+'/app.bsky.feed.searchPosts?q='+encodeURIComponent(t)+'&limit=100&sort=top')
            .then(function(r){return r.ok?r.json():{posts:[]};})
            .then(function(d){return d.posts||[];})
            .catch(function(){return [];});
        }));
        batches.forEach(add);
        // Also pull What's Hot
        const hotR = await api(PUB_PROXY+'/app.bsky.feed.getFeed?feed=at://did:plc:z72i7hdynmk6r22z27h6tvur/app.bsky.feed.generator/whats-hot&limit=100').catch(function(){return null;});
        if(hotR&&hotR.ok){ const d=await hotR.json(); add((d.feed||[]).map(function(i){return i.post;})); }
      } catch(e){ console.error('trending:',e); }
      // Sort by likes descending
      found.sort(function(a,b){ return (b.likeCount||0)-(a.likeCount||0); });
      if(!cancelled){ setVideos(found); setLoading(false); }
    }
    load();
    return function(){ cancelled=true; };
  }, [period]);

  return html`<div style=${{padding:24}}>
    <div style=${{display:'flex',alignItems:'center',gap:12,marginBottom:24,flexWrap:'wrap'}}>
      <h2 style=${{color:'#f1f1f1',fontSize:20,fontWeight:700,margin:0}}>Trending Videos</h2>
      <div style=${{display:'flex',gap:8}}>
        ${PERIODS.map(function(p){
          const active = period===p.id;
          return html`<button key=${p.id} onClick=${function(){setPeriod(p.id);}}
            style=${{padding:'6px 14px',borderRadius:0,border:'none',fontSize:13,
              background:active?'#00FF07':'#272727',
              color:active?'#0f0f0f':'#f1f1f1',
              fontWeight:active?600:400,cursor:'pointer',transition:'all 0.15s'}}>
            ${p.label}
          </button>`;
        })}
      </div>
      ${!loading?html`<span style=${{color:'#555',fontSize:13,marginLeft:'auto'}}>${videos.length} videos</span>`:null}
    </div>
    <${VideoGrid} videos=${videos} loading=${loading} onWatch=${props.onWatch} onChannel=${props.onChannel}/>
  </div>`;
}

// ── Subscriptions Page ────────────────────────────────────────────────────────
function SubsPage(props) {
  return html`<div style=${{padding:24}}>
    <h2 style=${{color:'#f1f1f1',fontSize:20,fontWeight:700,marginBottom:20}}>Subscriptions</h2>
    ${props.followStrip&&props.followStrip.length>0?html`
      <div style=${{marginBottom:24}}>
        <div style=${{display:'flex',gap:16,overflowX:'auto',paddingBottom:8,scrollbarWidth:'none'}}>
          ${props.followStrip.map(function(actor,i){
            return html`<div key=${actor.did||i}
              onClick=${function(){props.onChannel(actor.handle);}}
              style=${{display:'flex',flexDirection:'column',alignItems:'center',gap:8,cursor:'pointer',flexShrink:0,width:100}}
              onMouseEnter=${function(e){e.currentTarget.style.opacity='0.8';}}
              onMouseLeave=${function(e){e.currentTarget.style.opacity='1';}}>
              <div style=${{width:80,height:80,borderRadius:'50%',overflow:'hidden',background:'#272727',
                border: actor.hasNewVideo?'3px solid #00FF07':'3px solid #3f3f3f',
                boxSizing:'border-box'}}>
                ${actor.avatar?html`<img src=${actor.avatar} alt="" style=${{width:'100%',height:'100%',objectFit:'cover',display:'block'}}/>`:null}
              </div>
              <span style=${{fontSize:12,color:'#ccc',textAlign:'center',width:'100%',overflow:'hidden',
                textOverflow:'ellipsis',whiteSpace:'nowrap'}}>
                ${actor.displayName||actor.handle}
              </span>
            </div>`;
          })}
        </div>
      </div>
    `:null}
    ${props.loading?html`<div style=${{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(280px,1fr))',gap:'24px 16px'}}>
      ${[0,1,2,3,4,5,6,7].map(function(i){return html`<${SkeletonCard} key=${i}/>`;})}
    </div>`:null}
    ${!props.loading&&(!props.videos||!props.videos.length)?html`<div style=${{display:'flex',flexDirection:'column',alignItems:'center',justifyContent:'center',height:'40vh',gap:16,color:'#aaa'}}>
      <svg width="64" height="64" viewBox="0 0 24 24" fill="#3f3f3f"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 14.5v-9l6 4.5-6 4.5z"/></svg>
      <p style=${{fontSize:16}}>No videos from people you follow.</p>
      <p style=${{fontSize:13,color:'#555'}}>Follow people on Bluesky who post videos and they'll appear here.</p>
    </div>`:null}
    ${!props.loading&&props.videos&&props.videos.length?html`<div style=${{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(280px,1fr))',gap:'24px 16px'}}>
      ${props.videos.map(function(p,i){return html`<${VideoCard} key=${p.uri||i} post=${p} onWatch=${props.onWatch} onChannel=${props.onChannel}/>`;  })}
    </div>`:null}
  </div>`;
}

// ── Feed Page (single saved feed) ─────────────────────────────────────────────
function FeedPage(props) {
  return html`<div style=${{padding:24}}>
    <h2 style=${{color:'#f1f1f1',fontSize:20,fontWeight:700,marginBottom:20}}>${props.feedName||'Feed'}</h2>
    <${VideoGrid} videos=${props.videos} loading=${props.loading} onWatch=${props.onWatch} onChannel=${props.onChannel}/>
  </div>`;
}

// ── Sidebar ───────────────────────────────────────────────────────────────────
function Sidebar(props) {
  const open = props.open;
  const SearchIco = html`<svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path d="M15.5 14h-.79l-.28-.27A6.471 6.471 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/></svg>`;
  const SubsIco   = html`<svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path d="M16 11c1.66 0 2.99-1.34 2.99-3S17.66 5 16 5c-1.66 0-3 1.34-3 3s1.34 3 3 3zm-8 0c1.66 0 2.99-1.34 2.99-3S9.66 5 8 5C6.34 5 5 6.34 5 8s1.34 3 3 3zm0 2c-2.33 0-7 1.17-7 3.5V19h14v-2.5c0-2.33-4.67-3.5-7-3.5zm8 0c-.29 0-.62.02-.97.05 1.16.84 1.97 1.97 1.97 3.45V19h6v-2.5c0-2.33-4.67-3.5-7-3.5z"/></svg>`;
  const TrendIco  = html`<svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path d="M16 6l2.29 2.29-4.88 4.88-4-4L2 16.59 3.41 18l6-6 4 4 6.3-6.29L22 12V6h-6z"/></svg>`;
  const FeedIco   = html`<svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path d="M6.18 15.64a2.18 2.18 0 0 1 2.18 2.18C8.36 19.01 7.38 20 6.18 20C4.98 20 4 19.01 4 17.82a2.18 2.18 0 0 1 2.18-2.18M4 4.44A15.56 15.56 0 0 1 19.56 20h-2.83A12.73 12.73 0 0 0 4 7.27V4.44m0 5.66a9.9 9.9 0 0 1 9.9 9.9h-2.83A7.07 7.07 0 0 0 4 12.93V10.1z"/></svg>`;

  const feeds = props.feeds || [];

  return html`<aside style=${{position:'fixed',top:56,left:0,bottom:0,width:open?240:72,background:'#0f0f0f',
    padding:open?'12px':'12px 4px',overflowY:'auto',overflowX:'hidden',zIndex:100,
    transition:'width 0.15s ease',boxSizing:'border-box'}}>

    <${SideItem} open=${open} icon=${SearchIco} label="Search"
      active=${props.page==='search'} onClick=${props.onSearch}/>

    <${SideItem} open=${open} icon=${SubsIco}   label="Subscriptions"
      active=${props.page==='subs'}
      onClick=${function(){props.hasSession?props.onSubs():props.onLogin();}}/>

    <${SideItem} open=${open} icon=${TrendIco}  label="Trending"
      active=${props.page==='trending'} onClick=${props.onTrending}/>

    ${feeds.length>0?html`
      <div style=${{height:1,background:'#272727',margin:'10px 0 6px'}}/>
      ${open?html`<div style=${{color:'#666',fontSize:11,textTransform:'uppercase',letterSpacing:1,padding:'0 12px 6px'}}>Feeds</div>`:null}
      ${feeds.map(function(feed){
        const active = props.page==='feed' && props.activeFeed===feed.uri;
        return html`<${SideItem} key=${feed.uri} open=${open} icon=${FeedIco}
          label=${feed.displayName}
          active=${active}
          onClick=${function(){props.onFeedSelect(feed);}}/>`
      })}
    `:null}

    ${open?html`
      <div style=${{height:1,background:'#272727',margin:'12px 0 8px'}}/>
      <div style=${{padding:'0 12px'}}>
        <a href="https://bsky.app" target="_blank" rel="noreferrer" style=${{color:'#00FF07',fontSize:11}}>Powered by Bluesky AT Protocol</a>
        <div style=${{color:'#00FF07',fontSize:10,marginTop:4}}>✓ Running via local proxy</div>
      </div>
    `:null}
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
    style=${{flexShrink:0, background:subbed?'#1a1a1a':'#00FF07',
      border:subbed?'1px solid #555':'none', color:subbed?'#f1f1f1':'#000',
      padding:pad, borderRadius:0, fontWeight:600, fontSize:fsz,
      transition:'all 0.15s', opacity:loading?0.6:1, cursor:sess?'pointer':'default'}}>
    ${loading ? '...' : (subbed ? 'Subscribed' : 'Subscribe')}
  </button>`;
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

  return html`<div style=${{display:'flex',gap:24,padding:24,maxWidth:1600,margin:'0 auto'}}>
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
            <${Avatar} src=${author.avatar} size=${40}/>
            <div>
              <div style=${{color:'#f1f1f1',fontWeight:500,fontSize:14}}>${author.displayName||author.handle}</div>
              <div style=${{color:'#aaa',fontSize:12}}>@${author.handle}</div>
            </div>
          </div>
          <${SubscribeButton} did=${author.did} viewer=${author.viewer} session=${sess}/>
        </div>
        <div style=${{display:'flex',gap:8}}>
          <button onClick=${toggleLike} style=${bSt(liked,'#003300')}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill=${liked?'#00FF07':'currentColor'}><path d="M1 21h4V9H1v12zm22-11c0-1.1-.9-2-2-2h-6.31l.95-4.57.03-.32c0-.41-.17-.79-.44-1.06L14.17 1 7.59 7.59C7.22 7.95 7 8.45 7 9v10c0 1.1.9 2 2 2h9c.83 0 1.54-.5 1.84-1.22l3.02-7.05c.09-.23.14-.47.14-.73v-2z"/></svg>
            ${fmt(likeCount)}
          </button>
          <button onClick=${toggleRepost} style=${bSt(reposted,'#003300')}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill=${reposted?'#00FF07':'currentColor'}><path d="M7 7h10v3l4-4-4-4v3H5v6h2V7zm10 10H7v-3l-4 4 4 4v-3h12v-6h-2v4z"/></svg>
            ${fmt(repostCount)}
          </button>
          <button onClick=${function(){navigator.clipboard&&navigator.clipboard.writeText('https://bsky.app/profile/'+author.handle+'/post/'+postId);}} style=${bSt(false)}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M18 16.08c-.76 0-1.44.3-1.96.77L8.91 12.7c.05-.23.09-.46.09-.7s-.04-.47-.09-.7l7.05-4.11c.54.5 1.25.81 2.04.81 1.66 0 3-1.34 3-3s-1.34-3-3-3-3 1.34-3 3c0 .24.04.47.09.7L8.04 9.81C7.5 9.31 6.79 9 6 9c-1.66 0-3 1.34-3 3s1.34 3 3 3c.79 0 1.5-.31 2.04-.81l7.12 4.16c-.05.21-.08.43-.08.65 0 1.61 1.31 2.92 2.92 2.92 1.61 0 2.92-1.31 2.92-2.92s-1.31-2.92-2.92-2.92z"/></svg>
            Share
          </button>
        </div>
      </div>
      <div style=${{background:'#1a1a1a',border:'1px solid #272727',padding:'12px 16px',marginBottom:24}}>
        <div style=${{fontSize:13,color:'#f1f1f1',fontWeight:500,marginBottom:4}}>
          ${fmt(likeCount)} likes · ${fmt(post.replyCount||0)} comments · ${fmt(repostCount)} reposts · ${ago(post.indexedAt)}
        </div>
        ${rec&&rec.text?html`<div style=${{fontSize:14,color:'#f1f1f1',marginTop:8,whiteSpace:'pre-wrap',lineHeight:1.6}}>${rec.text}</div>`:null}
        <a href=${'https://bsky.app/profile/'+author.handle+'/post/'+postId} target="_blank" rel="noreferrer"
          style=${{display:'inline-block',marginTop:12,color:'#00FF07',fontSize:13}}>View on Bluesky →</a>
      </div>
      ${!sess?html`<div style=${{color:'#aaa',fontSize:13,marginBottom:16,padding:'8px 12px',background:'#1a1a1a',border:'1px solid #272727'}}>
        <a onClick=${function(){props.onLogin&&props.onLogin();}} style=${{color:'#00FF07',cursor:'pointer'}}>Sign in</a> to like, repost, and subscribe.
      </div>`:null}
      <h3 style=${{color:'#f1f1f1',fontSize:16,fontWeight:600,marginBottom:16}}>${fmt(post.replyCount||0)} Comments</h3>
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
        props.related.map(function(p,i){return html`<${VideoCardCompact} key=${p.uri||i} post=${p} onWatch=${props.onWatch} onChannel=${props.onChannel}/>`;})
      }
    </div>
  </div>`;
}

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
          background:filter===f?'#00FF07':'none',color:filter===f?'#000':'#f1f1f1',fontSize:14,borderRadius:0,cursor:'pointer',fontWeight:filter===f?600:400}}>${f}</button>`;
      })}
    </div>
    <div style=${{color:'#aaa',fontSize:13,marginBottom:16}}>
      Results for "${props.query}"
      ${!err&&results?html`<span style=${{marginLeft:12,color:'#555'}}>
        ${actors.length} channel${actors.length!==1?'s':''} · ${videos.length} video${videos.length!==1?'s':''}
      </span>`:null}
    </div>
    ${err?html`<div style=${{background:'#1a0000',border:'1px solid #882222',padding:'12px 16px',marginBottom:20,color:'#ff9999',fontSize:13,lineHeight:1.5}}>⚠️ ${err}</div>`:null}
    ${!err&&results&&actors.length===0&&videos.length===0?html`<div style=${{background:'#0d1a0d',border:'1px solid #00FF07',padding:'12px 16px',marginBottom:20,color:'#aaa',fontSize:14,lineHeight:1.6}}>
      💡 Try your exact handle like <code style=${{color:'#00FF07',background:'#0a1a0a',padding:'1px 5px'}}>yourname.bsky.social</code>
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
            style=${{background:'#00FF07',border:'none',color:'#000',padding:'8px 16px',fontWeight:600,fontSize:13,cursor:'pointer',borderRadius:0}}>View Channel</button>
        </div>
      </div>`;})}
    </div>`:null}
    ${(filter==='All'||filter==='Videos')?html`<div>
      <h3 style=${{color:'#f1f1f1',fontSize:15,fontWeight:600,marginBottom:12}}>Videos${videos.length?' ('+videos.length+')':''}</h3>
      ${videos.length===0?html`<div style=${{color:'#aaa',fontSize:14,padding:'24px 0'}}>No videos found for "${props.query}".</div>`:
        videos.map(function(p,i){return html`<${VideoCardCompact} key=${p.uri||i} post=${p} onWatch=${props.onWatch} onChannel=${props.onChannel}/>`;})
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
            border:'2px dashed '+(videoFile?'#00FF07':'#3f3f3f'),cursor:busy?'default':'pointer',
            background:videoFile?'rgba(0,255,7,0.05)':'#111',transition:'border-color 0.15s'}}>
            <svg width="28" height="28" viewBox="0 0 24 24" fill=${videoFile?'#00FF07':'#555'}><path d="M9 16h6v-6h4l-7-7-7 7h4v6zm-4 2h14v2H5v-2z"/></svg>
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
            border:'2px dashed '+(thumbFile?'#00FF07':'#3f3f3f'),cursor:busy?'default':'pointer',
            background:thumbFile?'rgba(0,255,7,0.05)':'#111',transition:'border-color 0.15s'}}>
            <svg width="28" height="28" viewBox="0 0 24 24" fill=${thumbFile?'#00FF07':'#555'}><path d="M21 19V5c0-1.1-.9-2-2-2H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2zM8.5 13.5l2.5 3.01L14.5 12l4.5 6H5l3.5-4.5z"/></svg>
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
            onFocus=${function(e){e.target.style.borderColor='#00FF07';}} onBlur=${function(e){e.target.style.borderColor='#3f3f3f';}}/>
          <div style=${{textAlign:'right',fontSize:11,color:'#555',marginTop:3}}>${title.length}/200</div>
        </div>
        <div style=${{marginBottom:20}}>
          <label style=${{display:'block',color:'#aaa',fontSize:13,fontWeight:500,marginBottom:8}}>Description</label>
          <textarea value=${desc} onInput=${function(e){setDesc(e.target.value);}} placeholder="Describe your video" rows="3" maxlength="2000"
            style=${Object.assign({},iSt,{resize:'vertical'})} disabled=${busy}
            onFocus=${function(e){e.target.style.borderColor='#00FF07';}} onBlur=${function(e){e.target.style.borderColor='#3f3f3f';}}/>
        </div>
        ${busy||status==='error'?html`<div style=${{marginBottom:20,background:'#111',border:'1px solid #272727',padding:'14px 16px'}}>
          ${STEPS.map(function(step,i){
            const cur=order.indexOf(status); const si=order.indexOf(step.id);
            const active=status===step.id; const complete=cur>si; const pending=cur<si;
            return html`<div key=${step.id} style=${{display:'flex',alignItems:'center',gap:10,padding:'5px 0',opacity:pending?0.3:1}}>
              <div style=${{width:22,height:22,flexShrink:0,display:'flex',alignItems:'center',justifyContent:'center',
                background:complete?'#003300':active?'#00FF07':'#2a2a2a',fontSize:10,fontWeight:700,color:active?'#000':'#fff'}}>
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
          style=${{width:'100%',padding:14,background:busy?'#003300':'#00FF07',color:busy?'#00FF07':'#000',
            border:'none',fontSize:15,fontWeight:700,borderRadius:0,
            opacity:(busy||!videoFile||!title.trim())?0.55:1,cursor:(busy||!videoFile||!title.trim())?'not-allowed':'pointer'}}>
          ${busy?(progress||'Working…'):(thumbFile?'Stitch & Upload':'Upload Video')}
        </button>
      `}
    </div>
  </div>`;
}


// ── Channel Page ──────────────────────────────────────────────────────────────
function ChannelPage(props) {
  const [tab, setTab] = useState('Videos');
  if (props.loading) return html`<div style=${{display:'flex',alignItems:'center',justifyContent:'center',height:'50vh',color:'#aaa'}}>Loading channel...</div>`;
  if (!props.data) return null;
  const d = props.data;
  return html`<div>
    ${props.onBack?html`
      <div style=${{padding:'12px 24px 0'}}>
        <button onClick=${props.onBack}
          style=${{background:'none',border:'1px solid #3f3f3f',color:'#f1f1f1',padding:'7px 16px',
            borderRadius:0,cursor:'pointer',fontSize:13,display:'flex',alignItems:'center',gap:6}}
          onMouseEnter=${function(e){e.currentTarget.style.background='#272727';}}
          onMouseLeave=${function(e){e.currentTarget.style.background='none';}}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z"/></svg>
          Back
        </button>
      </div>
    `:null}
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
      <div style=${{display:'flex'}}>
        ${['Videos','About'].map(function(t){return html`<button key=${t} onClick=${function(){setTab(t);}}
          style=${{padding:'12px 20px',background:'none',border:'none',color:tab===t?'#f1f1f1':'#aaa',
            fontSize:14,fontWeight:tab===t?500:400,borderBottom:'3px solid '+(tab===t?'#f1f1f1':'transparent'),cursor:'pointer'}}>${t}</button>`;
        })}
      </div>
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
          style=${{display:'inline-block',marginTop:24,color:'#00FF07',fontSize:14}}>View on Bluesky →</a>
      </div>`:null}
    </div>
  </div>`;
}


// ── App ───────────────────────────────────────────────────────────────────────
function App() {
  const [session,       setSession]       = useState(function(){return loadSession();});
  const [page,          setPage]          = useState(function(){return loadLastPage();});
  const [prevPage,      setPrevPage]      = useState(null);
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
  const [channelFromPage,setChannelFromPage]=useState(null);
  const pageRef = useRef('trending'); // always holds latest page value without causing dep issues
  const [searchQuery,   setSearchQuery]   = useState('');
  const [searchResults, setSearchResults] = useState(null);
  const [searchLoading, setSearchLoading] = useState(false);
  const [subsVideos,    setSubsVideos]    = useState([]);
  const [subsLoading,   setSubsLoading]   = useState(false);
  const [followStrip,   setFollowStrip]   = useState([]);

  // Save page to localStorage whenever it changes (except channel/watch)
  function navTo(p) {
    if(p!=='channel'&&p!=='watch') pageRef.current = p;
    setPage(p);
    if(p!=='channel'&&p!=='watch') saveLastPage(p);
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
      const r=await api(endpoint+'/app.bsky.feed.getFeed?feed='+encodeURIComponent(feedUri)+'&limit=100',authOpts);
      if(r.ok){ const d=await r.json(); add((d.feed||[]).map(function(i){return i.post;})); }
      if(videos.length<10){
        const r2=await api(PUB_PROXY+'/app.bsky.feed.getFeed?feed='+encodeURIComponent(feedUri)+'&limit=100').catch(function(){return null;});
        if(r2&&r2.ok){ const d=await r2.json(); add((d.feed||[]).map(function(i){return i.post;})); }
      }
    }catch(e){ console.error('loadFeedVideos:',e); }
    setFeedVideos(videos); setFeedLoading(false);
  },[]);

  const handleFeedSelect = useCallback(function(feed){
    setActiveFeed(feed.uri);
    saveLastPage('feed');
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
    setCurrentVideo(post); setThread(null); setRelated([]);
    setPage('watch'); window.scrollTo(0,0);
    try{
      const tR=await api(PUB_PROXY+'/app.bsky.feed.getPostThread?uri='+encodeURIComponent(post.uri)+'&depth=6');
      const fR=await api(PUB_PROXY+'/app.bsky.feed.getAuthorFeed?actor='+encodeURIComponent(post.author.did)+'&limit=50');
      if(tR.ok){const d=await tR.json();setThread(d.thread);}
      if(fR.ok){const d=await fR.json();setRelated((d.feed||[]).map(function(i){return i.post;}).filter(function(p){return isVid(p)&&p.uri!==post.uri;}).slice(0,15));}
    }catch(e){ console.error(e); }
  },[]);

  // ── Channel ─────────────────────────────────────────────────────────────────
  const handleChannel = useCallback(async function(actor){
    if(!actor) return;
    setChannelFromPage(pageRef.current);   // use ref, not state — avoids stale closure
    setChannelData(null); setChannelVideos([]); setChannelLoading(true);
    setPage('channel'); window.scrollTo(0,0);
    try{
      const pR=await api(PUB_PROXY+'/app.bsky.actor.getProfile?actor='+encodeURIComponent(actor));
      const fR=await api(PUB_PROXY+'/app.bsky.feed.getAuthorFeed?actor='+encodeURIComponent(actor)+'&limit=100&filter=posts_with_media');
      if(pR.ok){ const d=await pR.json(); setChannelData(d); }
      if(fR.ok){ const d=await fR.json(); setChannelVideos((d.feed||[]).map(function(i){return i.post;}).filter(isVid)); }
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
      const aR=await api(PUB_PROXY+'/app.bsky.actor.searchActors?q='+encodeURIComponent(stripped)+'&limit=20');
      const pR=await api(PUB_PROXY+'/app.bsky.feed.searchPosts?q='+encodeURIComponent(raw)+'&limit=100&sort=latest');
      let actors=aR.ok?((await aR.json()).actors||[]):[];
      const allPosts=pR.ok?((await pR.json()).posts||[]):[];
      const looksLikeHandle=!stripped.includes(' ');
      if(looksLikeHandle){
        const dR=await api(PUB_PROXY+'/app.bsky.actor.getProfile?actor='+encodeURIComponent(stripped)).catch(function(){return null;});
        if(dR&&dR.ok){const p=await dR.json();if(p&&p.handle)actors=[p].concat(actors.filter(function(a){return a.did!==p.did;}));}
      }
      function hasVideo(p){
        if(!p||!p.embed) return false;
        const t=p.embed['$type']||'';
        if(t==='app.bsky.embed.video#view'||t==='app.bsky.embed.video') return true;
        if(t==='app.bsky.embed.recordWithMedia#view'){const m=p.embed.media;if(m&&(m['$type']==='app.bsky.embed.video#view'||m['$type']==='app.bsky.embed.video'))return true;}
        return false;
      }
      setSearchResults({videos:allPosts.filter(hasVideo),actors:actors,totalPosts:allPosts.length,error:null});
    }catch(e){
      setSearchResults({videos:[],actors:[],totalPosts:0,error:e.message||String(e)});
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
  },[]);

  const mL=sidebarOpen?240:72;
  const currentFeedName = activeFeed&&feeds?(feeds.find(function(f){return f.uri===activeFeed;})||{}).displayName:'Feed';

  return html`<div style=${{minHeight:'100vh',background:'#0f0f0f',color:'#f1f1f1'}}>
    <${Header}
      onHome=${function(){navTo('trending');}}
      onSearch=${handleSearch}
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
      onTrending=${function(){navTo('trending');}}
      onFeedSelect=${handleFeedSelect}
      onLogin=${function(){setShowLogin(true);}}/>

    <main style=${{marginLeft:mL,marginTop:56,minHeight:'calc(100vh - 56px)',transition:'margin-left 0.15s ease'}}>
      ${page==='trending'?html`<${TrendingPage} onWatch=${handleWatch} onChannel=${handleChannel}/>`:null}
      ${page==='subs'?html`<${SubsPage} videos=${subsVideos} loading=${subsLoading} followStrip=${followStrip} onWatch=${handleWatch} onChannel=${handleChannel}/>`:null}
      ${page==='search'?html`<${SearchPage} results=${searchResults} loading=${searchLoading} query=${searchQuery} session=${session} onWatch=${handleWatch} onChannel=${handleChannel}/>`:null}
      ${page==='feed'?html`<${FeedPage} videos=${feedVideos} loading=${feedLoading} feedName=${currentFeedName} onWatch=${handleWatch} onChannel=${handleChannel}/>`:null}
      ${page==='watch'&&currentVideo?html`<${WatchPage} post=${currentVideo} related=${related} thread=${thread} session=${session} onLogin=${function(){setShowLogin(true);}} onWatch=${handleWatch} onChannel=${handleChannel}/>`:null}
      ${page==='channel'?html`<${ChannelPage} data=${channelData} videos=${channelVideos} loading=${channelLoading} session=${session} onBack=${function(){navTo(channelFromPage||'trending');}} onWatch=${handleWatch} onChannel=${handleChannel}/>`:null}
    </main>

    ${showLogin?html`<${LoginModal} onClose=${function(){setShowLogin(false);}} onSuccess=${handleLoginSuccess}/>`:null}
    ${showUpload&&session?html`<${UploadModal} session=${session} onClose=${function(){setShowUpload(false);}} onDone=${function(){setShowUpload(false);navTo('trending');}}/> `:null}
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

    def _proxy(self, method, url, body=None, timeout=30, cacheable=False):
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
        tmpdir = tempfile.mkdtemp(prefix="raccnet_")
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
║           Racc.net Local Server           ║
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
