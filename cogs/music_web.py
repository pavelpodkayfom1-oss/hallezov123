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
<title>HALLEZ · музыка семьи</title>
<style nonce="__NONCE__">
:root{--bg:#08080a;--surface:#0e0e12;--surface2:#15151b;--surface3:#1d1d25;--line:#26262f;
 --text:#f4f4f7;--text2:#a6a6b2;--text3:#6e6e7b;--accent:#f4f4f7;--red:#e03e4c;--ok:#3dd68c}
*{box-sizing:border-box;margin:0;padding:0}
html{-webkit-text-size-adjust:100%}
body{background:var(--bg);color:var(--text);min-height:100vh;overflow-x:hidden;
 font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
body::before{content:"";position:fixed;inset:0;z-index:0;pointer-events:none;
 background:radial-gradient(60% 45% at 12% -8%,rgba(224,62,76,.10),transparent 70%),
 radial-gradient(50% 40% at 92% 105%,rgba(255,255,255,.045),transparent 70%)}
.container{max-width:1240px;margin:0 auto;padding:0 20px;position:relative;z-index:1}

.header{padding:26px 0 18px;border-bottom:1px solid var(--line);margin-bottom:26px}
.header-top{display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.brand{font-size:1.55rem;font-weight:700;letter-spacing:-.5px}
.brand span{color:var(--text3);font-weight:400;letter-spacing:.14em;font-size:.78rem;
 text-transform:uppercase;margin-left:.55rem}
.status{display:inline-flex;align-items:center;gap:8px;padding:7px 13px;background:var(--surface2);
 border:1px solid var(--line);font-size:.72rem;text-transform:uppercase;letter-spacing:.1em;color:var(--text2)}
.status-dot{width:6px;height:6px;border-radius:50%;background:var(--text3)}
.status.active{color:var(--text);border-color:rgba(224,62,76,.4)}
.status.active .status-dot{background:var(--red);animation:pulse 1.8s infinite}
@keyframes pulse{0%,100%{opacity:1;box-shadow:0 0 0 0 rgba(224,62,76,.5)}50%{opacity:.55;box-shadow:0 0 0 5px rgba(224,62,76,0)}}
.subtitle{color:var(--text3);font-size:.86rem;margin-top:8px}

.main-grid{display:grid;grid-template-columns:1fr 360px;gap:22px;margin-bottom:22px}
.card{background:var(--surface);border:1px solid var(--line)}
.player-section{padding:26px}
.track-display{display:grid;grid-template-columns:170px 1fr;gap:24px}
.artwork{width:170px;height:170px;background:var(--surface2);border:1px solid var(--line);
 display:flex;align-items:center;justify-content:center;overflow:hidden;position:relative}
.artwork img{width:100%;height:100%;object-fit:cover;display:block}
.artwork.is-live::after{content:"";position:absolute;inset:0;box-shadow:inset 0 0 0 1px rgba(224,62,76,.45)}
.artwork-placeholder{font-size:3.2rem;color:var(--text3)}
.track-info{display:flex;flex-direction:column;min-width:0}
.track-title{font-size:1.3rem;font-weight:600;line-height:1.3;word-break:break-word}
.track-meta{color:var(--text2);font-size:.86rem;margin-top:6px}
.chips{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}
.chip{font-size:.7rem;text-transform:uppercase;letter-spacing:.08em;color:var(--text3);
 border:1px solid var(--line);padding:5px 9px;background:var(--surface2)}
.chip b{color:var(--text);font-weight:600;letter-spacing:0}
.progress-section{margin-top:auto;padding-top:22px}
.progress-time{display:flex;justify-content:space-between;font-size:.74rem;color:var(--text3);
 margin-bottom:7px;font-variant-numeric:tabular-nums}
.progress-bar{height:3px;background:var(--surface3);position:relative;overflow:hidden}
.progress-fill{height:100%;width:0;background:var(--accent);transition:width .9s linear}
.progress-fill::after{content:"";position:absolute;right:0;top:-2px;width:2px;height:7px;background:var(--accent)}
.waveform{display:flex;align-items:flex-end;gap:3px;height:44px;margin-top:20px;opacity:.5;
 transition:opacity .4s}
.waveform.on{opacity:1}
.wave-bar{flex:1;height:12%;background:var(--surface3);transform-origin:bottom;border-radius:1px}
.waveform.on .wave-bar{background:linear-gradient(180deg,var(--accent),#6d6d78);
 animation:wv 1.15s ease-in-out infinite alternate;animation-play-state:paused}
.waveform.playing .wave-bar{animation-play-state:running}
@keyframes wv{from{height:12%}to{height:var(--h,70%)}}

.controls-panel{background:var(--surface2);border:1px solid var(--line);padding:16px;margin-top:22px}
.controls-row{display:flex;gap:10px;flex-wrap:wrap}
.ctrl-btn{flex:1;min-width:110px;padding:13px 10px;background:var(--surface);border:1px solid var(--line);
 color:var(--text);cursor:pointer;font:inherit;font-size:.78rem;text-transform:uppercase;letter-spacing:.09em;
 transition:background .18s,border-color .18s,transform .12s,color .18s}
.ctrl-btn:hover{border-color:var(--accent);background:var(--surface3)}
.ctrl-btn:active{transform:translateY(1px)}
.ctrl-btn.primary{background:var(--accent);color:var(--bg);border-color:var(--accent);font-weight:600}
.ctrl-btn.primary:hover{background:#fff}
.ctrl-btn.on{border-color:var(--red);color:var(--red)}
.ctrl-btn:disabled{opacity:.35;cursor:default;transform:none}
.hint{color:var(--text3);font-size:.76rem;margin-top:12px}
.btn-wide{display:block;width:100%;min-width:0}
.queue-more{padding:6px 10px;font-size:.74rem;color:var(--text3)}

.sidebar{display:flex;flex-direction:column;gap:18px}
.panel{padding:18px}
.panel-title{font-size:.72rem;text-transform:uppercase;letter-spacing:.1em;color:var(--text3);
 margin-bottom:14px;padding-bottom:9px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between}
.panel-title b{color:var(--text);letter-spacing:0}
.input-group{margin-bottom:11px}
.input-label{display:block;font-size:.72rem;color:var(--text2);margin-bottom:6px;text-transform:uppercase;letter-spacing:.06em}
.text-input{width:100%;padding:11px 12px;background:var(--surface2);border:1px solid var(--line);color:var(--text);
 font:inherit;font-size:.9rem;transition:border-color .18s,background .18s}
.text-input:focus{outline:none;border-color:var(--accent);background:var(--surface3)}
.text-input::placeholder{color:var(--text3)}
.slider-row{display:flex;align-items:center;gap:12px;margin-top:14px}
.slider{-webkit-appearance:none;appearance:none;flex:1;height:3px;background:var(--surface3);border:none;outline:none}
.slider::-webkit-slider-thumb{-webkit-appearance:none;width:14px;height:14px;background:var(--accent);
 border:2px solid var(--bg);cursor:pointer;border-radius:50%}
.slider::-moz-range-thumb{width:14px;height:14px;background:var(--accent);border:2px solid var(--bg);cursor:pointer;border-radius:50%}
.slider-value{font-size:.78rem;color:var(--text);min-width:38px;text-align:right;font-variant-numeric:tabular-nums}
.queue-panel{max-height:340px;overflow-y:auto;margin:0 -4px}
.queue-panel::-webkit-scrollbar{width:6px}
.queue-panel::-webkit-scrollbar-track{background:transparent}
.queue-panel::-webkit-scrollbar-thumb{background:var(--line)}
.queue-item{display:flex;gap:10px;padding:11px 10px;background:var(--surface2);border-left:2px solid transparent;
 margin-bottom:7px;transition:background .18s,border-color .18s,transform .18s}
.queue-item:hover{background:var(--surface3);border-left-color:var(--red);transform:translateX(2px)}
.queue-number{color:var(--text3);font-size:.74rem;font-weight:600;min-width:18px;font-variant-numeric:tabular-nums}
.queue-body{min-width:0}
.queue-title{font-size:.86rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.queue-author{font-size:.74rem;color:var(--text3);margin-top:2px}
.empty-state{padding:26px 14px;text-align:center;color:var(--text3);font-size:.85rem;line-height:1.5}
.empty-state span{display:block;font-size:1.5rem;margin-bottom:8px;opacity:.55}
.message{padding:0;font-size:.8rem;margin-top:10px;min-height:1.2em;color:transparent;transition:color .2s}
.message.success{color:var(--ok)}
.message.error{color:#ff6b73}
.footer{padding:20px 0 34px;text-align:center;color:var(--text3);font-size:.75rem;letter-spacing:.04em}
@media (max-width:1024px){
 .main-grid{grid-template-columns:1fr}
 .track-display{grid-template-columns:130px 1fr;gap:18px}
 .artwork{width:130px;height:130px}
}
@media (max-width:560px){
 .container{padding:0 14px}
 .player-section{padding:18px}
 .track-display{grid-template-columns:1fr;gap:16px}
 .artwork{width:100%;height:190px}
 .ctrl-btn{min-width:calc(50% - 5px);flex:none}
 .brand{font-size:1.3rem}
 .brand span{display:block;margin:4px 0 0}
}
</style></head>
<body>
<div class="container">
<header class="header">
<div class="header-top">
<div class="brand">HALLEZ<span>музыка семьи</span></div>
<div class="status" id="status"><span class="status-dot"></span><span id="statusText">подключаемся…</span></div>
</div>
<div class="subtitle">Заказываете отсюда — трек попадает в общую очередь и играет в голосовом канале.</div>
</header>

<div class="main-grid">
<div class="card player-section">
<div class="track-display">
<div class="artwork" id="artwork"><div class="artwork-placeholder">♪</div></div>
<div class="track-info">
<div class="track-title" id="trackTitle">Сейчас ничего не играет</div>
<div class="track-meta" id="trackMeta">Закажи трек справа — он встанет в очередь.</div>
<div class="chips">
<div class="chip">очередь <b id="chipQueue">0</b></div>
<div class="chip">громкость <b id="chipVol">—</b></div>
<div class="chip">диджей <b id="chipDj">выкл</b></div>
</div>
<div class="progress-section">
<div class="progress-time"><span id="currentTime">0:00</span><span id="totalTime">0:00</span></div>
<div class="progress-bar"><div class="progress-fill" id="progressFill"></div></div>
</div>
</div>
</div>

<div class="waveform" id="waveform"></div>

<div class="controls-panel">
<div class="controls-row">
<button class="ctrl-btn primary" data-action="pause" id="pauseBtn">Пауза</button>
<button class="ctrl-btn" data-action="skip">Пропустить</button>
<button class="ctrl-btn" data-action="dj" id="djBtn">Авто-диджей</button>
<button class="ctrl-btn" data-action="stop">Стоп</button>
</div>
<div class="slider-row">
<input type="range" class="slider" id="volSlider" min="5" max="100" step="5" value="50" aria-label="Громкость">
<span class="slider-value" id="volValue">50%</span>
</div>
<div class="hint" id="hint">«Стоп» чистит всю очередь — он для своих.</div>
</div>
</div>

<aside class="sidebar">
<div class="card panel">
<div class="panel-title">Заказать трек</div>
<div class="input-group">
<label class="input-label" for="trackInput">Что включить</label>
<input type="text" class="text-input" id="trackInput" maxlength="200" autocomplete="off"
 placeholder="Имя из библиотеки или ссылка">
</div>
<div class="input-group">
<label class="input-label" for="nickInput">Кто заказал</label>
<input type="text" class="text-input" id="nickInput" maxlength="32" autocomplete="off" placeholder="Твоё имя">
</div>
<button class="ctrl-btn primary btn-wide" id="addBtn">В очередь</button>
<div class="message" id="message">&nbsp;</div>
</div>

<div class="card panel">
<div class="panel-title">Очередь <b id="queueCount">0</b></div>
<div class="queue-panel" id="queueList"></div>
</div>
</aside>
</div>

<footer class="footer">Общий плеер семьи · заказы видны всем в канале</footer>
</div>

<script nonce="__NONCE__">
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const $ = id => document.getElementById(id);
let refreshTimer = null;
let clock = {elapsed:0, duration:0, at:0, running:false};

// полоса из 44 столбиков: высота и задержка задаются один раз, дальше крутит CSS
(function buildWave(){
 const box = $('waveform'), frag = document.createDocumentFragment();
 for(let i=0;i<44;i++){
  const bar = document.createElement('div');
  bar.className = 'wave-bar';
  bar.style.setProperty('--h', (28 + Math.abs(Math.sin(i*1.7))*70).toFixed(0) + '%');
  bar.style.animationDelay = (i % 11) * 0.09 + 's';
  bar.style.animationDuration = (0.85 + (i % 5) * 0.13).toFixed(2) + 's';
  frag.appendChild(bar);
 }
 box.appendChild(frag);
})();

function fmt(sec){
 if(!sec || sec < 0) return '0:00';
 sec = Math.round(sec);
 return Math.floor(sec/60) + ':' + String(sec%60).padStart(2,'0');
}

async function api(path, body){
 const init = {method: body ? 'POST' : 'GET', cache:'no-store',
  headers:{'X-Requested-With':'hz-music'}};
 if(body){ init.headers['Content-Type']='application/json'; init.body=JSON.stringify(body); }
 const r = await fetch(path, init);
 let data = {};
 try{ data = await r.json(); }catch(e){ /* бот перезапустился — вместо json прилетела страница */ }
 if(!r.ok) throw new Error(data.error || ('Сервер ответил ' + r.status));
 return data;
}

function showMsg(text, type){
 const msg = $('message');
 msg.textContent = text;
 msg.className = 'message ' + (type || '');
 clearTimeout(showMsg._t);
 showMsg._t = setTimeout(()=>{ msg.className='message'; msg.innerHTML='&nbsp;'; }, 3200);
}

// прогресс дорисовываем между опросами, чтобы ползунок шёл плавно, а не скачками раз в 3 сек
function tick(){
 if(clock.running && clock.duration){
  const sec = clock.elapsed + (Date.now() - clock.at)/1000;
  const pct = Math.min(100, sec/clock.duration*100);
  $('progressFill').style.width = pct + '%';
  $('currentTime').textContent = fmt(Math.min(sec, clock.duration));
 }
}
setInterval(tick, 1000);

function updateUI(state){
 const live = !!state.connected, playing = !!state.playing, paused = !!state.paused;
 const status = $('status');
 status.classList.toggle('active', live);
 $('statusText').textContent = live
  ? (playing ? 'в эфире · ' + (state.channel || 'голосовой') : (paused ? 'на паузе' : 'ждёт заказов'))
  : 'бот не в голосовом';

 const wave = $('waveform');
 wave.classList.toggle('on', playing || paused);
 wave.classList.toggle('playing', playing);

 const cur = state.current;
 if(cur){
  $('trackTitle').textContent = cur.title || 'без названия';
  $('trackMeta').textContent = 'заказал ' + (cur.who || 'кто-то');
  clock = {elapsed: cur.elapsed || 0, duration: cur.duration || 0, at: Date.now(), running: playing};
  $('totalTime').textContent = fmt(cur.duration);
  $('currentTime').textContent = fmt(cur.elapsed);
  $('progressFill').style.width = (cur.duration ? Math.min(100, cur.elapsed/cur.duration*100) : 0) + '%';
  $('artwork').classList.toggle('is-live', playing);
  $('artwork').innerHTML = cur.thumb
   ? '<img src="' + esc(cur.thumb) + '" alt="">'
   : '<div class="artwork-placeholder">♪</div>';
 }else{
  clock = {elapsed:0, duration:0, at:0, running:false};
  $('trackTitle').textContent = playing ? 'звучит' : 'Сейчас ничего не играет';
  $('trackMeta').textContent = live ? 'Очередь пуста — закажи первым.' : 'Боту нужно зайти в голосовой канал.';
  $('currentTime').textContent = '0:00';
  $('totalTime').textContent = '0:00';
  $('progressFill').style.width = '0%';
  $('artwork').classList.remove('is-live');
  $('artwork').innerHTML = '<div class="artwork-placeholder">♪</div>';
 }

 const vol = state.volume || 0;
 $('volSlider').value = vol;
 $('volValue').textContent = vol + '%';
 $('chipVol').textContent = vol + '%';
 $('chipQueue').textContent = (state.queue || []).length;
 $('chipDj').textContent = state.dj ? 'вкл' : 'выкл';
 $('djBtn').classList.toggle('on', !!state.dj);
 $('pauseBtn').textContent = paused ? 'Продолжить' : 'Пауза';
 $('pauseBtn').disabled = !(playing || paused);

 const qlist = $('queueList'), queue = state.queue || [];
 $('queueCount').textContent = queue.length;
 qlist.innerHTML = '';
 if(queue.length){
  queue.slice(0, 20).forEach((t, i) => {
   const item = document.createElement('div');
   item.className = 'queue-item';
   item.innerHTML = '<div class="queue-number">' + (i+1) + '</div>' +
    '<div class="queue-body"><div class="queue-title">' + esc(t.title) + '</div>' +
    '<div class="queue-author">' + esc(t.who) + '</div></div>';
   qlist.appendChild(item);
  });
  if(queue.length > 20){
   const more = document.createElement('div');
   more.className = 'queue-more';
   more.textContent = 'и ещё ' + (queue.length - 20);
   qlist.appendChild(more);
  }
 }else{
  qlist.innerHTML = '<div class="empty-state"><span>♪</span>Очередь пустая. ' +
   'Напиши имя трека справа — он заиграет следом.</div>';
 }
}

async function refresh(){
 try{ updateUI(await api('/music/api/state')); }
 catch(e){ $('statusText').textContent = 'связь потеряна'; }
}

async function act(action, value){
 try{
  const r = await api('/music/api/action', value === undefined ? {action} : {action, value});
  showMsg(r.did || 'Готово', 'success');
  await refresh();
 }catch(e){ showMsg(e.message, 'error'); }
}

document.querySelectorAll('[data-action]').forEach(btn => {
 btn.addEventListener('click', async () => {
  btn.disabled = true;
  await act(btn.dataset.action);
  btn.disabled = false;
 });
});

async function order(){
 const query = $('trackInput').value.trim();
 const nick = $('nickInput').value.trim();
 if(!query){ showMsg('Напиши имя трека или вставь ссылку', 'error'); $('trackInput').focus(); return; }
 const btn = $('addBtn');
 btn.disabled = true; btn.textContent = 'Ставим…';
 try{
  const r = await api('/music/api/order', {query, nick: nick || 'гость'});
  $('trackInput').value = '';
  showMsg(r.did || 'Трек в очереди', 'success');
  if(nick) localStorage.setItem('nick', nick);
  await refresh();
 }catch(e){ showMsg(e.message, 'error'); }
 btn.disabled = false; btn.textContent = 'В очередь';
}

$('addBtn').addEventListener('click', order);
$('trackInput').addEventListener('keydown', e => { if(e.key === 'Enter') order(); });
$('volSlider').addEventListener('change', () => act('volume', Number($('volSlider').value)));
$('volSlider').addEventListener('input', () => { $('volValue').textContent = $('volSlider').value + '%'; });

const saved = localStorage.getItem('nick');
if(saved) $('nickInput').value = saved;

refresh();
refreshTimer = setInterval(refresh, 3000);
document.addEventListener('visibilitychange', () => {
 if(document.hidden){ clearInterval(refreshTimer); refreshTimer = null; }
 else if(!refreshTimer){ refresh(); refreshTimer = setInterval(refresh, 3000); }
});
</script>
</body></html>
"""


def setup(bot: commands.Bot):
    bot.add_cog(MusicWeb(bot))
