"""Ядро «уха»: накопление речи, обращение по имени, WAV. Без discord, чтобы проверялось отдельно.

Звук приходит кусками по 20 мс (48 кГц, 16 бит, стерео). Куски одного человека
складываются в реплику; реплика кончается, когда он замолчал на SILENCE секунд.
"""
import array
import io
import re
import threading
import time
import wave
from typing import Dict, List, Optional, Tuple

RATE, CHANNELS, WIDTH = 48000, 2, 2
BYTES_PER_SEC = RATE * CHANNELS * WIDTH

SILENCE = 1.0        # столько тишины — реплика закончилась
MIN_SEC = 0.7        # короче — это кашель или щелчок
MAX_SEC = 20.0       # длиннее режем: Gemini и так много за раз не нужно
MIN_LEVEL = 120      # средняя громкость ниже — шум, а не голос

DEFAULT_WAKE = "бот,bot,абу,abu"


class Utt:
    __slots__ = ("user", "guild_id", "chunks", "size", "first", "last")

    def __init__(self, user, guild_id: int, now: float):
        self.user, self.guild_id = user, guild_id
        self.chunks: List[bytes] = []
        self.size, self.first, self.last = 0, now, now

    @property
    def seconds(self) -> float:
        return self.size / BYTES_PER_SEC

    def pcm(self) -> bytes:
        return b"".join(self.chunks)


class Buffers:
    """Потокобезопасно: write() звука идёт из отдельного потока библиотеки."""

    def __init__(self):
        self._lock = threading.Lock()
        self._utts: Dict[Tuple[int, int], Utt] = {}

    def add(self, guild_id: int, user, pcm: bytes, now: Optional[float] = None) -> None:
        if not pcm:
            return
        now = time.monotonic() if now is None else now
        key = (guild_id, user.id)
        with self._lock:
            u = self._utts.get(key)
            if u is None:
                u = self._utts[key] = Utt(user, guild_id, now)
            if u.size < BYTES_PER_SEC * (MAX_SEC + 5):     # страховка от разбухания памяти
                u.chunks.append(pcm)
                u.size += len(pcm)
            u.last = now

    def take_ready(self, now: Optional[float] = None) -> List[Utt]:
        now = time.monotonic() if now is None else now
        out = []
        with self._lock:
            for key, u in list(self._utts.items()):
                if now - u.last >= SILENCE or u.seconds >= MAX_SEC:
                    out.append(self._utts.pop(key))
        return out

    def drop_guild(self, guild_id: int) -> None:
        with self._lock:
            for key in [k for k in self._utts if k[0] == guild_id]:
                self._utts.pop(key, None)


def loudness(pcm: bytes) -> float:
    """Средняя |амплитуда| по прореженным отсчётам: дёшево и хватает, чтобы отсечь шум."""
    a = array.array("h")
    a.frombytes(pcm[: len(pcm) // 2 * 2])
    sample = a[::97]
    return sum(abs(x) for x in sample) / max(1, len(sample))


def pcm_to_wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(CHANNELS)
        w.setsampwidth(WIDTH)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return buf.getvalue()


# ───────────────────────── обращение по имени ─────────────────────────

_TOKEN = re.compile(r"[^\W_]+(?:['-][^\W_]+)*")


def parse_wake(raw: str) -> List[str]:
    return [w.strip().lower() for w in (raw or DEFAULT_WAKE).split(",") if w.strip()]


def split_wake(text: str, wakes: List[str]) -> Tuple[bool, str]:
    """(позвали ли по имени, текст без имени). Имя ищем в первых четырёх словах:
    «бот, включи фонк», «эй бот что играет», «боту скажи». «Ботинки» не считается."""
    for i, m in enumerate(_TOKEN.finditer(text)):
        if i >= 4:
            break
        tok = m.group().lower()
        if any(tok == w or (len(w) >= 3 and tok.startswith(w) and len(tok) <= len(w) + 1) for w in wakes):
            rest = text[:m.start()] + " " + text[m.end():]
            rest = re.sub(r"(\s*[,;:.!?—-]\s*){2,}", ", ", rest)
            rest = re.sub(r"\s+", " ", rest).strip(" ,;:.!?—-")
            return True, rest
    return False, text.strip()


class Gate:
    """Решает, нужна ли реплика боту. Позвали по имени — берём. Просто позвали и замолчали
    («Бот…») — следующая реплика этого человека берётся без имени. После вопроса окно
    открыто ещё FOLLOW секунд: уточнение не надо начинать с имени заново."""

    def __init__(self, wakes: List[str], follow: float = 20.0, wait: float = 8.0, always: bool = False):
        self.wakes, self.follow, self.wait, self.always = wakes, follow, wait, always
        self._open: Dict[int, float] = {}

    def decide(self, user_id: int, text: str, now: Optional[float] = None) -> Tuple[Optional[str], bool]:
        """(вопрос или None, «только позвали»)."""
        now = time.monotonic() if now is None else now
        text = (text or "").strip()
        if not text:
            return None, False
        if self.always:
            return text, False
        woke, q = split_wake(text, self.wakes)
        if woke:
            if len(q) >= 3:
                self._open[user_id] = now + self.follow
                return q, False
            self._open[user_id] = now + self.wait
            return None, True
        if self._open.get(user_id, 0) > now:
            self._open[user_id] = now + self.follow
            return text, False
        return None, False
