"""
Снимки настроек и базы в папку backups/.
Файлы состояния лежат рядом с кодом, поэтому обновление из git или скрипт сборки
могут вернуть их к дефолтам — по держим последние копии, чтобы состав, каналы и
роли не терялись.
"""
import asyncio
import hashlib
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent
BACKUP_DIR = BASE_DIR / "backups"
PATTERNS = ("*.json", "*.db")
KEEP = 4
INTERVAL = 900
POSIX_FADV_DONTNEED = 4


def _drop_cache(path: Path) -> None:
    """Выкидывает прочитанный файл из кэша ядра.

    Дешёвые панели считают кэш файлов как память контейнера, и десятки копий
    базы выбивают счётчик за лимит, хотя процесс боты при этом маленький.
    """
    try:
        import ctypes

        libc = ctypes.CDLL("libc.so.6")
        fd = os.open(str(path), os.O_RDONLY)
        try:
            libc.posix_fadvise(ctypes.c_int(fd), ctypes.c_longlong(0), ctypes.c_longlong(0),
                               ctypes.c_int(POSIX_FADV_DONTNEED))
        finally:
            os.close(fd)
    except Exception:
        pass


def _signature() -> str:
    digest = hashlib.sha1()
    for pattern in PATTERNS:
        for path in sorted(BASE_DIR.glob(pattern)):
            stat = path.stat()
            digest.update(f"{path.name}:{stat.st_size}:{int(stat.st_mtime)}\n".encode())
    return digest.hexdigest()


def snapshot() -> Optional[Path]:
    """Копирует состояние, только если с прошлого снимка что-то изменилось."""
    signature = _signature()
    marker = BACKUP_DIR / "last.sig"
    if marker.exists() and marker.read_text(encoding="utf-8") == signature:
        return None

    dest = BACKUP_DIR / datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dest.mkdir(parents=True, exist_ok=True)
    for pattern in PATTERNS:
        for path in BASE_DIR.glob(pattern):
            copy = dest / path.name
            shutil.copy2(path, copy)
            _drop_cache(copy)
            _drop_cache(path)
    marker.write_text(signature, encoding="utf-8")

    for old in sorted(d for d in BACKUP_DIR.iterdir() if d.is_dir())[KEEP:]:
        shutil.rmtree(old, ignore_errors=True)
    return dest


async def run() -> None:
    while True:
        try:
            dest = await asyncio.to_thread(snapshot)
            if dest:
                print(f"💾 Снимок настроек и базы: {dest.name}", flush=True)
        except Exception as error:
            print(f"⚠️ Снимок не сделан: {error}", file=sys.stderr, flush=True)
        await asyncio.sleep(INTERVAL)
