"""
Снимки настроек и базы в папку backups/.
Файлы состояния лежат рядом с кодом, поэтому обновление из git или скрипт сборки
могут вернуть их к дефолтам — по держим последние копии, чтобы состав, каналы и
роли не терялись.

guard_after_deploy() вызывается ДО загрузки когов: он сверяет живые файлы с
отпечатками последнего снимка и, если деплой вернул старую версию, поднимает
обратно снимок. /update_save (cogs/state_save.py) делает такой снимок вручную.
"""
import asyncio
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent
BACKUP_DIR = BASE_DIR / "backups"
STATE_SUFFIXES = (".json", ".db")
MAX_FILE = 8 * 1024 * 1024          # больше 8 МБ не копируем: снимок не должен есть диск
PATTERNS = ("*.json", "*.db")       # совместимость со старыми снимками
KEEP = 4
INTERVAL = 900
SIG_NAME = "state_sig.json"
REJECT_DIR = "rejected"
POSIX_FADV_DONTNEED = 4

# Что подняли из снимка при этом запуске — показывает /update_save.
LAST_RESTORE: list = []


def _drop_cache(path: Path) -> None:
    """Выкидывает прочитанный файл из кэша ядра.

    Дешёвые панели считают кэш файлов как память контейнера, и десятки копий
    базы выбивают счётчик за лимит, хотя процесс бота при этом маленький.
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


def state_files() -> list:
    """Файлы состояния: все *.json и *.db в корне бота."""
    return sorted(p for p in BASE_DIR.iterdir()
                  if p.is_file() and p.suffix in STATE_SUFFIXES and p.stat().st_size <= MAX_FILE)


def sha1_of(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _signature() -> str:
    digest = hashlib.sha1()
    for path in state_files():
        stat = path.stat()
        digest.update(f"{path.name}:{stat.st_size}:{int(stat.st_mtime)}\n".encode())
    return digest.hexdigest()


def _snap_dirs() -> list:
    if not BACKUP_DIR.is_dir():
        return []
    stamp = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}$")
    return sorted(d for d in BACKUP_DIR.iterdir() if d.is_dir() and stamp.match(d.name))


def newest() -> Optional[Path]:
    dirs = _snap_dirs()
    return dirs[-1] if dirs else None


def snapshot(force: bool = False) -> Optional[Path]:
    """Копирует состояние, только если с прошлого снимка что-то изменилось.

    force=True — копирует всегда (это делает /update_save перед деплоем).
    """
    signature = _signature()
    marker = BACKUP_DIR / "last.sig"
    if not force and marker.exists() and marker.read_text(encoding="utf-8") == signature:
        return None

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    dest = BACKUP_DIR / datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dest.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for path in state_files():
        copy = dest / path.name
        shutil.copy2(path, copy)
        manifest[path.name] = sha1_of(copy)
        _drop_cache(copy)
        _drop_cache(path)
    marker.write_text(signature, encoding="utf-8")
    (BACKUP_DIR / SIG_NAME).write_text(
        json.dumps({"at": dest.name, "files": manifest}, ensure_ascii=False, indent=1),
        encoding="utf-8")

    # удаляем СТАРЫЕ снимки: список отсортирован по времени, свежие — в хвосте
    for old in _snap_dirs()[:-KEEP]:
        shutil.rmtree(old, ignore_errors=True)
    return dest


def guard_after_deploy() -> list:
    """Поднимает последний снимок, если деплой из git вернул файлы к старой версии.

    Отпечатки в state_sig.json — это то, что бот сам записал в последний снимок.
    Живой файл с другим отпечатком значит, что его перезаписали снаружи (деплой),
    а не сам бот. Всё, что отбрасываем, складываем в backups/rejected/<time>/.
    """
    LAST_RESTORE.clear()
    sig_path = BACKUP_DIR / SIG_NAME
    if not sig_path.exists():
        return LAST_RESTORE
    try:
        sig = json.loads(sig_path.read_text(encoding="utf-8"))
        manifest = sig["files"]
        dest = BACKUP_DIR / sig.get("at", "")
        if not dest.is_dir():
            dest = newest()
    except Exception:
        return LAST_RESTORE
    if dest is None:
        return LAST_RESTORE

    for name, want in manifest.items():
        src = dest / name
        if not src.exists():
            continue
        live = BASE_DIR / name
        try:
            if live.exists() and sha1_of(live) == want:
                continue
            if live.exists():
                junk = BACKUP_DIR / REJECT_DIR / datetime.now().strftime("%Y-%m-%d_%H%M%S")
                junk.mkdir(parents=True, exist_ok=True)
                shutil.copy2(live, junk / name)
            shutil.copy2(src, live)
            LAST_RESTORE.append(name)
        except Exception as error:
            print(f"⚠️ Не удалось восстановить {name}: {error}", file=sys.stderr, flush=True)
    return LAST_RESTORE


async def run() -> None:
    while True:
        try:
            dest = await asyncio.to_thread(snapshot)
            if dest:
                print(f"💾 Снимок настроек и базы: {dest.name}", flush=True)
        except Exception as error:
            print(f"⚠️ Снимок не сделан: {error}", file=sys.stderr, flush=True)
        await asyncio.sleep(INTERVAL)
