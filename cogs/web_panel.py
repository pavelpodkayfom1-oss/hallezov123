"""Веб-панель управления ботом.

Работает внутри процесса бота (aiohttp уже есть в requirements.txt), поэтому
отдельный сервер или база не нужны. Возможности:
  • обзор: пинг, аптайм, память, серверы, статистика БД
  • модули (cogs): включить / отключить / перезагрузить, выбор переживает перезапуск
  • редактор JSON-файлов настроек с проверкой и резервной копией (.bak)
  • логи: консоль бота и файл bot.log
  • статус бота в Discord, перезапуск и выключение

Переменные окружения (задаются на хостинге рядом с BOT_TOKEN):
  PANEL_PASSWORD  пароль входа, минимум 8 символов. Без него панель НЕ запускается.
  PANEL_PORT      порт панели (если не задан, берётся PORT, иначе 8080).
  PANEL_HOST      адрес прослушивания (по умолчанию 0.0.0.0).
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import sys
import time
import traceback
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

import disnake
from aiohttp import web
from disnake.ext import commands

from utils.panel_ui import INDEX_HTML

BASE_DIR = Path(__file__).resolve().parent.parent
COGS_DIR = BASE_DIR / "cogs"
STATE_PATH = BASE_DIR / "panel_state.json"
LOG_PATH = BASE_DIR / "bot.log"

SELF_COG = "cogs.web_panel"
COOKIE_NAME = "hz_panel"
SESSION_TTL = 12 * 3600
MIN_PASSWORD_LEN = 8
MAX_FILE_BYTES = 1_000_000
FILE_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}\.json$")
HIDDEN_FILES = {"panel_state.json"}
PUBLIC_API = {"/api/session", "/api/login", "/api/logout"}
STATUSES = {"online", "idle", "dnd", "invisible"}
ACTIVITY_KINDS = {
    "playing": disnake.ActivityType.playing,
    "watching": disnake.ActivityType.watching,
    "listening": disnake.ActivityType.listening,
    "competing": disnake.ActivityType.competing,
}

_PROCESS_START = time.time()


# ───────────────────────── Перехват консоли ─────────────────────────
# Всё, что бот печатает через print(), копируется в память, чтобы показывать в панели.
_CONSOLE: Deque[str] = getattr(sys, "_hz_console", None) or deque(maxlen=3000)
sys._hz_console = _CONSOLE  # type: ignore[attr-defined]


class _Tee:
    """Обёртка над stdout/stderr: пишет как раньше и дублирует строки в буфер."""

    _hz_tee = True

    def __init__(self, stream: Any, buf: Deque[str]):
        self._stream = stream
        self._buf = buf
        self._partial = ""

    def write(self, data: str) -> int:
        try:
            return self._stream.write(data)
        finally:
            self._capture(data)

    def _capture(self, data: Any) -> None:
        try:
            if not isinstance(data, str):
                return
            self._partial += data
            *lines, self._partial = self._partial.split("\n")
            stamp = time.strftime("%H:%M:%S")
            for line in lines:
                line = line.rstrip()
                if line:
                    self._buf.append(f"{stamp}  {line}"[:2000])
            if len(self._partial) > 4000:
                self._partial = ""
        except Exception:
            pass

    def flush(self) -> None:
        try:
            self._stream.flush()
        except Exception:
            pass

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


class _BufferHandler(logging.Handler):
    """Копирует записи logging (подключение к Discord, предупреждения) в консоль панели."""

    _hz_buffer_handler = True

    def __init__(self, buf: Deque[str]):
        super().__init__()
        self._buf = buf
        self.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            for line in self.format(record).splitlines():
                if line.strip():
                    self._buf.append(line[:2000])
        except Exception:
            pass


def _install_tee() -> None:
    for attr in ("stdout", "stderr"):
        stream = getattr(sys, attr, None)
        if stream is not None and not getattr(stream, "_hz_tee", False):
            setattr(sys, attr, _Tee(stream, _CONSOLE))
    root = logging.getLogger()
    if not any(getattr(h, "_hz_buffer_handler", False) for h in root.handlers):
        root.addHandler(_BufferHandler(_CONSOLE))


# ───────────────────────── Мелкие помощники ─────────────────────────
def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def _read_state() -> Dict[str, Any]:
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_state(state: Dict[str, Any]) -> None:
    _atomic_write(STATE_PATH, json.dumps(state, ensure_ascii=False, indent=2))


def _memory_mb() -> Optional[float]:
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024, 1)
    except Exception:
        pass
    try:
        import resource

        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
    except Exception:
        return None


def _tail_file(path: Path, max_lines: int) -> List[str]:
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            f.seek(max(0, size - 400_000))
            raw = f.read()
        return raw.decode("utf-8", errors="replace").splitlines()[-max_lines:]
    except FileNotFoundError:
        return []
    except Exception as e:
        return [f"Не удалось прочитать лог: {e}"]


def _json(data: Any, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


async def _body(request: web.Request) -> Dict[str, Any]:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


class _Sessions:
    def __init__(self) -> None:
        self._tokens: Dict[str, float] = {}

    def create(self) -> str:
        now = time.time()
        for tok in [t for t, exp in self._tokens.items() if exp < now]:
            del self._tokens[tok]
        tok = secrets.token_urlsafe(32)
        self._tokens[tok] = now + SESSION_TTL
        return tok

    def valid(self, tok: Optional[str]) -> bool:
        if not tok:
            return False
        exp = self._tokens.get(tok)
        if exp is None:
            return False
        if exp < time.time():
            self._tokens.pop(tok, None)
            return False
        return True

    def drop(self, tok: Optional[str]) -> None:
        if tok:
            self._tokens.pop(tok, None)


class _LoginLimiter:
    """5 неудачных попыток за 10 минут блокируют вход с этого адреса."""

    WINDOW = 600
    LIMIT = 5

    def __init__(self) -> None:
        self._fails: Dict[str, List[float]] = {}

    def _recent(self, ip: str) -> List[float]:
        now = time.time()
        recent = [t for t in self._fails.get(ip, []) if now - t < self.WINDOW]
        if recent:
            self._fails[ip] = recent
        else:
            self._fails.pop(ip, None)
        return recent

    def retry_after(self, ip: str) -> int:
        recent = self._recent(ip)
        if len(recent) < self.LIMIT:
            return 0
        return max(1, int(self.WINDOW - (time.time() - recent[0])))

    def fail(self, ip: str) -> None:
        self._fails.setdefault(ip, []).append(time.time())

    def ok(self, ip: str) -> None:
        self._fails.pop(ip, None)


# ───────────────────────── Сам модуль ─────────────────────────
class WebPanel(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.sessions = _Sessions()
        self.limiter = _LoginLimiter()
        self.password = os.getenv("PANEL_PASSWORD", "")
        self.runner: Optional[web.AppRunner] = None
        self._task: Optional[asyncio.Task] = None

        _install_tee()

        if len(self.password) < MIN_PASSWORD_LEN:
            print(
                f"ℹ️ Веб-панель выключена: задайте PANEL_PASSWORD (минимум {MIN_PASSWORD_LEN} символов) "
                "в переменных окружения хостинга.",
                flush=True,
            )
            return
        self._task = bot.loop.create_task(self._start())

    def cog_unload(self) -> None:
        if self._task:
            self._task.cancel()
        if self.runner:
            self.bot.loop.create_task(self.runner.cleanup())

    # ---- запуск сервера ----
    async def _start(self) -> None:
        try:
            app = web.Application(middlewares=[self._middleware], client_max_size=2 * 1024 * 1024)
            app.router.add_get("/", self.h_index)
            app.router.add_get("/favicon.ico", self.h_favicon)
            app.router.add_get("/api/session", self.h_session)
            app.router.add_post("/api/login", self.h_login)
            app.router.add_post("/api/logout", self.h_logout)
            app.router.add_get("/api/overview", self.h_overview)
            app.router.add_get("/api/modules", self.h_modules)
            app.router.add_post("/api/modules/{name}/{action}", self.h_module_action)
            app.router.add_get("/api/files", self.h_files)
            app.router.add_get("/api/files/{name}", self.h_file_get)
            app.router.add_put("/api/files/{name}", self.h_file_put)
            app.router.add_get("/api/logs", self.h_logs)
            app.router.add_get("/api/presence", self.h_presence_get)
            app.router.add_post("/api/presence", self.h_presence_set)
            app.router.add_post("/api/bot/restart", self.h_restart)
            app.router.add_post("/api/bot/shutdown", self.h_shutdown)

            self.runner = web.AppRunner(app, access_log=None)
            await self.runner.setup()
            host = os.getenv("PANEL_HOST", "0.0.0.0")
            port = int(os.getenv("PANEL_PORT") or os.getenv("PORT") or 8080)
            await web.TCPSite(self.runner, host, port).start()
            print(f"🌐 Веб-панель запущена: порт {port}", flush=True)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"❌ Не удалось запустить веб-панель: {e}", file=sys.stderr, flush=True)

    # ---- защита ----
    @web.middleware
    async def _middleware(self, request: web.Request, handler):
        # Изменяющие запросы принимаются только со своей страницы: этот заголовок
        # чужой сайт отправить не может (браузер заблокирует без CORS), плюс SameSite=Strict.
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if request.headers.get("X-Requested-With") != "hz-panel":
                return _json({"error": "Запрос отклонён"}, 403)
        if request.path.startswith("/api/") and request.path not in PUBLIC_API:
            if not self._authed(request):
                return _json({"error": "Нужен вход"}, 401)
        try:
            resp = await handler(request)
        except web.HTTPException:
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            traceback.print_exc()
            resp = _json({"error": "Внутренняя ошибка панели, подробности в консоли бота"}, 500)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    def _authed(self, request: web.Request) -> bool:
        return self.sessions.valid(request.cookies.get(COOKIE_NAME))

    @staticmethod
    def _is_https(request: web.Request) -> bool:
        return request.secure or request.headers.get("X-Forwarded-Proto", "").lower() == "https"

    # ---- страница и вход ----
    async def h_index(self, request: web.Request) -> web.Response:
        nonce = secrets.token_urlsafe(16)
        csp = (
            "default-src 'none'; "
            f"script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
            "img-src 'self' data: https://cdn.discordapp.com; connect-src 'self'; "
            "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
        )
        return web.Response(
            text=INDEX_HTML.replace("__NONCE__", nonce),
            content_type="text/html",
            headers={"Content-Security-Policy": csp, "Cache-Control": "no-store"},
        )

    async def h_favicon(self, request: web.Request) -> web.Response:
        return web.Response(status=204)

    async def h_session(self, request: web.Request) -> web.Response:
        if not self._authed(request):
            return _json({"authed": False})
        from utils.checks import load_config

        return _json({"authed": True, "bot_name": load_config().get("bot_name") or "Бот"})

    async def h_login(self, request: web.Request) -> web.Response:
        ip = request.remote or "?"
        wait = self.limiter.retry_after(ip)
        if wait:
            return _json({"error": f"Слишком много попыток. Подождите {max(1, wait // 60)} мин."}, 429)
        supplied = str((await _body(request)).get("password", ""))
        if hmac.compare_digest(supplied.encode("utf-8"), self.password.encode("utf-8")):
            self.limiter.ok(ip)
            resp = _json({"ok": True})
            resp.set_cookie(
                COOKIE_NAME,
                self.sessions.create(),
                max_age=SESSION_TTL,
                httponly=True,
                samesite="Strict",
                secure=self._is_https(request),
                path="/",
            )
            print(f"🔐 Вход в веб-панель с {ip}", flush=True)
            return resp
        self.limiter.fail(ip)
        print(f"⚠️ Неверный пароль веб-панели с {ip}", flush=True)
        await asyncio.sleep(0.6)
        return _json({"error": "Неверный пароль"}, 403)

    async def h_logout(self, request: web.Request) -> web.Response:
        self.sessions.drop(request.cookies.get(COOKIE_NAME))
        resp = _json({"ok": True})
        resp.del_cookie(COOKIE_NAME, path="/")
        return resp

    # ---- обзор ----
    async def h_overview(self, request: web.Request) -> web.Response:
        from utils.checks import load_config

        bot = self.bot
        latency = bot.latency
        latency_ms = None if latency != latency or latency == float("inf") else round(latency * 1000)

        stats: Dict[str, Any] = {}
        try:
            from database import get_stats

            stats = await get_stats()
        except Exception:
            stats = {}

        guilds = []
        for g in bot.guilds:
            icon = None
            try:
                icon = g.icon.url if g.icon else None
            except Exception:
                icon = None
            guilds.append({"id": str(g.id), "name": g.name, "members": g.member_count, "icon": icon})

        cogs = self._list_cogs()
        return _json(
            {
                "bot_name": load_config().get("bot_name") or "Бот",
                "user": str(bot.user) if bot.user else None,
                "user_id": str(bot.user.id) if bot.user else None,
                "ready": bot.is_ready(),
                "latency_ms": latency_ms,
                "uptime_s": int(time.time() - _PROCESS_START),
                "memory_mb": _memory_mb(),
                "guilds": guilds,
                "modules_loaded": sum(1 for c in cogs if c["loaded"]),
                "modules_total": sum(1 for c in cogs if not c["internal"]),
                "stats": stats,
            }
        )

    # ---- модули ----
    def _list_cogs(self) -> List[Dict[str, Any]]:
        disabled = set(_read_state().get("disabled_cogs", []))
        out = []
        for p in sorted(COGS_DIR.glob("*.py")):
            name = f"cogs.{p.stem}"
            out.append(
                {
                    "name": name,
                    "file": p.name,
                    "loaded": name in self.bot.extensions,
                    "disabled": name in disabled,
                    "internal": p.name.startswith("_"),
                    "protected": name == SELF_COG,
                }
            )
        return out

    async def h_modules(self, request: web.Request) -> web.Response:
        return _json({"modules": self._list_cogs()})

    async def h_module_action(self, request: web.Request) -> web.Response:
        name = request.match_info["name"]
        action = request.match_info["action"]
        known = {c["name"]: c for c in self._list_cogs()}
        info = known.get(name)
        if info is None or info["internal"]:
            return _json({"error": "Такого модуля нет"}, 404)
        if info["protected"]:
            return _json({"error": "Этот модуль отвечает за саму панель, его нельзя трогать отсюда"}, 400)
        if action not in ("enable", "disable", "reload"):
            return _json({"error": "Неизвестное действие"}, 400)

        short = name.replace("cogs.", "")
        state = _read_state()
        disabled = [c for c in state.get("disabled_cogs", []) if isinstance(c, str)]
        loaded = name in self.bot.extensions

        try:
            if action == "disable":
                if loaded:
                    self.bot.unload_extension(name)
                if name not in disabled:
                    disabled.append(name)
                message = f"Модуль «{short}» отключён"
            else:
                if loaded and action == "enable":
                    message = f"Модуль «{short}» уже работает"
                elif loaded:
                    self.bot.reload_extension(name)
                    message = f"Модуль «{short}» перезагружен"
                else:
                    self.bot.load_extension(name)
                    message = f"Модуль «{short}» запущен"
                disabled = [c for c in disabled if c != name]
        except Exception as e:
            traceback.print_exc()
            return _json({"error": f"Не получилось: {str(e)[:300]}"}, 400)

        state["disabled_cogs"] = sorted(set(disabled))
        _write_state(state)
        print(f"🧩 Панель: {message}", flush=True)
        return _json({"ok": True, "message": message, "loaded": name in self.bot.extensions})

    # ---- файлы настроек ----
    def _json_files(self) -> List[Path]:
        return sorted(
            p for p in BASE_DIR.glob("*.json") if FILE_NAME_RE.match(p.name) and p.name not in HIDDEN_FILES and p.is_file()
        )

    def _resolve_file(self, name: str) -> Optional[Path]:
        for p in self._json_files():
            if p.name == name:
                return p
        return None

    async def h_files(self, request: web.Request) -> web.Response:
        return _json({"files": [{"name": p.name, "size": p.stat().st_size} for p in self._json_files()]})

    async def h_file_get(self, request: web.Request) -> web.Response:
        path = self._resolve_file(request.match_info["name"])
        if path is None:
            return _json({"error": "Файл не найден"}, 404)
        if path.stat().st_size > MAX_FILE_BYTES:
            return _json({"error": "Файл слишком большой для редактора"}, 400)
        return _json({"name": path.name, "content": path.read_text(encoding="utf-8")})

    async def h_file_put(self, request: web.Request) -> web.Response:
        path = self._resolve_file(request.match_info["name"])
        if path is None:
            return _json({"error": "Файл не найден"}, 404)
        content = (await _body(request)).get("content")
        if not isinstance(content, str):
            return _json({"error": "Нет содержимого"}, 400)
        if len(content.encode("utf-8")) > MAX_FILE_BYTES:
            return _json({"error": "Файл слишком большой"}, 400)
        try:
            json.loads(content)
        except json.JSONDecodeError as e:
            return _json({"error": f"Ошибка в JSON: строка {e.lineno}, символ {e.colno}: {e.msg}"}, 400)
        try:
            shutil.copy2(path, path.with_name(path.name + ".bak"))
            _atomic_write(path, content)
        except Exception as e:
            return _json({"error": f"Не удалось записать файл: {e}"}, 500)
        print(f"📝 Панель: сохранён {path.name}", flush=True)
        return _json({"ok": True})

    # ---- логи ----
    async def h_logs(self, request: web.Request) -> web.Response:
        source = request.query.get("source", "console")
        try:
            limit = max(10, min(1000, int(request.query.get("lines", "300"))))
        except ValueError:
            limit = 300
        needle = request.query.get("q", "").strip().lower()[:100]
        if source == "file":
            lines = _tail_file(LOG_PATH, 5000)
        else:
            lines = list(_CONSOLE)
        if needle:
            lines = [ln for ln in lines if needle in ln.lower()]
        return _json({"lines": lines[-limit:]})

    # ---- статус бота ----
    async def _apply_presence(self, p: Dict[str, Any]) -> None:
        status = p.get("status")
        status = disnake.Status(status) if isinstance(status, str) and status in STATUSES else disnake.Status.online
        text = str(p.get("text") or "").strip()[:128]
        activity = None
        if text:
            kind = p.get("kind")
            kind = ACTIVITY_KINDS.get(kind, disnake.ActivityType.watching) if isinstance(kind, str) else disnake.ActivityType.watching
            activity = disnake.Activity(type=kind, name=text)
        await self.bot.change_presence(status=status, activity=activity)

    async def apply_saved_presence(self) -> None:
        """Вызывается из main.py после запуска, чтобы вернуть выбранный в панели статус."""
        saved = _read_state().get("presence")
        if isinstance(saved, dict):
            try:
                await self._apply_presence(saved)
            except Exception as e:
                print(f"⚠️ Не удалось применить статус из панели: {e}", file=sys.stderr, flush=True)

    async def h_presence_get(self, request: web.Request) -> web.Response:
        saved = _read_state().get("presence")
        if not isinstance(saved, dict):
            saved = {"status": "online", "kind": "watching", "text": ""}
        return _json(saved)

    async def h_presence_set(self, request: web.Request) -> web.Response:
        data = await _body(request)
        status = data.get("status", "online")
        kind = data.get("kind", "watching")
        if not isinstance(status, str) or not isinstance(kind, str) or status not in STATUSES or kind not in ACTIVITY_KINDS:
            return _json({"error": "Неверные значения"}, 400)
        presence = {"status": status, "kind": kind, "text": str(data.get("text") or "").strip()[:128]}
        try:
            await self._apply_presence(presence)
        except Exception as e:
            return _json({"error": f"Discord не принял статус: {str(e)[:200]}"}, 400)
        state = _read_state()
        state["presence"] = presence
        _write_state(state)
        return _json({"ok": True})

    # ---- питание ----
    async def h_restart(self, request: web.Request) -> web.Response:
        self.bot.loop.create_task(self._restart())
        return _json({"ok": True})

    async def h_shutdown(self, request: web.Request) -> web.Response:
        self.bot.loop.create_task(self._shutdown())
        return _json({"ok": True})

    async def _restart(self) -> None:
        await asyncio.sleep(1)
        print("♻️ Перезапуск по команде из веб-панели", flush=True)
        try:
            await self.bot.close()
        except Exception:
            pass
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
        try:
            os.execv(sys.executable, [sys.executable, "-u", *sys.argv])
        except Exception as e:
            print(f"❌ Не удалось перезапустить процесс сам ({e}). Нажмите Restart на хостинге.", file=sys.stderr, flush=True)
            os._exit(1)

    async def _shutdown(self) -> None:
        await asyncio.sleep(1)
        print("🛑 Выключение по команде из веб-панели", flush=True)
        try:
            await self.bot.close()
        except Exception:
            pass
        os._exit(0)


def setup(bot: commands.Bot):
    bot.add_cog(WebPanel(bot))
