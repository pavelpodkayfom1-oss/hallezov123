import os

files = {
    ".env": """# Токен вашего Discord бота из Discord Developer Portal
BOT_TOKEN=MTU1NDgwNTUyOTE0OTQ0ODMxNA.GaG9dx._8Rqp4mPFqQ6-6skhJD8spGNzw-m5V3mKkTSus

# ID сервера (Guild ID)
GUILD_ID=1554453749936820264
""",
    "config.json": """{
  "bot_name": "Hallez FAMQ",
  "embed_color": "0x990000",
  "success_color": "0x2ecc71",
  "error_color": "0xe74c3c",
  "warning_color": "0xf39c12",
  "logo_url": "",
  "admin_user_ids": [
    1453524259333869591,
    384783535585624068
  ],
  "admin_role_ids": [
    0
  ],
  "recruiter_role_ids": [
    0
  ],
  "channels": {
    "recruitment_channel_id": 0,
    "promotion_channel_id": 0,
    "promotion_review_channel_id": 0,
    "logs_channel_id": 0,
    "events_channel_id": 0
  },
  "voice_channels": [],
  "roles": {
    "family_role_id": 0,
    "rank_1_role_id": 0,
    "rank_2_role_id": 0,
    "rank_3_role_id": 0,
    "rank_4_role_id": 0
  },
  "ranks": {
    "1": {
      "name": "Новичок",
      "role_id": 0
    },
    "2": {
      "name": "Участник",
      "role_id": 0
    },
    "3": {
      "name": "Опытный",
      "role_id": 0
    },
    "4": {
      "name": "Старший",
      "role_id": 0
    }
  },
  "auto_nicknames": true,
  "recruitment_open": true,
  "texts": {
    "recruit_panel_title": "заголовок баннера набора",
    "recruit_panel_desc": "Мы ведем активный набор амбициозных, адекватных и стрессоустойчивых игроков в семью **Hallez FAMQ**!\\n\\n📌 **Критерии для вступления:**\\n• Адекватность, дисциплина и понимание субординации\\n• Наличие микрофона и возможность присутствовать на семейных МП\\n• Смена фамилии на **Hallez FAMQ** после испытательного срока (1 ранг)\\n\\n👇 Нажмите кнопку ниже, чтобы заполнить анкету кандидата:",
    "btn_recruit_apply": "Подать заявку в Hallez FAMQ",
    "btn_recruit_review": "Взять на рассмотрение",
    "btn_recruit_interview": "Вызвать на обзвон",
    "btn_recruit_accept": "Принять в семью",
    "btn_recruit_reject": "Отказать",
    "modal_recruit_title": "Анкета на вступление в Hallez FAMQ",
    "modal_recruit_nick_label": "Игровой никнейм (Имя Фамилия)",
    "modal_recruit_nick_ph": "Пример: Travis Hallez FAMQ",
    "modal_recruit_static_label": "Ваш статик (Static ID)",
    "modal_recruit_static_ph": "Пример: 12345",
    "modal_recruit_age_label": "Реальный возраст",
    "modal_recruit_age_ph": "Пример: 18",
    "modal_recruit_prev_label": "В каких семьях состояли ранее?",
    "modal_recruit_prev_ph": "Укажите названия семей и причину ухода...",
    "modal_recruit_why_label": "Почему именно Hallez FAMQ и как узнали о нас?",
    "modal_recruit_why_ph": "Ваши цели, планы в семье, откуда узнали...",
    "modal_reject_title": "Отклонение заявки в Hallez FAMQ",
    "modal_reject_label": "Причина отказа",
    "modal_reject_ph": "Укажите причину отказа (неподходящий возраст, отказ на обзвоне и т.д.)...",
    "recruit_card_title": "⚜️ Новая анкета кандидата: {nick}",
    "recruit_card_desc": "Кандидат {mention} подал заявку на вступление в семью **Hallez FAMQ**.\\nСтатус: 🟡 **Новая заявка**",
    "recruit_review_title": "🔎 Заявка взята на рассмотрение",
    "recruit_review_desc": "Рекрутер {reviewer} взял заявку кандидата {mention} на рассмотрение.",
    "recruit_interview_title": "🎙️ Вызов на собеседование (обзвон)",
    "recruit_interview_desc": "{mention}, рекрутер {reviewer} приглашает вас на обзвон в семью **Hallez FAMQ**!\\n\\n📍 **Голосовой канал:** {voice}\\n⏳ Пожалуйста, зайдите в указанный канал и ожидайте рекрутера.",
    "recruit_accepted_title": "Добро пожаловать в Hallez FAMQ!",
    "recruit_accepted_desc": "🎉 Кандидат {mention} успешно принят в семью!\\n\\n👤 **Никнейм:** `{nick}`\\n🆔 **Статик:** `{static}`\\n👑 **Принял:** {reviewer}\\n🎖️ **Выдан ранг:** 1 (Новичок)",
    "recruit_accepted_thread_msg": "🎉 Кандидат {mention} успешно принят в семью рекрутером {reviewer}!\\nРоли выданы. Ветка архивирована.",
    "recruit_accepted_dm": "🎉✨ **Поздравляем, добро пожаловать в Hallez FAMQ!** ✨🎉\\n\\n💎 Ваша заявка одобрена, и теперь вы официально часть нашей большой и дружной семьи **Hallez FAMQ**. Мы очень рады видеть вас в своих рядах!\\n\\n🎖️ На сервере уже выданы ваши семейные роли.\\n📜 Загляните в правила семьи и смело подключайтесь к общению и общим мероприятиям.\\n\\n🌟 Желаем вам яркого старта, надежных друзей, громких побед и только отличного настроения в игре!\\n🤝 Если появятся вопросы, рекрутеры и старший состав всегда рядом и поддержат вас.\\n\\n🏆 Добро пожаловать домой, мы верим, что вы станете гордостью семьи! 💫",
    "recruit_rejected_title": "Заявка отклонена",
    "recruit_rejected_desc": "👤 **Кандидат:** {mention}\\n👮 **Проверяющий:** {reviewer}\\n📝 **Причина:** {reason}",
    "recruit_rejected_dm": "🌸 Здравствуйте, {nick}!\\n\\nБлагодарим вас за интерес к семье **Hallez FAMQ** и за время, потраченное на анкету. 🤍\\nК сожалению, на этот раз заявка не была одобрена.\\n\\n📝 **Причина:** {reason}\\n\\n🔄 Это не окончательное решение: вы можете подать заявку повторно чуть позже, когда будете готовы.\\n✨ Желаем вам удачи, хорошего настроения и успехов в игре, надеемся увидеть вас снова!",
    "promo_panel_title": "📈 ОТЧЕТЫ НА ПОВЫШЕНИЕ В СЕМЬЕ HALLEZ FAMQ",
    "promo_panel_desc": "Для продвижения по карьерной лестнице семьи оставьте свой отчет.\\n\\n🎖️ **Критерии для повышения:**\\n• **1 ➔ 2 ранг (Участник):** Смена фамилии в игре на `Hallez FAMQ` (скриншот паспорта/смены)\\n• **2 ➔ 3 ранг (Опытный):** Нахождение в семье более 14 дней (скриншот планшета/профиля)\\n• **3 ➔ 4 ранг (Старший состав):** По индивидуальным нормативам и решению руководства\\n\\n👇 Выберите ранг из списка ниже для подачи отчета:",
    "btn_promo_accept": "Одобрить повышение",
    "btn_promo_reject": "Отклонить отчет",
    "select_promo_ph": "Выберите ранг, на который повышаетесь...",
    "select_promo_opt_1_2_label": "1 ➔ 2 ранг (Смена фамилии на Hallez FAMQ)",
    "select_promo_opt_1_2_desc": "Требуется скрин смены фамилии на Hallez FAMQ",
    "select_promo_opt_2_3_label": "2 ➔ 3 ранг (>2 недель в семье)",
    "select_promo_opt_2_3_desc": "Требуется скрин доказательства нахождения в семье > 14 дней",
    "modal_promo_nick_label": "Игровой никнейм (Имя Фамилия)",
    "modal_promo_nick_ph": "Пример: Travis Hallez FAMQ",
    "modal_promo_static_label": "Статический ID",
    "modal_promo_static_ph": "Пример: 12345",
    "modal_promo_proof_1_2_label": "Ссылка на док-ва смены фамилии",
    "modal_promo_proof_2_3_label": "Ссылка на док-ва нахождения >2 недель",
    "modal_promo_proof_ph": "Вставьте ссылку на скриншот (Imgur / Yapx / Discord)...",
    "modal_promo_reject_title": "Отклонение отчета на повышение",
    "modal_promo_reject_label": "Причина отклонения отчета",
    "modal_promo_reject_ph": "Недостаточно доказательств / не прошло 14 дней / не сменена фамилия...",
    "promo_card_title": "📈 Отчет на повышение #{id}",
    "promo_card_desc": "Участник {mention} подал отчет на повышение в должности семьи **Hallez FAMQ**.\\nКвалификация: **{prev_name} ({prev_rank}) ➔ {target_name} ({target_rank})**",
    "promo_approved_title": "Отчет одобрен • Повышение выдано!",
    "promo_approved_desc": "🎉 Участник {mention} повышен до **{target_name} ({target_rank} ранг)**!\\n\\n👤 **Никнейм:** `{nick}`\\n🆔 **Статик:** `{static}`\\n👮 **Проверил и одобрил:** {reviewer}\\n📎 **Доказательства:** {proof_url}",
    "promo_approved_dm": "🎊🌟 **Поздравляем с повышением в Hallez FAMQ!** 🌟🎊\\n\\n🏅 Ваш отчет проверен и **одобрен** руководителем {reviewer}. Это заслуженный результат вашей активности и преданности семье!\\n\\n🎖️ **Ваш новый ранг:** {target_name} ({target_rank} ранг)\\n\\n✨ Мы гордимся вами и видим, как вы растете вместе с семьей **Hallez FAMQ**.\\n🚀 Желаем новых высот, интересных побед, крепкой дружбы и отличного настроения!\\n💎 Продолжайте в том же духе, впереди вас ждет еще больше! 🔥",
    "promo_rejected_title": "Отчет на повышение отклонен",
    "promo_rejected_desc": "👤 **Сотрудник:** {mention}\\n👮 **Проверяющий:** {reviewer}\\n📝 **Причина:** {reason}",
    "promo_rejected_dm": "🌸 Здравствуйте, {nick}!\\n\\nСпасибо, что отправили отчет на повышение, мы ценим ваше стремление развиваться в семье **Hallez FAMQ**. 🤍\\nК сожалению, сейчас отчет не может быть одобрен.\\n\\n📝 **Причина:** {reason}\\n\\n🔧 Пожалуйста, исправьте указанные моменты и отправьте отчет заново, мы с радостью рассмотрим его еще раз.\\n✨ У вас обязательно все получится, желаем удачи и отличного настроения!",
    "btn_event_yes": "Буду (+)",
    "btn_event_late": "Опоздаю (+-)",
    "btn_event_no": "Не смогу (-)",
    "event_card_title": "⚔️ ОБЩИЙ СБОР СЕМЬИ: {title}",
    "event_card_desc": "Руководство объявило сбор бойцов **Hallez FAMQ**!\\n\\n⏰ **Время сбора:** `{time}`\\n📍 **Требования:** {info}\\n\\nОбязательно прожмите статус присутствия кнопками ниже:",
    "nickname_format": "[Hallez FAMQ] {nick} | {static}",
    "footer_text": "Majestic RP • {bot_name}"
  }
}
""",
    "hallez_settings.json": """{
  "welcome_enabled": true,
  "welcome_channel_id": 0,
  "rules_text": "Привет, {mention}! 🌟\\n\\nРады видеть тебя на сервере **{server}** — это дом семьи **Hallez FAMQ** на Majestic RP.\\n\\n📜 **Правила сервера:**\\n• Уважай всех участников, без оскорблений и токсичности\\n• Никакой рекламы и спама\\n• Слушай руководство семьи и соблюдай субординацию\\n• Читай закреплённые сообщения в каналах\\n\\n📝 **Как вступить в семью:**\\n1️⃣ Перейди в канал {recruit_channel}\\n2️⃣ Нажми кнопку **«Подать заявку в Hallez FAMQ»** и заполни анкету\\n3️⃣ Дождись рекрутера: он вызовет тебя на обзвон в голосовой канал\\n\\n✨ Желаем отличного настроения и удачи! Мы рады, что ты с нами 💎",
  "remind_enabled": true,
  "remind_hours": 336,
  "remind_mode": "both",
  "remind_channel_id": 0,
  "leave_channel_id": 0,
  "roster_channel_id": 0,
  "roster_message_id": 0,
  "roster_roles": []
}
""",
    "database.py": """import aiosqlite
import os
from typing import Optional, List, Dict, Any

DB_PATH = os.path.join(os.path.dirname(__file__), "hallez_famq.db")

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(\"\"\"
        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            nick TEXT NOT NULL,
            static_id TEXT NOT NULL,
            age INTEGER NOT NULL,
            prev_families TEXT NOT NULL,
            why_hallez TEXT NOT NULL,
            thread_id INTEGER,
            status TEXT DEFAULT 'pending',
            reviewer_id INTEGER,
            verdict_reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            closed_at TIMESTAMP
        )
        \"\"\")

        await db.execute(\"\"\"
        CREATE TABLE IF NOT EXISTS promotions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            nick TEXT NOT NULL,
            static_id TEXT NOT NULL,
            target_rank INTEGER NOT NULL,
            proof_url TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            reviewer_id INTEGER,
            reject_reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            closed_at TIMESTAMP
        )
        \"\"\")

        await db.execute(\"\"\"
        CREATE TABLE IF NOT EXISTS warns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            admin_id INTEGER NOT NULL,
            reason TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            active INTEGER DEFAULT 1
        )
        \"\"\")

        await db.execute(\"\"\"
        CREATE TABLE IF NOT EXISTS family_members (
            user_id INTEGER PRIMARY KEY,
            nick TEXT NOT NULL,
            static_id TEXT NOT NULL,
            rank INTEGER DEFAULT 1,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        \"\"\")

        await db.execute(\"\"\"
        CREATE TABLE IF NOT EXISTS economy (
            user_id INTEGER PRIMARY KEY,
            balance INTEGER NOT NULL DEFAULT 1000,
            last_daily TIMESTAMP,
            total_won INTEGER NOT NULL DEFAULT 0,
            total_lost INTEGER NOT NULL DEFAULT 0
        )
        \"\"\")
        await db.commit()

async def create_application(user_id: int, nick: str, static_id: str, age: int, prev_families: str, why_hallez: str, thread_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            \"\"\"
            INSERT INTO applications (user_id, nick, static_id, age, prev_families, why_hallez, thread_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            \"\"\",
            (user_id, nick, static_id, age, prev_families, why_hallez, thread_id)
        )
        await db.commit()
        return cursor.lastrowid

async def get_application_by_thread(thread_id: int) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM applications WHERE thread_id = ?", (thread_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def update_application_status(app_id: int, status: str, reviewer_id: Optional[int] = None, reason: Optional[str] = None):
    async with aiosqlite.connect(DB_PATH) as db:
        if status in ('accepted', 'rejected'):
            await db.execute(
                \"\"\"
                UPDATE applications 
                SET status = ?, reviewer_id = COALESCE(?, reviewer_id), verdict_reason = ?, closed_at = CURRENT_TIMESTAMP
                WHERE id = ?
                \"\"\",
                (status, reviewer_id, reason, app_id)
            )
        else:
            await db.execute(
                "UPDATE applications SET status = ?, reviewer_id = COALESCE(?, reviewer_id) WHERE id = ?",
                (status, reviewer_id, app_id)
            )
        await db.commit()

async def create_promotion_report(user_id: int, nick: str, static_id: str, target_rank: int, proof_url: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            \"\"\"
            INSERT INTO promotions (user_id, nick, static_id, target_rank, proof_url)
            VALUES (?, ?, ?, ?, ?)
            \"\"\",
            (user_id, nick, static_id, target_rank, proof_url)
        )
        await db.commit()
        return cursor.lastrowid

async def get_promotion_by_id(promo_id: int) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM promotions WHERE id = ?", (promo_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def update_promotion_status(promo_id: int, status: str, reviewer_id: int, reject_reason: Optional[str] = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            \"\"\"
            UPDATE promotions
            SET status = ?, reviewer_id = ?, reject_reason = ?, closed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            \"\"\",
            (status, reviewer_id, reject_reason, promo_id)
        )
        await db.commit()

async def upsert_member(user_id: int, nick: str, static_id: str, rank: int = 1):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            \"\"\"
            INSERT INTO family_members (user_id, nick, static_id, rank)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                nick = excluded.nick,
                static_id = excluded.static_id,
                rank = excluded.rank
            \"\"\",
            (user_id, nick, static_id, rank)
        )
        await db.commit()

async def get_member(user_id: int) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM family_members WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def find_member_by_static(static_id: str) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM family_members WHERE static_id = ?", (static_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def add_warn(user_id: int, admin_id: int, reason: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO warns (user_id, admin_id, reason) VALUES (?, ?, ?)",
            (user_id, admin_id, reason)
        )
        await db.commit()
        return cursor.lastrowid

async def get_active_warns(user_id: int) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM warns WHERE user_id = ? AND active = 1 ORDER BY created_at DESC", (user_id,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

async def remove_warn(warn_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("UPDATE warns SET active = 0 WHERE id = ?", (warn_id,))
        await db.commit()
        return cursor.rowcount > 0

async def get_stats() -> Dict[str, int]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM applications WHERE status = 'pending' OR status = 'in_review'") as cur:
            pending_apps = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM applications WHERE status = 'accepted'") as cur:
            accepted_apps = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM promotions WHERE status = 'pending'") as cur:
            pending_promos = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM warns WHERE active = 1") as cur:
            active_warns = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM family_members") as cur:
            total_members = (await cur.fetchone())[0]
        return {
            "pending_apps": pending_apps,
            "accepted_apps": accepted_apps,
            "pending_promos": pending_promos,
            "active_warns": active_warns,
            "total_members": total_members
        }

DEFAULT_BALANCE = 1000

async def get_balance(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance FROM economy WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
        if row is None:
            await db.execute(
                "INSERT INTO economy (user_id, balance) VALUES (?, ?)",
                (user_id, DEFAULT_BALANCE)
            )
            await db.commit()
            return DEFAULT_BALANCE
        return row[0]

async def change_balance(user_id: int, delta: int) -> int:
    await get_balance(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        if delta >= 0:
            await db.execute(
                "UPDATE economy SET balance = balance + ?, total_won = total_won + ? WHERE user_id = ?",
                (delta, delta, user_id)
            )
        else:
            await db.execute(
                "UPDATE economy SET balance = balance + ?, total_lost = total_lost + ? WHERE user_id = ?",
                (delta, -delta, user_id)
            )
        await db.commit()
        async with db.execute("SELECT balance FROM economy WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
        return row[0] if row else 0

async def set_balance(user_id: int, amount: int):
    await get_balance(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE economy SET balance = ? WHERE user_id = ?", (amount, user_id))
        await db.commit()

async def try_claim_daily(user_id: int, amount: int, cooldown_hours: int = 24) -> Dict[str, Any]:
    import time
    now = int(time.time())
    await get_balance(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT last_daily FROM economy WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
        last_daily = row[0] if row else None
        if last_daily:
            try:
                last_ts = int(last_daily)
            except (TypeError, ValueError):
                last_ts = 0
            elapsed = now - last_ts
            cooldown = cooldown_hours * 3600
            if elapsed < cooldown:
                return {"success": False, "seconds_left": cooldown - elapsed}
        await db.execute(
            "UPDATE economy SET balance = balance + ?, last_daily = ? WHERE user_id = ?",
            (amount, now, user_id)
        )
        await db.commit()
        async with db.execute("SELECT balance FROM economy WHERE user_id = ?", (user_id,)) as cur:
            new_row = await cur.fetchone()
        return {"success": True, "balance": new_row[0] if new_row else amount}

async def get_leaderboard(limit: int = 10) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT user_id, balance, total_won, total_lost FROM economy ORDER BY balance DESC LIMIT ?",
            (limit,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
""",
    "main.py": """import os
import sys
import asyncio
import disnake
from disnake.ext import commands
from dotenv import load_dotenv

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from database import init_db
from utils.checks import load_config
from cogs.recruitment import RecruitLaunchView
from cogs.promotions import PromotionLaunchView
from cogs.events import EventAttendanceView

load_dotenv()

intents = disnake.Intents.default()
intents.members = True

guild_id_str = os.getenv("GUILD_ID", "0")
test_guilds = [int(guild_id_str)] if guild_id_str.isdigit() and int(guild_id_str) > 0 else None

bot = commands.Bot(
    command_prefix=commands.when_mentioned,
    intents=intents,
    test_guilds=test_guilds,
    reload=True
)

COGS = [
    "cogs.recruitment",
    "cogs.promotions",
    "cogs.admin",
    "cogs.events",
    "cogs.profile",
    "cogs.games",
    "cogs.checkers",
    "cogs.hall_tools",
    "cogs.famq_control"
]

@bot.event
async def on_ready():
    bot.add_view(RecruitLaunchView())
    bot.add_view(PromotionLaunchView())
    bot.add_view(EventAttendanceView())

    try:
        await bot._sync_application_commands()
        print("⚡ Слеш-команды успешно синхронизированы с Discord API!", flush=True)
    except Exception as e:
        print(f"⚠️ Ошибка синхронизации команд: {e}", file=sys.stderr, flush=True)

    config = load_config()
    bot_name = config.get("bot_name", "Hallez FAMQ")
    
    activity = disnake.Activity(
        type=disnake.ActivityType.watching,
        name=f"за порядком в {bot_name} | /admin"
    )
    await bot.change_presence(status=disnake.Status.online, activity=activity)

    print("=" * 60, flush=True)
    print(f"⚜️  Бот {bot_name} успешно запущен!", flush=True)
    print(f"👤  Авторизован как: {bot.user} (ID: {bot.user.id})", flush=True)
    print(f"🌐  Серверов: {len(bot.guilds)} | Задержка: {round(bot.latency * 1000)}ms", flush=True)
    for g in bot.guilds:
        print(f"    • Сервер: {g.name} (ID: {g.id}) | Участников: {g.member_count}", flush=True)
    print(f"📁  Загружено модулей: {len(COGS)}", flush=True)
    print("=" * 60, flush=True)

@bot.event
async def on_slash_command_error(inter: disnake.ApplicationCommandInteraction, error: Exception):
    if isinstance(error, commands.CommandNotFound):
        return
    print(f"Ошибка при выполнении команды /{inter.data.name}: {error}", file=sys.stderr, flush=True)
    try:
        if not inter.response.is_done():
            await inter.response.send_message(f"❌ Произошла внутренняя ошибка: `{str(error)}`", ephemeral=True)
        else:
            await inter.followup.send(f"❌ Произошла внутренняя ошибка: `{str(error)}`", ephemeral=True)
    except Exception:
        pass

async def main():
    await init_db()
    
    for cog in COGS:
        try:
            bot.load_extension(cog)
            print(f"✅ Модуль {cog} успешно загружен", flush=True)
        except Exception as e:
            print(f"❌ Ошибка загрузки модуля {cog}: {e}", file=sys.stderr, flush=True)

    token = os.getenv("BOT_TOKEN")
    if not token or token.strip() == "your_bot_token_here":
        print("⚠️  ВНИМАНИЕ: BOT_TOKEN не установлен в файле .env!", flush=True)
        print("👉  Откройте .env и укажите токен вашего бота перед запуском.", flush=True)
        return

    print("🔌 Подключение к Discord Gateway...", flush=True)
    await bot.start(token)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\\n🛑 Бот остановлен администратором.", flush=True)
""",
    "start.bat": """@echo off
chcp 65001 > nul
title Hallez FAMQ Discord Bot

echo ============================================================
echo        ⚜️ ЗАПУСК DISCORD БОТА HALLEZ FAMQ (Majestic RP) ⚜️
echo ============================================================
echo.

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ОШИБКА] Python не найден в системе!
    echo Установите Python 3.10 или новее с python.org и включите "Add Python to PATH".
    pause
    exit /b
)

if not exist ".venv" (
    echo [*] Создаю виртуальное окружение .venv...
    python -m venv .venv
)

echo [*] Установка зависимостей...
.venv\\Scripts\\python.exe -m pip install --upgrade pip >nul 2>&1
.venv\\Scripts\\pip.exe install -r requirements.txt

if not exist ".env" (
    echo [ВНИМАНИЕ] Заполните токен в файле .env!
    pause
    exit /b
)

echo [*] Запуск бота Hallez FAMQ...
.venv\\Scripts\\python.exe main.py
pause
"""
}

for filename, content in files.items():
    with open(filename, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"[+] Создан файл: {filename}")

print("\\nВсе файлы успешно созданы! Удалите старую базу данных codex_famq.db (если она есть) и запустите start.bat")