"""Звук при входе: короткий знак, который накладывается на то, что уже играет в канале.

Людей через бота не слышно (disnake только отправляет звук), но отправлять — может,
и реплики в том числе. Здесь три вещи: где взять сам звук, как его подмешать в поток
плеера и как не устроить кашу, если в канал зашла толпа.

Формат тот же, что disnake отдаёт в голосовой канал: 48 кГц, стерео, 16 бит на канал,
поэтому в горячем пути перекодировки нет.
"""
from __future__ import annotations

import array
import asyncio
import math
import os
import random
import subprocess
import threading
import time
from typing import Callable, List, Optional

try:
    # без audioop громкость не крутится, значит на хосте он точно есть
    import audioop
except Exception:  # pragma: no cover - python 3.13 его потерял
    audioop = None

RATE = 48000
FRAME = 4                       # стерео, 16 бит на канал
MAX_SECONDS = 4.0               # длиннее реплика не живёт: человек ждёт трек, не озвучку
CACHE_LIMIT = 6                 # сколько файлов держим в памяти разом
CACHE_BYTES = 4 * 1024 * 1024   # и сколько всего: на 256 МБ лишние байты не нужны
FIRE_SECONDS = 8.0              # просроченная накладка не выстрелит при снятии с паузы
FADE_RATE = RATE * 0.06         # 60 мс на край, чтобы граница не щёлкала


def _fade_tail(pcm: bytes) -> bytes:
    """Гасит последние 60 мс: срезанный по длине кусок иначе звучит щелчком."""
    fade = int(FADE_RATE) // FRAME * FRAME
    if len(pcm) <= fade:
        return pcm
    head, body = pcm[:-fade], pcm[-fade:]
    s = array.array("h")
    s.frombytes(body)
    n = max(1, len(s))
    for i in range(len(s)):
        s[i] = int(s[i] * (1 - i / n) ** 2)
    return head + s.tobytes()


def chime() -> bytes:
    """Встроенный «дзынь»: два тона с затуханием, синтезируются на ходу.

    Нужен он затем, чтобы фича работала в ту же минуту: папка с репликами пустая, а
    человек уже зашёл в войс. Тишина в ответ выглядела бы как сломанная кнопка.
    """
    seconds = 0.9
    n = int(RATE * seconds)
    notes = ((880.0, 0.0), (1174.66, 0.12))
    onset = int(RATE * 0.008)
    out = array.array("h", [0]) * (n * 2)
    for k in range(n):
        t = k / RATE
        v = 0.0
        for freq, start in notes:
            dt = t - start
            if dt < 0:
                continue
            env = math.exp(-5.2 * dt)
            # вторая гармоника тише основной: без неё это тестовый писк, а не звонок
            v += (math.sin(6.2831853 * freq * dt) * 0.52
                  + math.sin(6.2831853 * freq * 2 * dt) * 0.11) * env
        if t < 0.008:
            v *= t / 0.008
        s = int(max(-31000, min(31000, v * 29000)))
        out[k * 2] = s
        out[k * 2 + 1] = int(s * 0.84)   # правый канал чуть тише — звонок, а не писк в mono
    return out.tobytes()


def _mix(a: bytes, b: bytes) -> bytes:
    """Сложение двух кусков одного размера с насыщением."""
    if audioop is not None:
        return audioop.add(a, b, 2)
    x = array.array("h")
    x.frombytes(a)
    y = array.array("h")
    y.frombytes(b)
    for i in range(len(x)):
        v = x[i] + y[i]
        x[i] = 32767 if v > 32767 else (-32768 if v < -32768 else v)
    return x.tobytes()


class Board:
    """Кладёт реплику поверх трека, не прерывая его.

    Пишет событие входа, читает аудиопоток disnake — поэтому накладка под локом.
    Файлы из папки декодируются лениво и в отдельном потоке: ffmpeg даже на 40 мс не
    должен вешать цикл бота, а заодно и весь Discord.
    """

    def __init__(self, folder: str = "", exe: Optional[Callable[[], str]] = None):
        self.folder = folder
        self.exe = exe
        self.lock = threading.Lock()
        self.hot = b""
        self.pos = 0
        self.deadline = 0.0
        self._cache = {}
        self._bell = None

    # ---- откуда брать звук ----
    def files(self) -> List[str]:
        if not self.folder or not os.path.isdir(self.folder):
            return []
        got = []
        for name in sorted(os.listdir(self.folder)):
            if name.startswith((".", "_")):
                continue
            if not name.lower().endswith((".mp3", ".ogg", ".oga", ".wav", ".m4a", ".aac", ".flac")):
                continue
            path = os.path.join(self.folder, name)
            if os.path.isfile(path):
                got.append(path)
        return got

    def names(self) -> List[str]:
        return [os.path.splitext(os.path.basename(p))[0][:40] for p in self.files()]

    def bell(self) -> bytes:
        if self._bell is None:
            self._bell = chime()
        return self._bell

    def _decode(self, path: str) -> Optional[bytes]:
        try:
            stamp = os.path.getmtime(path)
        except OSError:
            return None
        held = self._cache.get(path)
        if held and held[0] == stamp:
            return held[1]
        exe = (self.exe() if callable(self.exe) else self.exe) or "ffmpeg"
        cmd = [exe, "-hide_banner", "-loglevel", "error", "-i", path, "-vn",
               "-f", "s16le", "-acodec", "pcm_s16le", "-ac", "2", "-ar", str(RATE), "-"]
        try:
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  timeout=25)
        except Exception:
            return None
        raw = proc.stdout or b""
        if proc.returncode != 0 or len(raw) < FRAME * 8:
            return None
        if len(raw) % FRAME:
            raw = raw[: len(raw) - (len(raw) % FRAME)]
        limit = int(RATE * MAX_SECONDS) * FRAME
        if len(raw) > limit:
            raw = _fade_tail(raw[:limit])
        self._cache[path] = (stamp, raw)
        while len(self._cache) > CACHE_LIMIT or \
                sum(len(v[1]) for v in self._cache.values()) > CACHE_BYTES:
            self._cache.pop(next(iter(self._cache)))
        return raw

    def pick(self) -> bytes:
        """Один случайный звук из папки, а если папка пуста — встроенный звонок."""
        got = self.files()
        if not got:
            return self.bell()
        for path in random.sample(got, len(got)):
            pcm = self._decode(path)
            if pcm:
                return pcm
        return self.bell()

    async def apick(self) -> bytes:
        return await asyncio.to_thread(self.pick)

    # ---- как он попадает в поток ----
    def fire(self, pcm: bytes) -> None:
        with self.lock:
            self.hot = pcm
            self.pos = 0
            self.deadline = time.monotonic() + FIRE_SECONDS

    def busy(self) -> bool:
        with self.lock:
            return bool(self.hot)

    def drop(self) -> None:
        with self.lock:
            self.hot = b""
            self.pos = 0
            self.deadline = 0.0

    def mix(self, chunk: bytes) -> bytes:
        """Добавляет в порцию плеера то, что осталось от реплики."""
        with self.lock:
            if not self.hot:
                return chunk
            if time.monotonic() > self.deadline:
                self.hot = b""
                self.pos = 0
                return chunk
            take = self.hot[self.pos:self.pos + len(chunk)]
            self.pos += len(take)
            if self.pos >= len(self.hot):
                self.hot = b""
                self.pos = 0
        if not take:
            return chunk
        if len(take) < len(chunk):
            take = take + bytes(len(chunk) - len(take))
        return _mix(chunk, take)
