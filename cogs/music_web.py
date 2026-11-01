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
# Ключ анализа трека — 16 знаков sha1 от пути, размера и даты файла. В URL его
# пускают только таким: иначе чтение по чужому пути превращается в прогулку по диску.
AMBIENT_KEY = re.compile(r"\A[0-9a-f]{16}\Z")
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
            app.router.add_get(pre + "/api/library", self.h_library)
            app.router.add_get(pre + "/api/playlists", self.h_playlists)
            app.router.add_get(pre + "/api/cover", self.h_cover)
            app.router.add_get(pre + "/api/ambient", self.h_ambient)
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
        if not state.get("palette"):
            # первый опрос нового трека добирает цвета: обложка качается один раз и
            # дальше живёт в кэше плеера
            try:
                state["palette"] = (await cog.web_cover(guild))[1]
            except Exception:
                state["palette"] = []
        return _json(state)

    async def h_library(self, request: web.Request) -> web.Response:
        cog, guild = self._player()
        if cog is None:
            return _json({"tracks": [], "error": "Модуль музыки выключен"})
        try:
            return _json(cog.web_library(request.query.get("q", "")[:80]))
        except Exception as e:
            return _json({"tracks": [], "error": f"Библиотека не отвечает: {e.__class__.__name__}"})

    async def h_playlists(self, request: web.Request) -> web.Response:
        """Семейные плейлисты: тот же файл, что читает /музыка в Discord."""
        cog, guild = self._player()
        if cog is None:
            return _json({"playlists": [], "error": "Модуль музыки выключен"})
        try:
            return _json(cog.web_playlists())
        except Exception as e:
            return _json({"playlists": [],
                          "error": f"Плейлисты не открылись: {e.__class__.__name__}"})

    async def h_cover(self, request: web.Request) -> web.Response:
        """Обложка текущего трека: то, что плеер достал из mp3, страница не увидит иначе."""
        cog, guild = self._player()
        if cog is None:
            raise web.HTTPNotFound()
        try:
            raw = (await cog.web_cover(guild))[0]
        except Exception:
            raise web.HTTPNotFound()
        if not raw:
            raise web.HTTPNotFound()
        kind = "image/png" if raw[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
        return web.Response(body=raw, content_type=kind,
                            headers={"Cache-Control": "public, max-age=86400"})

    async def h_ambient(self, request: web.Request) -> web.Response:
        """Полосы уже сыгранного трека — по ним фон дышит вместе с музыкой.

        Считает сам бот: страница звук не получает (он уходит в голосовой канал),
        так что браузер ничего не анализирует, а только читает готовые кадры.
        Пока анализ не готов — отвечаем pending, страница переспросит.
        """
        key = request.query.get("k", "")
        if not AMBIENT_KEY.fullmatch(key):
            raise web.HTTPNotFound()
        cog, guild = self._player()
        if cog is None:
            return _json({"k": key, "pending": True})
        try:
            state = cog.web_ambient_state(guild)
            data = cog.web_ambient_bands(key)
        except Exception as e:
            return _json({"k": key, "pending": True,
                          "error": f"Анализ не отвечал: {e.__class__.__name__}"})
        if data is None:
            return _json({"k": key, "pending": True,
                          "fps": state.get("fps", 0), "frames": state.get("frames", 0)})
        resp = web.json_response(data)
        resp.headers["Cache-Control"] = "public, max-age=3600"
        return resp

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
        # кто крутит ручки — спрашиваем у страницы: звук в канале один на всех,
        # и «бас поднял Вася» должно быть видно остальным
        nick = re.sub(r"\s+", " ", str(body.get("nick") or "")).strip()[:NICK_MAX]
        res = await cog.web_action(guild, str(body.get("action") or ""),
                                   body.get("value"), who=nick)
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
<meta name="theme-color" content="#09090c">
<meta name="color-scheme" content="dark">
<meta name="robots" content="noindex">
<title>HALLEZ · музыка семьи</title>
<style nonce="__NONCE__">
/* ═════════ база: графит и стекло; свет берётся с обложки, но всегда приглушённый ═════════ */
:root{
  color-scheme:dark;
  --ink:#09090c; --ink-2:#0e0e13; --ink-3:#15151b;
  --text:#f3f2ef; --text-2:rgba(243,242,239,.68); --text-3:rgba(243,242,239,.42);
  --line:rgba(255,255,255,.07); --line-2:rgba(255,255,255,.14);
  --glass:rgba(255,255,255,.04); --glass-2:rgba(255,255,255,.075);
  --g1:#5a5f8f; --g2:#3f6f78; --g3:#7a5a6b;   /* эти цвета берёт с обложки трека скрипт */
  --accent:#a9acdc;
  --gold:#e2c58f;
  --ok:#6fd3a8; --warn:#e3b86a;
  --pulse:0; --hit:0; --mid:0; --hi:0;   /* живой уровень полос и удар баса, подставляет скрипт */
  --r-xl:30px; --r-lg:24px; --r-md:18px; --r-sm:14px; --r-pill:999px;
  --beat:1.15s;   /* такт фона: страница подставляет значение из ручки «Темп» */
  --shadow:0 30px 70px -34px rgba(0,0,0,.85);
  --ease:cubic-bezier(.22,.61,.36,1);
}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
html{scroll-behavior:smooth}
body{
  min-height:100vh; background:var(--ink); color:var(--text);
  font-family:"Inter","SF Pro Display","Segoe UI Variable Display","Segoe UI",system-ui,-apple-system,Roboto,sans-serif;
  font-size:16px; line-height:1.5; overflow-x:hidden;
  -webkit-font-smoothing:antialiased; text-rendering:optimizeLegibility;
}
button,input,select,textarea{font:inherit;color:inherit}
button{cursor:pointer;border:0;background:none;padding:0}
img{display:block;max-width:100%}
h1,h2,h3{margin:0;font-weight:700;letter-spacing:-.02em}
:focus-visible{outline:2px solid color-mix(in srgb,var(--accent) 80%,#fff);outline-offset:3px;border-radius:10px}

/* ═════════ живой фон ═════════
   главный вариант — холст `#amb`: страница получает настоящие полосы частот
   текущего трека и рисует мягкий свет по ним. пока анализа нет (поток, чужая ссылка,
   первый кадр) — работает запасной вариант: три размытых пятна дышат тактом пульта */
.amb{position:fixed;inset:0;z-index:0;width:100%;height:100%;pointer-events:none;
  opacity:0;transition:opacity 1.2s var(--ease)}
body.amb-on .amb{opacity:1}
.sky{position:fixed;inset:-25%;z-index:0;pointer-events:none;filter:blur(100px) saturate(1.05);
  opacity:.5;transform-origin:50% 55%;animation:breathe var(--beat) var(--ease) infinite}
body.amb-on .sky{opacity:.16;animation:none}
@keyframes breathe{0%{transform:scale(1);opacity:.4}
  12%{transform:scale(1.04) translateY(-1vh);opacity:.62}
  100%{transform:scale(1);opacity:.4}}
.orb{position:absolute;border-radius:50%;opacity:.55;
  transition:background-color 1600ms var(--ease);will-change:transform}
.orb-a{width:52vmax;height:52vmax;left:-8vmax;top:-6vmax;background-color:var(--g1);
  animation:float-a 26s ease-in-out infinite}
.orb-b{width:44vmax;height:44vmax;right:-6vmax;top:22vmax;background-color:var(--g2);
  animation:float-b 31s ease-in-out infinite}
.orb-c{width:38vmax;height:38vmax;left:26vw;bottom:-10vmax;background-color:var(--g3);
  animation:float-c 37s ease-in-out infinite}
@keyframes float-a{0%,100%{transform:translate3d(0,0,0) scale(1)}50%{transform:translate3d(6vw,4vh,0) scale(1.12)}}
@keyframes float-b{0%,100%{transform:translate3d(0,0,0) scale(1.06)}50%{transform:translate3d(-5vw,-6vh,0) scale(.94)}}
@keyframes float-c{0%,100%{transform:translate3d(0,0,0) scale(.96)}50%{transform:translate3d(3vw,-5vh,0) scale(1.1)}}
.veil{position:fixed;inset:0;z-index:1;pointer-events:none;
  background:radial-gradient(120% 90% at 50% -10%,rgba(0,0,0,0) 35%,rgba(4,4,7,.72) 100%)}
body.paused .sky{animation:none}
body.calm .sky{opacity:.3;animation:none}
body.calm .orb{animation:none}
body.calm .amb{opacity:0}

/* ═════════ знаки: один набор векторных иконок на все кнопки ═════════ */
.ic{width:1.15em;height:1.15em;display:block;fill:none;stroke:currentColor;stroke-width:1.7;
  stroke-linecap:round;stroke-linejoin:round;pointer-events:none;
  transition:transform .25s var(--ease)}
.ic.sol{fill:currentColor;stroke-width:1.8}
.ctrl .ic{width:1.2rem;height:1.2rem}
.icon-btn .ic,.star .ic{width:17px;height:17px}
.big .ic{width:1.7rem;height:1.7rem;fill:currentColor;stroke-width:2.6}
.mini .ctrl .ic{width:1.05rem;height:1.05rem}
.vol .ic,.order .grow .ic{width:17px;height:17px;color:var(--text-3)}
.sprite{position:absolute;width:0;height:0;overflow:hidden}
button:hover .ic{transform:scale(1.07)}

/* ═════════ стеклянные поверхности ═════════ */
.glass{position:relative;
  background:linear-gradient(180deg,rgba(255,255,255,.055),rgba(255,255,255,.022));
  border:1px solid var(--line);
  -webkit-backdrop-filter:blur(22px) saturate(1.15);backdrop-filter:blur(22px) saturate(1.15);
  box-shadow:var(--shadow),inset 0 1px 0 rgba(255,255,255,.06)}

/* ═════════ шапка ═════════ */
.wrap{position:relative;z-index:2;max-width:1160px;margin:0 auto;padding:22px clamp(14px,3vw,28px) 140px}
.top{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:4px 0 22px}
.brand{display:flex;align-items:baseline;gap:10px;font-weight:700;letter-spacing:.18em;
  text-transform:uppercase;font-size:.8rem}
.brand b{color:var(--text)}
.brand span{color:var(--text-3);letter-spacing:.14em;font-weight:500}
.pill{display:inline-flex;align-items:center;gap:8px;padding:7px 14px 7px 12px;border-radius:var(--r-pill);
  border:1px solid var(--line);background:var(--glass);font-size:.78rem;color:var(--text-2);
  white-space:nowrap;transition:border-color .3s,color .3s}
.pill i{width:7px;height:7px;border-radius:50%;background:var(--text-3);flex:none}
.pill.live{color:var(--text)}
.pill.live i{background:var(--ok);animation:beat 2.4s infinite}
.pill.idle i{background:var(--warn)}
body.amb-on .pill.live i{animation:none;transform:scale(calc(1 + .6 * var(--hit)));
  box-shadow:0 0 calc(2px + 12px * var(--hit)) rgba(111,211,168,.6)}
@keyframes beat{0%{box-shadow:0 0 0 0 rgba(111,211,168,.45)}70%{box-shadow:0 0 0 8px rgba(111,211,168,0)}
 100%{box-shadow:0 0 0 0 rgba(111,211,168,0)}}
@keyframes ping{0%{box-shadow:0 0 0 0 color-mix(in srgb,var(--accent) 50%,transparent)}
 70%{box-shadow:0 0 0 9px transparent}100%{box-shadow:0 0 0 0 transparent}}
.top .spacer{flex:1 1 24px}
.field{display:flex;align-items:center;gap:8px;padding:4px 14px;border-radius:var(--r-pill);
  border:1px solid var(--line);background:rgba(255,255,255,.035);
  transition:border-color .3s,box-shadow .3s}
.field:focus-within{border-color:color-mix(in srgb,var(--accent) 55%,transparent);
  box-shadow:0 0 0 4px color-mix(in srgb,var(--accent) 12%,transparent)}
.field label{font-size:.68rem;text-transform:uppercase;letter-spacing:.14em;color:var(--text-3)}
.text-input{background:transparent;border:0;outline:0;min-width:0;width:clamp(96px,18vw,160px);
  padding:7px 2px;font-size:.88rem}
.text-input:focus-visible{outline:none}
.text-input::placeholder{color:var(--text-3)}
.icon-btn{width:40px;height:40px;border-radius:var(--r-sm);border:1px solid var(--line);
  background:var(--glass);color:var(--text-2);display:grid;place-items:center;
  transition:transform .2s var(--ease),border-color .3s,color .3s,background-color .3s}
.icon-btn:hover{color:var(--text);background:var(--glass-2);border-color:var(--line-2);transform:translateY(-1px)}
.icon-btn:active{transform:scale(.93)}
.icon-btn.on{color:var(--accent);background:color-mix(in srgb,var(--accent) 14%,transparent);
  border-color:color-mix(in srgb,var(--accent) 36%,transparent)}
@media (max-width:640px){.top{gap:10px}.brand{font-size:.74rem}}

/* ═════════ главный экран: сейчас играет ═════════ */
.hero{border-radius:var(--r-xl);padding:clamp(18px,3vw,34px);display:grid;gap:clamp(22px,3.4vw,40px);
  grid-template-columns:minmax(240px,370px) minmax(0,1fr);align-items:center;overflow:hidden;
  transform-style:preserve-3d;transition:transform .3s var(--ease),border-color .6s}
.hero::before{content:"";position:absolute;left:-8%;top:8%;width:60%;height:84%;border-radius:50%;
  background:var(--accent);filter:blur(110px);opacity:.14;z-index:-1;pointer-events:none;
  transition:background-color 1400ms var(--ease);animation:halo var(--beat) var(--ease) infinite}
@keyframes halo{0%{opacity:.11;transform:scale(1)}12%{opacity:.22;transform:scale(1.04)}
  100%{opacity:.11;transform:scale(1)}}
body.paused .hero::before,body.calm .hero::before{animation:none}
.art{position:relative;border-radius:var(--r-lg);overflow:hidden;aspect-ratio:1;
  background:linear-gradient(150deg,var(--ink-3),var(--ink-2));
  box-shadow:0 30px 70px -30px #000,0 0 0 1px var(--line) inset,
             0 0 calc(6px + 40px * var(--pulse)) -8px color-mix(in srgb,var(--accent) 70%,transparent);
  transition:transform .4s var(--ease)}
.hero:hover .art{transform:translateY(-3px) scale(1.01)}
.art img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;opacity:0;
  transition:opacity .7s var(--ease)}
.art img.show{opacity:1}
.art .mark{position:absolute;inset:0;display:grid;place-items:center;color:var(--text-3)}
.art .mark .ic{width:26%;height:26%;opacity:.4}
.art.flip{animation:flip .7s var(--ease)}
@keyframes flip{0%{transform:rotateY(0) scale(1);opacity:.2}55%{transform:rotateY(-9deg) scale(1.03)}
 100%{transform:rotateY(0) scale(1);opacity:1}}

/* ── винил: пластинка за обложкой. угол считает скрипт по позиции трека ── */
.artwrap{position:relative}
.vinyl{position:absolute;top:5%;right:-9%;width:90%;height:90%;z-index:0;pointer-events:none;
  opacity:0;transition:opacity .8s var(--ease)}
body.has-vinyl .vinyl{opacity:1}
.vinyl .disc{position:absolute;inset:0;border-radius:50%;
  background:repeating-radial-gradient(circle at 50% 50%,rgba(255,255,255,.05) 0 1px,
    rgba(0,0,0,0) 1px 4px),
    conic-gradient(from 0deg,rgba(255,255,255,.11),rgba(255,255,255,0) 22%,
    rgba(255,255,255,.05) 48%,rgba(255,255,255,0) 72%,rgba(255,255,255,.11)),
    radial-gradient(circle at 50% 50%,#1d1d25 0 30%,#0c0c11 31% 100%);
  box-shadow:0 26px 50px -26px #000,0 0 0 1px rgba(255,255,255,.08)}
.vinyl .lbl{position:absolute;left:50%;top:50%;width:34%;height:34%;margin:-17% 0 0 -17%;
  border-radius:50%;background:radial-gradient(circle at 40% 35%,var(--accent),rgba(0,0,0,.6) 130%);
  box-shadow:0 0 0 3px rgba(0,0,0,.45)}
.vinyl .hole{position:absolute;left:50%;top:50%;width:3.6%;height:3.6%;margin:-1.8% 0 0 -1.8%;
  border-radius:50%;background:var(--ink);box-shadow:0 0 0 1px rgba(255,255,255,.22)}
.artwrap .art{position:relative;z-index:1}
.rays{position:absolute;inset:auto 0 -1px 0;height:40%;opacity:.45;pointer-events:none;
  background:linear-gradient(to top,var(--ink),transparent)}
.lab{font-size:.68rem;text-transform:uppercase;letter-spacing:.2em;font-weight:700;
  color:color-mix(in srgb,var(--accent) 85%,#fff);transition:color .9s linear}
.title{font-size:clamp(1.7rem,4.6vw,3rem);line-height:1.05;margin:10px 0 8px;font-weight:800;
  letter-spacing:-.03em;overflow-wrap:anywhere;
  transition:opacity .5s var(--ease),transform .5s var(--ease)}
.title.out{opacity:0;transform:translateY(10px)}
.hero .who{display:flex;align-items:center;gap:10px;flex-wrap:wrap;color:var(--text-2);font-size:.92rem}
#trackWho{display:inline-flex;align-items:center;gap:10px;flex-wrap:wrap;min-width:0}
#trackWho b{color:var(--text);font-weight:600}
.dot{width:4px;height:4px;border-radius:50%;background:var(--text-3);flex:none}
.time{font-variant-numeric:tabular-nums;color:var(--text-3);font-size:.84rem}

/* ── спектр: холст, который рисует настоящие полосы трека ── */
.viz{display:block;width:100%;height:58px;margin:18px 0 6px;pointer-events:none}
body.paused .viz{opacity:.55}
body.calm .viz{height:26px}

/* ── перемотка ── */
.seek{display:flex;align-items:center;gap:12px;margin-top:8px}
.seek .t{font-size:.78rem;color:var(--text-2);font-variant-numeric:tabular-nums;min-width:44px}
.seek .t.big-t{font-size:.98rem;color:var(--text);font-weight:700}
.seek .t-end{text-align:right}
.track-bar{position:relative;flex:1 1 0;height:30px;display:flex;align-items:center;min-width:0}
.track-bar::before{content:"";position:absolute;left:0;right:0;height:5px;border-radius:99px;
  background:rgba(255,255,255,.10)}
.track-bar i{position:absolute;left:0;height:5px;border-radius:99px;width:0;
  background:linear-gradient(90deg,color-mix(in srgb,var(--accent) 55%,var(--ink)),var(--accent));
  transition:width .25s linear;
  box-shadow:0 0 calc(4px + 16px * var(--pulse)) -3px var(--accent)}

/* ── ползунки: тонкая дорожка с заливкой цветом обложки (заливку считает скрипт) ── */
.range{--a:0%;--b:0%;-webkit-appearance:none;appearance:none;background:transparent;margin:0;
  width:100%;height:24px;display:block;cursor:pointer}
.range::-webkit-slider-runnable-track{height:4px;border-radius:99px;
  background:linear-gradient(90deg,transparent var(--a),var(--accent) var(--a),var(--accent) var(--b),transparent var(--b)),rgba(255,255,255,.14)}
.range::-moz-range-track{height:4px;border-radius:99px;
  background:linear-gradient(90deg,transparent var(--a),var(--accent) var(--a),var(--accent) var(--b),transparent var(--b)),rgba(255,255,255,.14)}
.range::-webkit-slider-thumb{-webkit-appearance:none;width:16px;height:16px;margin-top:-6px;border-radius:50%;
  background:#fff;border:0;cursor:grab;
  box-shadow:0 2px 8px rgba(0,0,0,.55),0 0 0 0 rgba(255,255,255,.12);
  transition:box-shadow .25s,transform .2s}
.range::-moz-range-thumb{width:16px;height:16px;border-radius:50%;background:#fff;border:0;cursor:grab;
  box-shadow:0 2px 8px rgba(0,0,0,.55)}
.range:hover::-webkit-slider-thumb{box-shadow:0 2px 8px rgba(0,0,0,.55),0 0 0 6px rgba(255,255,255,.10);
  transform:scale(1.08)}
.range:active::-webkit-slider-thumb{cursor:grabbing;transform:scale(1.16)}
.range.off{pointer-events:none;opacity:.4}
/* перемотка: дорожку рисует `.track-bar`, сам ползунок только ловит нажатия */
.track-bar .range{position:absolute;left:0;top:0;height:30px}
.track-bar .range::-webkit-slider-runnable-track{height:30px;background:transparent}
.track-bar .range::-moz-range-track{height:30px;background:transparent}
.track-bar .range::-webkit-slider-thumb{margin-top:7px}

/* ── транспорт: мягкое стекло, один отклик на нажатие ── */
.row{display:flex;align-items:center;gap:14px 20px;flex-wrap:wrap;margin-top:22px}
.grp{display:flex;align-items:center;gap:8px}
.big{width:68px;height:68px;border-radius:50%;display:grid;place-items:center;
  color:var(--ink);flex:none;position:relative;
  background:linear-gradient(180deg,#ffffff,#e5e3de);
  box-shadow:0 14px 34px -14px color-mix(in srgb,var(--accent) 85%,transparent),inset 0 1px 0 rgba(255,255,255,.8);
  transition:transform .2s var(--ease),box-shadow .35s}
.big:hover{transform:scale(1.05)}
.big:active{transform:scale(.94)}
.ctrl{width:46px;height:46px;border-radius:16px;display:grid;place-items:center;
  border:1px solid var(--line);color:var(--text-2);flex:none;
  background:linear-gradient(180deg,rgba(255,255,255,.08),rgba(255,255,255,.03));
  box-shadow:inset 0 1px 0 rgba(255,255,255,.07);
  transition:transform .2s var(--ease),color .25s,border-color .25s,background-color .3s}
.ctrl:hover{color:var(--text);border-color:var(--line-2);background:rgba(255,255,255,.09);transform:translateY(-1px)}
.ctrl:active{transform:scale(.93)}
.ctrl.on{color:var(--accent);border-color:color-mix(in srgb,var(--accent) 40%,transparent);
  background:color-mix(in srgb,var(--accent) 16%,transparent)}
.vol{display:flex;align-items:center;gap:10px;height:46px;padding:0 16px;border-radius:var(--r-pill);
  flex:1 1 170px;min-width:0;max-width:300px;
  border:1px solid var(--line);background:rgba(255,255,255,.03);transition:border-color .3s}
.vol:hover{border-color:var(--line-2)}
.vol .range{flex:1 1 60px;width:auto;min-width:50px}
.vol b{font-size:.78rem;color:var(--text-2);min-width:38px;text-align:right;font-variant-numeric:tabular-nums}
.kbd{margin-top:16px;font-size:.72rem;color:var(--text-3);text-align:right}
.kbd kbd{border:1px solid var(--line);border-radius:6px;padding:1px 6px;background:rgba(255,255,255,.04);
  font-family:inherit;font-size:.68rem;color:var(--text-2)}

/* ═════════ пульт: ручки = фильтры ffmpeg, слышит весь канал ═════════ */
.deck{border-radius:var(--r-xl);padding:clamp(18px,2.8vw,28px);margin-top:20px}
.head{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding-bottom:16px;margin-bottom:18px;
  border-bottom:1px solid var(--line)}
.head h2{font-size:.78rem;text-transform:uppercase;letter-spacing:.18em;font-weight:700;margin-right:4px}
.tag{font-size:.66rem;text-transform:uppercase;letter-spacing:.1em;color:var(--text-2);
  border:1px solid var(--line);border-radius:var(--r-pill);padding:4px 11px;background:var(--glass)}
.tag.who{color:color-mix(in srgb,var(--accent) 80%,#fff);
  border-color:color-mix(in srgb,var(--accent) 35%,transparent);
  background:color-mix(in srgb,var(--accent) 8%,transparent)}
.head .reset{margin-left:auto;display:inline-flex;align-items:center;gap:7px;height:34px;padding:0 14px;
  font-size:.7rem;text-transform:uppercase;letter-spacing:.1em;color:var(--text-2);
  border:1px solid var(--line);border-radius:var(--r-pill);background:var(--glass);
  transition:color .25s,border-color .25s,background-color .25s,transform .2s var(--ease)}
.head .reset:hover{color:var(--text);background:var(--glass-2);border-color:var(--line-2)}
.head .reset:active{transform:scale(.95)}
.head .reset .ic{width:14px;height:14px}
.knobs{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}
#k-filter,#k-echo{grid-column:span 2}
.knob{position:relative;display:flex;flex-direction:column;gap:14px;padding:16px 16px 14px;
  border-radius:var(--r-md);border:1px solid var(--line);background:rgba(255,255,255,.025);
  transition:border-color .3s,background-color .3s}
.knob:hover{background:rgba(255,255,255,.04);border-color:var(--line-2)}
.knob h3{font-size:.68rem;text-transform:uppercase;letter-spacing:.14em;color:var(--text-3);
  font-weight:700;display:flex;align-items:center;gap:7px}
.knob h3 .ic{width:15px;height:15px}
.knob .v{margin-left:auto;font-variant-numeric:tabular-nums;font-size:.76rem;color:var(--text);
  letter-spacing:0;text-transform:none;font-weight:600}
.knob .range,.knob .seg,.knob .switch{margin-top:auto}
.knob.live{border-color:color-mix(in srgb,var(--accent) 38%,transparent);
  background:color-mix(in srgb,var(--accent) 5%,transparent)}

/* ── переключатель режима и эха ── */
.seg{display:grid;grid-template-columns:repeat(4,1fr);gap:4px;padding:4px;border-radius:var(--r-sm);
  border:1px solid var(--line);background:rgba(0,0,0,.25)}
.seg button{padding:8px 6px;border-radius:10px;color:var(--text-3);font-size:.76rem;white-space:nowrap;
  transition:color .25s,background-color .25s,box-shadow .3s,transform .2s var(--ease)}
.seg button:hover{color:var(--text)}
.seg button:active{transform:scale(.95)}
.seg button.on{color:var(--ink);background:var(--text);font-weight:700;
  box-shadow:0 6px 16px -8px rgba(0,0,0,.7)}
.switch{display:flex;align-items:center;gap:12px;width:100%;padding:4px 0;color:var(--text-2);
  font-size:.82rem;transition:color .25s}
.switch:hover{color:var(--text)}
.switch i{position:relative;width:46px;height:28px;border-radius:99px;flex:none;
  background:rgba(255,255,255,.10);border:1px solid var(--line);
  transition:background-color .35s var(--ease),border-color .35s}
.switch i::after{content:"";position:absolute;left:3px;top:3px;width:20px;height:20px;
  border-radius:50%;background:#fff;opacity:.7;box-shadow:0 2px 6px rgba(0,0,0,.45);
  transition:transform .35s var(--ease),opacity .3s}
.switch[aria-checked="true"] i{background:color-mix(in srgb,var(--accent) 72%,var(--ink));border-color:transparent}
.switch[aria-checked="true"] i::after{transform:translateX(18px);opacity:1}
.switch span{margin-left:auto;font-size:.68rem;text-transform:uppercase;letter-spacing:.12em;
  color:var(--text-3)}

/* ── эквалайзер: три полосы = те же срезы, что считает бот (≤160 / 160–1400 / >1400 Гц) ── */
.eq{margin-top:12px;padding:18px 16px;border-radius:var(--r-md);border:1px solid var(--line);
  background:rgba(255,255,255,.025)}
.eq-head{display:flex;align-items:center;gap:10px;margin-bottom:14px;flex-wrap:wrap}
.eq-head h3{display:flex;align-items:center;gap:8px;
  font-size:.68rem;text-transform:uppercase;letter-spacing:.14em;color:var(--text-3);font-weight:700}
.eq-head h3 .ic{width:15px;height:15px}
.eq-head .tag{margin-left:auto}
.bands{display:flex;align-items:flex-end;gap:clamp(8px,2vw,16px);height:150px}
.band{flex:1 1 0;min-width:0;display:flex;flex-direction:column;align-items:center;gap:8px;height:100%}
.band .meter{position:relative;flex:1 1 auto;width:min(100%,150px);border-radius:14px;
  background:repeating-linear-gradient(to top,rgba(255,255,255,.06) 0 1px,transparent 1px 25%),
    rgba(255,255,255,.05);
  overflow:hidden;box-shadow:0 0 0 1px var(--line) inset}
.band .meter i{position:absolute;left:0;right:0;bottom:0;height:2%;border-radius:14px 14px 4px 4px;
  background:linear-gradient(to top,color-mix(in srgb,var(--accent) 40%,var(--ink)),var(--accent));
  transition:height .1s linear,background-color 1s var(--ease);
  box-shadow:0 0 calc(4px + 18px * var(--pulse)) -2px var(--accent)}
.band .meter b{position:absolute;left:-2px;right:-2px;height:2px;border-radius:99px;
  background:var(--text);opacity:.7;transition:bottom .3s var(--ease)}
.band .lv{font-size:.66rem;color:var(--text-3);font-variant-numeric:tabular-nums;letter-spacing:.06em}
.band .nm{font-size:.66rem;text-transform:uppercase;letter-spacing:.1em;color:var(--text-2);
  text-align:center;line-height:1.2}
.band .nm em{display:block;font-style:normal;color:var(--text-3);font-size:.6rem;letter-spacing:.06em}

/* ═════════ вкладки и списки ═════════ */
.tabs{display:flex;gap:4px;margin:28px 0 16px;position:sticky;top:10px;z-index:4;width:fit-content;
  max-width:100%;overflow-x:auto;padding:5px;border-radius:20px;border:1px solid var(--line);
  background:rgba(14,14,19,.62);-webkit-backdrop-filter:blur(18px) saturate(1.2);
  backdrop-filter:blur(18px) saturate(1.2);scrollbar-width:none}
.tabs::-webkit-scrollbar{display:none}
.tab{display:inline-flex;align-items:center;gap:8px;padding:9px 16px;border-radius:15px;
  color:var(--text-2);font-size:.84rem;white-space:nowrap;
  transition:color .25s,background-color .25s,box-shadow .3s,transform .2s var(--ease)}
.tab .ic{width:16px;height:16px;color:var(--text-3);transition:color .25s}
.tab:hover{color:var(--text);background:rgba(255,255,255,.05)}
.tab:active{transform:scale(.96)}
.tab.on{color:var(--text);background:rgba(255,255,255,.10);box-shadow:inset 0 0 0 1px var(--line-2)}
.tab.on .ic{color:var(--accent)}
.tab em{font-style:normal;color:var(--text-3);font-size:.74rem;font-variant-numeric:tabular-nums}
.tabs.flat{position:static;margin:16px 0 14px;background:rgba(0,0,0,.25);border-radius:16px}
.tabs.flat .tab{font-size:.78rem;padding:7px 14px;border-radius:12px}
.pane{display:none}
.pane.on{display:block;animation:rise .45s var(--ease)}
@keyframes rise{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}
.cards{display:grid;gap:10px;grid-template-columns:repeat(auto-fill,minmax(min(100%,330px),1fr))}
.card{border-radius:var(--r-md);padding:12px 14px;border:1px solid var(--line);
  background:rgba(255,255,255,.03);display:flex;align-items:center;gap:12px;min-width:0;
  transition:border-color .3s,background-color .3s,opacity .5s,transform .5s var(--ease),filter .5s}
.card:hover{background:rgba(255,255,255,.055);border-color:var(--line-2)}
.reveal{opacity:0;transform:translateY(16px);filter:blur(3px)}
.reveal.in{opacity:1;transform:none;filter:none}
.num{width:34px;flex:none;text-align:center;font-variant-numeric:tabular-nums;color:var(--text-3);
  font-size:.82rem}
.num .ic{width:16px;height:16px;margin:0 auto}
.card .txt{min-width:0;flex:1 1 auto}
.card .txt strong{display:block;font-weight:600;font-size:.92rem;overflow-wrap:anywhere}
.card .txt span{display:block;color:var(--text-3);font-size:.76rem;margin-top:2px}
.card .acts{display:flex;gap:6px;flex:none;align-items:center}
.mini-btn{display:inline-flex;align-items:center;gap:6px;height:34px;padding:0 14px;
  border-radius:var(--r-pill);border:1px solid var(--line);background:var(--glass);
  color:var(--text-2);font-size:.76rem;font-weight:600;white-space:nowrap;
  transition:color .25s,border-color .25s,background-color .25s,transform .2s var(--ease)}
.mini-btn .ic{width:14px;height:14px}
.mini-btn:hover{color:var(--text);background:var(--glass-2);border-color:var(--line-2)}
.mini-btn:active{transform:scale(.95)}
.mini-btn.go{color:var(--text);background:color-mix(in srgb,var(--accent) 18%,transparent);
  border-color:color-mix(in srgb,var(--accent) 34%,transparent)}
.mini-btn.go:hover{background:color-mix(in srgb,var(--accent) 30%,transparent)}
.star{width:34px;height:34px;border-radius:12px;border:1px solid var(--line);color:var(--text-3);
  display:grid;place-items:center;transition:color .25s,border-color .25s,background-color .25s,transform .2s}
.star:hover{color:var(--gold);border-color:rgba(226,197,143,.4);background:rgba(226,197,143,.06)}
.star:active{transform:scale(.9)}
.star.on{color:var(--gold);border-color:rgba(226,197,143,.45);background:rgba(226,197,143,.10)}
.star.on .ic{fill:currentColor}
.now{width:9px;height:9px;border-radius:50%;flex:none;background:var(--accent);margin:0 12px 0 8px;
  animation:ping 2.2s infinite}
.bar-line{display:flex;align-items:center;gap:12px;margin-bottom:14px;flex-wrap:wrap}
.bar-line .grow{flex:1 1 200px;min-width:0;color:var(--text-2);font-size:.86rem}
.hint{color:var(--text-3);font-size:.78rem;padding:11px 14px;border-radius:var(--r-sm);
  border:1px dashed var(--line);background:rgba(255,255,255,.02);margin-top:14px}
.hint b{color:var(--text-2);font-weight:600}
.empty{padding:34px 18px;text-align:center;color:var(--text-3);border:1px dashed var(--line);
  border-radius:var(--r-md);background:rgba(255,255,255,.02)}
.empty b{display:block;color:var(--text-2);font-size:1rem;margin-bottom:6px}
.spin{width:15px;height:15px;border-radius:50%;border:2px solid var(--line-2);
  border-top-color:var(--accent);display:inline-block;vertical-align:-3px;
  animation:sp .8s linear infinite}
@keyframes sp{to{transform:rotate(360deg)}}

/* ═════════ заказ ═════════ */
.order{border-radius:var(--r-xl);padding:clamp(18px,2.8vw,28px);margin-top:26px}
.order form{display:grid;gap:12px;grid-template-columns:minmax(0,1fr) auto}
.order .grow{display:flex;align-items:center;gap:10px;padding:0 18px;
  border-radius:var(--r-pill);border:1px solid var(--line);background:rgba(255,255,255,.035);
  transition:border-color .3s,box-shadow .3s}
.order .grow:focus-within{border-color:color-mix(in srgb,var(--accent) 55%,transparent);
  box-shadow:0 0 0 4px color-mix(in srgb,var(--accent) 12%,transparent)}
.order input{flex:1 1 0;min-width:0;background:transparent;border:0;outline:0;padding:15px 0;font-size:.95rem}
.order input::placeholder{color:var(--text-3)}
.send{height:50px;padding:0 26px;border-radius:var(--r-pill);color:var(--ink);display:inline-flex;
  align-items:center;justify-content:center;gap:9px;
  background:linear-gradient(180deg,#ffffff,#e5e3de);
  font-weight:700;letter-spacing:.01em;white-space:nowrap;
  box-shadow:0 12px 28px -16px color-mix(in srgb,var(--accent) 90%,transparent),inset 0 1px 0 rgba(255,255,255,.8);
  transition:transform .2s var(--ease),box-shadow .35s,opacity .2s}
.send .ic{width:16px;height:16px;color:var(--ink);fill:currentColor}
.send:hover{transform:translateY(-1px);
  box-shadow:0 18px 36px -16px color-mix(in srgb,var(--accent) 100%,transparent),inset 0 1px 0 rgba(255,255,255,.8)}
.send:active{transform:scale(.97)}
.send[disabled]{opacity:.5;cursor:progress}
a.send{text-decoration:none}

/* ═════════ низ страницы: ссылка на музыку ═════════ */
.dock{overflow:hidden;margin-top:26px;padding:clamp(20px,3vw,32px);border-radius:var(--r-xl)}
.dock::before{content:"";position:absolute;right:-8%;top:-45%;width:52%;height:170%;border-radius:50%;
  background:var(--accent);filter:blur(100px);opacity:.12;pointer-events:none;z-index:-1;
  animation:halo var(--beat) var(--ease) infinite;transition:background-color 1400ms var(--ease)}
body.paused .dock::before,body.calm .dock::before{animation:none}
.dock h2{font-size:clamp(1.1rem,2.6vw,1.45rem);margin:8px 0;position:relative}
.dock p{margin:0 0 16px;color:var(--text-2);font-size:.9rem;max-width:60ch;position:relative}
.addr{display:flex;align-items:center;gap:12px;min-width:0;position:relative;
  padding:13px 16px;border-radius:var(--r-sm);border:1px solid var(--line);
  background:rgba(0,0,0,.28);transition:border-color .3s}
.addr:hover{border-color:var(--line-2)}
.addr em{font-style:normal;font-size:.66rem;text-transform:uppercase;letter-spacing:.14em;
  color:var(--text-3);flex:none}
.addr span{flex:1 1 auto;min-width:0;overflow-wrap:anywhere;font-size:.92rem;font-weight:600;
  color:var(--text);font-variant-numeric:tabular-nums}
.dock-acts{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:16px;position:relative}
.dock-acts .mini-btn{height:50px;padding:0 20px;font-size:.82rem}

/* ═════════ мини-плеер и тост ═════════ */
.mini{position:fixed;left:50%;transform:translate(-50%,140%);bottom:16px;z-index:6;
  width:min(900px,calc(100vw - 24px));display:flex;align-items:center;gap:12px;
  padding:10px 12px;border-radius:24px;
  transition:transform .5s var(--ease);pointer-events:none}
.mini.up{transform:translate(-50%,0);pointer-events:auto}
.mini .thumb{width:48px;height:48px;border-radius:14px;overflow:hidden;flex:none;
  background:var(--ink-3)}
.mini .thumb img{width:100%;height:100%;object-fit:cover}
.mini .txt{min-width:0;flex:1 1 auto}
.mini .txt strong{display:block;font-size:.88rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mini .txt span{font-size:.72rem;color:var(--text-3)}
.mini .ctrl{width:42px;height:42px;border-radius:14px}
.toast{position:fixed;left:50%;bottom:96px;transform:translate(-50%,26px);z-index:7;
  padding:12px 20px;border-radius:var(--r-pill);background:rgba(28,28,35,.94);color:var(--text);
  border:1px solid var(--line-2);-webkit-backdrop-filter:blur(14px);backdrop-filter:blur(14px);
  font-size:.86rem;font-weight:600;opacity:0;pointer-events:none;max-width:min(92vw,560px);
  box-shadow:0 22px 44px -18px #000;transition:opacity .3s,transform .35s var(--ease)}
.toast.bad{color:#ffd3d3;border-color:rgba(229,138,138,.4)}
.toast.up{opacity:1;transform:translate(-50%,0)}
.skel{height:58px;border-radius:var(--r-md);background:linear-gradient(90deg,var(--glass),
  var(--glass-2),var(--glass));background-size:220% 100%;animation:sk 1.3s linear infinite}
@keyframes sk{to{background-position:-220% 0}}
@media (prefers-reduced-motion:reduce){
  *,*::before,*::after{animation-duration:.001ms !important;animation-iteration-count:1 !important;
  transition-duration:.001ms !important}
  html{scroll-behavior:auto}
}

/* ═════════ адаптив: телефон, планшет, ноутбук ═════════ */
@media (max-width:980px){
  .knobs{grid-template-columns:repeat(2,minmax(0,1fr))}
}
@media (max-width:940px){
  .hero{grid-template-columns:minmax(0,1fr)}
  /* ширину задаём явно: у плашки все дети абсолютные, и без width она схлопывается в 0 */
  .hero .artwrap{width:min(100%,420px);max-width:420px;margin-inline:auto}
}
@media (max-width:640px){
  .wrap{padding-bottom:150px}
  .hero{border-radius:26px}
  .hero .artwrap{max-width:230px}
  .vinyl{right:-13%;width:86%;height:86%}
  .top .spacer{display:none}
  .field{flex:1 1 150px;min-width:0}
  .text-input{width:100%;flex:1 1 auto}
  .viz{height:40px}
  .big{width:62px;height:62px}
  .row{gap:12px}
  .kbd{display:none}
  .vol{order:5;flex:1 1 100%;justify-content:space-between;padding:0 18px}
  .vol .range{flex:1 1 auto;width:auto;min-width:0}
  .tabs{margin-top:22px}
  .card{padding:12px}
  .bands{height:122px}
  .knob{padding:14px 13px 12px}
  .knob h3{letter-spacing:.08em;font-size:.64rem;gap:5px;white-space:nowrap}
  .knob .v{white-space:nowrap}
  .dock{border-radius:26px}
  .addr{padding:11px 13px}
  .dock-acts .mini-btn,.dock-acts .send{flex:1 1 auto;justify-content:center}
  .order form{grid-template-columns:minmax(0,1fr)}
  .send{width:100%}
}
@media (max-width:420px){
  .mini{border-radius:20px;padding:8px 10px}
  .brand span{display:none}
  .band .nm{font-size:.6rem}
  .seg button{font-size:.7rem;padding:8px 2px}
}

/* ═════════ бит-слой: тихие вспышки в такт музыке ═════════
   включается только пока у трека есть настоящий анализ (body.amb-on).
   --pulse бас · --mid середина · --hi верха · --hit удар баса (0…1).
   Контуры и рамки у плашек остаются спокойными: играет свет фона, обложка,
   кнопка play, полоса времени, спектр и три ручки своих частот */
body.amb-on .veil{opacity:calc(1 - .3 * var(--hit))}
body.amb-on .hero::before,body.amb-on .dock::before{
  opacity:calc(.12 + .34 * var(--pulse) + .16 * var(--hit));
  transform:scale(calc(1 + .1 * var(--pulse) + .06 * var(--hit)))}
body.amb-on .hero{border-color:color-mix(in srgb,var(--accent) calc(var(--hit) * 28%),var(--line))}
body.amb-on .art{scale:calc(1 + .022 * var(--hit));
  box-shadow:0 30px 70px -30px #000,0 0 0 1px var(--line) inset,
             0 0 calc(14px + 70px * var(--pulse) + 20px * var(--hit)) calc(4px * var(--hit))
             color-mix(in srgb,var(--accent) calc(25% + 40% * var(--pulse)),transparent)}
body.amb-on .big{scale:calc(1 + .05 * var(--hit));
  box-shadow:0 14px 34px -14px color-mix(in srgb,var(--accent) 85%,transparent),
             0 0 calc(8px + 40px * var(--hit)) -6px var(--accent),inset 0 1px 0 rgba(255,255,255,.8)}
body.amb-on .title{text-shadow:0 0 calc(8px + 30px * var(--hit)) color-mix(in srgb,var(--accent) 40%,transparent)}
body.amb-on .vinyl .lbl{filter:brightness(calc(1 + .4 * var(--hit))) saturate(1.2)}
body.amb-on .track-bar i{filter:brightness(calc(1 + .3 * var(--hit)));
  box-shadow:0 0 calc(6px + 26px * var(--pulse)) -2px var(--accent)}
body.amb-on .band .meter i{filter:brightness(calc(1 + .25 * var(--hit)));
  box-shadow:0 0 calc(8px + 32px * var(--pulse)) -2px var(--accent)}
/* три ручки пульта играют каждая со своей полосой: бас, середина, верха */
.knob{--k:var(--hit)}
#k-bass{--k:var(--pulse)}
#k-mid{--k:var(--mid)}
#k-high{--k:var(--hi)}
body.amb-on .knob{border-color:color-mix(in srgb,var(--accent) calc(var(--k) * 42%),var(--line))}
body.amb-on .knob.live{border-color:color-mix(in srgb,var(--accent) calc(38% + var(--k) * 30%),transparent)}
body.amb-on .range::-webkit-slider-thumb{
  box-shadow:0 2px 8px rgba(0,0,0,.55),
             0 0 calc(2px + 14px * var(--k,var(--hit))) color-mix(in srgb,var(--accent) 85%,transparent)}
body.amb-on .range::-moz-range-thumb{
  box-shadow:0 2px 8px rgba(0,0,0,.55),
             0 0 calc(2px + 14px * var(--k,var(--hit))) color-mix(in srgb,var(--accent) 85%,transparent)}
body.amb-on .switch[aria-checked="true"] i{box-shadow:0 0 calc(4px + 16px * var(--hit)) -4px var(--accent)}
</style>
</head>
<body>
<svg xmlns="http://www.w3.org/2000/svg" class="sprite" aria-hidden="true" focusable="false"><defs>
<symbol id="i-play" viewBox="0 0 24 24"><path d="M8 5.2 19 12 8 18.8Z"/></symbol>
<symbol id="i-pause" viewBox="0 0 24 24"><path d="M9.2 5v14M14.8 5v14"/></symbol>
<symbol id="i-prev" viewBox="0 0 24 24"><path d="M17.6 5.8 8.8 12l8.8 6.2Z"/><path d="M6.2 5.4v13.2"/></symbol>
<symbol id="i-next" viewBox="0 0 24 24"><path d="M6.4 5.8 15.2 12l-8.8 6.2Z"/><path d="M17.8 5.4v13.2"/></symbol>
<symbol id="i-shuffle" viewBox="0 0 24 24"><path d="M3.6 7h3.2c1.7 0 2.6 1 3.6 2.6l2.4 4c.9 1.5 1.8 2.4 3.4 2.4h4.2"/><path d="M3.6 17h3.2c1.7 0 2.6-1 3.6-2.6l.7-1.2"/><path d="m14.3 9.6.7-1.1c.9-1.5 1.8-2.5 3.4-2.5h5"/><path d="m17.4 3.9 2.9 2-2.9 2M17.4 14.1l2.9 2-2.9 2"/></symbol>
<symbol id="i-repeat" viewBox="0 0 24 24"><path d="M7.2 5.6h8.4a4 4 0 0 1 4 4v.6"/><path d="m10.4 3 -3.2 2.6 3.2 2.6"/><path d="M16.8 18.4H8.4a4 4 0 0 1-4-4v-.6"/><path d="m13.6 21 3.2-2.6-3.2-2.6"/></symbol>
<symbol id="i-stop" viewBox="0 0 24 24"><rect x="6.4" y="6.4" width="11.2" height="11.2" rx="3"/></symbol>
<symbol id="i-auto" viewBox="0 0 24 24"><path d="m11 3.4 1.5 4 4 1.5-4 1.5L11 14.4 9.5 10.4l-4-1.5 4-1.5Z"/><path d="m18.4 14.6.8 2.1 2.1.8-2.1.8-.8 2.1-.8-2.1-2.1-.8 2.1-.8Z"/></symbol>
<symbol id="i-motion" viewBox="0 0 24 24"><path d="M3 12c2-4.6 4-4.6 6 0s4 4.6 6 0 4-4.6 6 0"/></symbol>
<symbol id="i-volume" viewBox="0 0 24 24"><path d="M4.6 9.4h3l4.6-4v13.2l-4.6-4h-3Z"/><path d="M16.2 9.2a4.2 4.2 0 0 1 0 5.6M18.8 6.6a7.8 7.8 0 0 1 0 10.8"/></symbol>
<symbol id="i-note" viewBox="0 0 24 24"><path d="M9.4 18V6.4l9.6-2v11.2"/><circle cx="7" cy="18" r="2.4"/><circle cx="16.6" cy="15.6" r="2.4"/></symbol>
<symbol id="i-search" viewBox="0 0 24 24"><circle cx="11" cy="11" r="6.2"/><path d="m20.2 20.2-4.7-4.7"/></symbol>
<symbol id="i-headphones" viewBox="0 0 24 24"><path d="M4.6 14.4v-2.2a7.4 7.4 0 0 1 14.8 0v2.2"/><rect x="3" y="13.6" width="4.6" height="7" rx="2.3"/><rect x="16.4" y="13.6" width="4.6" height="7" rx="2.3"/></symbol>
<symbol id="i-star" viewBox="0 0 24 24"><path d="m12 4 2.5 5.1 5.7.8-4.1 4 1 5.6-5.1-2.7-5.1 2.7 1-5.6-4.1-4 5.7-.8Z"/></symbol>
<symbol id="i-reset" viewBox="0 0 24 24"><path d="M5 9.4a7.4 7.4 0 1 1-.4 3.4"/><path d="M4.2 4.4v5h5"/></symbol>
<symbol id="i-copy" viewBox="0 0 24 24"><rect x="8.4" y="8.4" width="11.2" height="11.2" rx="3"/><path d="M15.6 5.4H7.8a3 3 0 0 0-3 3v7.8"/></symbol>
<symbol id="i-share" viewBox="0 0 24 24"><path d="M12 15.4V4.6"/><path d="m8.2 8.4 3.8-3.8 3.8 3.8"/><path d="M5.4 13.4v4.4a2 2 0 0 0 2 2h9.2a2 2 0 0 0 2-2v-4.4"/></symbol>
<symbol id="i-tempo" viewBox="0 0 24 24"><circle cx="12" cy="13.4" r="7.2"/><path d="M12 13.4V9"/><path d="M9.4 4.4h5.2"/><path d="m17.8 6 1.6-1.6"/></symbol>
<symbol id="i-mode" viewBox="0 0 24 24"><path d="M4.6 8.4h14.8M4.6 15.6h14.8"/><circle cx="9.6" cy="8.4" r="2.3"/><circle cx="14.6" cy="15.6" r="2.3"/></symbol>
<symbol id="i-echo" viewBox="0 0 24 24"><path d="M8.4 6.4v11.2M4.8 9.4v5.2M12 4.4v15.2"/><path d="M15.6 8.8a4.6 4.6 0 0 1 0 6.4M18.6 6.2a8.2 8.2 0 0 1 0 11.6"/></symbol>
<symbol id="i-eq" viewBox="0 0 24 24"><path d="M7 4.6v14.8M12 4.6v14.8M17 4.6v14.8"/><path d="M4.8 9.2h4.4M9.8 15h4.4M14.8 7.4h4.4"/></symbol>
<symbol id="i-low" viewBox="0 0 24 24"><path d="M3 13.2c3-6 6-6 9 0s6 6 9 0"/></symbol>
<symbol id="i-mid" viewBox="0 0 24 24"><path d="M3 13.2c1.8-5 3.6-5 5.4 0s3.6 5 5.4 0 3.6-5 5.4 0"/></symbol>
<symbol id="i-high" viewBox="0 0 24 24"><path d="M3 13.2c1.1-4.6 2.2-4.6 3.3 0s2.2 4.6 3.3 0 2.2-4.6 3.3 0 2.2 4.6 3.3 0 2.2-4.6 3.3 0"/></symbol>
<symbol id="i-list" viewBox="0 0 24 24"><path d="M9 6.6h10.4M9 12h10.4M9 17.4h10.4"/><circle cx="5" cy="6.6" r="1.1"/><circle cx="5" cy="12" r="1.1"/><circle cx="5" cy="17.4" r="1.1"/></symbol>
<symbol id="i-history" viewBox="0 0 24 24"><circle cx="12" cy="12" r="7.6"/><path d="M12 7.6V12l3.2 1.9"/></symbol>
</defs></svg>
<canvas id="amb" class="amb" aria-hidden="true"></canvas>
<div class="sky" aria-hidden="true"><i class="orb orb-a"></i><i class="orb orb-b"></i><i class="orb orb-c"></i></div>
<div class="veil" aria-hidden="true"></div>

<div class="wrap">
  <header class="top">
    <div class="brand"><b>Hallez</b><span>музыка семьи</span></div>
    <div class="pill idle" id="statusPill"><i></i><span id="statusText">подключаюсь…</span></div>
    <div class="spacer"></div>
    <div class="field"><label>я</label><input class="text-input" id="nickInput" maxlength="32"
      placeholder="как вас подписать" autocomplete="nickname"></div>
    <button class="icon-btn" id="motionBtn" title="Анимации: включить/выключить"
      aria-label="Выключить анимации"><svg class="ic" aria-hidden="true"><use href="#i-motion"></use></svg></button>
  </header>

  <section class="hero glass" id="hero">
    <div class="artwrap" id="artWrap">
      <span class="vinyl" aria-hidden="true"><i class="disc" id="disc"></i><i class="lbl"></i><i class="hole"></i></span>
      <div class="art" id="art"><div class="mark"><svg class="ic" aria-hidden="true"><use href="#i-note"></use></svg></div><img id="artImg" alt=""><div class="rays"></div></div>
    </div>
    <div>
      <div class="lab" id="liveLab">тишина</div>
      <h1 class="title" id="trackTitle">Ждём первый заказ</h1>
      <div class="who"><span id="trackWho">Задай трек внизу — он заиграет в голосовом канале</span></div>
      <canvas class="viz" id="viz" aria-hidden="true"></canvas>
      <div class="seek">
        <span class="t big-t" id="tNow">0:00</span>
        <span class="track-bar"><i id="bar"></i>
          <input class="range off" type="range" id="seekRange" min="0" max="100" value="0"
                 step="1" aria-label="Перемотка">
        </span>
        <span class="t t-end" id="tEnd">—</span>
      </div>
      <div class="row">
        <div class="grp">
          <button class="ctrl" id="prevBtn" title="Предыдущий трек" aria-label="Назад"><svg class="ic sol" aria-hidden="true"><use href="#i-prev"></use></svg></button>
          <button class="big" id="playBtn" title="Пауза / играть" aria-label="Пауза или играть"><svg class="ic" aria-hidden="true"><use href="#i-play"></use></svg></button>
          <button class="ctrl" id="skipBtn" title="Пропустить" aria-label="Пропустить"><svg class="ic sol" aria-hidden="true"><use href="#i-next"></use></svg></button>
        </div>
        <div class="grp">
          <button class="ctrl" id="shuffleBtn" title="Перемешать очередь" aria-label="Перемешать"><svg class="ic" aria-hidden="true"><use href="#i-shuffle"></use></svg></button>
          <button class="ctrl" id="repeatBtn" title="Повтор" aria-label="Повтор"><svg class="ic" aria-hidden="true"><use href="#i-repeat"></use></svg></button>
          <button class="ctrl" id="djBtn" title="Авто-диджей: брать треки из библиотеки сам"
            aria-label="Авто-диджей"><svg class="ic" aria-hidden="true"><use href="#i-auto"></use></svg></button>
          <button class="ctrl" id="stopBtn" title="Остановить и очистить очередь"
            aria-label="Остановить"><svg class="ic sol" aria-hidden="true"><use href="#i-stop"></use></svg></button>
        </div>
        <span class="vol"><svg class="ic" aria-hidden="true"><use href="#i-volume"></use></svg>
          <input class="range" type="range" id="volRange" min="5" max="100" step="1" value="40"
                 aria-label="Громкость, проценты"><b id="volVal">40%</b></span>
      </div>
      <div class="kbd"><kbd>Space</kbd> пауза · <kbd>←</kbd><kbd>→</kbd> 5 сек ·
        <kbd>↑</kbd><kbd>↓</kbd> громкость по 1%</div>
    </div>
  </section>

  <section class="deck glass" id="deckSec">
    <div class="head">
      <h2>Пульт</h2>
      <span class="tag">меняет звук в канале</span>
      <span class="tag who" id="fxBy">ручки не трогали</span>
      <button class="reset" id="fxReset" type="button"><svg class="ic" aria-hidden="true"><use href="#i-reset"></use></svg>Сбросить</button>
    </div>
    <div class="knobs">
      <div class="knob" id="k-bass"><h3><svg class="ic" aria-hidden="true"><use href="#i-low"></use></svg>Бас <span class="v" id="v-bass">0</span></h3>
        <input class="range" type="range" min="-12" max="12" step="1" value="0"
               data-knob="bass" aria-label="Бас, децибелы"></div>
      <div class="knob" id="k-mid"><h3><svg class="ic" aria-hidden="true"><use href="#i-mid"></use></svg>Середина <span class="v" id="v-mid">0</span></h3>
        <input class="range" type="range" min="-12" max="12" step="1" value="0"
               data-knob="mid" aria-label="Средние частоты, децибелы"></div>
      <div class="knob" id="k-high"><h3><svg class="ic" aria-hidden="true"><use href="#i-high"></use></svg>Верха <span class="v" id="v-high">0</span></h3>
        <input class="range" type="range" min="-12" max="12" step="1" value="0"
               data-knob="high" aria-label="Верхние частоты, децибелы"></div>
      <div class="knob" id="k-speed"><h3><svg class="ic" aria-hidden="true"><use href="#i-tempo"></use></svg>Темп <span class="v" id="v-speed">1.00×</span></h3>
        <input class="range" type="range" min="0.7" max="1.5" step="0.05" value="1"
               data-knob="speed" aria-label="Темп"></div>
      <div class="knob" id="k-filter">
        <h3><svg class="ic" aria-hidden="true"><use href="#i-mode"></use></svg>Режим звука</h3>
        <div class="seg" id="filterSeg">
          <button data-knob="filter" data-v="0">чисто</button>
          <button data-knob="filter" data-v="1">мягко</button>
          <button data-knob="filter" data-v="2">плотно</button>
          <button data-knob="filter" data-v="3">бас-буст</button>
        </div>
      </div>
      <div class="knob" id="k-echo">
        <h3><svg class="ic" aria-hidden="true"><use href="#i-echo"></use></svg>Эхо</h3>
        <button class="switch" id="echoSw" type="button" role="switch" aria-checked="false"
                data-knob="echo"><i></i><span id="echoState">выкл</span></button>
      </div>
    </div>

    <div class="eq" id="eqSec">
      <div class="eq-head">
        <h3><svg class="ic" aria-hidden="true"><use href="#i-eq"></use></svg>Полосы частот</h3>
        <span class="tag" id="eqNote">живой спектр трека</span>
      </div>
      <div class="bands" id="bands">
        <div class="band"><span class="meter"><i id="f-low"></i><b id="g-low"></b></span>
          <span class="lv" id="l-low">—</span>
          <span class="nm">низ<em>до 160 Гц</em></span></div>
        <div class="band"><span class="meter"><i id="f-mid"></i><b id="g-mid"></b></span>
          <span class="lv" id="l-mid">—</span>
          <span class="nm">середина<em>160–1400 Гц</em></span></div>
        <div class="band"><span class="meter"><i id="f-high"></i><b id="g-high"></b></span>
          <span class="lv" id="l-high">—</span>
          <span class="nm">верх<em>от 1400 Гц</em></span></div>
      </div>
    </div>
    <p class="hint">Звук в голосовом один на весь канал: что крутит пульт, слышат все, и в
      канале подписано, кто именно. Трек при этом не начинается сначала — ffmpeg
      подхватывает его с той же секунды.</p>
  </section>

  <nav class="tabs" id="tabs" aria-label="Разделы">
    <button class="tab on" data-pane="queue"><svg class="ic" aria-hidden="true"><use href="#i-note"></use></svg>Очередь<em id="cQueue">0</em></button>
    <button class="tab" data-pane="search"><svg class="ic" aria-hidden="true"><use href="#i-search"></use></svg>Поиск<em id="cLib">—</em></button>
    <button class="tab" data-pane="playlists"><svg class="ic" aria-hidden="true"><use href="#i-list"></use></svg>Плейлисты<em id="cPl">—</em></button>
    <button class="tab" data-pane="history"><svg class="ic" aria-hidden="true"><use href="#i-history"></use></svg>История<em id="cHist">0</em></button>
    <button class="tab" data-pane="favs"><svg class="ic" aria-hidden="true"><use href="#i-star"></use></svg>Избранное<em id="cFav">0</em></button>
  </nav>

  <section class="pane on" id="pane-queue">
    <div class="bar-line">
      <div class="grow" id="queueNote"><span class="spin" id="qSpin"></span></div>
    </div>
    <div class="cards" id="queueList"></div>
    <p class="hint">Первый в списке играет сейчас, дальше по порядку. «Пропустить» считает
      один раз, голос за скип в Discord — как раньше.</p>
  </section>

  <section class="pane" id="pane-search">
    <div class="order glass">
      <form id="searchForm" autocomplete="off">
        <span class="grow"><svg class="ic" aria-hidden="true"><use href="#i-search"></use></svg>
          <input id="searchInput" placeholder="название из библиотеки — результаты сразу"
                 maxlength="80" aria-label="Поиск по библиотеке"></span>
        <button class="send" type="submit">Искать</button>
      </form>
    </div>
    <div class="tabs flat" id="searchFilters">
      <button class="tab on" data-filter="all">Всё</button>
      <button class="tab" data-filter="fav">Избранное</button>
      <button class="tab" data-filter="fresh">Недавно играло</button>
    </div>
    <div class="cards" id="searchList"></div>
    <p class="hint" id="searchInfo">Библиотека — файлы с хостинга; ищем по имени.</p>
  </section>

  <section class="pane" id="pane-playlists">
    <div class="cards" id="plList"></div>
    <p class="hint">Это те же плейлисты, что собирает /музыка в Discord. «Включить» ставит
      весь список в очередь одним запросом.</p>
  </section>

  <section class="pane" id="pane-history">
    <div class="cards" id="histList"></div>
    <p class="hint">Сверху — что бот проиграл на самом деле, снизу — что заказывали вы
      с этой страницы. «Повторить» заказывает тот же трек снова.</p>
  </section>

  <section class="pane" id="pane-favs">
    <div class="cards" id="favList"></div>
    <p class="hint">Избранное живёт в этом браузере: звёздочка ставится на любом треке —
      в очереди, поиске и истории.</p>
  </section>

  <section class="order glass" id="orderSec">
    <div class="head"><h2>Заказать</h2><span class="tag">в голосовой канал</span>
      <span class="tag" id="whoTag">подпишем как: «я» вверху</span></div>
    <form id="orderForm" autocomplete="off">
      <span class="grow"><svg class="ic" aria-hidden="true"><use href="#i-note"></use></svg>
        <input id="trackInput" placeholder="название трека или ссылка" maxlength="300"
               aria-label="Что заказать"></span>
      <button class="send" type="submit" id="orderBtn"><svg class="ic" aria-hidden="true"><use href="#i-play"></use></svg><span>В очередь</span></button>
    </form>
    <p class="hint" id="orderHint">Можно имя из библиотеки, можно ссылку. Между заказами
      с одного адреса — <b id="coolVal">15</b> секунд.</p>
  </section>

  <footer class="dock glass" id="dock">
    <span class="lab">эта страница</span>
    <h2>Ссылка на музыку — сохрани или перешли семье</h2>
    <p>Адрес не меняется, вход не нужен. Открой со своего телефона и заказывай в общий
      голосовой канал, пока кто-то другой слушает.</p>
    <div class="addr"><em>адрес</em><span id="dockUrl">…</span></div>
    <div class="dock-acts">
      <a class="send" id="dockOpen" href="#" target="_blank" rel="noopener"><svg class="ic" aria-hidden="true"><use href="#i-headphones"></use></svg><span>Открыть музыку</span></a>
      <button class="mini-btn" id="dockCopy" type="button"><svg class="ic" aria-hidden="true"><use href="#i-copy"></use></svg>Скопировать ссылку</button>
      <button class="mini-btn" id="dockShare" type="button" hidden><svg class="ic" aria-hidden="true"><use href="#i-share"></use></svg>Поделиться</button>
    </div>
  </footer>
</div>

<div class="mini glass" id="mini" aria-label="Мини-плеер">
  <span class="thumb"><img id="miniImg" alt=""></span>
  <span class="txt"><strong id="miniTitle">—</strong><span id="miniSub">—</span></span>
  <button class="ctrl" id="miniPrev" aria-label="Назад"><svg class="ic sol" aria-hidden="true"><use href="#i-prev"></use></svg></button>
  <button class="ctrl" id="miniPlay" aria-label="Пауза или играть"><svg class="ic sol" aria-hidden="true"><use href="#i-play"></use></svg></button>
  <button class="ctrl" id="miniSkip" aria-label="Пропустить"><svg class="ic sol" aria-hidden="true"><use href="#i-next"></use></svg></button>
</div>
<div class="toast" id="toast" role="status" aria-live="polite"></div>

<script nonce="__NONCE__">
"use strict";
const P = location.pathname.indexOf("/music") === 0 ? "/music" : "";
const $ = (id) => document.getElementById(id);
const KEY = {nick:"hz-nick", fav:"hz-fav", recent:"hz-recent", calm:"hz-calm", tab:"hz-tab"};
const read = (k, def) => { try { const v = localStorage.getItem(k); return v === null ? def : JSON.parse(v); } catch (e) { return def; } };
const save = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} };

let S = null;                  // последнее состояние плеера
let seenTitle = "";            // чтобы ловить смену трека и проигрывать переход
let dragSeek = false;          // пока человек тянет ползунок, сервер его не перебивает
let playlists = null;          // грузятся один раз, при смене вкладки
let histSig = "";              // состав истории: без него список перерисовывался бы каждые 2 секунды
let filter = "all";
let palSig = "";               // цвета последнего сыгранного трека: свет меняем только при смене
const deb = {};                // общие таймеры для debounce

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function fmt(sec) {
  const n = Math.max(0, Math.round(Number(sec) || 0));
  return Math.floor(n / 60) + ":" + String(n % 60).padStart(2, "0");
}
function later(name, fn, ms) {
  clearTimeout(deb[name]);
  deb[name] = setTimeout(fn, ms);
}

/* знаки: тот же приём, что в разметке — <use> на готовый символ, поэтому цвет
   и размер берутся от кнопки, а не от эмодзи системы пользователя */
const SVG_NS = "http://www.w3.org/2000/svg";
function iconEl(id) {
  const s = document.createElementNS(SVG_NS, "svg");
  s.setAttribute("class", "ic");
  s.setAttribute("aria-hidden", "true");
  const u = document.createElementNS(SVG_NS, "use");
  u.setAttribute("href", "#" + id);
  s.append(u);
  return s;
}
function setIcon(el, id) {
  const u = el.querySelector("use");
  if (u) u.setAttribute("href", "#" + id);
  else el.replaceChildren(iconEl(id));
}

/* заливка ползунка: у громкости и темпа от левого края, у бас/середина/верха — от нуля */
function fillRange(el) {
  const min = Number(el.min) || 0, max = Number(el.max) || 100, v = Number(el.value) || 0;
  const p = max > min ? (v - min) / (max - min) * 100 : 0;
  const pos = (q) => "calc(" + q.toFixed(2) + "% + " + ((.5 - q / 100) * 16).toFixed(2) + "px)";
  const bi = min < 0;
  el.style.setProperty("--a", pos(bi ? Math.min(50, p) : 0));
  el.style.setProperty("--b", pos(bi ? Math.max(50, p) : p));
}
function fillAll() { document.querySelectorAll(".knob .range, #volRange").forEach(fillRange); }
document.addEventListener("input", (e) => {
  const t = e.target;
  if (t && t.matches && t.matches(".knob .range, #volRange")) fillRange(t);
});

async function api(path, body) {
  const opt = {headers: {"X-Requested-With": "hz-music"}};
  if (body) {
    opt.method = "POST";
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(Object.assign({nick: nick()}, body));
  }
  const r = await fetch(P + path, opt);
  let data = {};
  try { data = await r.json(); } catch (e) {}
  return {code: r.status, data};
}
function nick() {
  return ($("nickInput").value || "").trim().slice(0, 32);
}

let toastTimer = null;
function toast(text, bad) {
  const el = $("toast");
  el.textContent = text;
  el.classList.toggle("bad", !!bad);
  el.classList.add("up");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("up"), 2600);
}

/* ── свет от обложки: три цвета правят фоном, гранями и подсветкой ── */
const DEF_PAL = ["#5a5f8f", "#3f6f78", "#7a5a6b"];
function rgbToHsl(c) {
  const r = c[0] / 255, g = c[1] / 255, b = c[2] / 255;
  const mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn;
  let h = 0;
  if (d) {
    if (mx === r) h = ((g - b) / d) % 6; else if (mx === g) h = (b - r) / d + 2; else h = (r - g) / d + 4;
    h *= 60;
    if (h < 0) h += 360;
  }
  const l = (mx + mn) / 2;
  const s = d ? d / (1 - Math.abs(2 * l - 1)) : 0;
  return [h, s, l];
}
function hslToRgb(h, s, l) {
  h = ((h % 360) + 360) % 360;
  const a = s * Math.min(l, 1 - l);
  const f = (n) => {
    const k = (n + h / 30) % 12;
    return l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1));
  };
  return [Math.round(f(0) * 255), Math.round(f(8) * 255), Math.round(f(4) * 255)];
}
/* цвета обложки приводим к спокойной палитре: насыщенность и яркость зажаты, а оттенки
   двух других пятен уходят от главного не дальше чем на 38°. Любая обложка даёт
   гармоничный приглушённый свет, а не радугу */
function calmPalette(list) {
  const base = (list || []).slice(0, 3).map(toRgb).filter(Boolean);
  while (base.length < 3) base.push(toRgb(DEF_PAL[base.length]));
  const hs = base.map(rgbToHsl);
  let ref = hs[0];
  hs.forEach((x) => { if (x[1] > ref[1]) ref = x; });
  const mk = (i, s, l) => {
    const x = hs[i];
    let d = ((x[0] - ref[0] + 540) % 360) - 180;
    d = Math.max(-38, Math.min(38, d));
    const sv = x[1] < .08 ? x[1] : Math.max(.24, Math.min(x[1], s));
    return hslToRgb(ref[0] + d, sv, l);
  };
  return {
    blob: [mk(0, .4, .42), mk(1, .36, .38), mk(2, .34, .36)],    // пятна фона
    glow: [mk(0, .46, .64), mk(1, .42, .62), mk(2, .4, .6)],     // волны, искры, столбики
    accent: mk(0, .48, .74)                                      // кнопки, ползунки, подписи
  };
}
const PAL0 = calmPalette([]);
let PALB = PAL0.blob, PALG = PAL0.glow;
let rings = [], prevHit = 0;   // волны от обложки: рождаются на каждом ударе баса
function toRgb(c) {
  const m = /^#([0-9a-f]{6})$/i.exec(c || "");
  if (m) { const n = parseInt(m[1], 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; }
  const r = /rgba?\(([^)]+)\)/.exec(c || "");
  if (r) { const p = r[1].split(",").map((x) => Math.round(Number(x) || 0)); return [p[0], p[1], p[2]]; }
  return null;
}
function ambient(colors) {
  const pal = calmPalette(colors);
  const root = document.documentElement.style;
  const css = (c) => "rgb(" + c.join(",") + ")";
  root.setProperty("--g1", css(pal.blob[0]));
  root.setProperty("--g2", css(pal.blob[1]));
  root.setProperty("--g3", css(pal.blob[2]));
  root.setProperty("--accent", css(pal.accent));
  // холст рисует те же цвета, что и стили
  PALB = pal.blob;
  PALG = pal.glow;
}

/* ═════════ опрос плеера ═════════ */
async function poll(force) {
  // на скрытой вкладке не опрашиваем: семья слушает музыку, а не страницу
  if ((document.hidden && !force) || dragSeek) return;
  let out;
  try { out = await api("/api/state"); } catch (e) { return setStatus("нет связи", false); }
  if (!out.data || out.data.error) {
    setStatus(out.data && out.data.error ? out.data.error : "нет связи", false);
    $("trackWho").textContent = "Страница жива, но плеер отвечает: " +
      ((out.data && out.data.error) || "молчит");
    return;
  }
  S = out.data;
  paint();
}
function setStatus(text, live) {
  $("statusText").textContent = text;
  const pill = $("statusPill");
  pill.classList.toggle("live", !!live);
  pill.classList.toggle("idle", !live);
}

function paint() {
  const live = !!(S.connected && (S.playing || S.paused));
  setStatus(live ? ("в эфире · " + (S.channel || "голосовой")) : "бот не в голосовом",
            live && !!S.playing);
  $("cQueue").textContent = S.queue.length;
  $("cHist").textContent = (S.history || []).length;
  $("coolVal").textContent = S.cooldown;
  $("djBtn").classList.toggle("on", !!S.dj);
  $("repeatBtn").classList.toggle("on", S.repeat !== "off");
  $("repeatBtn").title = S.repeat === "one" ? "Повтор: этот трек"
    : (S.repeat === "all" ? "Повтор: вся очередь" : "Повтор выключен");
  $("playBtn") && setIcon($("playBtn"), S.paused ? "i-play" : "i-pause");
  $("miniPlay") && setIcon($("miniPlay"), S.paused ? "i-play" : "i-pause");
  if (document.activeElement !== $("volRange")) {
    $("volRange").value = S.volume;
    $("volVal").textContent = S.volume + "%";
  }
  document.body.classList.toggle("paused", !S.playing);
  /* часы фона: сервер даёт только секунду трека, дальше страница считает сама,
     чтобы свет шёл ровно за музыкой между опросами */
  posBase = (S.current && S.current.elapsed) || 0;
  posAt = performance.now();
  isPlaying = !!S.playing;
  posSpeed = Number((S.fx || {}).speed) || 1;
  paintTrack();
  paintDeck();
  syncAmbient();
  document.body.classList.toggle("amb-on", !!AMB.lo && !!S.current);
  document.body.classList.toggle("has-vinyl", !!S.current);
  paintQueue();
  if (currentTab() === "history") paintHistory();
  fillAll();
}

/* ═════════ пульт: значения с сервера, отправка — debounce ═════════ */

function paintTrack() {
  const t = S.current;
  $("liveLab").textContent = t ? (S.playing ? "звучит сейчас" : "на паузе") : "тишина в канале";
  if (!t) {
    if (palSig) { palSig = ""; ambient([]); }
    $("trackTitle").textContent = S.error || "Ждём первый заказ";
    $("trackWho").textContent = "Закажи трек внизу — он заиграет в голосовом канале семьи.";
    $("artImg").classList.remove("show");
    $("seekRange").classList.add("off");
    $("bar").style.width = "0";
    $("tNow").textContent = "0:00";
    $("tEnd").textContent = "—";
    $("miniTitle").textContent = "Тишина";
    $("miniSub").textContent = "закажи первым";
    return;
  }
  if (seenTitle !== t.title) {
    const art = $("art");
    art.classList.remove("flip");
    $("trackTitle").classList.add("out");
    setTimeout(() => $("trackTitle").classList.remove("out"), 40);
    requestAnimationFrame(() => art.classList.add("flip"));
    seenTitle = t.title;
  }
  $("trackTitle").textContent = t.title;
  $("trackWho").innerHTML = "";
  const who = document.createElement("span");
  who.innerHTML = "заказал(а) <b>" + esc(t.who || "неизвестно") + "</b>";
  $("trackWho").append(who, sep(), dur(t.duration), link(t));
  const src = S.cover || (t.thumb && /^https:\/\/i\.ytimg\.com\//.test(t.thumb) ? t.thumb : "");
  const img = $("artImg");
  if (src) {
    img.onload = () => img.classList.add("show");
    if (img.getAttribute("src") !== src) img.src = src; else img.classList.add("show");
  } else {
    img.classList.remove("show");
    img.removeAttribute("src");
  }
  $("miniImg").toggleAttribute("hidden", !src);
  if (src) { if ($("miniImg").getAttribute("src") !== src) $("miniImg").src = src; }
  else $("miniImg").removeAttribute("src");
  $("miniTitle").textContent = t.title;
  $("miniSub").textContent = (t.who || "кто-то") + " · " + fmt(t.elapsed) + " / " +
    (t.duration ? fmt(t.duration) : "—");
  const dur2 = t.duration || 0;
  const pal = S.palette || [];
  const sig = (t.title || "") + pal.join(",");
  if (sig !== palSig) { palSig = sig; ambient(pal); }
  $("seekRange").classList.toggle("off", !dur2);
  $("seekRange").max = dur2 || 100;
  if (!dragSeek) {
    const at = Math.min(t.elapsed || 0, dur2 || 1e9);
    $("seekRange").value = at;
    $("bar").style.width = dur2 ? Math.min(100, at / dur2 * 100) + "%" : "0";
    $("tNow").textContent = fmt(at);
    $("tEnd").textContent = dur2 ? fmt(dur2) : "—";
  }
}
function sep() { const s = document.createElement("span"); s.className = "dot"; return s; }
function dur(d) {
  const s = document.createElement("span");
  s.className = "time";
  s.textContent = d ? fmt(d) : "длительность не известна";
  return s;
}
function link(t) {
  const box = document.createElement("span");
  if (t.url && t.url.indexOf("http") === 0) {
    const a = document.createElement("a");
    a.href = t.url;
    a.textContent = "открыть источник";
    a.style.color = "var(--accent)";
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    box.append(sep(), a);
  }
  return box;
}

/* ── такт фона: страница звук не слышит (он уходит в Discord), поэтому ритм
      берёт из ручки «Темп» — фон и волны бьются вместе с пультом ── */
function setBeat(speed) {
  const s = Math.max(.5, Math.min(2, Number(speed) || 1));
  document.documentElement.style.setProperty("--beat", (1.15 / s).toFixed(3) + "s");
}

/* ═════════ живой фон: настоящие полосы этого трека ═════════
   звук уходит в голосовой канал Discord, так что Web Audio в браузере анализировало
   бы тишину. Поэтому бот один раз раскладывает файл на три полосы по 50 мс и отдаёт
   их готовыми кадрами, а странице остаётся самое дешёвое: взять кадр по реальной
   позиции трека и нарисовать свет. Анимация честно идёт за музыкой, а не вразноброд */
const AMB = {k: "", fps: 20, frames: 0, lo: null, mid: null, hi: null, note: ""};
const LIVE = {l: 0, m: 0, h: 0, hit: 0, spin: 0, vel: 0};
let posBase = 0, posAt = 0, posSpeed = 1, isPlaying = false;
let raf = null, lastT = 0, tick = 0, asking = false, retryAt = 0;
let ambCv = null, ambCx = null, vizCv = null, vizCx = null, AW = 0, AH = 0, parts = [];

function position() {
  if (!S || !S.current) return 0;
  const max = S.current.duration || 1e9;
  if (dragSeek) return Number($("seekRange").value) || 0;
  const dt = isPlaying ? (performance.now() - posAt) / 1000 * posSpeed : 0;
  return Math.min(max, Math.max(0, posBase + dt));
}
function dropAmb() {
  AMB.k = ""; AMB.lo = AMB.mid = AMB.hi = null; AMB.frames = 0;
  document.body.classList.remove("amb-on");
}
function trackBands(pos) {
  if (!AMB.lo) return null;
  const f = pos * AMB.fps, n = AMB.frames;
  if (!n) return null;
  const i = ((Math.floor(f) % n) + n) % n, j = (i + 1) % n, r = f - Math.floor(f);
  const mix = (a) => (a[i] + (a[j] - a[i]) * r) / 255;
  return [mix(AMB.lo), mix(AMB.mid), mix(AMB.hi)];
}
async function askAmb(k) {
  if (asking || AMB.k === k) return;
  asking = true;
  let out = {data: {}};
  try { out = await api("/api/ambient?k=" + k); } catch (e) {}
  asking = false;
  const d = out.data || {};
  if (!d.bands) {
    AMB.note = "бот считает полосы трека…";
    eqNote();
    retryAt = performance.now() + 3500;
    return;
  }
  const raw = Uint8Array.from(atob(d.bands), (c) => c.charCodeAt(0));
  const n = Number(d.frames) || 0;
  if (!n || raw.length < n * 3) { AMB.note = "полосы не читются"; eqNote(); return; }
  AMB.k = k; AMB.fps = Number(d.fps) || 20; AMB.frames = n;
  AMB.lo = raw.subarray(0, n); AMB.mid = raw.subarray(n, n * 2); AMB.hi = raw.subarray(n * 2, n * 3);
  AMB.note = "";
  eqNote();
}
function syncAmbient() {
  const a = (S && S.ambient) || {};
  if (!a.k) {
    if (AMB.k) dropAmb();
    AMB.note = S && S.current ? "полосы считаем только для файлов из библиотеки" : "";
    eqNote();
    return;
  }
  if (AMB.k === a.k) return;
  eqNote();
  if (a.ready) { askAmb(a.k); return; }
  if (performance.now() > retryAt) askAmb(a.k);
}
function eqNote() {
  const el = $("eqNote");
  if (el) el.textContent = AMB.note || "живой спектр трека";
}

/* ── фон: мягкие пятна света, вертикальные волны и пылинки, всё на одном холсте.
      холст маленький (до 720 px) и растягивается стилями: свет размытый по задумке,
      а на телефоне это решает вопрос с частотой кадров ── */
function fitCanvases() {
  const w = Math.min(innerWidth, 720);
  const h = Math.round(w * innerHeight / Math.max(1, innerWidth));
  if (!ambCv) return;
  if (AW !== w || AH !== h) {
    AW = ambCv.width = w; AH = ambCv.height = h;
    syncViz();
    parts = [];
  }
}
function rgba(c, a) { return "rgba(" + c[0] + "," + c[1] + "," + c[2] + "," + a.toFixed(3) + ")"; }
function blob(cx, x, y, r, col, alpha) {
  if (r <= 1 || alpha <= .004) return;
  const g = cx.createRadialGradient(x, y, 0, x, y, r);
  g.addColorStop(0, rgba(col, alpha));
  g.addColorStop(.55, rgba(col, alpha * .35));
  g.addColorStop(1, rgba(col, 0));
  cx.fillStyle = g;
  cx.beginPath();
  cx.arc(x, y, r, 0, 6.2832);
  cx.fill();
}
function artOrigin() {
  const a = document.querySelector(".art");
  if (!a) return [AW * .3, AH * .4];
  const r = a.getBoundingClientRect();
  return [(r.left + r.width / 2) / Math.max(1, innerWidth) * AW,
          (r.top + r.height / 2) / Math.max(1, innerHeight) * AH];
}
function paintAmb(dt, t) {
  const cx = ambCx;
  cx.clearRect(0, 0, AW, AH);
  cx.globalCompositeOperation = "lighter";
  const bass = LIVE.l, mid = LIVE.m, hi = LIVE.h, hit = LIVE.hit;
  const seed = t / 1000;
  const edge = hit - prevHit > .12;   // фронт удара: здесь рождаются волна и искры
  prevHit = hit;
  let org = null;
  const origin = () => org || (org = artOrigin());
  /* три больших мягких пятна: в тишине едва заметны, на ударе раздуваются и светлеют.
     цвета спокойные (см. calmPalette), поэтому «играет» яркость и размер, а не радуга */
  blob(cx, AW * (.2 + .06 * Math.sin(seed * .21)),
           AH * (.3 + .08 * Math.cos(seed * .17) - .12 * bass),
           AW * (.4 + .34 * bass + .12 * hit), PALB[0], .09 + .34 * bass + .1 * hi + .12 * hit);
  blob(cx, AW * (.78 + .05 * Math.cos(seed * .13)),
           AH * (.4 + .07 * Math.sin(seed * .19) - .1 * mid),
           AW * (.3 + .32 * mid), PALB[1], .07 + .28 * mid);
  blob(cx, AW * (.5 + .12 * Math.sin(seed * .11)),
           AH * (.88 - .05 * Math.cos(seed * .23)),
           AW * (.26 + .3 * hi + .08 * hit), PALB[2], .06 + .26 * hi);
  if (hit > .02) {
    blob(cx, AW * .04, AH * .06, AW * (.2 + .28 * hit), PALB[2], .18 * hit);
    blob(cx, AW * .97, AH * .95, AW * (.2 + .28 * hit), PALB[1], .18 * hit);
    /* вспышка от обложки: мягкая и короткая, чтобы не слепила */
    const o = origin();
    blob(cx, o[0], o[1], AW * (.28 + .34 * hit), PALB[0], Math.min(.22, .2 * hit * hit + .04 * hit));
  }
  if (edge) {
    const o = origin();
    if (rings.length < 3) {
      rings.push({x: o[0], y: o[1], r: AW * .06, a: Math.min(.9, .5 + hit * .4),
                  c: PALG[Math.floor(Math.random() * 3)]});
    }
    for (let k = 0; k < 6 && parts.length < 48; k++) {
      const ang = Math.random() * 6.2832, sp = 20 + Math.random() * 50 * (.5 + bass);
      parts.push({x: o[0], y: o[1], r: 1 + Math.random() * 2, a: .7 + Math.random() * .25,
                  vx: Math.cos(ang) * sp, v: Math.sin(ang) * sp, c: PALG[Math.floor(Math.random() * 3)]});
    }
  }
  /* тонкие волны от обложки */
  for (let i = rings.length - 1; i >= 0; i--) {
    const g = rings[i];
    g.r += dt * AW * (.5 + .45 * bass);
    g.a -= dt * 1.3;
    if (g.a <= .02) { rings.splice(i, 1); continue; }
    cx.strokeStyle = rgba(g.c, g.a * .35);
    cx.lineWidth = 1 + 3 * g.a;
    cx.beginPath();
    cx.arc(g.x, g.y, g.r, 0, 6.2832);
    cx.stroke();
  }
  /* световые волны: их высота = настоящая энергия полосы */
  cx.lineCap = "round";
  for (let b = 0; b < 3; b++) {
    const e = [bass, mid, hi][b];
    if (e < .02) continue;
    const col = PALG[b];
    cx.beginPath();
    for (let x = 0; x <= AW; x += 12) {
      const ph = x / AW * 6.283 + seed * (.6 + b * .35);
      const y = AH * (.62 - b * .12) - Math.sin(ph) * AH * (.05 + .2 * e)
                - Math.sin(ph * 2.3 + seed) * AH * .03 * e;
      if (x === 0) cx.moveTo(x, y); else cx.lineTo(x, y);
    }
    const g = cx.createLinearGradient(0, AH * .2, 0, AH);
    g.addColorStop(0, rgba(col, 0));
    g.addColorStop(.6, rgba(col, .08 + .3 * e));
    g.addColorStop(1, rgba(col, 0));
    cx.strokeStyle = g;
    cx.lineWidth = 1.5 + 5 * e;
    cx.stroke();
  }
  /* спектр по нижней кромке экрана: те же три полосы, разложенные по ширине */
  {
    const N = 40, bw = AW / N;
    for (let i = 0; i < N; i++) {
      const p = i / (N - 1);
      const w1 = Math.max(0, 1 - Math.abs(p - .12) / .3);
      const w2 = Math.max(0, 1 - Math.abs(p - .5) / .34);
      const w3 = Math.max(0, 1 - Math.abs(p - .88) / .32);
      const e = (bass * w1 + mid * w2 + hi * w3) / Math.max(.001, w1 + w2 + w3);
      const h = AH * (.015 + .28 * e * (.8 + .2 * Math.sin(seed * 3 + i * .7)));
      cx.fillStyle = rgba(p < .34 ? PALG[0] : (p < .7 ? PALG[1] : PALG[2]), .04 + .13 * e);
      cx.beginPath();
      if (cx.roundRect) cx.roundRect(i * bw + 2, AH - h, bw - 4, h + 12, 8);
      else cx.rect(i * bw + 2, AH - h, bw - 4, h + 12);
      cx.fill();
    }
  }
  /* искры: вспыхивают от удара баса и тянутся вверх */
  if (hit > .4 && parts.length < 48) {
    parts.push({x: Math.random() * AW, y: AH * (.3 + .5 * Math.random()),
                r: 1 + Math.random() * 2, v: 6 + Math.random() * 26 * (0.4 + bass), vx: 0,
                a: .5 + Math.random() * .4, c: PALG[Math.floor(Math.random() * 3)]});
  }
  for (let i = parts.length - 1; i >= 0; i--) {
    const p = parts[i];
    p.y -= p.v * dt;
    p.x += (p.vx || 0) * dt;
    if (p.vx) p.vx *= 1 - Math.min(1, dt * 1.8);
    p.a -= dt * .34;
    if (p.a <= .02 || p.y < -6 || p.y > AH + 6) { parts.splice(i, 1); continue; }
    cx.fillStyle = rgba(p.c, p.a * .7);
    cx.beginPath();
    cx.arc(p.x, p.y, p.r, 0, 6.2832);
    cx.fill();
  }
  cx.globalCompositeOperation = "source-over";
}
/* ── столбики под названием трека: те же три полосы, но разложенные по ширине.
      в тишине это ряд ровных точек, на музыке они вытягиваются в капсулы ── */
function syncViz() {
  if (!vizCv) return;
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  const w = Math.round((vizCv.clientWidth || 320) * dpr);
  const h = Math.round((vizCv.clientHeight || 58) * dpr);
  if (vizCv.width !== w) vizCv.width = w;
  if (vizCv.height !== h) vizCv.height = h;
}
function paintViz(t) {
  const cx = vizCx, W = vizCv.width, H = vizCv.height, N = 30;
  const k = W / Math.max(1, vizCv.clientWidth || W);
  const gap = 4 * k;
  const bw = (W - gap * (N - 1)) / N;
  cx.clearRect(0, 0, W, H);
  const seed = t / 1000;
  for (let i = 0; i < N; i++) {
    const p = i / (N - 1);
    /* каждый столбик — своя частота: слева бас, к середине середина, справа верха.
       величина берётся из реальной полосы, лёгкая волна только разводит их по фазе */
    const w1 = Math.max(0, 1 - Math.abs(p - .12) / .3);
    const w2 = Math.max(0, 1 - Math.abs(p - .5) / .34);
    const w3 = Math.max(0, 1 - Math.abs(p - .88) / .32);
    const e = (LIVE.l * w1 + LIVE.m * w2 + LIVE.h * w3) / Math.max(.001, w1 + w2 + w3);
    const wob = .86 + .14 * Math.sin(seed * 3.4 + i * .7);
    const h = Math.min(H, Math.max(bw, e * wob * H * .94 + bw));
    const col = p < .34 ? PALG[0] : (p < .7 ? PALG[1] : PALG[2]);
    const g = cx.createLinearGradient(0, H - h, 0, H);
    g.addColorStop(0, rgba(col, .95));
    g.addColorStop(1, rgba(col, .3));
    cx.fillStyle = g;
    const x = i * (bw + gap), y = H - h;
    cx.beginPath();
    if (cx.roundRect) cx.roundRect(x, y, bw, h, bw / 2);
    else cx.rect(x, y, bw, h);
    cx.fill();
  }
}
const VAR_NAMES = ["--pulse", "--mid", "--hi", "--hit"], varLast = [-1, -1, -1, -1];
function setVars() {
  /* --pulse = бас, --mid = середина, --hi = верха, --hit = удар баса.
     пишем только то, что заметно изменилось: меньше пересчётов стилей на телефоне */
  const root = document.documentElement.style;
  const v = [LIVE.l, LIVE.m, LIVE.h, LIVE.hit];
  for (let i = 0; i < 4; i++) {
    if (Math.abs(v[i] - varLast[i]) > .006 || (v[i] === 0 && varLast[i] !== 0)) {
      varLast[i] = v[i];
      root.setProperty(VAR_NAMES[i], v[i].toFixed(3));
    }
  }
}
function eqLive() {
  const fx = (S && S.fx) || {};
  const pairs = [["low", LIVE.l, fx.bass], ["mid", LIVE.m, fx.mid], ["high", LIVE.h, fx.high]];
  pairs.forEach(([k, e, db]) => {
    const fill = $("f-" + k), lab = $("l-" + k), mark = $("g-" + k);
    if (!fill) return;
    fill.style.height = Math.max(2, Math.min(100, e * 104)).toFixed(1) + "%";
    if (lab) lab.textContent = AMB.lo ? Math.round(e * 100) + "%" : "—";
    if (mark) mark.style.bottom = (((Number(db) || 0) + 12) / 24 * 100).toFixed(1) + "%";
  });
}
function frame(t) {
  raf = requestAnimationFrame(frame);
  const dt = Math.min(.06, Math.max(.004, (t - lastT) / 1000)); lastT = t;
  const band = trackBands(position());
  const tgt = band || [0, 0, 0];
  /* удар ловим ДО сглаживания: после него прыжок уже не виден */
  const jump = tgt[0] - LIVE.l;
  /* атака быстрая, отпускание медленное: удар видно сразу, а свет уходит плавно */
  ["l", "m", "h"].forEach((k, i) => {
    const v = tgt[i];
    LIVE[k] += (v - LIVE[k]) * Math.min(1, dt * (v > LIVE[k] ? 26 : 5.5));
  });
  LIVE.hit = Math.min(1, Math.max(0, LIVE.hit - dt * 2.6) + (jump > .06 ? jump * 2.4 : 0));
  /* пластинка: крутится, пока играет, и докручивается по инерции на паузе */
  const want = isPlaying ? 118 : 0;
  LIVE.vel += (want - LIVE.vel) * Math.min(1, dt * 2.4);
  if (LIVE.vel > .4) {
    LIVE.spin = (LIVE.spin + LIVE.vel * dt) % 360;
    $("disc").style.transform = "rotate(" + LIVE.spin.toFixed(1) + "deg)";
  }
  fitCanvases();
  paintAmb(dt, t);
  paintViz(t);
  setVars();
  tick++;
  if (tick % 8 === 0) eqLive();
  if (tick % 30 === 1) syncViz();
}
function startLoop() {
  if (raf !== null || document.body.classList.contains("calm") || !ambCv) return;
  lastT = performance.now();
  raf = requestAnimationFrame(frame);
}
function stopLoop() {
  if (raf !== null) cancelAnimationFrame(raf);
  raf = null;
  if (ambCx) {
    ambCx.clearRect(0, 0, AW, AH);
    vizCx.clearRect(0, 0, vizCv.width, vizCv.height);
  }
  ["l", "m", "h", "hit"].forEach((k) => { LIVE[k] = 0; });
  LIVE.vel = 0;
  setVars();
  eqLive();
}

/* ═════════ пульт: значения с сервера, отправка — debounce ─════════ */
const FX = ["bass", "mid", "high", "filter", "echo", "speed"];
function paintDeck() {
  const fx = S.fx || {};
  FX.forEach((k) => {
    const v = fx[k] == null ? (k === "speed" ? 1 : 0) : fx[k];
    if (k === "echo") {
      setEcho(Number(v) ? 1 : 0);
      return;
    }
    document.querySelectorAll('[data-knob="' + k + '"]').forEach((el) => {
      if (el.tagName === "INPUT") {
        if (document.activeElement !== el) el.value = v;
        const lab = $("v-" + k);
        if (lab) lab.textContent = k === "speed" ? Number(v).toFixed(2) + "×"
          : (k === "filter" ? "" : (v > 0 ? "+" + v : v) + " дБ");
      } else {
        el.classList.toggle("on", String(v) === el.dataset.v);
      }
    });
    const box = $("k-" + k);
    if (box) box.classList.toggle("live", Number(v) !== 0 && !(k === "speed" && Number(v) === 1));
  });
  $("fxBy").textContent = S.fx_by ? "крутит: " + S.fx_by : "ручки не трогали";
  $("fxBy").classList.toggle("who", !!S.fx_by);
  setBeat(fx.speed);
  eqLive();
}
/* эхо — один переключатель: ручка та же, что и раньше, только вид честный */
function setEcho(on) {
  const sw = $("echoSw");
  if (!sw) return;
  sw.setAttribute("aria-checked", on ? "true" : "false");
  $("echoState").textContent = on ? "включено" : "выкл";
  $("k-echo").classList.toggle("live", !!on);
}
function fxNow() {
  const patch = {};
  ["bass", "mid", "high", "speed"].forEach((k) => {
    patch[k] = Number(document.querySelector('[data-knob="' + k + '"]').value);
  });
  const on = document.querySelector('button.on[data-knob="filter"]');
  patch.filter = on ? Number(on.dataset.v) : 0;
  patch.echo = $("echoSw").getAttribute("aria-checked") === "true" ? 1 : 0;
  return patch;
}
function sendFx() {
  api("/api/action", {action: "fx", value: fxNow()}).then((out) => {
    if (out.data.error) toast(out.data.error, true);
    else if (out.data.did) toast(out.data.did);
    poll(true);
  });
}

document.querySelectorAll(".knob .range").forEach((r) => {
  r.addEventListener("input", () => {
    const k = r.dataset.knob;
    const lab = $("v-" + k);
    if (lab) lab.textContent = k === "speed" ? Number(r.value).toFixed(2) + "×" :
      (r.value > 0 ? "+" + r.value : r.value) + " дБ";
    const box = r.closest(".knob");
    if (box) box.classList.add("live");
    if (k === "speed") setBeat(r.value);
    later("fx", sendFx, 420);
  });
});
document.querySelectorAll(".seg button").forEach((b) => {
  b.addEventListener("click", () => {
    const k = b.dataset.knob;
    document.querySelectorAll('[data-knob="' + k + '"]').forEach((x) => {
      if (x.tagName !== "INPUT") x.classList.toggle("on", x === b);
    });
    sendFx();
  });
});
$("echoSw").addEventListener("click", () => {
  setEcho($("echoSw").getAttribute("aria-checked") !== "true");
  sendFx();
});
$("fxReset").addEventListener("click", () => {
  document.querySelectorAll(".knob .range").forEach((r) => { r.value = r.dataset.knob === "speed" ? 1 : 0; });
  document.querySelectorAll(".seg button").forEach((b) => b.classList.toggle("on", b.dataset.v === "0"));
  setEcho(0);
  setBeat(1);
  fillAll();
  sendFx();
  toast("Пульт вернулся в чистый звук");
});

/* ═════════ транспорт ═════════ */
async function act(action, value) {
  const out = await api("/api/action", value === undefined
    ? {action: action} : {action: action, value: value});
  if (out.data.error) toast(out.data.error, true);
  else if (out.data.did) toast(out.data.did);
  poll(true);
  return out;
}
$("playBtn").addEventListener("click", () => act("pause"));
$("miniPlay").addEventListener("click", () => act("pause"));
$("skipBtn").addEventListener("click", () => act("skip"));
$("miniSkip").addEventListener("click", () => act("skip"));
$("prevBtn").addEventListener("click", () => act("prev"));
$("miniPrev").addEventListener("click", () => act("prev"));
$("shuffleBtn").addEventListener("click", () => act("shuffle"));
$("repeatBtn").addEventListener("click", () => act("repeat"));
$("djBtn").addEventListener("click", () => act("dj"));
$("stopBtn").addEventListener("click", () => { act("stop"); });
$("volRange").addEventListener("input", () => {
  $("volVal").textContent = $("volRange").value + "%";
  later("vol", () => act("volume", Number($("volRange").value)), 260);
});
$("seekRange").addEventListener("pointerdown", () => { dragSeek = true; });
$("seekRange").addEventListener("input", () => {
  const v = Number($("seekRange").value), max = Number($("seekRange").max) || 1;
  $("tNow").textContent = fmt(v);
  $("bar").style.width = Math.min(100, v / max * 100) + "%";
});
["change", "pointerup", "keyup"].forEach((ev) => $("seekRange").addEventListener(ev, () => {
  if (!dragSeek) return;
  const at = Number($("seekRange").value);
  dragSeek = false;
  act("seek", at);
}));

/* ═════════ очередь ═════════ */
function trackCard(t, opts) {
  opts = opts || {};
  const card = document.createElement("div");
  card.className = "card reveal";
  if (opts.now) {
    const dot = document.createElement("span");
    dot.className = "now";
    card.append(dot);
  } else if (opts.star) {
    const n = document.createElement("span");
    n.className = "num";
    n.append(iconEl("i-star"));
    n.style.color = "var(--gold)";
    card.append(n);
  } else if (opts.num) {
    const n = document.createElement("span");
    n.className = "num";
    n.textContent = opts.num;
    card.append(n);
  }
  const txt = document.createElement("span");
  txt.className = "txt";
  const strong = document.createElement("strong");
  strong.textContent = t.title;
  const sub = document.createElement("span");
  sub.textContent = opts.sub || [t.who, t.duration ? fmt(t.duration) : ""].
    filter(Boolean).join(" · ") || "в очереди";
  txt.append(strong, sub);
  card.append(txt);
  const acts = document.createElement("span");
  acts.className = "acts";
  const star = starBtn(t.title);
  acts.append(star);
  if (opts.order) {
    const b = document.createElement("button");
    b.className = "mini-btn go";
    b.textContent = opts.orderBtn || "Заказать";
    b.addEventListener("click", () => order(t.title));
    acts.append(b);
  }
  if (opts.playlist) {
    const b = document.createElement("button");
    b.className = "mini-btn go";
    b.textContent = "Включить весь";
    b.addEventListener("click", () => act("playlist", t.name));
    acts.append(b);
  }
  if (opts.now) {
    const s = document.createElement("button");
    s.className = "mini-btn";
    s.textContent = "Дальше";
    s.addEventListener("click", () => act("skip"));
    acts.append(s);
  }
  card.append(acts);
  reveal(card);
  return card;
}
function paintQueue() {
  const box = $("queueList");
  box.innerHTML = "";
  const items = S.queue || [];
  $("queueNote").textContent = items.length
    ? "Дальше " + items.length + " из " + S.queue_max + " мест"
    : "Очередь пуста";
  if (S.current) box.append(trackCard(S.current, {now: true}));
  items.forEach((t, i) => box.append(trackCard(t, {num: i + 2})));
  if (!items.length && !S.current) {
    box.append(emptyBox("Ни одного трека", "Напиши название внизу — страница поставит его в голосовой канал."));
  }
}
function emptyBox(title, text) {
  const d = document.createElement("div");
  d.className = "empty";
  const b = document.createElement("b");
  b.textContent = title;
  d.append(b, document.createTextNode(text));
  return d;
}

/* ═════════ избранное и локальная память ═════════ */
let favs = new Set(read(KEY.fav, []));
function favKey(t) { return String(t || "").trim().toLowerCase(); }
function isFav(t) { return favs.has(favKey(t)); }
function starBtn(title) {
  const b = document.createElement("button");
  b.className = "star" + (isFav(title) ? " on" : "");
  b.append(iconEl("i-star"));
  b.title = isFav(title) ? "Убрать из избранного" : "В избранное";
  b.addEventListener("click", () => {
    if (isFav(title)) favs.delete(favKey(title)); else favs.add(favKey(title));
    save(KEY.fav, Array.from(favs));
    b.classList.toggle("on", isFav(title));
    b.title = isFav(title) ? "Убрать из избранного" : "В избранное";
    $("cFav").textContent = favs.size;
    if (currentTab() === "favs") paintFavs();
    if (currentTab() === "search") paintSearch(lastQuery);
  });
  return b;
}
function paintFavs() {
  const box = $("favList");
  box.innerHTML = "";
  $("cFav").textContent = favs.size;
  if (!favs.size) {
    box.append(emptyBox("Избранного пока нет",
      "Поставь звёздочку любому треку — он появится здесь и переживёт закрытие страницы."));
    return;
  }
  Array.from(favs).slice().reverse().forEach((title) => {
    box.append(trackCard({title: title, who: "избранное"}, {order: true, star: true}));
  });
}

/* ═════════ история: серверная + локальные заказы ═════════ */
let recent = read(KEY.recent, []);
function paintHistory() {
  const box = $("histList");
  if (!box || !S) return;
  const hist = S.history || [];
  $("cHist").textContent = hist.length;
  // карточки перерисовываем только когда состав правда изменился: иначе каждые пару
  // секунд список мигал бы анимацией появления
  const sig = hist.map((t) => t.title).join("|") + "#" + recent.slice(0, 6).join("|");
  if (sig === histSig) return;
  histSig = sig;
  box.innerHTML = "";
  hist.forEach((t) => box.append(trackCard(t, {order: true, orderBtn: "Повторить"})));
  recent.filter((r) => !hist.some((h) => favKey(h.title) === favKey(r))).forEach((r) => {
    box.append(trackCard({title: r, who: "ты заказывал(а) со страницы"},
                         {order: true, orderBtn: "Снова"}));
  });
  if (!hist.length && !recent.length) {
    box.append(emptyBox("История пуста", "Здесь появятся треки, которые канал уже сыграл."));
  }
}

/* ═════════ живой поиск ═════════ */
let lastQuery = "";
async function loadLibrary(q) {
  const out = await api("/api/library?q=" + encodeURIComponent(q || ""));
  if (out.data.error) { $("searchInfo").textContent = out.data.error; return []; }
  $("cLib").textContent = out.data.total;
  return out.data.tracks || [];
}
async function paintSearch(q) {
  lastQuery = q || "";
  const box = $("searchList");
  const items = await loadLibrary(lastQuery);
  box.innerHTML = "";
  const hist = ((S && S.history) || []).map((t) => favKey(t.title)).concat(recent.map(favKey));
  let show = items;
  if (filter === "fav") show = items.filter((t) => isFav(t.title));
  if (filter === "fresh") show = items.filter((t) => hist.indexOf(favKey(t.title)) >= 0);
  $("searchInfo").textContent = "Нашлось " + show.length + " из " + items.length +
    (lastQuery ? " по слову «" + lastQuery + "»" : " — введите слово");
  if (!show.length) {
    box.append(emptyBox(lastQuery ? "Ничего не найдено" : "Библиотека пуста",
      lastQuery ? "Попробуй другое слово — ищем по именам файлов с хостинга."
                : "Загрузи mp3 в папку media на хостинге — они появятся здесь сами."));
    return;
  }
  show.forEach((t, i) => box.append(trackCard(t, {order: true, num: i + 1, sub: "из библиотеки"})));
}
$("searchInput").addEventListener("input", () => later("srch", () => paintSearch($("searchInput").value.trim()), 220));
$("searchForm").addEventListener("submit", (e) => {
  e.preventDefault();
  paintSearch($("searchInput").value.trim());
});
document.querySelectorAll("#searchFilters .tab").forEach((b) => {
  b.addEventListener("click", () => {
    filter = b.dataset.filter;
    document.querySelectorAll("#searchFilters .tab").forEach((x) => x.classList.toggle("on", x === b));
    paintSearch(lastQuery);
  });
});

/* ═════════ плейлисты ═════════ */
async function paintPlaylists() {
  const box = $("plList");
  box.innerHTML = "";
  const out = await api("/api/playlists");
  playlists = out.data.playlists || [];
  $("cPl").textContent = playlists.length;
  if (out.data.error) { box.append(emptyBox("Плейлисты не открылись", out.data.error)); return; }
  if (!playlists.length) {
    box.append(emptyBox("Плейлистов нет",
      "Собираются в Discord: /музыка → Плейлисты → Сохранить текущий."));
    return;
  }
  playlists.forEach((pl) => {
    const card = document.createElement("div");
    card.className = "card reveal";
    const n = document.createElement("span");
    n.className = "num";
    n.textContent = pl.tracks.length;
    const txt = document.createElement("span");
    txt.className = "txt";
    const strong = document.createElement("strong");
    strong.textContent = pl.name;
    const sub = document.createElement("span");
    sub.textContent = pl.tracks.slice(0, 4).join(" · ") + (pl.tracks.length > 4 ? " …" : "");
    txt.append(strong, sub);
    const acts = document.createElement("span");
    acts.className = "acts";
    const b = document.createElement("button");
    b.className = "mini-btn go";
    b.textContent = "Включить";
    b.addEventListener("click", () => act("playlist", pl.name));
    acts.append(b);
    card.append(n, txt, acts);
    box.append(card);
    reveal(card);
  });
}

/* ═════════ заказ ═════════ */
async function order(query) {
  if (!S || S.open === false) { toast("Заказы со страницы сейчас закрыты", true); return; }
  const btn = $("orderBtn"), lab = btn.querySelector("span") || btn;
  btn.disabled = true;
  lab.textContent = "Ставлю…";
  const out = await api("/api/order", {query: query});
  btn.disabled = false;
  lab.textContent = "В очередь";
  if (out.data.error) { toast(out.data.error, true); return; }
  if (out.data.did) toast(out.data.did);
  // помним свой заказ локально: серверная история знает только то, что канал сыграл
  recent = [query].concat(recent.filter((r) => favKey(r) !== favKey(query))).slice(0, 30);
  save(KEY.recent, recent);
  histSig = "";
  poll(true);
}
$("orderForm").addEventListener("submit", (e) => {
  e.preventDefault();
  const v = $("trackInput").value.trim();
  if (!v) { toast("Напиши название трека или вставь ссылку", true); return; }
  $("trackInput").value = "";
  order(v);
});
$("trackInput").addEventListener("keydown", (e) => { if (e.key === "Enter") $("orderForm").requestSubmit(); });

/* ═════════ вкладки, меню на телефоне, появление при прокрутке ═════════ */
const TABS = ["queue", "search", "playlists", "history", "favs"];
function currentTab() {
  const b = document.querySelector("#tabs .tab.on");
  return b ? b.dataset.pane : "queue";
}
function openTab(name) {
  TABS.forEach((t) => {
    const pane = $("pane-" + t);
    if (pane) pane.classList.toggle("on", t === name);
  });
  document.querySelectorAll("#tabs .tab").forEach((b) => b.classList.toggle("on", b.dataset.pane === name));
  save(KEY.tab, name);
  if (name === "search" && !$("searchList").childElementCount) paintSearch($("searchInput").value.trim());
  if (name === "playlists" && !playlists) paintPlaylists();
  if (name === "favs") paintFavs();
  if (name === "history") paintHistory();
  document.querySelectorAll("#pane-" + name + " .reveal").forEach((el) => reveal(el));
}
document.querySelectorAll("#tabs .tab").forEach((b) => {
  b.addEventListener("click", () => openTab(b.dataset.pane));
});

const io = "IntersectionObserver" in window ? new IntersectionObserver((rows) => {
  rows.forEach((r) => { if (r.isIntersecting) { r.target.classList.add("in"); io.unobserve(r.target); } });
}, {rootMargin: "0px 0px -8% 0px"}) : null;
function reveal(el) {
  if (!io) { el.classList.add("in"); return; }
  io.observe(el);
  // то, что уже на экране, показываем сами: наблюдатель оживает только когда
  // браузер рисует кадр, а на фоновой вкладке человек увидел бы пустой список
  setTimeout(() => {
    const b = el.getBoundingClientRect();
    if (b.height && b.top < innerHeight && b.bottom > 0) el.classList.add("in");
  }, 320);
}

/* мини-плеер: прячем, когда большой экран в кадре */
if ("IntersectionObserver" in window) {
  new IntersectionObserver((rows) => {
    rows.forEach((r) => $("mini").classList.toggle("up", !r.isIntersecting && !!S && S.current));
  }, {threshold: 0, rootMargin: "-120px 0px 0px 0px"}).observe($("hero"));
}
$("mini").addEventListener("click", (e) => {
  if (e.target.tagName === "BUTTON") return;
  window.scrollTo({top: 0, behavior: "smooth"});
});

/* 3D-наклон обложки за курсором — только если анимации не выключены */
const hero = $("hero");
hero.addEventListener("pointermove", (e) => {
  if (document.body.classList.contains("calm") || e.pointerType === "touch") return;
  const b = hero.getBoundingClientRect();
  const dx = (e.clientX - b.left) / b.width - .5;
  const dy = (e.clientY - b.top) / b.height - .5;
  hero.style.transform = "perspective(1100px) rotateY(" + (dx * 5).toFixed(2) +
    "deg) rotateX(" + (-dy * 4).toFixed(2) + "deg) translateZ(0)";
});
hero.addEventListener("pointerleave", () => { hero.style.transform = ""; });

/* клавиатура: плеер слушается и без мыши */
window.addEventListener("keydown", (e) => {
  const tag = (e.target.tagName || "").toLowerCase();
  if (tag === "input" || tag === "textarea" || e.metaKey || e.ctrlKey) return;
  const k = e.key.toLowerCase();
  if (k === " " || e.code === "Space") { e.preventDefault(); act("pause"); }
  else if (k === "arrowleft") seekBy(-5);
  else if (k === "arrowright") seekBy(5);
  else if (k === "arrowup" || k === "arrowdown") {
    e.preventDefault();
    /* один процент за нажатие, как у нормального плеера; с Shift — грубо по 10 */
    const step = (e.shiftKey ? 10 : 1) * (k === "arrowup" ? 1 : -1);
    const v = Math.max(5, Math.min(100, Number($("volRange").value) + step));
    $("volRange").value = v;
    $("volVal").textContent = v + "%";
    fillRange($("volRange"));
    later("vol", () => act("volume", v), 260);
  }
  else if (k === "s") act("shuffle");
  else if (k === "r") act("repeat");
  else if (k === "p") act("prev");
  else if (k === "f") openTab(currentTab() === "search" ? "queue" : "search");
});
function seekBy(delta) {
  if (!S || !S.current || !S.current.duration) return;
  const at = Math.max(0, Math.min(S.current.duration - 1, (S.current.elapsed || 0) + delta));
  act("seek", Math.round(at));
}

/* анимации: один выключатель для всего, память между входами */
function setCalm(on) {
  document.body.classList.toggle("calm", on);
  $("motionBtn").classList.toggle("on", !on);
  $("motionBtn").title = on ? "Включить анимации" : "Выключить анимации";
  // выключенные анимации — это не «медленнее», а правда выключенный цикл рисования
  if (on) stopLoop(); else startLoop();
  save(KEY.calm, on);
}
$("motionBtn").addEventListener("click", () => setCalm(!document.body.classList.contains("calm")));
if (matchMedia("(prefers-reduced-motion: reduce)").matches) setCalm(true);
else setCalm(read(KEY.calm, false));

/* имя помнится: заказывает с тем же подписантом */
$("nickInput").value = read(KEY.nick, "");
$("nickInput").addEventListener("input", () => {
  save(KEY.nick, nick());
  $("whoTag").textContent = "подпишем как: «" + (nick() || "гость") + "»";
});
$("whoTag").textContent = "подпишем как: «" + (nick() || "гость") + "»";

/* холсты: фон и столбики под названием. Оба рисуются в одном rAF-цикле, чтобы
   не будить браузер двумя анимациями */
ambCv = $("amb"); vizCv = $("viz");
if (ambCv && vizCv && ambCv.getContext) {
  ambCx = ambCv.getContext("2d");
  vizCx = vizCv.getContext("2d");
  fitCanvases();
  startLoop();
}

/* ═════════ низ страницы: адрес, копирование и шеринг — всё по-настоящему ═════════ */
const MUSIC_URL = location.origin + location.pathname;
$("dockUrl").textContent = MUSIC_URL;
$("dockOpen").href = MUSIC_URL;
function copyUrl() {
  const done = () => toast("Ссылка скопирована — можно кидать в чат");
  const legacy = () => {
    const ta = document.createElement("textarea");
    ta.value = MUSIC_URL;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.top = "-1200px";
    document.body.append(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (e) {}
    ta.remove();
    if (ok) done(); else toast("Скопируй адрес руками: " + MUSIC_URL, true);
  };
  if (navigator.clipboard && navigator.clipboard.writeText)
    navigator.clipboard.writeText(MUSIC_URL).then(done, legacy);
  else legacy();
}
$("dockCopy").addEventListener("click", copyUrl);
if (navigator.share) {
  const b = $("dockShare");
  b.hidden = false;
  b.addEventListener("click", () => {
    navigator.share({title: "Hallez · музыка семьи", url: MUSIC_URL}).catch((e) => {
      // если шеринг не открылся (на компьютере его часто нет), не молчим, а копируем
      if (!e || e.name !== "AbortError") copyUrl();
    });
  });
}

/* старт: сначала скелетон, потом живое состояние; на вкладке не опрашиваем молча */
$("queueList").append((() => {
  const s = document.createElement("div");
  s.className = "skel";
  return s;
})());
openTab(read(KEY.tab, "queue"));
$("l-low").textContent = $("l-mid").textContent = $("l-high").textContent = "—";
eqNote();
fillAll();
poll();
setInterval(() => { if (!document.hidden) poll(); }, 2200);
// вернулись во вкладку — опрос и цикл рисования заново: в фоновой вкладке браузер
// сам заглушает requestAnimationFrame, и догонять это нужно сразу
document.addEventListener("visibilitychange", () => {
  if (document.hidden) return;
  lastT = performance.now();
  startLoop();
  poll(true);
});
</script>
</body></html>
"""


def setup(bot: commands.Bot):
    bot.add_cog(MusicWeb(bot))
