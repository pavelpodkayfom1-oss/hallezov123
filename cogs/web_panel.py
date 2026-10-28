"""Веб-панель управления ботом.

Работает внутри процесса бота (aiohttp уже есть в requirements.txt), поэтому
отдельный сервер или база не нужны. Возможности:
  • обзор: пинг, аптайм, память, серверы, статистика БД
  • заявки: список, анкета, история, принять/отказать/обзвон прямо из браузера
  • сообщения: текст в канал, ЛС и рассылка
  • команды: экраны админ-хаба без Discord
  • диагностика: сутки работы, память против лимита хостинга, молчуны
  • снимки: откат файлов настроек с показом отличий
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

import ast
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
SLEEP_MAX = 720
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


_ABOUT_CACHE: Dict[str, tuple] = {}


def _about(path: Path) -> str:
    """Первая строка docstring'а файла — панель показывает, за что отвечает модуль."""
    try:
        key, stamp = str(path), path.stat().st_mtime
    except Exception:
        return ""
    hit = _ABOUT_CACHE.get(key)
    if hit and hit[0] == stamp:
        return hit[1]
    line = ""
    try:
        doc = ast.get_docstring(ast.parse(path.read_text(encoding="utf-8", errors="replace"))) or ""
        line = next((s.strip() for s in doc.splitlines() if s.strip()), "")[:160]
    except Exception:
        pass
    _ABOUT_CACHE[key] = (stamp, line)
    return line


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
        self._sleep_task: Optional[asyncio.Task] = None
        self._sleep_until: Optional[int] = None

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
        if self._sleep_task:
            self._sleep_task.cancel()
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
            app.router.add_get("/api/apps", self.h_apps)
            app.router.add_get("/api/apps/{app_id}", self.h_app_detail)
            app.router.add_post("/api/apps/{app_id}/act", self.h_app_act)
            app.router.add_get("/api/staff", self.h_staff)
            app.router.add_get("/api/channels", self.h_channels)
            app.router.add_get("/api/members", self.h_members)
            app.router.add_get("/api/voices", self.h_voices)
            app.router.add_post("/api/send", self.h_send)
            app.router.add_post("/api/dm", self.h_dm)
            app.router.add_post("/api/broadcast", self.h_broadcast)
            app.router.add_get("/api/texts", self.h_texts)
            app.router.add_post("/api/texts/save", self.h_text_save)
            app.router.add_post("/api/texts/reset", self.h_text_reset)
            app.router.add_post("/api/texts/preview", self.h_text_preview)
            app.router.add_get("/api/stickers", self.h_stickers)
            app.router.add_get("/api/stickers/image", self.h_sticker_image)
            app.router.add_post("/api/stickers/send", self.h_sticker_send)
            app.router.add_get("/api/emojis", self.h_emojis)
            app.router.add_get("/api/emojis/image", self.h_emoji_image)
            app.router.add_post("/api/emojis/upload", self.h_emoji_upload)
            app.router.add_get("/api/commands", self.h_commands)
            app.router.add_post("/api/commands/run", self.h_command_run)
            app.router.add_post("/api/commands/press", self.h_command_press)
            app.router.add_post("/api/commands/modal", self.h_command_modal)
            app.router.add_post("/api/commands/visibility", self.h_visibility)
            app.router.add_post("/api/bot/reload", self.h_reload_code)
            app.router.add_get("/api/bot/sleep", self.h_sleep_get)
            app.router.add_post("/api/bot/sleep", self.h_sleep_set)
            app.router.add_get("/api/control/center", self.h_center)
            app.router.add_get("/api/diag/day", self.h_diag_day)
            app.router.add_get("/api/diag/system", self.h_diag_system)
            app.router.add_get("/api/diag/silents", self.h_diag_silents)
            app.router.add_get("/api/audit/channels", self.h_audit_channels)
            app.router.add_get("/api/audit/emojis", self.h_audit_emojis)
            app.router.add_get("/api/snapshots", self.h_snapshots)
            app.router.add_get("/api/snapshots/{name}", self.h_snapshot_get)
            app.router.add_post("/api/snapshots/{name}/restore", self.h_snapshot_restore)
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
        except Exception as exc:
            traceback.print_exc()
            # называем причину прямо в ответе: «ошибка 500» без слов ничего не объясняет
            resp = _json({"error": f"Ошибка внутри бота: {exc.__class__.__name__}: {str(exc)[:200]}",
                          "detail": "Полный текст ошибки лежит в разделе «Логи»."}, 500)
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
                    "about": _about(p),
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
        tools, _ = self._tools()
        if tools:
            tools.invalidate_texts()
        state = _read_state()
        disabled = [c for c in state.get("disabled_cogs", []) if isinstance(c, str)]
        loaded = name in self.bot.extensions
        names_before = {c.name for c in self.bot.application_commands}

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
        now = name in self.bot.extensions
        print(f"🧩 Панель: {message}", flush=True)
        # Перезапись списка — дорогая: на /commands летит 429 с очередью на несколько
        # минут, поэтому дёргаем её только когда набор команд реально поменялся.
        if {c.name for c in self.bot.application_commands} != names_before:
            message += ". " + await self._resync_menu()
        elif now != loaded or action == "reload":
            message += ". Список команд не менялся — Discord не перезаписываем"
        return _json({"ok": True, "message": message, "loaded": now})

    async def _resync_menu(self) -> str:
        """Донести команды до слэш-меню Discord.

        load_extension регистрирует команду только в реестре внутри процесса, и Discord
        про неё не знает, пока список не перезаписать. Без этого шага получается
        «модуль работает, а команды нет».
        """
        hub = sys.modules.get("cogs.admin_hub")
        if hub is None:
            return "Меню Discord не пересоберётся: выключен cogs.admin_hub"
        try:
            hub.apply_visibility(self.bot)
            res = await hub.resync(self.bot)
        except Exception as e:
            return f"В Discord список не перезаписался ({type(e).__name__}: {str(e)[:120]}), поможет рестарт"
        if not res.get("checked"):
            return "Список перезаписан, живой меню Discord проверить не удалось"
        left = res.get("left") or []
        return (f"меню Discord перезаписано на {res['guilds']} серверах"
                if not left else
                f"список перезаписан, но убрать не удалось: {', '.join('/' + n for n in left)}")

    # ---- взрослые разделы ----
    # Всё дело лежит в utils/web_tools.py: тут только разбор запроса. Импорт на месте,
    # чтобы отсутствующий файл не ронял панель на старте, а честно показывал ошибку.
    @staticmethod
    def _tools():
        try:
            from utils import web_tools
        except Exception as e:
            return None, f"Раздел не загрузился: utils/web_tools.py не импортируется ({e.__class__.__name__}: {e}). Смотрите «Логи» при старте бота."
        return web_tools, None

    @staticmethod
    def _fail(res: Dict[str, Any]) -> bool:
        return bool(res.get("error"))

    async def h_apps(self, request: web.Request) -> web.Response:
        tools, err = self._tools()
        if err:
            return _json({"error": err}, 503)
        return _json(await tools.apps_list(request.query.get("status"),
                                          request.query.get("q"),
                                          request.query.get("limit") or 60))

    async def h_app_detail(self, request: web.Request) -> web.Response:
        tools, err = self._tools()
        if err:
            return _json({"error": err}, 503)
        if not request.match_info["app_id"].isdigit():
            return _json({"error": "Непонятный номер заявки"})
        return _json(await tools.app_detail(self.bot, int(request.match_info["app_id"])))

    async def h_app_act(self, request: web.Request) -> web.Response:
        tools, err = self._tools()
        if err:
            return _json({"error": err}, 503)
        body = await _body(request)
        action = str(body.get("action") or "")
        if action not in ("review", "interview", "accept", "reject"):
            return _json({"error": "Неизвестное действие с заявкой"}, 400)
        app_id = int(request.match_info["app_id"]) if request.match_info["app_id"].isdigit() else 0
        res = await tools.app_action(self.bot, app_id, action, int(body.get("actor") or 0),
                                    str(body.get("reason") or "")[:500],
                                    str(body.get("voice") or "")[:120])
        who = res.get("error") or " → ".join(res.get("steps", []))[:200]
        print(f"🗂 Панель: заявка #{app_id} {action}: {who}", flush=True)
        return _json(res, 400 if self._fail(res) else 200)

    async def h_staff(self, request: web.Request) -> web.Response:
        tools, err = self._tools()
        if err:
            return _json({"error": err}, 503)
        return _json(tools.staff_list(self.bot))

    async def h_channels(self, request: web.Request) -> web.Response:
        tools, err = self._tools()
        if err:
            return _json({"error": err}, 503)
        return _json(tools.channel_rows(self.bot, request.query.get("q", "")))

    async def h_members(self, request: web.Request) -> web.Response:
        tools, err = self._tools()
        if err:
            return _json({"error": err}, 503)
        return _json(tools.member_rows(self.bot, request.query.get("q", ""),
                                      request.query.get("limit") or 50))

    async def h_voices(self, request: web.Request) -> web.Response:
        tools, err = self._tools()
        if err:
            return _json({"error": err}, 503)
        return _json(tools.voice_choices(self.bot))

    async def h_send(self, request: web.Request) -> web.Response:
        tools, err = self._tools()
        if err:
            return _json({"error": err}, 503)
        body = await _body(request)
        res = await tools.post_channel(self.bot, body.get("channel"),
                                      str(body.get("text") or ""), str(body.get("title") or ""),
                                      str(body.get("color") or ""), bool(body.get("ping")))
        print(f"✉️ Панель: в {res.get('where', '?')}: {str(res.get('error') or res.get('link'))[:120]}",
              flush=True)
        return _json(res, 400 if self._fail(res) else 200)

    async def h_dm(self, request: web.Request) -> web.Response:
        tools, err = self._tools()
        if err:
            return _json({"error": err}, 503)
        body = await _body(request)
        res = await tools.post_dm(self.bot, body.get("user"), str(body.get("text") or ""),
                                 str(body.get("title") or ""), str(body.get("color") or ""))
        print(f"✉️ Панель: ЛС {res.get('where', '?')}: {str(res.get('error') or res.get('link'))[:120]}",
              flush=True)
        return _json(res, 400 if self._fail(res) else 200)

    async def h_broadcast(self, request: web.Request) -> web.Response:
        tools, err = self._tools()
        if err:
            return _json({"error": err}, 503)
        body = await _body(request)
        res = await tools.broadcast(self.bot, body.get("users"), str(body.get("text") or ""),
                                   str(body.get("title") or ""), str(body.get("color") or ""),
                                   dry=bool(body.get("dry", True)))
        print(f"📣 Панель: рассылка {'накидка' if res.get('dry') else 'отправлена'}: "
              f"{str(res.get('error') or res.get('sent') or res.get('would'))[:120]}", flush=True)
        return _json(res, 400 if self._fail(res) else 200)

    # ---- тексты бота ----
    async def h_texts(self, request: web.Request) -> web.Response:
        tools, err = self._tools()
        if err:
            return _json({"error": err}, 503)
        force = request.query.get("fresh") == "1"
        return _json(tools.texts_catalog(self.bot, force=force))

    async def h_text_save(self, request: web.Request) -> web.Response:
        tools, err = self._tools()
        if err:
            return _json({"error": err}, 503)
        body = await _body(request)
        res = tools.text_save(self.bot, str(body.get("id") or ""), body.get("value"))
        print(f"📝 Панель: текст {res.get('key') or body.get('id')}: "
              f"{str(res.get('error') or 'сохранён')[:120]}", flush=True)
        return _json(res, 400 if self._fail(res) else 200)

    async def h_text_reset(self, request: web.Request) -> web.Response:
        tools, err = self._tools()
        if err:
            return _json({"error": err}, 503)
        body = await _body(request)
        res = tools.text_reset(self.bot, str(body.get("id") or ""))
        print(f"📝 Панель: вернул как в коде {body.get('id')}: {str(res.get('error'))[:80]}", flush=True)
        return _json(res, 400 if self._fail(res) else 200)

    async def h_text_preview(self, request: web.Request) -> web.Response:
        tools, err = self._tools()
        if err:
            return _json({"error": err}, 503)
        body = await _body(request)
        return _json(tools.text_preview(self.bot, str(body.get("id") or ""), body.get("value")))

    # ---- стикеры ----
    async def h_stickers(self, request: web.Request) -> web.Response:
        tools, err = self._tools()
        if err:
            return _json({"error": err}, 503)
        return _json(tools.sticker_rows())

    async def h_sticker_image(self, request: web.Request) -> web.Response:
        tools, err = self._tools()
        if err:
            return _json({"error": err}, 503)
        data, mime = tools.sticker_image(request.query.get("name"))
        if data is None:
            return _json({"error": "Картинки нет в папке stickers/"}, 404)
        return web.Response(body=data, content_type=mime,
                            headers={"Cache-Control": "public, max-age=600"})

    async def h_sticker_send(self, request: web.Request) -> web.Response:
        tools, err = self._tools()
        if err:
            return _json({"error": err}, 503)
        body = await _body(request)
        res = await tools.post_sticker(self.bot, body.get("channel"), body.get("name"),
                                       str(body.get("text") or ""), str(body.get("title") or ""),
                                       str(body.get("color") or ""))
        print(f"🎴 Панель: стикер в {res.get('where', '?')}: "
              f"{str(res.get('error') or res.get('link'))[:120]}", flush=True)
        return _json(res, 400 if self._fail(res) else 200)

    # ---- эмодзи из папки emojis/ ----
    async def h_emojis(self, request: web.Request) -> web.Response:
        tools, err = self._tools()
        if err:
            return _json({"error": err}, 503)
        return _json(tools.emoji_catalog(self.bot))

    async def h_emoji_image(self, request: web.Request) -> web.Response:
        tools, err = self._tools()
        if err:
            return _json({"error": err}, 503)
        data, mime = tools.emoji_image(request.query.get("file"))
        if data is None:
            return _json({"error": "Такой картинки нет в папке emojis/"}, 404)
        return web.Response(body=data, content_type=mime,
                            headers={"Cache-Control": "public, max-age=600"})

    async def h_emoji_upload(self, request: web.Request) -> web.Response:
        tools, err = self._tools()
        if err:
            return _json({"error": err}, 503)
        body = await _body(request)
        res = await tools.emoji_upload(self.bot, body.get("names"))
        print(f"🙂 Панель: эмодзи загружено {len(res.get('uploaded', []))}, "
              f"не вышло {len(res.get('failed', []))}: {str(res.get('error') or '')[:100]}", flush=True)
        return _json(res, 400 if self._fail(res) else 200)

    async def h_commands(self, request: web.Request) -> web.Response:
        tools, err = self._tools()
        if err:
            return _json({"error": err}, 503)
        return _json(tools.command_rows(self.bot))

    async def h_command_run(self, request: web.Request) -> web.Response:
        tools, err = self._tools()
        if err:
            return _json({"error": err}, 503)
        body = await _body(request)
        args = body.get("args") if isinstance(body.get("args"), dict) else {}
        res = await tools.run_screen(self.bot, str(body.get("ref") or ""), body.get("actor"),
                                    body.get("channel"), {str(k): str(v) for k, v in args.items()})
        print(f"🧭 Панель: экран {res.get('cmd') or body.get('ref')}: "
              f"{str(res.get('error') or res.get('kind'))[:120]}", flush=True)
        return _json(res, 400 if self._fail(res) else 200)

    async def h_command_press(self, request: web.Request) -> web.Response:
        tools, err = self._tools()
        if err:
            return _json({"error": err}, 503)
        body = await _body(request)
        values = body.get("values") if isinstance(body.get("values"), list) else []
        res = await tools.press(self.bot, body.get("token"), body.get("index"), body.get("actor"),
                               [str(v) for v in values])
        return _json(res, 400 if self._fail(res) else 200)

    async def h_command_modal(self, request: web.Request) -> web.Response:
        tools, err = self._tools()
        if err:
            return _json({"error": err}, 503)
        body = await _body(request)
        fields = body.get("fields") if isinstance(body.get("fields"), dict) else {}
        res = await tools.submit_modal(self.bot, body.get("token"),
                                      {str(k): str(v) for k, v in fields.items()},
                                      body.get("actor"))
        return _json(res, 400 if self._fail(res) else 200)

    async def h_diag_day(self, request: web.Request) -> web.Response:
        tools, err = self._tools()
        if err:
            return _json({"error": err}, 503)
        hours = request.query.get("hours") or 24
        return _json(await tools.day_report(int(hours) if hours.isdigit() else 24))

    async def h_diag_system(self, request: web.Request) -> web.Response:
        tools, err = self._tools()
        if err:
            return _json({"error": err}, 503)
        return _json(tools.system_report(self.bot))

    async def h_diag_silents(self, request: web.Request) -> web.Response:
        tools, err = self._tools()
        if err:
            return _json({"error": err}, 503)
        days = request.query.get("days") or 7
        return _json(await tools.silents(self.bot, int(days) if days.isdigit() else 7))

    async def h_audit_channels(self, request: web.Request) -> web.Response:
        tools, err = self._tools()
        if err:
            return _json({"error": err}, 503)
        limit = request.query.get("limit") or 40
        return _json(await tools.channel_audit(self.bot, int(limit) if limit.isdigit() else 40))

    async def h_audit_emojis(self, request: web.Request) -> web.Response:
        tools, err = self._tools()
        if err:
            return _json({"error": err}, 503)
        return _json(tools.emoji_audit(self.bot))

    async def h_snapshots(self, request: web.Request) -> web.Response:
        tools, err = self._tools()
        if err:
            return _json({"error": err}, 503)
        return _json(tools.snapshot_rows())

    async def h_snapshot_get(self, request: web.Request) -> web.Response:
        tools, err = self._tools()
        if err:
            return _json({"error": err}, 503)
        return _json(tools.snapshot_diff(request.match_info["name"]))

    async def h_snapshot_restore(self, request: web.Request) -> web.Response:
        tools, err = self._tools()
        if err:
            return _json({"error": err}, 503)
        res = await tools.snapshot_restore(request.match_info["name"], self.bot)
        print(f"↩️ Панель: откат к {request.match_info['name']}: "
              f"{str(res.get('error') or res.get('steps'))[:200]}", flush=True)
        return _json(res, 400 if self._fail(res) else 200)

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

    # ---- применить код, меню Discord, сон ----
    async def h_reload_code(self, request: web.Request) -> web.Response:
        """Перезагрузить рабочие коги: новый код с GitHub вступает без рестарта процесса.

        disnake снимает ког до импорта нового файла, поэтому упавший ког остаётся выключенным —
        панель честно называет его, а не делает вид, что применила всё.
        """
        done: List[str] = []
        failed: List[Dict[str, str]] = []
        for name in list(self.bot.extensions):
            if name == SELF_COG:
                continue
            short = name.replace("cogs.", "")
            try:
                self.bot.reload_extension(name)
                done.append(short)
            except Exception as e:
                failed.append({"name": short, "error": str(e)[:200]})
        tools, _ = self._tools()
        if tools:
            tools.invalidate_texts()
        print(f"🐣 Панель: код применила — {len(done)} модулей, осеклось {len(failed)}", flush=True)
        return _json({"ok": True, "reloaded": done, "failed": failed})

    async def h_visibility(self, request: web.Request) -> web.Response:
        """Спрятать дубли или вернуть команды — та же кнопка, что и в /панель в Discord."""
        hub = sys.modules.get("cogs.admin_hub")
        if hub is None:
            return _json({"error": "Модуль cogs.admin_hub выключен — меню команд отсюда не пересобрать"}, 400)
        body = await _body(request)
        hide = bool(body.get("hide"))
        hub.flag_set(hide)
        changed = hub.apply_visibility(self.bot)
        try:
            res = await hub.resync(self.bot)
            tail = (f"Список перезаписан на {res['guilds']} серверах; меню Discord обновится само "
                    "(обычно до минуты)." if res.get("checked") and not res.get("left")
                    else "Часть списка Discord не отдал: "
                         f"{', '.join('/' + n for n in res.get('left') or []) or 'проверить не удалось'}. "
                         "Перезапишется при следующем запуске.")
        except Exception as e:
            tail = f"В Discord список не перезаписался ({type(e).__name__}: {str(e)[:120]}). Поможет рестарт."
        names = ", ".join(f"/{n}" for n in changed) or ("нечего прятать" if hide else "нечего возвращать")
        message = ("🙈 Спрятал: " if hide else "👁 Вернул: ") + names + ". " + tail
        print(f"🎛 Панель: {message}", flush=True)
        return _json({"ok": True, "hide": hide, "changed": changed, "message": message})

    async def h_sleep_get(self, request: web.Request) -> web.Response:
        return _json({"until": self._sleep_until})

    async def h_sleep_set(self, request: web.Request) -> web.Response:
        """Отложить выключение: ночью бот только жрёт трафик хостинга."""
        body = await _body(request)
        try:
            minutes = int(body.get("minutes"))
        except (TypeError, ValueError):
            return _json({"error": "Сколько минут — непонятно"}, 400)
        if self._sleep_task is not None:
            self._sleep_task.cancel()
            self._sleep_task = None
        self._sleep_until = None
        if minutes <= 0:
            print("🎛 Панель: сон отменён", flush=True)
            return _json({"ok": True, "until": None, "message": "Сон отменён, бот работает дальше."})
        if minutes > SLEEP_MAX:
            return _json({"error": f"Сон не длиннее {SLEEP_MAX} минут"}, 400)
        self._sleep_until = round(time.time() + minutes * 60)
        self._sleep_task = self.bot.loop.create_task(self._sleep_then(minutes * 60))
        message = f"💤 Бот выключится через {minutes} мин. Кнопка «Отменить сон» оставляет его работать."
        print("🎛 Панель: " + message, flush=True)
        return _json({"ok": True, "until": self._sleep_until, "message": message})

    async def _sleep_then(self, seconds: float) -> None:
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            return
        self._sleep_task = None
        self._sleep_until = None
        print("💤 Панель: время вышло, выключаю бота по таймеру сна", flush=True)
        await self._shutdown()

    async def h_center(self, request: web.Request) -> web.Response:
        tools, err = self._tools()
        if err:
            return _json({"error": err}, 503)
        return _json(tools.control_center(self.bot))

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
