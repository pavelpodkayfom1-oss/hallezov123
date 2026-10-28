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
<title>Музыка семьи</title>
<style nonce="__NONCE__">
:root{--bg:#000;--panel:#0f0f0f;--panel2:#1a1a1a;--accent:#00ff00;--accent2:#00cc00;--text:#fff;--muted:#666;--border:#333}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font:14px/1.5 "Courier New",monospace;min-height:100vh;
 background-image:repeating-linear-gradient(0deg,transparent,transparent 2px,rgba(0,255,0,.02) 2px,rgba(0,255,0,.02) 4px)}
.wrap{max-width:900px;margin:0 auto;padding:20px}
.top{display:flex;align-items:center;gap:1rem;margin-bottom:20px;padding:15px;background:var(--panel);
 border:2px solid var(--border);box-shadow:inset 0 0 20px rgba(0,255,0,.1)}
.logo{width:50px;height:50px;border:2px solid var(--accent);display:grid;place-items:center;font-size:2rem;
 animation:pulse 2s infinite;box-shadow:0 0 20px var(--accent)}
@keyframes pulse{0%,100%{box-shadow:0 0 20px var(--accent)}50%{box-shadow:0 0 40px var(--accent),inset 0 0 20px var(--accent)}}
h1{flex:1;font-size:1.8rem;letter-spacing:4px;text-transform:uppercase;text-shadow:0 0 10px var(--accent)}
.live{padding:.4rem 1rem;border:2px solid var(--accent);background:var(--panel2);font-size:.75rem;text-transform:uppercase;
 letter-spacing:2px}
.live.on{background:var(--accent);color:#000;animation:blink 1s infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.7}}
.sub{margin-bottom:20px;color:var(--muted);text-align:center;font-size:.9rem}
.card{background:var(--panel);border:2px solid var(--border);padding:20px;margin-bottom:15px;
 box-shadow:0 0 30px rgba(0,255,0,.1),inset 0 0 10px rgba(0,255,0,.05)}
.now{display:flex;gap:20px;align-items:center;position:relative}
.cover{width:120px;height:120px;border:3px solid var(--accent);background:var(--bg);display:grid;place-items:center;
 font-size:3rem;overflow:hidden;position:relative;box-shadow:0 0 30px rgba(0,255,0,.4)}
.cover::before{content:"";position:absolute;inset:0;border:1px solid var(--accent);animation:spin 3s linear infinite}
@keyframes spin{from{transform:rotate(0deg)}to{transform:rotate(360deg)}}
.cover img{width:100%;height:100%;object-fit:cover}
.meta{flex:1}
.meta b{font-size:1.3rem;text-transform:uppercase;letter-spacing:2px;display:block;margin-bottom:.5rem}
.meta small{color:var(--muted);display:block;margin-top:.3rem}
.bar{height:8px;background:#111;margin-top:15px;position:relative;border:1px solid var(--border);overflow:hidden}
.bar::before{content:"";position:absolute;inset:0;background:repeating-linear-gradient(90deg,var(--border),var(--border) 1px,transparent 1px,transparent 20px)}
.bar i{display:block;height:100%;width:0;background:linear-gradient(90deg,var(--accent2),var(--accent));
 box-shadow:0 0 20px var(--accent);transition:width .4s linear}
.viz{display:flex;gap:3px;height:60px;align-items:flex-end;margin-top:15px}
.viz span{flex:1;background:var(--accent);box-shadow:0 0 10px var(--accent);animation:bar 1s ease-in-out infinite}
.viz span:nth-child(1){animation-delay:0s}
.viz span:nth-child(2){animation-delay:.1s}
.viz span:nth-child(3){animation-delay:.2s}
.viz span:nth-child(4){animation-delay:.3s}
.viz span:nth-child(5){animation-delay:.4s}
.viz span:nth-child(6){animation-delay:.5s}
.viz span:nth-child(7){animation-delay:.4s}
.viz span:nth-child(8){animation-delay:.3s}
.viz span:nth-child(9){animation-delay:.2s}
.viz span:nth-child(10){animation-delay:.1s}
@keyframes bar{0%,100%{height:20%}50%{height:100%}}
.form{display:grid;grid-template-columns:1fr auto auto;gap:10px;margin-bottom:10px}
input{background:var(--bg);border:2px solid var(--border);color:var(--text);padding:.8rem 1rem;font:inherit;
 text-transform:uppercase;letter-spacing:1px}
input:focus{outline:none;border-color:var(--accent);box-shadow:0 0 15px rgba(0,255,0,.3)}
button{background:var(--panel2);border:2px solid var(--accent);color:var(--accent);padding:.8rem 1.5rem;
 font:inherit;text-transform:uppercase;letter-spacing:2px;cursor:pointer;transition:all .2s;position:relative;overflow:hidden}
button::before{content:"";position:absolute;inset:0;background:var(--accent);transform:translateY(100%);transition:transform .2s}
button:hover::before{transform:translateY(0)}
button:hover{color:#000;box-shadow:0 0 30px var(--accent)}
button span{position:relative;z-index:1}
button.pr{background:var(--accent);color:#000;font-weight:bold;box-shadow:0 0 30px rgba(0,255,0,.6)}
button.pr:hover{box-shadow:0 0 50px rgba(0,255,0,.8)}
.hint,.msg{font-size:.8rem;color:var(--muted);margin-top:5px}
.msg.ok{color:var(--accent)}.msg.err{color:#ff3333}
.keys{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}
.keys button{padding:1.2rem;font-size:1.2rem}
.keys button.on{background:var(--accent);color:#000;animation:blink 1s infinite}
.vol{display:grid;grid-template-columns:auto 1fr auto;gap:15px;align-items:center;padding:15px}
.vol input[type=range]{-webkit-appearance:none;appearance:none;height:8px;background:#111;border:1px solid var(--border);
 outline:none}
.vol input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;appearance:none;width:20px;height:20px;
 background:var(--accent);border:2px solid #000;cursor:pointer;box-shadow:0 0 20px var(--accent)}
.vol input[type=range]::-moz-range-thumb{width:20px;height:20px;background:var(--accent);border:2px solid #000;
 cursor:pointer;box-shadow:0 0 20px var(--accent)}
.vol b{font-size:1.2rem;letter-spacing:2px}
h2{font-size:.9rem;letter-spacing:3px;text-transform:uppercase;color:var(--accent);margin-bottom:15px;
 text-shadow:0 0 10px var(--accent)}
.q{display:flex;flex-direction:column;gap:8px;max-height:300px;overflow-y:auto}
.q::-webkit-scrollbar{width:8px}
.q::-webkit-scrollbar-track{background:#111}
.q::-webkit-scrollbar-thumb{background:var(--accent);box-shadow:0 0 10px var(--accent)}
.row{display:flex;align-items:center;gap:12px;background:#0a0a0a;border:1px solid var(--border);border-left:3px solid transparent;
 padding:.8rem 1rem;font-size:.9rem;transition:all .2s}
.row:hover{border-left-color:var(--accent);background:#111;box-shadow:0 0 15px rgba(0,255,0,.2)}
.row i{width:25px;text-align:right;color:var(--accent);font-style:normal;font-weight:bold}
.row span{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.row em{color:var(--muted);font-size:.75rem;font-style:normal}
.off{opacity:.3;pointer-events:none}
.foot{color:var(--muted);font-size:.7rem;text-align:center;margin-top:20px;text-transform:uppercase;letter-spacing:2px}
@media (max-width:620px){.keys{grid-template-columns:repeat(2,1fr)}.form{grid-template-columns:1fr}}
</style></head>
<body><div class="wrap">
<div class="top"><div class="logo">🎵</div><h1>Музыка семьи</h1><span class="live" id="live">…</span></div>
<p class="sub">Вставил ссылку — трек заиграл в общем голосе. Ник пишем, чтобы семья видела, чей заказ.</p>
<div class="card now">
  <div class="cover" id="cover">🎧</div>
  <div class="meta">
    <b id="title">тишина</b>
    <small id="who">никто ничего не включил</small>
    <div class="bar"><i id="prog"></i></div>
    <div class="viz">
      <span></span><span></span><span></span><span></span><span></span>
      <span></span><span></span><span></span><span></span><span></span>
    </div>
  </div>
</div>
<div class="card" id="orderBox">
  <div class="form">
    <input class="q" id="q" placeholder="Ссылка на YouTube или на звук… или имя трека" maxlength="200">
    <input class="n" id="nick" placeholder="Твой ник" maxlength="32">
    <button class="pr" id="go">В очередь</button>
  </div>
  <div class="hint" id="hint"></div>
  <div class="msg" id="msg"></div>
</div>
<div class="card keys">
  <button data-act="pause">⏸ Пауза / играть</button>
  <button data-act="skip">⏭ Пропустить</button>
  <button data-act="stop">⏹ Очистить</button>
  <button data-act="dj" id="dj">🔁 Авто-диджей</button>
</div>
<div class="card vol">🔉 <input type="range" id="vol" min="5" max="100" step="5"> <b id="volx">—</b></div>
<div class="card"><h2>Очередь</h2><div class="q" id="queue"></div></div>
<p class="foot">Сайт музыки · очередь одна на всех: и с этого сайта, и из Discord</p>
</div>
<script nonce="__NONCE__">
const $ = (id) => document.getElementById(id);
let timer = null;

function fmt(sec) {
  sec = Math.max(0, Math.round(sec || 0));
  return Math.floor(sec / 60) + ':' + String(sec % 60).padStart(2, '0');
}

async function api(path, body) {
  const init = { method: body ? 'POST' : 'GET', cache: 'no-store',
    headers: { 'X-Requested-With': 'hz-music' } };
  if (body) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(body); }
  const r = await fetch(path, init);
  let data = {};
  try { data = await r.json(); } catch (e) { /* бот перезапустился — вместо ответа прилетела страница */ }
  if (!r.ok) throw new Error(data.error || ('Сервер ответил ' + r.status));
  return data;
}

function note(text, kind) {
  $('msg').textContent = text || '';
  $('msg').className = 'msg ' + (kind || '');
}

function cover(url) {
  const box = $('cover');
  box.textContent = '';
  if (url) {
    const img = document.createElement('img');
    img.src = url; img.alt = '';
    img.addEventListener('error', () => { box.textContent = '🎧'; });
    box.append(img);
  } else box.textContent = '🎧';
}

function paint(s) {
  const live = $('live');
  live.textContent = s.connected ? ('в голосовом: ' + (s.channel || '—')) : 'бот не в голосовом';
  live.className = 'live ' + (s.connected ? 'on' : 'off');

  const cur = s.current;
  $('title').textContent = cur ? cur.title : (s.playing ? 'играет' : 'тишина');
  cover(cur ? cur.thumb : null);
  $('who').textContent = cur
    ? 'заказал(а) ' + cur.who + ' · ' + (cur.elapsed != null && cur.duration ? fmt(cur.elapsed) + ' / ' + fmt(cur.duration) : (cur.duration ? fmt(cur.duration) : '—'))
    : (s.connected ? 'очередь пуста' : 'сначала «В войс» в /музыка');
  const pct = cur && cur.duration && cur.elapsed != null
    ? Math.min(100, cur.elapsed / cur.duration * 100) : 0;
  $('prog').style.width = pct + '%';

  $('vol').value = s.volume; $('volx').textContent = s.volume + '%';
  $('dj').classList.toggle('on', !!s.dj);

  const box = $('queue');
  box.textContent = '';
  const list = s.queue || [];
  list.slice(0, 15).forEach((t, i) => {
    const row = document.createElement('div'); row.className = 'row';
    const n = document.createElement('i'); n.textContent = i + 1;
    const name = document.createElement('span'); name.textContent = t.title;
    const who = document.createElement('em'); who.textContent = t.who;
    row.append(n, name, who); box.append(row);
  });
  if (!list.length) {
    const empty = document.createElement('div'); empty.className = 'row';
    empty.textContent = 'Очередь пустая'; box.append(empty);
  } else if (list.length > 15) {
    const more = document.createElement('div'); more.className = 'row';
    more.textContent = '… и ещё ' + (list.length - 15); box.append(more);
  }

  $('orderBox').classList.toggle('off', s.open === false);
  $('hint').textContent = s.open === false ? 'Заказы со страницы закрыты — попроси штаб.'
    : 'Очередь принимает до ' + (s.queue_max || 25) + ' треков, следующий заказ через '
      + (s.cooldown || 0) + ' с.';
}

async function refresh() {
  try { paint(await api('api/state')); }
  catch (e) { note(e.message, 'err'); }
}

$('go').addEventListener('click', () => {
  const q = $('q').value.trim();
  if (!q) { note('Вставь ссылку или назови трек', 'err'); return; }
  const nick = $('nick').value.trim();
  localStorage.setItem('hz-music-nick', nick);
  const btn = $('go'); btn.disabled = true; note('Ищу трек…', '');
  api('api/order', { query: q, nick: nick })
    .then((r) => { note(r.did || 'Готово', 'ok'); $('q').value = ''; })
    .catch((e) => note(e.message, 'err'))
    .then(() => { btn.disabled = false; refresh(); });
});
$('q').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('go').click(); });

document.querySelectorAll('[data-act]').forEach((b) => b.addEventListener('click', async () => {
  b.disabled = true;
  try { const r = await api('api/action', { action: b.dataset.act }); note(r.did || 'Готово', 'ok'); }
  catch (e) { note(e.message, 'err'); }
  b.disabled = false; refresh();
}));

$('vol').addEventListener('change', async () => {
  $('volx').textContent = $('vol').value + '%';
  try { await api('api/action', { action: 'volume', value: Number($('vol').value) }); }
  catch (e) { note(e.message, 'err'); }
  refresh();
});

$('nick').value = localStorage.getItem('hz-music-nick') || '';
refresh();
timer = setInterval(refresh, 3000);
document.addEventListener('visibilitychange', () => {
  if (document.hidden) { clearInterval(timer); timer = null; }
  else if (!timer) { refresh(); timer = setInterval(refresh, 3000); }
});
</script>
</body></html>
"""


def setup(bot: commands.Bot):
    bot.add_cog(MusicWeb(bot))
