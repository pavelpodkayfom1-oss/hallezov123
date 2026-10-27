"""Путь к ffmpeg для озвучки (govorit, govorit_stay).

Системный ffmpeg есть не везде: на Windows через start.bat и на бесплатных
хостингах его нет в PATH. Тогда берём статический бинарник из imageio-ffmpeg,
он уже в requirements.txt (так же сделано в музыке).
"""
import os
import shutil
from typing import Optional

_EXE: Optional[str] = None
_DONE = False


def ffmpeg_exe() -> Optional[str]:
    """Путь к рабочему ffmpeg или None, если его нигде нет."""
    global _EXE, _DONE
    if _DONE and _EXE:
        return _EXE
    exe = os.environ.get("FFmpegPath") or shutil.which("ffmpeg")
    if not exe:
        try:
            from imageio_ffmpeg import get_ffmpeg_exe
            exe = get_ffmpeg_exe()
        except Exception:
            exe = None
    _EXE, _DONE = exe, bool(exe)
    return _EXE
