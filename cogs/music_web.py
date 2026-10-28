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
:root{--bg:#07070a;--panel:#101015;--panel2:#16161d;--panel3:#1d1d26;--line:rgba(255,255,255,.08);
 --text:#f3f3f7;--muted:#8b8b9a;--accent:#d92d3a;--accent2:#ff5a64;--ok:#3dd68c;--err:#ff6369}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;color:var(--text);background:var(--bg);
 font:15px/1.5 "Inter",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;position:relative}
body::before{content:"";position:fixed;inset:0;z-index:-1;pointer-events:none;
 background:radial-gradient(52% 44% at 8% -6%,rgba(217,45,58,.22),transparent 70%),
 radial-gradient(44% 40% at 100% 108%,rgba(120,70,255,.12),transparent 70%)}
.wrap{max-width:760px;margin:0 auto;padding:26px 18px 44px}
.top{display:flex;align-items:center;gap:.7rem;margin-bottom:.3rem}
.logo{width:38px;height:38px;border-radius:12px;flex:none;display:grid;place-items:center;font-size:1.15rem;
 background:linear-gradient(180deg,var(--accent2),var(--accent));box-shadow:0 8px 22px rgba(217,45,58,.35)}
h1{margin:0;font-size:1.42rem;letter-spacing:-.02em}
.live{margin-left:auto;font-size:.72rem;padding:.28rem .6rem;border-radius:999px;border:1px solid var(--line);
 background:var(--panel2);color:var(--muted);white-space:nowrap}
.live.on{color:var(--ok);border-color:rgba(61,214,140,.35)}
.live.off{color:var(--err);border-color:rgba(255,99,105,.3)}
.sub{margin:.2rem 0 18px;color:var(--muted);font-size:.86rem}
.card{background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--line);
 border-radius:16px;padding:14px;margin-bottom:12px;box-shadow:0 14px 36px rgba(0,0,0,.32)}
.now{display:flex;gap:14px;align-items:center}
.cover{width:82px;height:82px;border-radius:12px;flex:none;background:#0c0c11;border:1px solid var(--line);
 display:grid;place-items:center;font-size:1.7rem;overflow:hidden}
.cover img{width:100%;height:100%;object-fit:cover}
.meta{min-width:0;flex:1}
.meta b{display:block;font-size:1.02rem;word-break:break-word}
.meta small{color:var(--muted);display:block;margin-top:.15rem}
.bar{height:4px;border-radius:4px;background:var(--panel3);margin-top:9px;overflow:hidden}
.bar i{display:block;height:100%;width:0;background:var(--accent);transition:width .4s linear}
.form{display:flex;gap:8px;flex-wrap:wrap}
input{background:#0c0c11;border:1px solid var(--line);color:var(--text);border-radius:11px;
 padding:.62rem .75rem;font-size:.9rem;font-family:inherit;min-width:0}
input:focus{outline:none;border-color:rgba(255,90,100,.55)}
input.q{flex:1 1 16rem}input.n{flex:0 1 9rem}
button{border:1px solid rgba(255,255,255,.14);background:var(--panel2);color:var(--text);
 border-radius:11px;padding:.55rem .8rem;font-size:.84rem;cursor:pointer;font-family:inherit;
 transition:transform .12s ease,border-color .15s ease,background .15s ease}
button:hover{border-color:rgba(255,255,255,.26);transform:translateY(-1px)}
button:active{transform:translateY(0)}
button.pr{background:linear-gradient(180deg,var(--accent2),var(--accent));border-color:transparent;
 color:#fff;font-weight:600}
button.on{border-color:var(--accent);color:var(--accent2)}
button:disabled{opacity:.45;cursor:default;transform:none}
.hint{font-size:.76rem;color:var(--muted);margin:.55rem 0 0}
.keys{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
.vol{display:flex;align-items:center;gap:10px}
.vol input[type=range]{flex:1;padding:0;accent-color:var(--accent2)}
.vol b{min-width:3.2rem;text-align:right;font-size:.86rem}
h2{font-size:.8rem;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);margin:0 0 10px}
.q{display:flex;flex-direction:column;gap:6px}
.row{display:flex;align-items:center;gap:10px;background:var(--panel);border:1px solid var(--line);
 border-radius:11px;padding:.5rem .65rem;font-size:.86rem}
.row i{width:18px;text-align:right;color:var(--muted);font-size:.76rem;font-style:normal;flex:none}
.row span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.row em{margin-left:auto;font-style:normal;color:var(--muted);font-size:.74rem;white-space:nowrap}
.msg{min-height:1.1rem;font-size:.84rem;margin-top:.5rem}
.msg.ok{color:var(--ok)}.msg.err{color:var(--err)}
.off{opacity:.55;pointer-events:none}
.foot{color:var(--muted);font-size:.72rem;text-align:center;margin-top:4px}
@media (max-width:620px){.keys{grid-template-columns:repeat(2,1fr)}.sub{font-size:.8rem}}
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
