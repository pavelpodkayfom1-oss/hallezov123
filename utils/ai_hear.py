"""Слышать войс-сообщение: расшифровка голоса в текст через Gemini.

Бот не умеет принимать звук из голосового канала (в disnake 2.12 у VoiceClient только
отдача), но человек может записать голосовое сообщение в чат — оно приходит обычным
файлом, и его можно расшифровать. Отдельного ключа не нужно: берётся GEMINI_API_KEY,
тот же, что для картинок событий.

Цепочка: файл .ogg (opus) → ffmpeg → wav 16 кГц моно → inline_data в Gemini → текст.
Здесь же think() — ответ моделью одним текстом: тот же ключ спасает, когда мозгу
не хватило ANTHROPIC_API_KEY.

Gemini часто отвечает 503 «model is currently experiencing high demand» или 429. Это
временная перегрузка конкретной модели, поэтому на такие коды мы один раз повторяем
запрос и затем переходим к следующей модели, а не падаем сразу.
"""
import asyncio
import base64
import os
import shutil
import subprocess
import tempfile
import time
from typing import List, Optional

_GEM = "https://generativelanguage.googleapis.com/v1beta"
_MAX_AUDIO_BYTES = 8 * 1024 * 1024      # больше на 256 МБ хостинге не разжать, да и слушать долго
_MAX_SECONDS = 150

# Свежие flash умеют аудио, старые — нет.
_FALLBACK_MODELS = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash", "gemini-flash-latest"]

# Что в списке моделей не годится для расшифровки и ответов текстом.
_SKIP_WORDS = ("tts", "image", "imagen", "embed", "veo", "aqa", "robotics", "computer-use",
               "learnlm", "gemma", "live", "native-audio", "vision", "thinking-exp")
_MAX_MODELS = 6                          # больше шести моделей подряд пробовать незачем
_TRANSIENT = (429, 500, 502, 503, 504)   # временные сбои: перегрузка, лимит, сервер упал

HEAR_PROMPT = (
    "Перед тобой запись человеческого голоса. Точно расшифруй, что сказано, по-русски, "
    "одним абзацем, без пояснений и без кавычек. Если слов нет или неразборчиво — "
    "ответь ровно: пусто"
)

_EMPTY_MARKS = ("пусто", "empty", "нет слов")

_cache = {"models": None, "at": 0.0}


class HearError(RuntimeError):
    """Причина, которую можно показать человеку без кода и трейсбеков."""


def available() -> bool:
    return bool(os.getenv("GEMINI_API_KEY", "").strip())


def ffmpeg_exe() -> Optional[str]:
    try:
        from utils.ffmpeg_path import ffmpeg_exe as _exe

        return _exe()
    except Exception:
        return shutil.which("ffmpeg")


def _to_wav(data: bytes) -> bytes:
    """Любой голосовой контейнер → wav 16 кГц моно: Gemini opus внутри ogg не ест."""
    exe = ffmpeg_exe()
    if not exe:
        raise HearError("на хостинге нет ffmpeg — голос не расшифровать")
    fd, src = tempfile.mkstemp(suffix=".bin")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    try:
        p = subprocess.run(
            [exe, "-hide_banner", "-loglevel", "error", "-i", src,
             "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", "pipe:1"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
        )
    except subprocess.TimeoutExpired:
        raise HearError("запись слишком длинная, ffmpeg не успел её пережать")
    finally:
        try:
            os.remove(src)
        except OSError:
            pass
    if p.returncode != 0 or not p.stdout:
        raise HearError("не удалось прочитать запись: " + (p.stderr.decode(errors="ignore")[-180:] or "пустой ответ"))
    return p.stdout


async def _models(session, key: str) -> List[str]:
    now = time.time()
    if _cache["models"] and now - _cache["at"] < 3600:
        return _cache["models"]
    names: List[str] = []
    try:
        async with session.get(f"{_GEM}/models", headers={"x-goog-api-key": key},
                               params={"page_size": 600}) as r:
            blob = await r.json(content_type=None)
            for m in blob.get("models", []) or []:
                if "generateContent" in (m.get("supportedGenerationMethods") or []):
                    names.append(str(m.get("name", "")).split("/")[-1])
    except Exception:
        names = []
    names = [n for n in names if n]
    # Пустой список (сеть моргнула, ключ не тот) не кладём в кэш на час — иначе
    # временный сбой на старте отравит все ответы запасным списком надолго.
    if names:
        _cache.update(models=names, at=now)
        return names
    return list(_FALLBACK_MODELS)


def _order(models: List[str]) -> List[str]:
    """Проверенные flash — первыми, дальше остальные текстовые gemini, не больше шести."""
    ok = [m for m in models
          if m.startswith("gemini") and not any(w in m for w in _SKIP_WORDS)]
    head = [m for m in _FALLBACK_MODELS if m in ok]
    rest = [m for m in ok if m not in head and "flash" in m]
    chain = (head + rest)[:_MAX_MODELS]
    return chain or list(_FALLBACK_MODELS)


def _extract_text(blob: dict) -> str:
    parts = (((blob.get("candidates") or [{}])[0]).get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()


_HEDGE = 3.0   # если модель молчит дольше, параллельно запускаем следующую


def _tune(model: str, body: dict) -> dict:
    """У gemini-2.5-flash по умолчанию включены «размышления»: ответ идёт 5–15 секунд.
    Для чата и расшифровки они не нужны — выключаем, это главное ускорение."""
    if not model.startswith("gemini-2.5-flash"):
        return body
    out = dict(body)
    cfg = dict(out.get("generationConfig") or {})
    cfg["thinkingConfig"] = {"thinkingBudget": 0}
    out["generationConfig"] = cfg
    return out


async def _call_model(session, hdr: dict, model: str, body: dict):
    """Один запрос к одной модели. Не бросает: возвращает (вид, текст или причина).

    Виды: ok — есть текст; busy — временный сбой (503/429/5xx/таймаут); auth — ключ не
    подошёл (фатально); skip — модель не умеет такой запрос или ответила пустотой.
    """
    import aiohttp

    try:
        async with session.post(f"{_GEM}/models/{model}:generateContent",
                                headers=hdr, json=_tune(model, body)) as r:
            try:
                blob = await r.json(content_type=None)
            except Exception:
                blob = {}
            if not isinstance(blob, dict):
                blob = {}
            status = r.status
    except asyncio.TimeoutError:
        return "busy", f"модель {model} не успела ответить"
    except aiohttp.ClientError as e:
        return "busy", f"сеть до Gemini: {e}"
    except Exception as e:
        return "skip", f"{type(e).__name__}: {e}"

    if status == 200:
        text = _extract_text(blob)
        return ("ok", text) if text else ("skip", "модель ответила пустотой")
    msg = str((blob.get("error") or {}).get("message", status))[:200]
    if status in (401, 403):
        return "auth", f"Gemini не принял ключ GEMINI_API_KEY ({status}): {msg}"
    if status in _TRANSIENT:
        return "busy", f"Gemini: {msg}"
    return "skip", f"Gemini: {msg}"


async def _generate(body: dict, timeout: float) -> str:
    """Запрос к Gemini с «хеджем»: если первая модель молчит дольше _HEDGE секунд или
    упала, сразу идёт следующая, а первая не отменяется. Берём ответ того, кто успел."""
    import aiohttp

    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise HearError("в переменных хостинга нет GEMINI_API_KEY")

    deadline = time.monotonic() + timeout
    hdr = {"x-goog-api-key": key, "Content-Type": "application/json"}
    per_request = aiohttp.ClientTimeout(total=min(10.0, timeout))
    err, busy = "", False

    async with aiohttp.ClientSession(timeout=per_request) as session:
        models = _order(await _models(session, key))
        tasks = {}
        idx = 0

        def start():
            nonlocal idx
            model = models[idx]
            idx += 1
            tasks[asyncio.ensure_future(_call_model(session, hdr, model, body))] = model

        start()
        try:
            while tasks:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                more = idx < len(models)
                done, _ = await asyncio.wait(list(tasks), timeout=min(_HEDGE, left) if more else left,
                                             return_when=asyncio.FIRST_COMPLETED)
                if not done:
                    if more:
                        start()
                    continue
                for d in done:
                    tasks.pop(d, None)
                    kind, val = d.result()
                    if kind == "ok":
                        return val
                    if kind == "auth":
                        raise HearError(val)
                    busy = busy or kind == "busy"
                    err = val
                if not tasks and idx < len(models):
                    start()
        finally:
            for t in tasks:
                t.cancel()

    if busy:
        raise HearError("Gemini сейчас перегружен (высокий спрос) — попробуй ещё раз через минуту")
    raise HearError(err or "ни одна модель не ответила")


async def transcribe(data: bytes, timeout: float = 45.0) -> str:
    """Голосовой файл → текст. Бросает HearError с человеческой причиной."""
    if not os.getenv("GEMINI_API_KEY", "").strip():
        raise HearError("в переменных хостинга нет GEMINI_API_KEY")
    if not data:
        raise HearError("вложение пустое")
    if len(data) > _MAX_AUDIO_BYTES:
        raise HearError("запись весит больше 8 МБ — на этом хостинге не потянуть")

    wav = await asyncio.to_thread(_to_wav, data)
    b64 = base64.b64encode(wav).decode()
    body = {
        "contents": [{"parts": [{"text": HEAR_PROMPT},
                                {"inline_data": {"mime_type": "audio/wav", "data": b64}}]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 500},
    }
    text = await _generate(body, timeout)
    if text.lower().strip(" .!") in _EMPTY_MARKS:
        raise HearError("в записи не слышно слов")
    return text[:1200]


async def think(system: str, prompt: str, timeout: float = 11.0, json_mode: bool = False) -> str:
    """Думать текст по-человечески через Gemini.

    Это запасной мозг: ключ GEMINI_API_KEY на хостинге уже есть, а ANTHROPIC_API_KEY
    может не быть — без него бот не обязан молчать.
    """
    if not os.getenv("GEMINI_API_KEY", "").strip():
        raise HearError("в переменных хостинга нет ни ANTHROPIC_API_KEY, ни GEMINI_API_KEY")

    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.5, "maxOutputTokens": 700},
    }
    if json_mode:
        body["generationConfig"]["responseMimeType"] = "application/json"
        body["generationConfig"]["temperature"] = 0.3
    return await _generate(body, timeout)


def size_guard(attachment) -> str:
    """Почему запись брать не стоит (пусто — если стоит)."""
    try:
        size = int(getattr(attachment, "size", 0) or 0)
    except (TypeError, ValueError):
        size = 0
    if size and size > _MAX_AUDIO_BYTES:
        return "запись больше 8 МБ"
    try:
        secs = int(getattr(attachment, "duration", 0) or 0)
    except (TypeError, ValueError):
        secs = 0
    if secs > _MAX_SECONDS:
        return f"запись дольше {int(_MAX_SECONDS / 60)} минут"
    return ""
