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


PAGE_HTML = r"""
<!doctype html>
<html lang="ru"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>HALLEZ · музыка семьи</title>
<style nonce="__NONCE__">
:root{
 --bg:#08070d;--bg2:#120f1c;--surface:#161322;--surface2:#1d1930;
 --surface3:#272238;--line:#342d4a;--text:#f0e7d8;--text2:#c3b6a8;--text3:#877b8c;
 --cream:#f0e7d8;--amber:#e8b477;--teal:#7fd6c1;--violet:#b39cff;--red:#ef6a6a;--gold:#d9b56d;
 --accent:var(--violet);--r-big:24px;--r-card:18px;--r-box:13px;--r-in:11px;--pill:999px;
 --beat:.5s}
*{box-sizing:border-box;margin:0;padding:0}
html{-webkit-text-size-adjust:100%}
body{background:radial-gradient(120% 80% at 50% -10%,var(--bg2),var(--bg) 62%) fixed,var(--bg);
 color:var(--text);min-height:100vh;overflow-x:hidden;position:relative;
 font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
/* звёзды: два слоя точек со своей скоростью — параллакс без картинок и скриптов */
body::before,body::after{content:"";position:fixed;inset:-60vh -20vw;z-index:0;pointer-events:none}
body::before{background-image:radial-gradient(1.4px 1.4px at 25% 35%,rgba(240,231,216,.9),transparent 60%),
 radial-gradient(1.2px 1.2px at 70% 15%,rgba(179,156,255,.85),transparent 60%),
 radial-gradient(1px 1px at 45% 80%,rgba(232,180,119,.8),transparent 60%),
 radial-gradient(1px 1px at 88% 62%,rgba(240,231,216,.7),transparent 60%);
 background-size:340px 340px;animation:drift 190s linear infinite,twinkle 7s ease-in-out infinite}
body::after{background-image:radial-gradient(.8px .8px at 15% 70%,rgba(240,231,216,.55),transparent 60%),
 radial-gradient(.8px .8px at 60% 40%,rgba(127,214,193,.5),transparent 60%),
 radial-gradient(.7px .7px at 92% 22%,rgba(240,231,216,.45),transparent 60%);
 background-size:170px 170px;animation:drift 120s linear infinite reverse}
@keyframes drift{to{transform:translate3d(-340px,-240px,0)}}
@keyframes twinkle{0%,100%{opacity:.55}50%{opacity:1}}
@keyframes spin{to{transform:rotate(360deg)}}
.glow{position:fixed;left:50%;top:-32vh;width:120vw;height:84vh;transform:translateX(-50%);z-index:0;
 pointer-events:none;background:radial-gradient(50% 50% at 50% 50%,rgba(179,156,255,.16),transparent 70%)}
.beat{position:fixed;left:50%;top:-32vh;width:120vw;height:84vh;transform:translateX(-50%);z-index:0;
 pointer-events:none;opacity:0;background:radial-gradient(45% 45% at 50% 50%,rgba(127,214,193,.14),transparent 72%);
 animation:beat var(--beat) ease-in-out infinite;animation-play-state:paused}
.beat.on{animation-play-state:running}
@keyframes beat{0%,100%{opacity:0}18%{opacity:1}}
.container{max-width:1280px;margin:0 auto;padding:0 20px;position:relative;z-index:1}

.header{padding:26px 0 18px;position:relative;margin-bottom:24px}
.header::after{content:"";position:absolute;left:4%;right:4%;bottom:0;height:1px;
 background:linear-gradient(90deg,transparent,var(--line) 20%,var(--line) 80%,transparent)}
.header-top{display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.brand{font-size:1.55rem;font-weight:700;letter-spacing:-.5px;color:var(--cream)}
.brand span{color:var(--text3);font-weight:400;letter-spacing:.14em;font-size:.78rem;
 text-transform:uppercase;margin-left:.55rem}
.status{display:inline-flex;align-items:center;gap:8px;padding:7px 14px;background:var(--surface2);
 border:1px solid var(--line);border-radius:var(--pill);font-size:.72rem;text-transform:uppercase;
 letter-spacing:.1em;color:var(--text2)}
.status-dot{width:6px;height:6px;border-radius:50%;background:var(--text3)}
.status.active{color:var(--violet);border-color:rgba(179,156,255,.45)}
.status.active .status-dot{background:var(--teal);animation:pulse 1.8s infinite}
@keyframes pulse{0%,100%{opacity:1;box-shadow:0 0 0 0 rgba(127,214,193,.55)}50%{opacity:.5;box-shadow:0 0 0 6px rgba(127,214,193,0)}}
.subtitle{color:var(--text3);font-size:.86rem;margin-top:8px}

.card{background:linear-gradient(180deg,rgba(39,34,56,.55),rgba(22,19,34,.92));
 border:1px solid var(--line);border-radius:var(--r-big);
 box-shadow:0 20px 46px -30px rgba(0,0,0,.95)}
.main-grid{display:grid;grid-template-columns:minmax(0,1fr) 356px;gap:22px;margin-bottom:22px}
.player-section{padding:26px;position:relative;overflow:hidden}
/* рамка вспыхивает один раз, когда в плеере сменился трек */
.player-section.flash{animation:flash 1.1s ease-out}
@keyframes flash{0%{border-color:var(--teal);box-shadow:0 0 40px -12px rgba(127,214,193,.55)}
 100%{border-color:var(--line);box-shadow:0 20px 46px -30px rgba(0,0,0,.95)}}
.track-display{display:grid;grid-template-columns:196px minmax(0,1fr);gap:26px;align-items:center}
.artwork-wrap{position:relative;width:196px;height:176px}
.vinyl{position:absolute;right:0;top:3px;width:170px;height:170px;border-radius:50%;
 background:radial-gradient(circle,#221d31 0 20%,#141120 21%,#0d0b15 62%,#08070d 100%);
 box-shadow:0 12px 26px -14px rgba(0,0,0,.95),inset 0 0 26px rgba(0,0,0,.85);
 animation:spin 3.4s linear infinite;animation-play-state:paused}
.vinyl::before{content:"";position:absolute;inset:7%;border-radius:50%;
 background:repeating-radial-gradient(circle,rgba(240,231,216,.05) 0 1px,transparent 1px 4px)}
.vinyl::after{content:"";position:absolute;inset:42%;border-radius:50%;
 background:linear-gradient(135deg,var(--amber),var(--gold));opacity:.75}
.vinyl.spin{animation-play-state:running}
.vinyl.scratch{animation:scratch .55s cubic-bezier(.5,-.4,.4,1.6)}
@keyframes scratch{0%{transform:rotate(0)}30%{transform:rotate(-26deg)}60%{transform:rotate(14deg)}100%{transform:rotate(0)}}
.artwork{position:absolute;left:0;top:3px;width:170px;height:170px;background:var(--surface2);
 border:1px solid var(--line);border-radius:var(--r-card);display:flex;align-items:center;
 justify-content:center;overflow:hidden;z-index:1}
.artwork img{width:100%;height:100%;object-fit:cover;display:block}
.artwork.is-live{border-color:rgba(127,214,193,.5);box-shadow:0 0 34px -8px rgba(127,214,193,.45)}
.artwork-placeholder{font-size:3.2rem;color:var(--text3)}
.track-info{display:flex;flex-direction:column;min-width:0}
.track-kicker{font-size:.68rem;text-transform:uppercase;letter-spacing:.16em;color:var(--text3)}
.track-title{font-size:1.42rem;font-weight:700;line-height:1.25;color:var(--cream);margin-top:4px;
 word-break:break-word}
.track-meta{color:var(--text2);font-size:.86rem;margin-top:6px}
.chips{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}
.chip{font-size:.7rem;text-transform:uppercase;letter-spacing:.08em;color:var(--text3);
 border:1px solid var(--line);border-radius:var(--pill);padding:5px 11px;background:rgba(29,25,48,.65)}
.chip b{color:var(--cream);font-weight:600;letter-spacing:0}
.progress-section{margin-top:auto;padding-top:20px}
.progress-time{display:flex;justify-content:space-between;font-size:.74rem;color:var(--text3);
 margin-bottom:7px;font-variant-numeric:tabular-nums}
.progress-bar{height:6px;background:var(--surface3);position:relative;overflow:hidden;border-radius:var(--pill)}
.progress-fill{height:100%;width:0;border-radius:var(--pill);position:relative;
 background:linear-gradient(90deg,var(--violet),var(--teal));transition:width .9s linear}
.progress-fill::after{content:"";position:absolute;top:0;right:0;width:7px;height:100%;
 border-radius:var(--pill);background:var(--cream);box-shadow:0 0 12px 2px rgba(240,231,216,.6)}

.waveform{display:flex;align-items:flex-end;gap:3px;height:96px;margin-top:22px;padding:0 2px;
 opacity:.3;transition:opacity .45s,filter .3s}
.waveform.on{opacity:.92}
.wave-bar{position:relative;flex:1;height:10%;background:linear-gradient(180deg,var(--teal),#2f4a52);
 transform-origin:bottom;border-radius:var(--pill) var(--pill) 2px 2px;
 animation:wv 1.15s ease-in-out infinite alternate;animation-play-state:paused}
.waveform.playing .wave-bar{animation-play-state:running}
.wave-bar::after{content:"";position:absolute;left:0;right:0;bottom:-14px;height:14px;border-radius:50%;
 background:inherit;filter:blur(5px);opacity:0;transform:scaleY(.4)}
.waveform.echo .wave-bar::after{opacity:.4}
.waveform.echo .wave-bar{animation-duration:1.7s}
@keyframes wv{from{height:8%}to{height:var(--h,70%)}}

.controls-panel{background:rgba(29,25,48,.62);border:1px solid var(--line);
 border-radius:var(--r-card);padding:18px;margin-top:22px}
.controls-row{display:flex;gap:10px;flex-wrap:wrap}
.ctrl-btn{position:relative;overflow:hidden;flex:1;min-width:112px;padding:13px 10px;
 background:var(--surface);border:1px solid var(--line);border-radius:var(--r-box);
 color:var(--c,var(--text));cursor:pointer;font:inherit;font-size:.78rem;letter-spacing:.06em;
 transition:border-color .2s,color .2s,background .2s,box-shadow .25s,transform .12s}
.ctrl-btn::after{content:"";position:absolute;top:0;left:-60%;width:40%;height:100%;transform:skewX(-18deg);
 background:linear-gradient(90deg,transparent,rgba(240,231,216,.16),transparent);transition:left .5s ease}
.ctrl-btn:hover::after{left:130%}
.ctrl-btn:hover{border-color:var(--c,var(--cream));box-shadow:0 0 22px -10px var(--c,rgba(240,231,216,.8))}
.ctrl-btn:active{transform:translateY(1px)}
.ctrl-btn:focus-visible,.knob:focus-visible,.pad:focus-visible,.tap:focus-visible,.text-input:focus-visible{
 outline:2px solid var(--teal);outline-offset:2px}
.ctrl-btn.primary{background:linear-gradient(180deg,var(--cream),#d8ccb6);color:#15121c;
 border-color:transparent;font-weight:700}
.ctrl-btn.on{background:var(--cbg,var(--surface3));border-color:var(--c);
 box-shadow:inset 0 0 22px -12px var(--c)}
.ctrl-btn:disabled{opacity:.32;cursor:default;transform:none;box-shadow:none}
.ctrl-btn kbd{font:inherit;font-size:.62rem;opacity:.55;border:1px solid currentColor;
 border-radius:4px;padding:0 4px;margin-left:6px}
[data-action=pause]{--c:var(--amber);--cbg:rgba(232,180,119,.14)}
[data-action=skip]{--c:var(--teal);--cbg:rgba(127,214,193,.14)}
[data-action=dj]{--c:var(--violet);--cbg:rgba(179,156,255,.16)}
[data-action=stop]{--c:var(--red);--cbg:rgba(239,106,106,.13);flex:.6 1 84px;min-width:84px}
#addBtn{--c:var(--cream)}
.slider-row{display:flex;align-items:center;gap:12px;margin-top:16px}
.slider-label{font-size:.7rem;text-transform:uppercase;letter-spacing:.1em;color:var(--text3)}
.tap{width:30px;height:30px;flex:none;border-radius:50%;background:var(--surface3);
 border:1px solid var(--line);color:var(--text2);cursor:pointer;font:inherit;font-size:.9rem;line-height:1}
.tap:hover{border-color:var(--teal);color:var(--teal)}
.slider{-webkit-appearance:none;appearance:none;flex:1;height:5px;border-radius:var(--pill);
 background:linear-gradient(90deg,var(--violet),var(--surface3));border:none;outline:none}
.slider::-webkit-slider-thumb{-webkit-appearance:none;width:16px;height:16px;background:var(--cream);
 border:2px solid #15121c;cursor:pointer;border-radius:50%;box-shadow:0 0 10px rgba(179,156,255,.7)}
.slider::-moz-range-thumb{width:16px;height:16px;background:var(--cream);border:2px solid #15121c;
 cursor:pointer;border-radius:50%}
.slider-value{font-size:.78rem;color:var(--cream);min-width:40px;text-align:right;font-variant-numeric:tabular-nums}
.hint{color:var(--text3);font-size:.76rem;margin-top:14px;padding:9px 12px;
 border:1px dashed var(--line);border-radius:var(--r-box);background:rgba(29,25,48,.45)}

/* ── пульт: только картинка этого экрана, звук в канале он не трогает ── */
.deck{padding:22px 24px;margin-bottom:22px;border-radius:26px 8px 26px 8px;
 border-left:2px solid rgba(179,156,255,.45)}
.deck-head{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding-bottom:14px;position:relative}
.deck-head::after{content:"";position:absolute;left:0;right:22%;bottom:0;height:1px;
 background:linear-gradient(90deg,var(--line),transparent)}
.deck-title{font-size:.74rem;text-transform:uppercase;letter-spacing:.14em;color:var(--cream);font-weight:600}
.deck-badge{font-size:.64rem;text-transform:uppercase;letter-spacing:.1em;color:var(--violet);
 border:1px solid rgba(179,156,255,.4);border-radius:var(--pill);padding:3px 10px;
 background:rgba(179,156,255,.1)}
.deck-reset{margin-left:auto;background:transparent;border:1px solid var(--line);color:var(--text3);
 border-radius:var(--pill);padding:6px 14px;font:inherit;font-size:.72rem;text-transform:uppercase;
 letter-spacing:.08em;cursor:pointer;transition:color .2s,border-color .2s}
.deck-reset:hover{color:var(--cream);border-color:var(--cream)}
.deck-grid{display:grid;grid-template-columns:186px minmax(0,1fr);gap:24px;align-items:center;margin-top:18px}
.bpm-box{background:rgba(29,25,48,.6);border:1px solid var(--line);border-radius:var(--r-card);
 padding:16px 18px;text-align:center}
.bpm-num{font-size:2.5rem;font-weight:800;line-height:1;color:var(--teal);font-variant-numeric:tabular-nums;
 letter-spacing:-1px}
.bpm-lab{font-size:.66rem;text-transform:uppercase;letter-spacing:.18em;color:var(--text3);margin-top:4px}
.bpm-row{display:flex;align-items:center;justify-content:center;gap:10px;margin-top:14px}
.bpm-row .tap{width:26px;height:26px}
.bpm-row span{font-size:.68rem;text-transform:uppercase;letter-spacing:.1em;color:var(--text2)}
.knobs{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px}
.knob-group{display:flex;flex-direction:column;align-items:center;gap:9px}
.knob-lab{font-size:.68rem;text-transform:uppercase;letter-spacing:.12em;color:var(--text3)}
.knob{width:72px;height:72px;border-radius:50%;cursor:ns-resize;position:relative;
 background:radial-gradient(circle at 34% 30%,var(--surface3),var(--surface) 72%);
 border:1px solid var(--line);box-shadow:inset 0 2px 10px rgba(0,0,0,.6);touch-action:none}
.knob::before{content:"";position:absolute;inset:-6px;border-radius:50%;opacity:0;transition:opacity .2s;
 background:conic-gradient(from 225deg,var(--c,var(--teal)) 0 var(--p,0deg),transparent var(--p,0deg) 300deg)}
.knob:hover::before,.knob:focus-visible::before{opacity:.55}
.knob i{position:absolute;left:50%;top:8px;width:3px;height:24px;margin-left:-1.5px;border-radius:2px;
 background:var(--c,var(--teal));transform-origin:50% 28px;transform:rotate(var(--deg,0deg));
 box-shadow:0 0 8px var(--c,var(--teal))}
.knob-val{font-size:.72rem;color:var(--cream);font-variant-numeric:tabular-nums;min-width:52px;text-align:center}
.knob-group[data-knob=bass]{--c:var(--violet)}
.knob-group[data-knob=mid]{--c:var(--teal)}
.knob-group[data-knob=high]{--c:var(--amber)}
.knob-group[data-knob=filter]{--c:var(--red)}
.pads{grid-column:1 / -1;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-top:4px}
.pad{padding:14px 8px;background:var(--surface);border:1px solid var(--line);color:var(--text2);
 cursor:pointer;font:inherit;font-size:.74rem;text-transform:uppercase;letter-spacing:.1em;
 border-radius:var(--r-box);transition:all .2s}
.pad:hover{border-color:var(--c,var(--violet));color:var(--c,var(--violet))}
.pad.on{background:var(--cbg,rgba(179,156,255,.14));border-color:var(--c,var(--violet));
 color:var(--c,var(--violet));box-shadow:inset 0 0 22px -12px var(--c,var(--violet))}
.pad[data-pad=strobe]{--c:var(--cream);--cbg:rgba(240,231,216,.12)}
.pad[data-pad=neon]{--c:var(--teal);--cbg:rgba(127,214,193,.14)}
.pad[data-pad=scratch]{--c:var(--amber);--cbg:rgba(232,180,119,.14)}
.deck-note{color:var(--text3);font-size:.74rem;margin-top:16px;padding-top:12px;position:relative}
.deck-note::before{content:"";position:absolute;left:0;right:30%;top:0;height:1px;
 background:linear-gradient(90deg,var(--line),transparent)}

.sidebar{display:flex;flex-direction:column;gap:18px}
.panel{padding:18px;border-radius:var(--r-card)}
.panel-title{font-size:.72rem;text-transform:uppercase;letter-spacing:.1em;color:var(--text3);
 margin-bottom:14px;padding-bottom:9px;position:relative;display:flex;justify-content:space-between}
.panel-title::after{content:"";position:absolute;left:0;right:16%;bottom:0;height:1px;
 background:linear-gradient(90deg,var(--line),transparent)}
.panel-title b{color:var(--cream);letter-spacing:0}
.input-group{margin-bottom:11px}
.input-label{display:block;font-size:.72rem;color:var(--text2);margin-bottom:6px;text-transform:uppercase;
 letter-spacing:.06em}
.text-input{width:100%;padding:11px 13px;background:var(--surface2);border:1px solid var(--line);
 border-radius:var(--r-in);color:var(--text);font:inherit;font-size:.9rem;
 transition:border-color .18s,box-shadow .18s,background .18s}
.text-input:focus{outline:none;border-color:var(--violet);background:var(--surface3);
 box-shadow:0 0 0 3px rgba(179,156,255,.14)}
.text-input::placeholder{color:var(--text3)}
.btn-wide{display:block;width:100%;min-width:0;border-radius:var(--pill);margin-top:14px}
.vol-pill{margin-top:12px;text-align:center;font-size:.74rem;color:var(--teal);
 border:1px solid rgba(127,214,193,.32);border-radius:var(--pill);padding:6px 10px;
 background:rgba(127,214,193,.07)}
.queue-panel{max-height:340px;overflow-y:auto;margin:0 -4px}
.queue-panel::-webkit-scrollbar{width:6px}
.queue-panel::-webkit-scrollbar-track{background:transparent}
.queue-panel::-webkit-scrollbar-thumb{background:var(--line);border-radius:var(--pill)}
.queue-item{display:flex;align-items:center;gap:11px;padding:9px 11px;background:rgba(29,25,48,.7);
 border:1px solid transparent;border-radius:var(--r-box);margin-bottom:7px;
 transition:background .18s,border-color .18s,transform .18s}
.queue-item:hover{background:var(--surface3);border-color:rgba(179,156,255,.4);transform:translateX(2px)}
.queue-number{display:flex;align-items:center;justify-content:center;width:24px;height:24px;flex:none;
 border-radius:50%;background:var(--surface3);color:var(--text2);font-size:.7rem;font-weight:600;
 font-variant-numeric:tabular-nums}
.queue-item:hover .queue-number{background:rgba(179,156,255,.22);color:var(--violet)}
.queue-body{min-width:0}
.queue-title{font-size:.86rem;color:var(--cream);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.queue-author{font-size:.74rem;color:var(--text3);margin-top:2px}
.queue-more{padding:6px 10px;font-size:.74rem;color:var(--text3)}
.empty-state{margin:6px 0;padding:24px 14px;text-align:center;color:var(--text3);font-size:.85rem;
 line-height:1.5;border:1px dashed var(--line);border-radius:var(--r-box)}
.empty-state span{display:block;font-size:1.5rem;margin-bottom:8px;opacity:.5}
.message{padding:0;font-size:.8rem;margin-top:10px;min-height:1.2em;color:transparent;transition:color .2s}
.message.success{color:var(--teal);padding:9px 12px;border:1px solid rgba(127,214,193,.3);
 border-radius:var(--r-box);background:rgba(127,214,193,.08);min-height:0}
.message.error{color:var(--red);padding:9px 12px;border:1px solid rgba(239,106,106,.3);
 border-radius:var(--r-box);background:rgba(239,106,106,.08);min-height:0}
.footer{display:table;margin:4px auto 34px;padding:10px 20px;text-align:center;color:var(--text3);
 font-size:.75rem;letter-spacing:.04em;border:1px solid var(--line);border-radius:var(--pill);
 background:rgba(29,25,48,.45)}

/* неон: тот же экран, другая подсветка — переключается подушкой на пульте */
body.neon{--violet:#61f0c8;--teal:#8ef5ff;--accent:#61f0c8}
body.strobe .player-section{animation:strobe var(--beat) steps(2,jump-none) infinite}
@keyframes strobe{0%{box-shadow:0 0 0 0 rgba(240,231,216,0)}
 50%{box-shadow:0 0 44px -14px rgba(240,231,216,.55)}100%{box-shadow:0 0 0 0 rgba(240,231,216,0)}}

@media (max-width:1024px){
 .main-grid{grid-template-columns:minmax(0,1fr)}
 .track-display{grid-template-columns:150px minmax(0,1fr);gap:18px}
 .artwork-wrap{width:150px;height:140px}
 .artwork,.vinyl{width:140px;height:140px}
 .deck-grid{grid-template-columns:minmax(0,1fr)}
 .bpm-box{display:flex;align-items:center;justify-content:center;gap:22px}
 .bpm-row{margin-top:0}
}
@media (max-width:640px){
 .container{padding:0 14px}
 .player-section,.deck{padding:18px}
 .track-display{grid-template-columns:minmax(0,1fr);gap:16px}
 .artwork-wrap{width:100%;height:auto}
 .artwork{position:relative;left:auto;top:auto;width:100%;height:200px}
 .vinyl{display:none}
 .waveform{height:64px}
 .ctrl-btn{min-width:calc(50% - 5px);flex:none}
 .knobs{grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}
 .pads{grid-template-columns:repeat(2,minmax(0,1fr))}
 .brand{font-size:1.3rem}
 .brand span{display:block;margin:4px 0 0}
}
@media (prefers-reduced-motion:reduce){
 body::before,body::after,.beat,.vinyl,.wave-bar{animation:none}
 .waveform.on .wave-bar{height:45%}
 .status.active .status-dot{animation:none}
 .ctrl-btn::after{display:none}
 body.strobe .player-section{animation:none}
 .player-section.flash{animation:none}
}
</style></head>
<body>
<div class="glow"></div>
<div class="beat" id="beat"></div>
<div class="container">
<header class="header">
<div class="header-top">
<div class="brand">HALLEZ<span>музыка семьи</span></div>
<div class="status" id="status"><span class="status-dot"></span><span id="statusText">подключаемся…</span></div>
</div>
<div class="subtitle">Заказываете отсюда — трек попадает в общую очередь и играет в голосовом канале.</div>
</header>

<div class="main-grid">
<div class="card player-section" id="playerCard">
<div class="track-display">
<div class="artwork-wrap">
<div class="vinyl" id="vinyl"></div>
<div class="artwork" id="artwork"><div class="artwork-placeholder">♪</div></div>
</div>
<div class="track-info">
<div class="track-kicker">сейчас в эфире</div>
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

<div class="waveform" id="waveform" aria-hidden="true"></div>

<div class="controls-panel">
<div class="controls-row">
<button class="ctrl-btn primary" data-action="pause" id="pauseBtn">Пауза<kbd>пробел</kbd></button>
<button class="ctrl-btn" data-action="skip">Пропустить<kbd>S</kbd></button>
<button class="ctrl-btn" data-action="dj" id="djBtn">Авто-диджей<kbd>D</kbd></button>
<button class="ctrl-btn" data-action="stop">Стоп</button>
</div>
<div class="slider-row">
<span class="slider-label">громкость</span>
<button class="tap" id="volDown" aria-label="Тише на 5">−</button>
<input type="range" class="slider" id="volSlider" min="5" max="100" step="5" value="50" aria-label="Громкость">
<button class="tap" id="volUp" aria-label="Громче на 5">+</button>
<span class="slider-value" id="volValue">50%</span>
</div>
<div class="hint">«Стоп» чистит всю очередь — он для своих. Пробел, S и D работают, пока ничего не набираешь.</div>
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
<div class="vol-pill" id="volPill">громкость 50%</div>
<div class="message" id="message">&nbsp;</div>
</div>

<div class="card panel">
<div class="panel-title">Очередь <b id="queueCount">0</b></div>
<div class="queue-panel" id="queueList"></div>
</div>
</aside>
</div>

<section class="card deck">
<div class="deck-head">
<div class="deck-title">DJ-пульт</div>
<div class="deck-badge">визуальный</div>
<button class="deck-reset" id="deckReset">сбросить</button>
</div>
<div class="deck-grid">
<div class="bpm-box">
<div>
<div class="bpm-num" id="bpmNum">120</div>
<div class="bpm-lab">удара в минуту</div>
</div>
<div class="bpm-row">
<button class="tap" id="bpmDown" aria-label="Медленнее">−</button>
<span>такт</span>
<button class="tap" id="bpmUp" aria-label="Быстрее">+</button>
</div>
</div>
<div class="knobs" id="knobs">
<div class="knob-group" data-knob="bass">
<div class="knob-lab">бас</div>
<div class="knob" role="slider" tabindex="0" aria-label="Бас" aria-valuemin="-12" aria-valuemax="12"
 aria-valuenow="0" data-min="-12" data-max="12" data-step="1"><i></i></div>
<div class="knob-val" id="bassVal">0 dB</div>
</div>
<div class="knob-group" data-knob="mid">
<div class="knob-lab">мид</div>
<div class="knob" role="slider" tabindex="0" aria-label="Середина" aria-valuemin="-12" aria-valuemax="12"
 aria-valuenow="0" data-min="-12" data-max="12" data-step="1"><i></i></div>
<div class="knob-val" id="midVal">0 dB</div>
</div>
<div class="knob-group" data-knob="high">
<div class="knob-lab">верха</div>
<div class="knob" role="slider" tabindex="0" aria-label="Верха" aria-valuemin="-12" aria-valuemax="12"
 aria-valuenow="0" data-min="-12" data-max="12" data-step="1"><i></i></div>
<div class="knob-val" id="highVal">0 dB</div>
</div>
<div class="knob-group" data-knob="filter">
<div class="knob-lab">фильтр</div>
<div class="knob" role="slider" tabindex="0" aria-label="Фильтр" aria-valuemin="0" aria-valuemax="3"
 aria-valuenow="0" data-min="0" data-max="3" data-step="1"><i></i></div>
<div class="knob-val" id="filterVal">выкл</div>
</div>
</div>
<div class="pads">
<button class="pad" data-pad="echo">эхо</button>
<button class="pad" data-pad="strobe">строб</button>
<button class="pad" data-pad="neon">неон</button>
<button class="pad" data-pad="scratch">скретч</button>
</div>
</div>
<div class="deck-note">Ручки: тяни мышью вверх-вниз, крути колесо, стрелки с клавиатуры, двойной клик — в ноль.
Пульт меняет только картинку у тебя на экране — звук в канале остаётся как есть.</div>
</section>

<footer class="footer">Общий плеер семьи · заказы видны всем в канале</footer>
</div>

<script nonce="__NONCE__">
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const $ = id => document.getElementById(id);
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
let refreshTimer = null;
let clock = {elapsed:0, duration:0, at:0, running:false};
let lastTitle = null;

/* ── визуализатор: столбики делятся на три полосы, ручки пульта двигают свою ── */
const BANDS = 3;
(function buildWave(){
 const box = $('waveform'), frag = document.createDocumentFragment();
 for(let i=0;i<48;i++){
  const bar = document.createElement('div');
  bar.className = 'wave-bar';
  bar.dataset.band = i < 18 ? 0 : (i < 34 ? 1 : 2);
  bar.style.setProperty('--base', (26 + Math.abs(Math.sin(i*1.7))*70).toFixed(0));
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

/* прогресс дорисовываем между опросами, чтобы ползунок шёл плавно, а не скачком раз в 3 сек */
function tick(){
 if(clock.running && clock.duration){
  const sec = clock.elapsed + (Date.now() - clock.at)/1000;
  $('progressFill').style.width = Math.min(100, sec/clock.duration*100) + '%';
  $('currentTime').textContent = fmt(Math.min(sec, clock.duration));
 }
}
setInterval(tick, 1000);

/* ── пульт ── */
const DECK_KEY = 'hz-deck';
const FILTERS = ['выкл', 'мягкий', 'плотный', 'обрезан'];
let deck = {bpm:120, bass:0, mid:0, high:0, filter:0, echo:false, strobe:false, neon:false};
try{ Object.assign(deck, JSON.parse(localStorage.getItem(DECK_KEY) || '{}')); }catch(e){ /* битый кэш — не страшно */ }
deck.bpm = clamp(+deck.bpm || 120, 60, 200);
deck.filter = clamp(Math.round(+deck.filter || 0), 0, 3);
['bass', 'mid', 'high'].forEach(k => deck[k] = clamp(Math.round(+deck[k] || 0), -12, 12));

function paintDeck(){
 $('bpmNum').textContent = deck.bpm;
 document.documentElement.style.setProperty('--beat', (60 / deck.bpm).toFixed(3) + 's');
 $('beat').classList.toggle('on', deck.strobe || deck.bpm !== 120);
 document.body.classList.toggle('neon', deck.neon);
 document.body.classList.toggle('strobe', deck.strobe);
 $('waveform').classList.toggle('echo', deck.echo);
 document.querySelectorAll('.pad').forEach(p => {
  if(p.dataset.pad !== 'scratch') p.classList.toggle('on', !!deck[p.dataset.pad]);
 });
 const gain = [deck.bass, deck.mid, deck.high];
 document.querySelectorAll('.wave-bar').forEach(bar => {
  const b = +bar.dataset.band;
  const cut = [1, 1 - deck.filter*0.18, 1 - deck.filter*0.42][b];
  const h = clamp((+bar.style.getPropertyValue('--base') || 40) * (1 + gain[b]/22) * cut, 6, 100);
  bar.style.setProperty('--h', h.toFixed(0) + '%');
 });
 document.querySelectorAll('.knob-group').forEach(g => {
  const k = g.querySelector('.knob');
  const min = +k.dataset.min, max = +k.dataset.max, v = deck[g.dataset.knob];
  const pct = (v - min) / (max - min);
  k.style.setProperty('--deg', Math.round(-135 + pct*270) + 'deg');
  k.style.setProperty('--p', Math.round(pct*300) + 'deg');
  k.setAttribute('aria-valuenow', v);
 });
 $('bassVal').textContent = (deck.bass > 0 ? '+' : '') + deck.bass + ' dB';
 $('midVal').textContent = (deck.mid > 0 ? '+' : '') + deck.mid + ' dB';
 $('highVal').textContent = (deck.high > 0 ? '+' : '') + deck.high + ' dB';
 $('filterVal').textContent = FILTERS[deck.filter];
 try{ localStorage.setItem(DECK_KEY, JSON.stringify(deck)); }catch(e){ /* приватный режим */ }
}

function setKnob(name, value){
 const k = document.querySelector('.knob-group[data-knob=' + name + '] .knob');
 deck[name] = Math.round(clamp(value, +k.dataset.min, +k.dataset.max));
 paintDeck();
}

document.querySelectorAll('.knob-group').forEach(group => {
 const name = group.dataset.knob, knob = group.querySelector('.knob');
 const min = +knob.dataset.min, max = +knob.dataset.max, step = +knob.dataset.step;
 let drag = null;
 knob.addEventListener('pointerdown', e => {
  knob.setPointerCapture(e.pointerId);
  drag = {y: e.clientY, v: deck[name]};
  e.preventDefault();
 });
 knob.addEventListener('pointermove', e => {
  if(!drag) return;
  setKnob(name, drag.v + (drag.y - e.clientY) / 6 * (max - min) / 24);
 });
 const stop = () => { drag = null; };
 knob.addEventListener('pointerup', stop);
 knob.addEventListener('pointercancel', stop);
 knob.addEventListener('wheel', e => {
  e.preventDefault();
  setKnob(name, deck[name] + (e.deltaY < 0 ? step : -step));
 }, {passive: false});
 knob.addEventListener('keydown', e => {
  const map = {ArrowUp: step, ArrowRight: step, ArrowDown: -step, ArrowLeft: -step};
  if(e.key in map){ e.preventDefault(); setKnob(name, deck[name] + map[e.key]); }
  else if(e.key === 'Home'){ e.preventDefault(); setKnob(name, min); }
  else if(e.key === 'End'){ e.preventDefault(); setKnob(name, max); }
 });
 knob.addEventListener('dblclick', () => setKnob(name, 0));
});

$('bpmUp').addEventListener('click', () => { deck.bpm = clamp(deck.bpm + 4, 60, 200); paintDeck(); });
$('bpmDown').addEventListener('click', () => { deck.bpm = clamp(deck.bpm - 4, 60, 200); paintDeck(); });
document.querySelectorAll('.pad').forEach(pad => {
 pad.addEventListener('click', () => {
  const what = pad.dataset.pad;
  if(what === 'scratch'){
   const disc = $('vinyl');
   disc.classList.remove('scratch');
   void disc.offsetWidth;          // перезапуск анимации: без этого второй клик молчит
   disc.classList.add('scratch');
   pad.classList.add('on');
   setTimeout(() => pad.classList.remove('on'), 550);
   return;
  }
  deck[what] = !deck[what];
  paintDeck();
 });
});
$('deckReset').addEventListener('click', () => {
 deck = {bpm:120, bass:0, mid:0, high:0, filter:0, echo:false, strobe:false, neon:false};
 paintDeck();
 showMsg('Пульт в ноле', 'success');
});
paintDeck();

function updateUI(state){
 const live = !!state.connected, playing = !!state.playing, paused = !!state.paused;
 const status = $('status');
 status.classList.toggle('active', live);
 // сервер сам говорит, почему молчит (ког выключен, плеер упал) — показываем его слова,
 // а не свою догадку «бот не в голосовом»
 $('statusText').textContent = state.error ? 'нет связи' : live
  ? (playing ? 'в эфире · ' + (state.channel || 'голосовой') : (paused ? 'на паузе' : 'ждёт заказов'))
  : 'бот не в голосовом';

 $('waveform').classList.toggle('on', playing || paused);
 $('waveform').classList.toggle('playing', playing);
 $('vinyl').classList.toggle('spin', playing);

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
  if(lastTitle && lastTitle !== cur.title){
   const card = $('playerCard');
   card.classList.remove('flash'); void card.offsetWidth; card.classList.add('flash');
  }
  lastTitle = cur.title;
 }else{
  clock = {elapsed:0, duration:0, at:0, running:false};
  lastTitle = null;
  $('trackTitle').textContent = playing ? 'звучит' : 'Сейчас ничего не играет';
  $('trackMeta').textContent = state.error || (live ? 'Очередь пуста — закажи первым.' : 'Боту нужно зайти в голосовой канал.');
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
 $('volPill').textContent = 'громкость ' + vol + '%';
 $('chipQueue').textContent = (state.queue || []).length;
 $('chipDj').textContent = state.dj ? 'вкл' : 'выкл';
 $('djBtn').classList.toggle('on', !!state.dj);
 $('pauseBtn').firstChild.textContent = paused ? 'Продолжить' : 'Пауза';
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
$('nickInput').addEventListener('keydown', e => { if(e.key === 'Enter') order(); });
$('volSlider').addEventListener('change', () => act('volume', Number($('volSlider').value)));
$('volSlider').addEventListener('input', () => { $('volValue').textContent = $('volSlider').value + '%'; });
$('volUp').addEventListener('click', () => act('volume', clamp(Number($('volSlider').value) + 5, 5, 100)));
$('volDown').addEventListener('click', () => act('volume', clamp(Number($('volSlider').value) - 5, 5, 100)));

/* горячие клавиши: только когда человек не набирает текст */
document.addEventListener('keydown', e => {
 if(e.ctrlKey || e.metaKey || e.altKey) return;
 const t = e.target;
 if(t && (t.tagName === 'INPUT' || t.classList && t.classList.contains('knob'))) return;
 if(e.code === 'Space'){ e.preventDefault(); act('pause'); }
 else if(e.key === 's' || e.key === 'ы'){ act('skip'); }
 else if(e.key === 'd' || e.key === 'в'){ act('dj'); }
});

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
