"""
Резолвер треков yt-dlp — вынесен в отдельный процесс.

yt-dlp на пике съедает 60-100 МБ RAM, а в Python освобождённая память обратно в
ОС возвращается неохотно. На слабом хосте (250-256 МБ) этого хватает, чтобы бота
убил OOM-killer. Поэтому бот yt-dlp вообще не импортирует: запускается этот
скрипт, печатает одну строку JSON и умирает — память сразу освобождается.

Запуск вручную для проверки:
    echo '{"query": "linkin park numb"}' | python utils/ytdlp_resolve.py
"""
import contextlib
import json
import os
import re
import sys

import yt_dlp

YTDL_OPTS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "noprogress": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    # Лёгкий режим для слабых хостов: не виснем на долгих повторах
    "socket_timeout": 15,
    "retries": 1,
    "extractor_retries": 1,
    "cachedir": False,
    "playlist_items": "1",
    "flat_playlist": True,
}

BOT_CHECK_MARK = "not a bot"
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _pick(url: str, opts: dict) -> dict:
    with yt_dlp.YoutubeDL(opts) as ydl:
        data = ydl.extract_info(url, download=False)
    if "entries" in data:
        data = next((x for x in data["entries"] if x), None)
    if not data:
        raise RuntimeError("Пустой результат")
    return data


def _track(data: dict, query: str) -> dict:
    stream = data.get("url")
    if not stream:
        for f in data.get("formats") or []:
            if f.get("url") and (f.get("acodec") not in (None, "none")):
                stream = f["url"]
                break
    if not stream:
        raise RuntimeError("Нет аудиопотока у этого трека")
    return {
        "title": data.get("title", "Без названия"),
        "stream_url": stream,
        "webpage_url": data.get("webpage_url") or data.get("original_url") or query,
        "duration": data.get("duration"),
        "thumbnail": data.get("thumbnail"),
    }


def resolve(
    query: str,
    cookies: str = None,
    prefer_soundcloud: bool = True,
    search_results: int = 3,
) -> dict:
    """Находит трек по названию или ссылке. Возвращает dict, либо RuntimeError с понятным текстом."""
    is_url = query.startswith(("http://", "https://"))
    opts = dict(YTDL_OPTS)
    if cookies and os.path.exists(cookies):
        opts["cookiefile"] = cookies
    # куки YouTube для SoundCloud не нужны — они только тормозят и добавляют памяти
    sc_opts = {k: v for k, v in opts.items() if k != "cookiefile"}
    last = None

    def soundcloud():
        nonlocal last
        try:
            with yt_dlp.YoutubeDL(dict(sc_opts, extract_flat=True)) as ydl:
                res = ydl.extract_info(f"scsearch{search_results}:{query}", download=False)
            for e in (res.get("entries") or []):
                url = e.get("url") if e else None
                if not url:
                    continue
                try:
                    return _track(_pick(url, sc_opts), query)
                except Exception as ex:
                    last = ex
        except Exception as ex:
            last = ex
        return None

    if not is_url and prefer_soundcloud:
        track = soundcloud()
        if track:
            return track

    try:
        if is_url:
            candidates = [query]
        else:
            with yt_dlp.YoutubeDL(dict(opts, extract_flat=True)) as ydl:
                res = ydl.extract_info(f"ytsearch{search_results}:{query}", download=False)
            candidates = []
            for e in (res.get("entries") or []):
                if not e:
                    continue
                url = e.get("url") or (f"https://www.youtube.com/watch?v={e['id']}" if e.get("id") else None)
                if url:
                    candidates.append(url)
        for url in candidates:
            try:
                return _track(_pick(url, opts), query)
            except Exception as e:
                last = e
                if BOT_CHECK_MARK in str(e):
                    break  # YouTube блокирует вход — остальные варианты упадут так же
    except Exception as e:
        last = e

    if not is_url and not prefer_soundcloud:
        track = soundcloud()
        if track:
            return track

    msg = _ANSI.sub("", str(last)) if last else "Ничего не найдено"
    if "confirm you" in msg and "not a bot" in msg:
        if cookies and os.path.exists(cookies):
            msg = ("YouTube не принял cookies.txt (устарел или аккаунт разлогинен). "
                   "Экспортируйте свежий cookies.txt и положите рядом с main.py.")
        else:
            msg = ("YouTube требует вход. Положите cookies.txt рядом с main.py "
                   "(экспорт из браузера, формат Netscape) и перезапустите бота.")
    raise RuntimeError(msg[:300])


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    try:
        req = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace") or "{}")
        query = (req.get("query") or "").strip()
        if not query:
            raise RuntimeError("Пустой запрос")
        # yt-dlp может что-то писать в stdout — не мешаем нашему JSON
        with contextlib.redirect_stdout(sys.stderr):
            track = resolve(
                query,
                req.get("cookies"),
                bool(req.get("prefer_soundcloud", True)),
                int(req.get("search_results", 3)),
            )
        out = {"ok": True, "track": track}
    except Exception as e:
        out = {"ok": False, "error": str(e)[:300]}
    sys.stdout.write(json.dumps(out, ensure_ascii=False))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
