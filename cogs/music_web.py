"""Сайт музыки: отдельная страница, отдельный сервер, отдельный порт.

Панель семьи спрятана за паролем, а музыку хотят включать все, кто сидит в голосе.
Поэтому это не раздел панели, а самостоятельный сайт: своё приложение aiohttp, свои
маршруты и ни одного общего обработчика с панелью — `cogs/web_panel.py` этот файл не
трогает.

Хостинг даёт один веб-адрес на один порт, так что если `PANEL_PORT` задан и отличается
от `MUSIC_PORT`, сайт музыки встаёт на публичный порт, а всё остальное (панель со её
маршрутами) проксируется на внутренний порт панели. Файлы панели при этом не правятся.

Открыт без входа, но с ограничением по адресам: заказы считаются с одного IP, а
очередь и кулдаун плеера не да забить канал одним человеком.
"""
from __future__ import annotations

import os
import re
import secrets
import time
from collections import defaultdict, deque
from typing import Any, Deque, Dict

import aiohttp
from aiohttp import web
from disnake.ext import commands

from utils.checks import load_config

DEFAULT_PORT = 8081
PREFIX = "/music"
WRITE_LIMIT = 20             # записей на один адрес в минуту
WRITE_WINDOW = 60
NICK_MAX = 32
# Защита от чужих POST'ов и выключатель заказов действуют только на маршруты сайта.
WRITE_PATHS = frozenset(f"{pre}/api/{name}"
                        for pre in ("", PREFIX) for name in ("order", "action"))


def _panel_port() -> int:
    try:
        return int(os.getenv("PANEL_PORT") or 0)
    except (TypeError, ValueError):
        return 0


def _conf() -> dict:
    try:
        return load_config() or {}
    except Exception:
        return {}


def _open_for_writes() -> bool:
    """Одним ключом в конфиге закрываются заказы и кнопки — на случай спама."""
    return bool(_conf().get("music_page_open", True))


class _Hits:
    """Сколько записей прилетело с одного адреса за последнее время."""

    def __init__(self):
        self._by_ip: Dict[str, Deque[float]] = defaultdict(deque)
        self._prune_at = 0.0

    def allow(self, ip: str) -> bool:
        now = time.time()
        if now > self._prune_at:
            for key in [k for k, box in self._by_ip.items() if not box or now - box[-1] > WRITE_WINDOW]:
                self._by_ip.pop(key, None)
            self._prune_at = now + WRITE_WINDOW * 10
        box = self._by_ip[ip]
        while box and now - box[0] > WRITE_WINDOW:
            box.popleft()
        if len(box) >= WRITE_LIMIT:
            return False
        box.append(now)
        return True

    @staticmethod
    def ip(request: web.Request) -> str:
        fwd = request.headers.get("X-Forwarded-For", "")
        return (fwd.split(",")[0].strip() if fwd else (request.remote or "?"))[:64]


def _guild(bot):
    """Сервер, где музыка звучит: сначала тот, где бот в голосовом, потом домашний."""
    for g in bot.guilds:
        vc = g.voice_client
        if vc is not None and vc.is_connected():
            return g
    gid = int(_conf().get("guild_id") or 0)
    if gid:
        return bot.get_guild(gid)
    return bot.guilds[0] if bot.guilds else None


@web.middleware
async def _guard(request: web.Request, handler):
    if request.path in WRITE_PATHS:
        if request.headers.get("X-Requested-With") != "hz-music":
            return web.json_response({"error": "Только со страницы сайта"}, status=403)
        if not _open_for_writes():
            return web.json_response({"error": "Заказы со страницы сейчас закрыты"}, status=403)
    return await handler(request)


class MusicWeb(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.hits = _Hits()
        self.runner: web.AppRunner | None = None
        self.session: aiohttp.ClientSession | None = None
        self.port = int(os.getenv("MUSIC_PORT") or _conf().get("music_port") or DEFAULT_PORT)

        app = web.Application(middlewares=[_guard])
        self.panel_port = _panel_port()
        # адрес на хостинге один: в этом режиме корень остаётся панели, а музыка
        # живёт на /music и ничьих маршрутов не перехватывает
        self.share_front = bool(self.panel_port and self.panel_port != self.port)
        for pre in ((PREFIX,) if self.share_front else ("", PREFIX)):
            app.router.add_get(pre + "/api/state", self.h_state)
            app.router.add_post(pre + "/api/order", self.h_order)
            app.router.add_post(pre + "/api/action", self.h_action)
        app.router.add_get(PREFIX, self.h_page_moved)
        app.router.add_get(PREFIX + "/", self.h_page)
        if self.share_front:
            app.router.add_route("*", "/{tail:.*}", self.h_proxy)
        else:
            app.router.add_get("/", self.h_page)
        self.app = app

    async def cog_load(self) -> None:
        self.session = aiohttp.ClientSession(auto_decompress=False)
        try:
            self.runner = web.AppRunner(self.app, access_log=None)
            await self.runner.setup()
            await web.TCPSite(self.runner, "0.0.0.0", self.port).start()
            front = f" + панель с порта {self.panel_port}" if self.share_front else ""
            print(f"🎵 Сайт музыки запущен: порт {self.port}{front}", flush=True)
        except OSError as e:
            self.runner = None
            print(f"⚠️ Сайт музыки не поднялся на порту {self.port} ({e.__class__.__name__}). "
                  f"Освободи порт или задай MUSIC_PORT.", flush=True)

    async def cog_unload(self) -> None:
        if self.runner is not None:
            await self.runner.cleanup()
            self.runner = None
        if self.session is not None:
            await self.session.close()
            self.session = None

    # ---- общее ----
    def _player(self):
        cog = self.bot.get_cog("Music")
        if cog is None:
            return None, {"error": "Модуль музыки выключен — включи его в панели"}
        guild = _guild(self.bot)
        if guild is None:
            return None, {"error": "Бот ни на одном сервере — играть негде"}
        return cog, guild

    # ---- маршруты ----
    async def h_page(self, request: web.Request) -> web.Response:
        nonce = secrets.token_urlsafe(16)
        csp = (
            "default-src 'none'; "
            f"script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
            "img-src 'self' data: https://i.ytimg.com https://cdn.discordapp.com; "
            "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
        )
        return web.Response(
            text=PAGE_HTML.replace("__NONCE__", nonce),
            content_type="text/html",
            headers={"Content-Security-Policy": csp, "Cache-Control": "no-store"},
        )

    async def h_page_moved(self, request: web.Request) -> web.Response:
        raise web.HTTPFound(PREFIX + "/")

    async def h_proxy(self, request: web.Request) -> web.StreamResponse:
        """Хостинг наружу отдаёт один порт, поэтому панель сидит за этим сайтом:
        всё, что не /music, уходит на её внутренний порт как есть."""
        url = f"http://127.0.0.1:{self.panel_port}{request.path_qs}"
        skip = {"host", "connection", "content-length", "accept-encoding"}
        headers = {k: v for k, v in request.headers.items() if k.lower() not in skip}
        try:
            async with self.session.request(
                    request.method, url, data=await request.read(), headers=headers,
                    timeout=aiohttp.ClientTimeout(total=120)) as resp:
                payload = await resp.read()
        except Exception:
            return web.Response(status=502, content_type="text/plain",
                                text="Панель семьи сейчас не отвечает — перезапусти бота.")
        out = {k: v for k, v in resp.headers.items()
               if k.lower() not in {"transfer-encoding", "content-length", "connection",
                                    "content-encoding", "date", "server"}}
        return web.Response(status=resp.status, body=payload, headers=out)

    async def h_state(self, request: web.Request) -> web.Response:
        cog, guild = self._player()
        if cog is None:
            return _json(guild)
        try:
            state = cog.web_state(guild)
        except Exception as e:
            return _json({"error": f"Плеер не ответил: {e.__class__.__name__}"})
        state["open"] = _open_for_writes()
        return _json(state)

    async def h_order(self, request: web.Request) -> web.Response:
        if not self.hits.allow(self.hits.ip(request)):
            return _json({"error": "Слишком много заказов с одного адреса — подожди минуту"}, 429)
        body = await _body(request)
        cog, guild = self._player()
        if cog is None:
            return _json(guild)
        nick = re.sub(r"\s+", " ", str(body.get("nick") or "")).strip()[:NICK_MAX]
        res = await cog.web_enqueue(guild, str(body.get("query") or ""), nick or "гость",
                                    key=self.hits.ip(request))
        return _json(res, 400 if res.get("error") else 200)

    async def h_action(self, request: web.Request) -> web.Response:
        if not self.hits.allow(self.hits.ip(request)):
            return _json({"error": "Слишком часто — подожди минуту"}, 429)
        body = await _body(request)
        cog, guild = self._player()
        if cog is None:
            return _json(guild)
        res = await cog.web_action(guild, str(body.get("action") or ""), body.get("value"))
        return _json(res, 400 if res.get("error") else 200)


def _json(payload: Dict[str, Any], status: int = 200) -> web.Response:
    return web.json_response(payload, status=status, headers={"Cache-Control": "no-store"})


async def _body(request: web.Request) -> Dict[str, Any]:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


PAGE_HTML = r"""<!doctype html>
<html lang="ru"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>HALLEZ | Music Studio</title>
<style nonce="__NONCE__">
:root{--bg:#000;--surface:#0d0d0d;--surface2:#1a1a1a;--surface3:#262626;--text:#fff;--text2:#b3b3b3;
 --text3:#808080;--border:#333;--accent:#fff;--accent2:#d9d9d9}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
 min-height:100vh;overflow-x:hidden}
body::before{content:"";position:fixed;inset:0;background:radial-gradient(circle at 20% 50%,rgba(255,255,255,.03),transparent 50%),
 radial-gradient(circle at 80% 80%,rgba(255,255,255,.02),transparent 40%);pointer-events:none;z-index:0}
.container{max-width:1400px;margin:0 auto;padding:0 20px;position:relative;z-index:1}
.header{padding:25px 0;border-bottom:1px solid var(--border);margin-bottom:30px}
.header-top{display:flex;align-items:center;justify-content:space-between;margin-bottom:15px}
.brand{font-size:1.8rem;font-weight:700;letter-spacing:-1px}
.status{display:inline-flex;align-items:center;gap:8px;padding:8px 16px;background:var(--surface2);
 border:1px solid var(--border);font-size:.8rem;text-transform:uppercase;letter-spacing:1px}
.status-dot{width:6px;height:6px;background:var(--text3);border-radius:50%}
.status.active .status-dot{background:var(--accent);animation:pulse-dot 2s infinite}
@keyframes pulse-dot{0%,100%{opacity:1}50%{opacity:.3}}
.subtitle{color:var(--text2);font-size:.9rem}
.main-grid{display:grid;grid-template-columns:1fr 380px;gap:25px;margin-bottom:25px}
.player-section{background:var(--surface);border:1px solid var(--border);padding:30px}
.track-display{display:grid;grid-template-columns:180px 1fr;gap:25px;margin-bottom:30px}
.artwork{width:180px;height:180px;background:var(--surface2);border:1px solid var(--border);
 display:flex;align-items:center;justify-content:center;position:relative;overflow:hidden}
.artwork img{width:100%;height:100%;object-fit:cover}
.artwork-placeholder{font-size:4rem;opacity:.3}
.track-info{display:flex;flex-direction:column;justify-content:center}
.track-title{font-size:1.4rem;font-weight:600;margin-bottom:8px;line-height:1.3}
.track-meta{color:var(--text2);font-size:.9rem;margin-bottom:20px}
.progress-section{margin-top:auto}
.progress-time{display:flex;justify-content:space-between;font-size:.75rem;color:var(--text3);margin-bottom:6px}
.progress-bar{height:4px;background:var(--surface2);position:relative;cursor:pointer}
.progress-bar:hover{height:6px}
.progress-fill{height:100%;background:var(--accent);transition:width .3s linear}
.waveform{display:flex;gap:2px;height:50px;align-items:flex-end;margin-top:15px}
.wave-bar{flex:1;background:var(--surface3);min-height:4px;transition:all .3s}
.wave-bar.active{background:var(--accent)}
.controls-panel{background:var(--surface2);padding:20px;margin-top:25px;border:1px solid var(--border)}
.controls-row{display:flex;gap:12px;margin-bottom:15px}
.controls-row:last-child{margin-bottom:0}
.ctrl-btn{flex:1;padding:14px;background:var(--surface);border:1px solid var(--border);color:var(--text);
 cursor:pointer;font:inherit;transition:all .2s;text-align:center;font-size:.85rem;text-transform:uppercase;
 letter-spacing:.5px}
.ctrl-btn:hover{background:var(--surface3);border-color:var(--accent)}
.ctrl-btn.primary{background:var(--accent);color:var(--bg);border-color:var(--accent);font-weight:600}
.ctrl-btn.primary:hover{background:var(--accent2)}
.ctrl-btn.active{background:var(--surface3);border-color:var(--accent)}
.ctrl-btn:disabled{opacity:.3;cursor:not-allowed}
.sidebar{display:flex;flex-direction:column;gap:20px}
.panel{background:var(--surface);border:1px solid var(--border);padding:20px}
.panel-title{font-size:.75rem;text-transform:uppercase;letter-spacing:1px;color:var(--text3);margin-bottom:15px;
 padding-bottom:10px;border-bottom:1px solid var(--border)}
.input-group{margin-bottom:12px}
.input-group:last-child{margin-bottom:0}
.input-label{font-size:.75rem;color:var(--text2);margin-bottom:6px;display:block;text-transform:uppercase;letter-spacing:.5px}
.text-input{width:100%;padding:10px 12px;background:var(--surface2);border:1px solid var(--border);color:var(--text);
 font:inherit;font-size:.9rem}
.text-input:focus{outline:none;border-color:var(--accent);background:var(--surface3)}
.text-input::placeholder{color:var(--text3)}
.slider-group{margin-bottom:18px}
.slider-group:last-child{margin-bottom:0}
.slider-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
.slider-label{font-size:.75rem;color:var(--text2);text-transform:uppercase;letter-spacing:.5px}
.slider-value{font-size:.75rem;color:var(--text);font-weight:600}
.slider{-webkit-appearance:none;appearance:none;width:100%;height:3px;background:var(--surface2);outline:none;border:none}
.slider::-webkit-slider-thumb{-webkit-appearance:none;appearance:none;width:14px;height:14px;background:var(--accent);
 cursor:pointer;border:2px solid var(--bg)}
.slider::-moz-range-thumb{width:14px;height:14px;background:var(--accent);cursor:pointer;border:2px solid var(--bg)}
.queue-panel{max-height:400px;overflow-y:auto}
.queue-panel::-webkit-scrollbar{width:6px}
.queue-panel::-webkit-scrollbar-track{background:var(--surface2)}
.queue-panel::-webkit-scrollbar-thumb{background:var(--border)}
.queue-item{padding:12px;background:var(--surface2);border-left:2px solid transparent;margin-bottom:8px;
 transition:all .2s;cursor:pointer}
.queue-item:hover{background:var(--surface3);border-left-color:var(--accent)}
.queue-number{display:inline-block;width:24px;color:var(--text3);font-size:.75rem;font-weight:600}
.queue-title{font-size:.85rem;color:var(--text);margin-bottom:4px}
.queue-author{font-size:.75rem;color:var(--text3)}
.empty-state{padding:30px;text-align:center;color:var(--text3);font-size:.9rem}
.message{padding:10px 12px;font-size:.8rem;margin-top:8px;border-left:2px solid transparent}
.message.success{color:var(--text);border-left-color:var(--accent)}
.message.error{color:#ff6b6b;border-left-color:#ff6b6b}
.footer{padding:20px 0;text-align:center;color:var(--text3);font-size:.75rem;text-transform:uppercase;
 letter-spacing:1px;border-top:1px solid var(--border);margin-top:30px}
@media (max-width:1024px){
.main-grid{grid-template-columns:1fr}
.track-display{grid-template-columns:120px 1fr;gap:15px}
.artwork{width:120px;height:120px}
.controls-row{flex-wrap:wrap}
.ctrl-btn{flex:1 1 calc(50% - 6px)}
}
</style></head>
<body>
<div class="container">
<header class="header">
<div class="header-top">
<div class="brand">HALLEZ MUSIC</div>
<div class="status" id="status"><span class="status-dot"></span><span id="statusText">offline</span></div>
</div>
<div class="subtitle">Professional Music Control System</div>
</header>

<div class="main-grid">
<div class="player-section">
<div class="track-display">
<div class="artwork" id="artwork"><div class="artwork-placeholder">♪</div></div>
<div class="track-info">
<div class="track-title" id="trackTitle">No track playing</div>
<div class="track-meta" id="trackMeta">Select a track to start</div>
<div class="progress-section">
<div class="progress-time"><span id="currentTime">0:00</span><span id="totalTime">0:00</span></div>
<div class="progress-bar" id="progressBar"><div class="progress-fill" id="progressFill"></div></div>
</div>
</div>
</div>

<div class="waveform" id="waveform"></div>

<div class="controls-panel">
<div class="controls-row">
<button class="ctrl-btn" data-action="pause" id="pauseBtn">PAUSE</button>
<button class="ctrl-btn" data-action="skip">SKIP</button>
<button class="ctrl-btn" data-action="stop">STOP</button>
<button class="ctrl-btn" data-action="dj" id="djBtn">AUTO DJ</button>
</div>
</div>
</div>

<aside class="sidebar">
<div class="panel">
<div class="panel-title">Add Track</div>
<div class="input-group">
<label class="input-label">YouTube URL or Track Name</label>
<input type="text" class="text-input" id="trackInput" placeholder="Enter URL or search...">
</div>
<div class="input-group">
<label class="input-label">Your Name</label>
<input type="text" class="text-input" id="nickInput" placeholder="Your nickname">
</div>
<button class="ctrl-btn primary" id="addBtn">ADD TO QUEUE</button>
<div class="message" id="message"></div>
</div>

<div class="panel">
<div class="panel-title">Audio Settings</div>
<div class="slider-group">
<div class="slider-header"><span class="slider-label">Volume</span><span class="slider-value" id="volValue">50%</span></div>
<input type="range" class="slider" id="volSlider" min="5" max="100" step="5" value="50">
</div>
<div class="slider-group">
<div class="slider-header"><span class="slider-label">Bass</span><span class="slider-value" id="bassValue">0</span></div>
<input type="range" class="slider" id="bassSlider" min="-10" max="10" step="1" value="0">
</div>
<div class="slider-group">
<div class="slider-header"><span class="slider-label">Treble</span><span class="slider-value" id="trebleValue">0</span></div>
<input type="range" class="slider" id="trebleSlider" min="-10" max="10" step="1" value="0">
</div>
<div class="slider-group">
<div class="slider-header"><span class="slider-label">Speed</span><span class="slider-value" id="speedValue">1.0x</span></div>
<input type="range" class="slider" id="speedSlider" min="0.5" max="2" step="0.1" value="1">
</div>
</div>

<div class="panel">
<div class="panel-title">Queue (<span id="queueCount">0</span>)</div>
<div class="queue-panel" id="queueList"></div>
</div>
</aside>
</div>

<footer class="footer">Hallez Music Control System &bull; Advanced Audio Management</footer>
</div>

<script nonce="__NONCE__">
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const $ = id => document.getElementById(id);
let refreshTimer = null;

// Waveform bars
for(let i=0;i<30;i++){
const bar = document.createElement('div');
bar.className = 'wave-bar';
$('waveform').appendChild(bar);
}

function updateWaveform(){
const bars = document.querySelectorAll('.wave-bar');
bars.forEach((bar,i)=>{
const h = Math.random()*100;
bar.style.height = h + '%';
if(Math.random()>.7) bar.classList.add('active');
else bar.classList.remove('active');
});
}
setInterval(updateWaveform,150);

function fmt(sec){
if(!sec) return '0:00';
sec = Math.round(sec);
const m = Math.floor(sec/60);
const s = sec%60;
return m+':'+(s<10?'0':'')+s;
}

async function api(path,body){
const init = {method:body?'POST':'GET',cache:'no-store',
headers:{'X-Requested-With':'hz-music'}};
if(body){init.headers['Content-Type']='application/json';init.body=JSON.stringify(body);}
const r = await fetch(path,init);
let data = {};
try{data = await r.json();}catch(e){}
if(!r.ok) throw new Error(data.error||'Error '+r.status);
return data;
}

function showMsg(text,type){
const msg = $('message');
msg.textContent = text;
msg.className = 'message '+(type||'');
setTimeout(()=>msg.className='message',3000);
}

function updateUI(state){
// Status
const status = $('status');
const statusText = $('statusText');
if(state.connected){
status.classList.add('active');
statusText.textContent = 'live: '+( state.channel||'voice');
}else{
status.classList.remove('active');
statusText.textContent = 'offline';
}

// Track
const cur = state.current;
if(cur){
$('trackTitle').textContent = cur.title||'Unknown';
$('trackMeta').textContent = 'Requested by '+cur.who;
$('currentTime').textContent = fmt(cur.elapsed);
$('totalTime').textContent = fmt(cur.duration);
const pct = cur.duration ? (cur.elapsed/cur.duration*100):0;
$('progressFill').style.width = pct+'%';

if(cur.thumb){
$('artwork').innerHTML = '<img src="'+esc(cur.thumb)+'" alt="">';
}else{
$('artwork').innerHTML = '<div class="artwork-placeholder">♪</div>';
}
}else{
$('trackTitle').textContent = 'No track playing';
$('trackMeta').textContent = 'Queue is empty';
$('currentTime').textContent = '0:00';
$('totalTime').textContent = '0:00';
$('progressFill').style.width = '0%';
$('artwork').innerHTML = '<div class="artwork-placeholder">♪</div>';
}

// Volume
$('volSlider').value = state.volume||50;
$('volValue').textContent = (state.volume||50)+'%';

// DJ
$('djBtn').classList.toggle('active',!!state.dj);

// Queue
const qlist = $('queueList');
const queue = state.queue||[];
$('queueCount').textContent = queue.length;
qlist.innerHTML = '';
if(queue.length){
queue.slice(0,20).forEach((t,i)=>{
const item = document.createElement('div');
item.className = 'queue-item';
item.innerHTML = '<span class="queue-number">'+(i+1)+'</span>'+
'<div class="queue-title">'+esc(t.title)+'</div>'+
'<div class="queue-author">'+esc(t.who)+'</div>';
qlist.appendChild(item);
});
if(queue.length>20){
const more = document.createElement('div');
more.className = 'empty-state';
more.textContent = '+'+(queue.length-20)+' more tracks';
qlist.appendChild(more);
}
}else{
qlist.innerHTML = '<div class="empty-state">Queue is empty</div>';
}
}

async function refresh(){
try{
const state = await api('/music/api/state');
updateUI(state);
}catch(e){
console.error(e);
}
}

// Controls
document.querySelectorAll('[data-action]').forEach(btn=>{
btn.addEventListener('click',async()=>{
const action = btn.dataset.action;
btn.disabled = true;
try{
await api('/music/api/action',{action});
showMsg('Done','success');
await refresh();
}catch(e){
showMsg(e.message,'error');
}
btn.disabled = false;
});
});

// Add track
$('addBtn').addEventListener('click',async()=>{
const query = $('trackInput').value.trim();
const nick = $('nickInput').value.trim();
if(!query){showMsg('Enter track URL or name','error');return;}
$('addBtn').disabled = true;
$('addBtn').textContent = 'ADDING...';
try{
await api('/music/api/order',{query,nick:nick||'Guest'});
$('trackInput').value = '';
showMsg('Track added to queue','success');
localStorage.setItem('nick',nick);
await refresh();
}catch(e){
showMsg(e.message,'error');
}
$('addBtn').disabled = false;
$('addBtn').textContent = 'ADD TO QUEUE';
});

// Volume
$('volSlider').addEventListener('input',()=>{
const v = $('volSlider').value;
$('volValue').textContent = v+'%';
});
$('volSlider').addEventListener('change',async()=>{
try{
await api('/music/api/action',{action:'volume',value:Number($('volSlider').value)});
}catch(e){
showMsg(e.message,'error');
}
});

// Audio settings (visual only for now)
['bass','treble','speed'].forEach(name=>{
const slider = $(name+'Slider');
const value = $(name+'Value');
slider.addEventListener('input',()=>{
let v = slider.value;
if(name==='speed') v = v+'x';
value.textContent = v;
});
});

// Load saved nick
const savedNick = localStorage.getItem('nick');
if(savedNick) $('nickInput').value = savedNick;

// Auto refresh
refresh();
refreshTimer = setInterval(refresh,3000);
document.addEventListener('visibilitychange',()=>{
if(document.hidden){
if(refreshTimer) clearInterval(refreshTimer);
refreshTimer = null;
}else if(!refreshTimer){
refresh();
refreshTimer = setInterval(refresh,3000);
}
});
</script>
</body></html>
"""


def setup(bot: commands.Bot):
    bot.add_cog(MusicWeb(bot))
