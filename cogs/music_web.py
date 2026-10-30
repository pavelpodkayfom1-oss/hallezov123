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


# Хостинг наружу отдаёт один адрес, и мы его видим только в заголовках живых
# запросов. Запоминаем, чтобы кнопка «Сайт» на панели в Discord появилась сама,
# без ручной строчки в .env.
_front: Dict[str, str] = {}


def _note_front(request: web.Request) -> None:
    host = (request.headers.get("X-Forwarded-Host") or request.headers.get("Host") or "").strip()
    if not host or host.startswith(("127.", "localhost", "0.0.0.0")):
        return
    secure = request.headers.get("X-Forwarded-Proto", "").lower() == "https" or request.is_secure()
    _front["url"] = f"{'https' if secure else 'http'}://{host}"


def public_url() -> str:
    """Наружный адрес сайта музыки: пустой, пока бот ещё не видел ни одного входа.

    Без косой черты на конце: `/music` сам перенаправляет на страницу, а короткая
    ссылка людям понятнее.
    """
    base = _front.get("url") or ""
    if not base:
        return ""
    return base + (PREFIX if _front.get("share") else "/")


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
    _note_front(request)
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
        _front["share"] = "1" if self.share_front else ""

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
<title>HALLEZ | DJ Studio</title>
<style nonce="__NONCE__">
:root{--bg:#000;--surface:#0a0a0a;--surface2:#141414;--surface3:#1f1f1f;--text:#fff;--text2:#999;
 --text3:#666;--border:#222;--accent:#00ff88;--accent2:#00cc6a;--vinyl:#1a1a1a;--glow:rgba(0,255,136,.3)}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
 min-height:100vh;overflow-x:hidden;position:relative}
body::before{content:"";position:fixed;inset:0;background:
 radial-gradient(circle at 30% 40%,rgba(0,255,136,.04),transparent 50%),
 radial-gradient(circle at 70% 60%,rgba(0,100,200,.03),transparent 40%);pointer-events:none;z-index:0}
.container{max-width:1600px;margin:0 auto;padding:20px;position:relative;z-index:1}
.header{padding:20px 0;border-bottom:1px solid var(--border);margin-bottom:25px;display:flex;
 align-items:center;justify-content:space-between}
.brand{font-size:2rem;font-weight:800;letter-spacing:-1.5px;background:linear-gradient(135deg,var(--accent),#00aaff);
 -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}
.status-badge{display:inline-flex;align-items:center;gap:8px;padding:8px 14px;background:var(--surface2);
 border:1px solid var(--border);font-size:.75rem;text-transform:uppercase;letter-spacing:1.2px}
.status-dot{width:7px;height:7px;background:var(--text3);border-radius:50%;transition:all .3s}
.status-badge.live .status-dot{background:var(--accent);box-shadow:0 0 10px var(--glow);animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.5}}
.main-grid{display:grid;grid-template-columns:minmax(0,1fr) 380px;gap:25px;margin-bottom:25px}
.deck-section{background:var(--surface);border:1px solid var(--border);padding:35px;position:relative}
.vinyl-display{display:grid;grid-template-columns:220px minmax(0,1fr);gap:30px;margin-bottom:30px;align-items:center}
.vinyl-container{width:220px;height:220px;position:relative;display:flex;align-items:center;justify-content:center}
.vinyl-disc{width:100%;height:100%;background:radial-gradient(circle,#0a0a0a 0%,#1a1a1a 30%,#0f0f0f 70%,#050505 100%);
 border-radius:50%;position:relative;box-shadow:0 8px 30px rgba(0,0,0,.7),inset 0 0 40px rgba(255,255,255,.03);
 transition:transform .8s cubic-bezier(.4,0,.2,1)}
.vinyl-disc.spinning{animation:spin 3s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.vinyl-disc::before{content:"";position:absolute;inset:20%;background:var(--bg);border-radius:50%;
 box-shadow:0 0 20px rgba(0,0,0,.8)}
.vinyl-disc::after{content:"";position:absolute;inset:45%;background:var(--surface3);border-radius:50%}
.vinyl-label{position:absolute;inset:25%;border-radius:50%;background:linear-gradient(135deg,var(--surface2),var(--surface));
 display:flex;align-items:center;justify-content:center;border:1px solid var(--border)}
.vinyl-label img{width:80%;height:80%;border-radius:50%;object-fit:cover}
.vinyl-icon{font-size:2rem;opacity:.4}
.track-info{display:flex;flex-direction:column;justify-content:center;min-width:0}
.track-title{font-size:1.5rem;font-weight:700;margin-bottom:10px;line-height:1.3;white-space:nowrap;
 overflow:hidden;text-overflow:ellipsis}
.track-meta{color:var(--text2);font-size:.9rem;margin-bottom:20px}
.progress-section{margin-top:auto}
.progress-time{display:flex;justify-content:space-between;font-size:.75rem;color:var(--text3);margin-bottom:8px;
 font-variant-numeric:tabular-nums}
.progress-bar{height:5px;background:var(--surface2);position:relative;cursor:pointer;border-radius:3px;overflow:hidden}
.progress-bar:hover{height:7px}
.progress-fill{height:100%;background:linear-gradient(90deg,var(--accent),var(--accent2));transition:width .3s linear;
 box-shadow:0 0 8px var(--glow)}
.visualizer{height:80px;background:var(--surface2);margin-top:25px;border:1px solid var(--border);position:relative;
 overflow:hidden;display:flex;align-items:flex-end;gap:2px;padding:5px}
.vis-bar{flex:1;background:var(--surface3);min-height:3px;transition:all .15s;border-radius:2px 2px 0 0}
.vis-bar.active{background:linear-gradient(to top,var(--accent2),var(--accent));box-shadow:0 0 6px var(--glow)}
.dj-panel{background:var(--surface2);padding:25px;margin-top:25px;border:1px solid var(--border);position:relative}
.dj-panel::before{content:"DJ CONTROL";position:absolute;top:8px;left:12px;font-size:.65rem;
 text-transform:uppercase;letter-spacing:1.5px;color:var(--text3)}
.dj-grid{display:grid;grid-template-columns:210px minmax(0,1fr);gap:22px 26px;align-items:center;margin-top:15px}
.transport{display:flex;gap:10px}
.transport-btn{width:56px;height:56px;background:var(--surface);border:2px solid var(--border);color:var(--text);
 cursor:pointer;font-size:1.3rem;transition:all .2s;display:flex;align-items:center;justify-content:center;
 position:relative}
.transport-btn:hover{background:var(--surface3);border-color:var(--accent);transform:translateY(-2px)}
.transport-btn.play{border-color:var(--accent);background:var(--accent);color:var(--bg)}
.transport-btn.play:hover{background:var(--accent2)}
.transport-btn:disabled{opacity:.3;cursor:not-allowed;transform:none}
.knobs{display:grid;grid-template-columns:repeat(3,1fr);gap:15px}
.knob-group{display:flex;flex-direction:column;align-items:center;gap:8px}
.knob-label{font-size:.7rem;text-transform:uppercase;letter-spacing:1px;color:var(--text3)}
.knob{width:70px;height:70px;background:radial-gradient(circle at 30% 30%,var(--surface3),var(--surface));
 border-radius:50%;position:relative;cursor:pointer;border:3px solid var(--border);transition:all .2s}
.knob:hover{border-color:var(--accent);box-shadow:0 0 15px var(--glow)}
.knob::before{content:"";position:absolute;top:8px;left:50%;width:3px;height:28px;background:var(--accent);
 transform:translateX(-50%);border-radius:2px;box-shadow:0 0 8px var(--glow)}
.knob-value{font-size:.75rem;color:var(--text);font-weight:600;min-width:45px;text-align:center}
.pads{grid-column:1 / -1;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-top:15px}
.pad{padding:16px;background:var(--surface);border:2px solid var(--border);color:var(--text);cursor:pointer;
 font:inherit;transition:all .2s;text-align:center;font-size:.8rem;text-transform:uppercase;letter-spacing:.8px;
 white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pad:hover{background:var(--surface3);border-color:var(--accent);box-shadow:0 0 12px var(--glow)}
.pad.active{background:var(--surface3);border-color:var(--accent);box-shadow:inset 0 0 20px var(--glow)}
.pad:disabled{opacity:.25;cursor:not-allowed}
.sidebar{display:flex;flex-direction:column;gap:20px;min-width:0}
.panel{background:var(--surface);border:1px solid var(--border);padding:22px}
.panel-title{font-size:.7rem;text-transform:uppercase;letter-spacing:1.5px;color:var(--text3);margin-bottom:18px;
 padding-bottom:12px;border-bottom:1px solid var(--border)}
.input-group{margin-bottom:14px}
.input-group:last-of-type{margin-bottom:0}
.input-label{font-size:.7rem;color:var(--text2);margin-bottom:7px;display:block;text-transform:uppercase;
 letter-spacing:.8px}
.text-input{width:100%;padding:11px 13px;background:var(--surface2);border:1px solid var(--border);color:var(--text);
 font:inherit;font-size:.9rem;transition:all .2s}
.text-input:focus{outline:none;border-color:var(--accent);background:var(--surface3);box-shadow:0 0 8px var(--glow)}
.text-input::placeholder{color:var(--text3)}
.btn-primary{width:100%;padding:14px;background:var(--accent);color:var(--bg);border:none;cursor:pointer;
 font:inherit;font-weight:700;font-size:.85rem;text-transform:uppercase;letter-spacing:1px;transition:all .2s;
 margin-top:14px}
.btn-primary:hover{background:var(--accent2);box-shadow:0 0 15px var(--glow)}
.btn-primary:disabled{opacity:.4;cursor:not-allowed}
.volume-control{margin-bottom:20px}
.volume-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}
.volume-label{font-size:.7rem;color:var(--text2);text-transform:uppercase;letter-spacing:.8px}
.volume-value{font-size:.9rem;color:var(--accent);font-weight:700;font-variant-numeric:tabular-nums}
.volume-slider{-webkit-appearance:none;appearance:none;width:100%;height:6px;background:var(--surface2);
 outline:none;border:none;border-radius:3px;position:relative}
.volume-slider::-webkit-slider-thumb{-webkit-appearance:none;appearance:none;width:18px;height:18px;
 background:var(--accent);cursor:pointer;border-radius:50%;box-shadow:0 0 10px var(--glow);transition:all .2s}
.volume-slider::-webkit-slider-thumb:hover{transform:scale(1.2)}
.volume-slider::-moz-range-thumb{width:18px;height:18px;background:var(--accent);cursor:pointer;border:none;
 border-radius:50%;box-shadow:0 0 10px var(--glow)}
.queue-panel{max-height:380px;overflow-y:auto;margin-top:10px}
.queue-panel::-webkit-scrollbar{width:5px}
.queue-panel::-webkit-scrollbar-track{background:var(--surface2)}
.queue-panel::-webkit-scrollbar-thumb{background:var(--border);border-radius:3px}
.queue-panel::-webkit-scrollbar-thumb:hover{background:var(--text3)}
.queue-item{padding:12px;background:var(--surface2);border-left:3px solid transparent;margin-bottom:8px;
 transition:all .2s;cursor:pointer}
.queue-item:hover{background:var(--surface3);border-left-color:var(--accent);transform:translateX(3px)}
.queue-number{display:inline-block;width:28px;color:var(--text3);font-size:.7rem;font-weight:700;
 font-variant-numeric:tabular-nums}
.queue-title{font-size:.85rem;color:var(--text);margin-bottom:5px;white-space:nowrap;overflow:hidden;
 text-overflow:ellipsis}
.queue-author{font-size:.7rem;color:var(--text3)}
.empty-state{padding:35px 20px;text-align:center;color:var(--text3);font-size:.85rem}
.message{padding:11px 13px;font-size:.8rem;margin-top:10px;border-left:3px solid transparent;background:var(--surface2);
 transition:all .3s;opacity:0;max-height:0;overflow:hidden}
.message.show{opacity:1;max-height:100px;margin-top:10px}
.message.success{color:var(--accent);border-left-color:var(--accent);background:rgba(0,255,136,.08)}
.message.error{color:#ff5555;border-left-color:#ff5555;background:rgba(255,85,85,.08)}
.footer{padding:20px 0;text-align:center;color:var(--text3);font-size:.7rem;text-transform:uppercase;
 letter-spacing:1.5px;border-top:1px solid var(--border);margin-top:30px}
@media (max-width:1100px){
 .main-grid{grid-template-columns:minmax(0,1fr)}
 .dj-grid{grid-template-columns:1fr 1fr}
 .knobs{grid-column:1 / -1;order:3}
}
@media (max-width:640px){
 .vinyl-display{grid-template-columns:minmax(0,1fr);gap:20px}
 .vinyl-container{width:180px;height:180px;margin:0 auto}
 .dj-grid{grid-template-columns:minmax(0,1fr)}
 .pads{grid-template-columns:repeat(2,minmax(0,1fr))}
 .transport{justify-content:center}
}
</style></head>
<body>
<div class="container">
<header class="header">
<div class="brand">HALLEZ DJ STUDIO</div>
<div class="status-badge" id="statusBadge"><span class="status-dot"></span><span id="statusText">OFFLINE</span></div>
</header>

<div class="main-grid">
<div class="deck-section">
<div class="vinyl-display">
<div class="vinyl-container">
<div class="vinyl-disc" id="vinylDisc">
<div class="vinyl-label" id="vinylLabel"><div class="vinyl-icon">♪</div></div>
</div>
</div>
<div class="track-info">
<div class="track-title" id="trackTitle">No track loaded</div>
<div class="track-meta" id="trackMeta">Add tracks to start playing</div>
<div class="progress-section">
<div class="progress-time"><span id="currentTime">0:00</span><span id="totalTime">0:00</span></div>
<div class="progress-bar" id="progressBar"><div class="progress-fill" id="progressFill"></div></div>
</div>
</div>
</div>

<div class="visualizer" id="visualizer"></div>

<div class="dj-panel">
<div class="dj-grid">
<div class="transport">
<button class="transport-btn" data-action="pause" id="pauseBtn" title="Play/Pause">▶</button>
<button class="transport-btn" data-action="skip" title="Skip">⏭</button>
<button class="transport-btn" data-action="stop" title="Stop">⏹</button>
</div>
<div class="knobs">
<div class="knob-group">
<div class="knob-label">BASS</div>
<div class="knob" data-knob="bass" data-min="-10" data-max="10" data-value="0"></div>
<div class="knob-value" id="bassValue">0 dB</div>
</div>
<div class="knob-group">
<div class="knob-label">TREBLE</div>
<div class="knob" data-knob="treble" data-min="-10" data-max="10" data-value="0"></div>
<div class="knob-value" id="trebleValue">0 dB</div>
</div>
<div class="knob-group">
<div class="knob-label">SPEED</div>
<div class="knob" data-knob="speed" data-min="0.5" data-max="2" data-value="1" data-step="0.1"></div>
<div class="knob-value" id="speedValue">1.0x</div>
</div>
</div>
<div class="pads">
<button class="pad" data-action="dj" id="djBtn">AUTO DJ</button>
<button class="pad" disabled>FX 1</button>
<button class="pad" disabled>FX 2</button>
<button class="pad" disabled>LOOP</button>
</div>
</div>
</div>
</div>

<aside class="sidebar">
<div class="panel">
<div class="panel-title">ADD TRACK</div>
<div class="input-group">
<label class="input-label">YouTube URL / Search</label>
<input type="text" class="text-input" id="trackInput" placeholder="Paste URL or track name...">
</div>
<div class="input-group">
<label class="input-label">Your Name</label>
<input type="text" class="text-input" id="nickInput" placeholder="DJ Name">
</div>
<button class="btn-primary" id="addBtn">ADD TO QUEUE</button>
<div class="message" id="message"></div>
</div>

<div class="panel">
<div class="panel-title">VOLUME</div>
<div class="volume-control">
<div class="volume-header"><span class="volume-label">Master</span><span class="volume-value" id="volValue">50%</span></div>
<input type="range" class="volume-slider" id="volSlider" min="5" max="100" step="5" value="50">
</div>
</div>

<div class="panel">
<div class="panel-title">QUEUE (<span id="queueCount">0</span>)</div>
<div class="queue-panel" id="queueList"></div>
</div>
</aside>
</div>

<footer class="footer">Hallez DJ Studio &bull; Professional Music Control</footer>
</div>

<script nonce="__NONCE__">
const $ = id => document.getElementById(id);
let refreshTimer = null;

// Visualizer bars
for(let i=0;i<40;i++){
const bar = document.createElement('div');
bar.className = 'vis-bar';
$('visualizer').appendChild(bar);
}

let animFrame = null;
function animateVisualizer(){
const bars = document.querySelectorAll('.vis-bar');
const playing = $('vinylDisc').classList.contains('spinning');
bars.forEach((bar,i)=>{
if(playing){
const h = 15 + Math.random()*85;
bar.style.height = h + '%';
bar.classList.toggle('active', Math.random()>.6);
}else{
bar.style.height = '5%';
bar.classList.remove('active');
}
});
animFrame = setTimeout(()=>requestAnimationFrame(animateVisualizer), 120);
}
animateVisualizer();

// Knobs
document.querySelectorAll('[data-knob]').forEach(knob=>{
const name = knob.dataset.knob;
const min = parseFloat(knob.dataset.min);
const max = parseFloat(knob.dataset.max);
const step = parseFloat(knob.dataset.step||'1');
let value = parseFloat(knob.dataset.value);
let startY = 0;
let startValue = 0;

function updateKnob(){
const pct = (value-min)/(max-min);
const deg = -135 + pct*270;
knob.style.transform = `rotate(${deg}deg)`;
}
updateKnob();

knob.addEventListener('mousedown', e=>{
e.preventDefault();
startY = e.clientY;
startValue = value;
document.addEventListener('mousemove', onMove);
document.addEventListener('mouseup', onUp);
});

function onMove(e){
const delta = (startY - e.clientY) * (max-min) / 200;
value = Math.max(min, Math.min(max, startValue + delta));
value = Math.round(value/step)*step;
updateKnob();
const valueEl = $(`${name}Value`);
if(name==='speed'){
valueEl.textContent = value.toFixed(1)+'x';
}else{
valueEl.textContent = (value>=0?'+':'')+value+' dB';
}
}

function onUp(){
document.removeEventListener('mousemove', onMove);
document.removeEventListener('mouseup', onUp);
}
});

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
msg.className = 'message show '+(type||'');
setTimeout(()=>msg.className='message',3500);
}

function updateUI(state){
const statusBadge = $('statusBadge');
const statusText = $('statusText');
if(state.connected){
statusBadge.classList.add('live');
statusText.textContent = 'LIVE: '+(state.channel||'VOICE').toUpperCase();
}else{
statusBadge.classList.remove('live');
statusText.textContent = 'OFFLINE';
}

const cur = state.current;
const vinyl = $('vinylDisc');
const pauseBtn = $('pauseBtn');
if(cur){
$('trackTitle').textContent = cur.title||'Unknown Track';
$('trackMeta').textContent = 'Requested by '+(cur.who||'Unknown');
$('currentTime').textContent = fmt(cur.elapsed);
$('totalTime').textContent = fmt(cur.duration);
const pct = cur.duration ? (cur.elapsed/cur.duration*100):0;
$('progressFill').style.width = pct+'%';

if(cur.thumb){
$('vinylLabel').innerHTML = '<img src="'+cur.thumb+'" alt="">';
}else{
$('vinylLabel').innerHTML = '<div class="vinyl-icon">♪</div>';
}

if(state.playing && !state.paused){
vinyl.classList.add('spinning');
pauseBtn.innerHTML = '⏸';
pauseBtn.classList.remove('play');
}else{
vinyl.classList.remove('spinning');
pauseBtn.innerHTML = '▶';
pauseBtn.classList.add('play');
}
}else{
$('trackTitle').textContent = 'No track loaded';
$('trackMeta').textContent = 'Queue is empty';
$('currentTime').textContent = '0:00';
$('totalTime').textContent = '0:00';
$('progressFill').style.width = '0%';
$('vinylLabel').innerHTML = '<div class="vinyl-icon">♪</div>';
vinyl.classList.remove('spinning');
pauseBtn.innerHTML = '▶';
pauseBtn.classList.add('play');
}

$('volSlider').value = state.volume||50;
$('volValue').textContent = (state.volume||50)+'%';

$('djBtn').classList.toggle('active',!!state.dj);

const qlist = $('queueList');
const queue = state.queue||[];
$('queueCount').textContent = queue.length;
qlist.innerHTML = '';
if(queue.length){
queue.slice(0,25).forEach((t,i)=>{
const item = document.createElement('div');
item.className = 'queue-item';
item.innerHTML = '<span class="queue-number">'+(i+1)+'</span>'+
'<div class="queue-title">'+(t.title||'Unknown')+'</div>'+
'<div class="queue-author">'+(t.who||'Unknown')+'</div>';
qlist.appendChild(item);
});
if(queue.length>25){
const more = document.createElement('div');
more.className = 'empty-state';
more.textContent = '+ '+(queue.length-25)+' more tracks';
qlist.appendChild(more);
}
}else{
qlist.innerHTML = '<div class="empty-state">No tracks in queue</div>';
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

document.querySelectorAll('[data-action]').forEach(btn=>{
btn.addEventListener('click',async()=>{
const action = btn.dataset.action;
if(btn.disabled) return;
btn.disabled = true;
try{
await api('/music/api/action',{action});
await refresh();
}catch(e){
showMsg(e.message,'error');
}
btn.disabled = false;
});
});

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
if(nick) localStorage.setItem('nick',nick);
await refresh();
}catch(e){
showMsg(e.message,'error');
}
$('addBtn').disabled = false;
$('addBtn').textContent = 'ADD TO QUEUE';
});

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

const savedNick = localStorage.getItem('nick');
if(savedNick) $('nickInput').value = savedNick;

refresh();
refreshTimer = setInterval(refresh,3000);
document.addEventListener('visibilitychange',()=>{
if(document.hidden){
if(refreshTimer) clearInterval(refreshTimer);
refreshTimer = null;
if(animFrame) clearTimeout(animFrame);
animFrame = null;
}else{
if(!refreshTimer){
refresh();
refreshTimer = setInterval(refresh,3000);
}
if(!animFrame) animateVisualizer();
}
});
</script>
</body></html>

"""


def setup(bot: commands.Bot):
    bot.add_cog(MusicWeb(bot))
