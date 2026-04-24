"""
SkyTube local proxy server
Run:  python skytube_server.py
Then open:  http://localhost:8080
"""
import http.server
import urllib.request
import urllib.error
import urllib.parse
import json
import os
import sys

PORT = 8080

# ── Embedded HTML (the full SkyTube app, pointing at /proxy/ instead of bsky.app) ──
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>SkyTube</title>
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
const PUB_PROXY  = '/proxy/pub/xrpc';
const AUTH_PROXY = '/proxy/auth/xrpc';

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
      <div onClick=${props.onHome} style=${{display:'flex',alignItems:'center',gap:6,cursor:'pointer',userSelect:'none'}}>
        <div style=${{width:34,height:24,background:'#FF0000',borderRadius:6,display:'flex',alignItems:'center',justifyContent:'center'}}>
          <svg width="18" height="14" viewBox="0 0 18 14" fill="none"><polygon points="5,1 5,13 15,7" fill="white"/></svg>
        </div>
        <span style=${{fontSize:20,fontWeight:700,color:'#f1f1f1',letterSpacing:-0.5}}>
          Sky<span style=${{color:'#aaa',fontWeight:400}}>Tube</span>
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
    <div style=${{flexShrink:0}}>
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
        <div style=${{color:'#aaa',fontSize:12,marginBottom:6}}>Powered by</div>
        <a href="https://bsky.app" target="_blank" rel="noreferrer" style=${{color:'#3ea6ff',fontSize:12}}>Bluesky AT Protocol</a>
        <div style=${{color:'#3a3',fontSize:11,marginTop:8}}>✓ Running via local proxy</div>
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
        <div style=${{width:28,height:20,background:'#FF0000',borderRadius:5,display:'flex',alignItems:'center',justifyContent:'center'}}>
          <svg width="14" height="10" viewBox="0 0 14 10" fill="none"><polygon points="4,1 4,9 11,5" fill="white"/></svg>
        </div>
        <h2 style=${{color:'#f1f1f1',fontSize:20,fontWeight:600}}>Sign in to SkyTube</h2>
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

function WatchPage(props) {
  const post=props.post, embed=post.embed, author=post.author, rec=post.record;
  const replies=((props.thread&&props.thread.replies)||[]).filter(function(r){return r.post;}).slice(0,20);
  const postId=post.uri.split('/').pop();
  const bSt={background:'#272727',border:'none',color:'#f1f1f1',padding:'8px 16px',borderRadius:20,fontSize:14,display:'flex',alignItems:'center',gap:6};
  return html`<div style=${{display:'flex',gap:24,padding:24,maxWidth:1600,margin:'0 auto'}}>
    <div style=${{flex:1,minWidth:0}}>
      <div style=${{borderRadius:12,overflow:'hidden',background:'#000'}}>
        <${VideoPlayer} playlist=${embed.playlist} thumbnail=${embed.thumbnail}/>
      </div>
      <h1 style=${{fontSize:18,fontWeight:600,color:'#f1f1f1',margin:'16px 0 8px',lineHeight:1.4}}>
        ${(rec&&rec.text&&rec.text.split('\n')[0])||'Video from Bluesky'}
      </h1>
      <div style=${{display:'flex',alignItems:'center',justifyContent:'space-between',flexWrap:'wrap',gap:12,marginBottom:16}}>
        <div style=${{display:'flex',alignItems:'center',gap:12,cursor:'pointer'}} onClick=${function(){props.onChannel(author.handle);}}>
          <${Avatar} src=${author.avatar} size=${40}/>
          <div>
            <div style=${{color:'#f1f1f1',fontWeight:500,fontSize:14}}>${author.displayName||author.handle}</div>
            <div style=${{color:'#aaa',fontSize:12}}>@${author.handle}</div>
          </div>
          <button onClick=${function(e){e.stopPropagation();}}
            style=${{background:'#f1f1f1',border:'none',color:'#0f0f0f',padding:'8px 16px',borderRadius:20,fontWeight:600,fontSize:13,marginLeft:8}}>Follow</button>
        </div>
        <div style=${{display:'flex',gap:8}}>
          <button style=${bSt}><svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M1 21h4V9H1v12zm22-11c0-1.1-.9-2-2-2h-6.31l.95-4.57.03-.32c0-.41-.17-.79-.44-1.06L14.17 1 7.59 7.59C7.22 7.95 7 8.45 7 9v10c0 1.1.9 2 2 2h9c.83 0 1.54-.5 1.84-1.22l3.02-7.05c.09-.23.14-.47.14-.73v-2z"/></svg>${fmt(post.likeCount||0)}</button>
          <button style=${bSt}><svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M18 16.08c-.76 0-1.44.3-1.96.77L8.91 12.7c.05-.23.09-.46.09-.7s-.04-.47-.09-.7l7.05-4.11c.54.5 1.25.81 2.04.81 1.66 0 3-1.34 3-3s-1.34-3-3-3-3 1.34-3 3c0 .24.04.47.09.7L8.04 9.81C7.5 9.31 6.79 9 6 9c-1.66 0-3 1.34-3 3s1.34 3 3 3c.79 0 1.5-.31 2.04-.81l7.12 4.16c-.05.21-.08.43-.08.65 0 1.61 1.31 2.92 2.92 2.92 1.61 0 2.92-1.31 2.92-2.92s-1.31-2.92-2.92-2.92z"/></svg>Share</button>
        </div>
      </div>
      <div style=${{background:'#212121',borderRadius:12,padding:'12px 16px',marginBottom:24}}>
        <div style=${{fontSize:13,color:'#f1f1f1',fontWeight:500,marginBottom:4}}>
          ${fmt(post.likeCount||0)} likes · ${fmt(post.replyCount||0)} comments · ${fmt(post.repostCount||0)} reposts · ${ago(post.indexedAt)}
        </div>
        ${rec&&rec.text?html`<div style=${{fontSize:14,color:'#f1f1f1',marginTop:8,whiteSpace:'pre-wrap',lineHeight:1.6}}>${rec.text}</div>`:null}
        <a href=${'https://bsky.app/profile/'+author.handle+'/post/'+postId} target="_blank" rel="noreferrer"
          style=${{display:'inline-block',marginTop:12,color:'#3ea6ff',fontSize:13}}>View on Bluesky →</a>
      </div>
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
        <button style=${{flexShrink:0,background:'#f1f1f1',border:'none',color:'#0f0f0f',padding:'10px 20px',borderRadius:20,fontWeight:600,fontSize:14}}>Follow</button>
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
          <button onClick=${function(e){e.stopPropagation();}} style=${{background:'#f1f1f1',border:'none',color:'#0f0f0f',padding:'8px 16px',borderRadius:20,fontWeight:600,fontSize:13}}>Follow</button>
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

function App() {
  const [session,setSession]=useState(null);
  const [page,setPage]=useState('home');
  const [sidebarOpen,setSidebarOpen]=useState(true);
  const [showLogin,setShowLogin]=useState(false);
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
        const authHeader=sess?{Authorization:'Bearer '+sess.accessJwt}:{};
        const r=await api(PUB_PROXY+'/app.bsky.feed.getFeed?feed='+encodeURIComponent(feedUri)+'&limit=100',{headers:authHeader});
        if(r.ok){const d=await r.json();add((d.feed||[]).map(function(i){return i.post;}));}
        // If not many videos, also try the feed without auth
        if(videos.length<5&&sess){
          const r2=await api(PUB_PROXY+'/app.bsky.feed.getFeed?feed='+encodeURIComponent(feedUri)+'&limit=100').catch(function(){return null;});
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
  useEffect(function(){
    if(page==='home'){
      loadSavedFeeds(session);
      loadFeedVideos('all',session);
    }
  },[page]);

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

  const mL=sidebarOpen?240:72;
  return html`<div style=${{minHeight:'100vh',background:'#0f0f0f',color:'#f1f1f1'}}>
    <${Header} onHome=${function(){setPage('home');window.scrollTo(0,0);}} onSearch=${handleSearch}
      session=${session} onLogin=${function(){setShowLogin(true);}} onLogout=${function(){setSession(null);}}
      input=${searchInput} setInput=${setSearchInput} toggleSidebar=${function(){setSidebarOpen(function(o){return!o;});}}/>
    <${Sidebar} open=${sidebarOpen} page=${page}
      onHome=${function(){setPage('home');window.scrollTo(0,0);}}
      onExplore=${handleSearch} onFeed=${function(){handleSearch('video');}}
      onSubs=${function(){session?handleSearch('video'):setShowLogin(true);}}
      hasSession=${!!session}/>
    <main style=${{marginLeft:mL,marginTop:56,minHeight:'calc(100vh - 56px)',transition:'margin-left 0.15s ease'}}>
      ${page==='home'?html`<${HomePage} videos=${homeVideos} loading=${homeLoading} onWatch=${handleWatch} onChannel=${handleChannel} onExplore=${handleSearch} feeds=${feeds||DEFAULT_FEEDS} activeFeed=${activeFeed} onFeedSelect=${handleFeedSelect}/>`:null}
      ${page==='watch'&&currentVideo?html`<${WatchPage} post=${currentVideo} related=${related} thread=${thread} onWatch=${handleWatch} onChannel=${handleChannel}/>`:null}
      ${page==='channel'?html`<${ChannelPage} data=${channelData} videos=${channelVideos} loading=${channelLoading} onWatch=${handleWatch} onChannel=${handleChannel}/>`:null}
      ${page==='search'?html`<${SearchPage} results=${searchResults} loading=${searchLoading} query=${searchQuery} onWatch=${handleWatch} onChannel=${handleChannel}/>`:null}
    </main>
    ${showLogin?html`<${LoginModal} onClose=${function(){setShowLogin(false);}} onSuccess=${function(d){setSession(d);setShowLogin(false);loadHome(d);}}/> `:null}
  </div>`;
}

render(html`<${App}/>`, document.getElementById('app'));
</script>
</body>
</html>"""

# ── Request handler ───────────────────────────────────────────────────────────
class Handler(http.server.BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
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
            self._proxy('GET', 'https://public.api.bsky.app/' + self.path[len('/proxy/pub/'):])
        elif self.path.startswith('/proxy/auth/'):
            self._proxy('GET', 'https://bsky.social/' + self.path[len('/proxy/auth/'):])
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path.startswith('/proxy/auth/'):
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length) if length else b''
            self._proxy('POST', 'https://bsky.social/' + self.path[len('/proxy/auth/'):], body=body)
        else:
            self.send_response(404)
            self.end_headers()

    def _serve_html(self):
        data = HTML.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _proxy(self, method, url, body=None):
        # Forward headers from the original request
        fwd_headers = {}
        for h in ('Authorization', 'Content-Type', 'Accept'):
            v = self.headers.get(h)
            if v:
                fwd_headers[h] = v
        if 'Accept' not in fwd_headers:
            fwd_headers['Accept'] = 'application/json'

        try:
            req = urllib.request.Request(url, data=body, headers=fwd_headers, method=method)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()
                self.send_response(resp.status)
                self.send_header('Content-Type', resp.headers.get('Content-Type', 'application/json'))
                self.send_header('Content-Length', str(len(data)))
                self.send_cors()
                self.end_headers()
                self.wfile.write(data)
        except urllib.error.HTTPError as e:
            data = e.read()
            self.send_response(e.code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.send_cors()
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            msg = json.dumps({'error': str(e)}).encode()
            self.send_response(502)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(msg)))
            self.send_cors()
            self.end_headers()
            self.wfile.write(msg)


# ── Start server ──────────────────────────────────────────────────────────────
if __name__ == '__main__':
    server = http.server.HTTPServer(('localhost', PORT), Handler)
    print(f"""
╔══════════════════════════════════════════╗
║          SkyTube Local Server            ║
╠══════════════════════════════════════════╣
║  Open this in Firefox:                   ║
║  http://localhost:{PORT}                    ║
║                                          ║
║  Press Ctrl+C to stop                    ║
╚══════════════════════════════════════════╝
""")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
        server.server_close()
