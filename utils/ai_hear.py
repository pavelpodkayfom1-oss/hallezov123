"""Слышать войс-сообщение: расшифровка голоса в текст через Gemini.

Бот не умеет принимать звук из голосового канала (в disnake 2.12 у VoiceClient только
отдача), но человек может записать голосовое сообщение в чат — оно приходит обычным
файлом, и его можно расшифровать. Отдельного ключа не нужно: берётся GEMINI_API_KEY,
тот же, что для картинок событий.

Цепочка: файл .ogg (opus) → ffmpeg → wav 16 кГц моно → inline_data в Gemini → текст.
Здесь же think() — ответ моделью одним текстом: тот же ключ спасает, когда мозгу
не хватило ANTHROPIC_API_KEY.
"""
import asyncio
import base64
import os
import shutil
import subprocess
import tempfile
from typing import List, Optional

_GEM = "https://generativelanguage.googleapis.com/v1beta"
_MAX_AUDIO_BYTES = 8 * 1024 * 1024      # больше на 256 МБ хостинге не разжать, да и слушать долго
_MAX_SECONDS = 150

# Свежие flash умеют аудио, старые — нет.
_FALLBACK_MODELS = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash", "gemini-flash-latest"]

HEAR_PROMPT = (
    "Перед тобой запись человеческого голоса. Точно расшифруй, что сказано, по-русски, "
    "одним абзацем, без пояснений и без кавычек. Если слов нет или неразборчиво — "
    "ответь ровно: пусто"
)

_cache = {"models": None, "at": 0.0}


class HearError(RuntimeError):
    """Причина, которую можно показать человеку безexplainа кода."""


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
    import time

    now = time.time()
    if _cache["models"] and now - _cache["at"] < 3600:
        return _cache["models"]
    names: List[str] = []
    try:
        async with session.get(f"{_GEM}/models", headers={"x-goog-api-key": key},
                               params={"page_size": 600}) as r:
            blob = await r.json()
            for m in blob.get("models", []) or []:
                if "generateContent" in (m.get("supportedGenerationMethods") or []):
                    names.append(str(m.get("name", "")).split("/")[-1])
    except Exception:
        names = []
    _cache.update(models=names or list(_FALLBACK_MODELS), at=now)
    return _cache["models"]


def _order(models: List[str]) -> List[str]:
    """Сначала те, что точно умеют аудио, остальное — как было."""
    audio = [m for m in models if "live" in m or "native-audio" in m]
    plain = [m for m in models if m not in audio]
    head = [m for m in _FALLBACK_MODELS if m in plain or m in audio]
    return head + audio + [m for m in plain if m not in head]


async def transcribe(data: bytes, timeout: float = 45.0) -> str:
    """Голосовой файл → текст. Бросает HearError с человеческой причиной."""
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
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

    import aiohttp

    err = ""
    timeout_cfg = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=timeout_cfg) as session:
        hdr = {"x-goog-api-key": key, "Content-Type": "application/json"}
        for model in _order(await _models(session, key)):
            try:
                async with session.post(f"{_GEM}/models/{model}:generateContent",
                                        headers=hdr, json=body) as r:
                    blob = await r.json()
                    if r.status != 200:
                        err = str(blob.get("error", {}).get("message", r.status))[:200]
                        if r.status in (400, 404):
                            continue          # модель не умеет аудио — пробуем следующую
                        raise HearError(f"Gemini: {err}")
                    parts = (((blob.get("candidates") or [{}])[0]).get("content") or {}).get("parts") or []
                    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()
                    if not text or text.lower() in ("пусто", "empty", "нет слов"):
                        raise HearError("в записи не слышно слов")
                    return text[:1200]
            except HearError:
                raise
            except aiohttp.ClientError as e:
                err = f"сеть до Gemini: {e}"
                break
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
    raise HearError(err or "ни одна модель не взяла аудио")


async def think(system: str, prompt: str, timeout: float = 45.0) -> str:
    """Думать текст по-человечески через Gemini.

    Это запасной мозг: ключ GEMINI_API_KEY на хостинге уже есть, а ANTHROPIC_API_KEY
    может не быть — без него бот не обязан молчать.
    """
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise HearError("в переменных хостинга нет ни ANTHROPIC_API_KEY, ни GEMINI_API_KEY")

    import aiohttp

    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.5, "maxOutputTokens": 700},
    }
    err = ""
    hdr = {"x-goog-api-key": key, "Content-Type": "application/json"}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
        for model in _order(await _models(session, key)):
            try:
                async with session.post(f"{_GEM}/models/{model}:generateContent",
                                        headers=hdr, json=body) as r:
                    blob = await r.json()
                    if r.status != 200:
                        err = str(blob.get("error", {}).get("message", r.status))[:200]
                        if r.status in (400, 404):
                            continue
                        raise HearError(f"Gemini: {err}")
                    parts = (((blob.get("candidates") or [{}])[0]).get("content") or {}).get("parts") or []
                    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()
                    if text:
                        return text
                    err = "модель ответила пустотой"
            except HearError:
                raise
            except aiohttp.ClientError as e:
                err = f"сеть до Gemini: {e}"
                break
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
    raise HearError(err or "ни одна модель не ответила")


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
