import aiosqlite
import os
from typing import Optional, List, Dict, Any

DB_PATH = os.path.join(os.path.dirname(__file__), "hallez_famq.db")

_ACCEPT_HOOKS: Dict[str, Any] = {}

def register_accept_hook(name: str, fn):
    """Регистрирует async-функцию fn(app_id, reviewer_id), которая вызывается при принятии заявки."""
    _ACCEPT_HOOKS[name] = fn

def unregister_accept_hook(name: str):
    _ACCEPT_HOOKS.pop(name, None)

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
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
        """)

        await db.execute("""
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
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS warns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            admin_id INTEGER NOT NULL,
            reason TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            active INTEGER DEFAULT 1
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS family_members (
            user_id INTEGER PRIMARY KEY,
            nick TEXT NOT NULL,
            static_id TEXT NOT NULL,
            rank INTEGER DEFAULT 1,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS economy (
            user_id INTEGER PRIMARY KEY,
            balance INTEGER NOT NULL DEFAULT 1000,
            last_daily TIMESTAMP,
            total_won INTEGER NOT NULL DEFAULT 0,
            total_lost INTEGER NOT NULL DEFAULT 0
        )
        """)
        await _init_loyalty(db)
        await db.commit()

async def create_application(user_id: int, nick: str, static_id: str, age: int, prev_families: str, why_codex: str, thread_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO applications (user_id, nick, static_id, age, prev_families, why_hallez, thread_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, nick, static_id, age, prev_families, why_codex, thread_id)
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
                """
                UPDATE applications 
                SET status = ?, reviewer_id = COALESCE(?, reviewer_id), verdict_reason = ?, closed_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (status, reviewer_id, reason, app_id)
            )
        else:
            await db.execute(
                "UPDATE applications SET status = ?, reviewer_id = COALESCE(?, reviewer_id) WHERE id = ?",
                (status, reviewer_id, app_id)
            )
        await db.commit()
    if status == 'accepted':
        for _name, _hook in list(_ACCEPT_HOOKS.items()):
            try:
                await _hook(app_id, reviewer_id)
            except Exception as e:
                import sys
                print(f"[hook {_name}] ошибка: {e}", file=sys.stderr, flush=True)

async def create_promotion_report(user_id: int, nick: str, static_id: str, target_rank: int, proof_url: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO promotions (user_id, nick, static_id, target_rank, proof_url)
            VALUES (?, ?, ?, ?, ?)
            """,
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
            """
            UPDATE promotions
            SET status = ?, reviewer_id = ?, reject_reason = ?, closed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, reviewer_id, reject_reason, promo_id)
        )
        await db.commit()

async def upsert_member(user_id: int, nick: str, static_id: str, rank: int = 1):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO family_members (user_id, nick, static_id, rank)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                nick = excluded.nick,
                static_id = excluded.static_id,
                rank = excluded.rank
            """,
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


# ───────────────────────── БАЛЛЫ РЕКРУТОВ / ПРИГЛАШЕНИЯ / МАГАЗИН ─────────────────────────

async def _init_loyalty(db):
    await db.execute("""
    CREATE TABLE IF NOT EXISTS rp_points (
        user_id INTEGER PRIMARY KEY,
        points INTEGER NOT NULL DEFAULT 0,
        earned INTEGER NOT NULL DEFAULT 0,
        spent INTEGER NOT NULL DEFAULT 0
    )""")
    await db.execute("""
    CREATE TABLE IF NOT EXISTS rp_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        delta INTEGER NOT NULL,
        reason TEXT NOT NULL,
        ref TEXT UNIQUE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    await db.execute("""
    CREATE TABLE IF NOT EXISTS rp_referrals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        inviter_id INTEGER NOT NULL,
        inviter_nick TEXT NOT NULL,
        inviter_static TEXT NOT NULL,
        invitee_nick TEXT NOT NULL,
        invitee_static TEXT NOT NULL,
        proof TEXT NOT NULL,
        channel_id INTEGER,
        message_id INTEGER,
        status TEXT DEFAULT 'pending',
        app_id INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        closed_at TIMESTAMP
    )""")
    await db.execute("""
    CREATE TABLE IF NOT EXISTS rp_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        item_key TEXT NOT NULL,
        item_name TEXT NOT NULL,
        cost INTEGER NOT NULL,
        status TEXT DEFAULT 'pending',
        channel_id INTEGER,
        message_id INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        closed_at TIMESTAMP
    )""")
    await db.execute("""
    CREATE TABLE IF NOT EXISTS rp_answers (
        app_id INTEGER PRIMARY KEY,
        data TEXT NOT NULL
    )""")


async def get_application_by_id(app_id: int) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM applications WHERE id = ?", (app_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def get_open_application_by_user(user_id: int) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM applications WHERE user_id = ? AND status NOT IN ('accepted','rejected') ORDER BY id DESC LIMIT 1",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def get_accepted_application_by_static(static_id: str) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM applications WHERE static_id = ? AND status = 'accepted' ORDER BY id DESC LIMIT 1",
            (static_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def save_answers(app_id: int, data_json: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO rp_answers (app_id, data) VALUES (?, ?)", (app_id, data_json))
        await db.commit()

async def get_answers(app_id: int) -> Optional[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT data FROM rp_answers WHERE app_id = ?", (app_id,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def get_points(user_id: int) -> Dict[str, int]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT points, earned, spent FROM rp_points WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
    if not row:
        return {"points": 0, "earned": 0, "spent": 0}
    return {"points": row[0], "earned": row[1], "spent": row[2]}

async def add_points(user_id: int, delta: int, reason: str, ref: Optional[str] = None) -> Optional[int]:
    """Начисляет баллы. Если ref уже встречался — ничего не делает и возвращает None (защита от дублей)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT OR IGNORE INTO rp_log (user_id, delta, reason, ref) VALUES (?, ?, ?, ?)",
            (user_id, delta, reason, ref)
        )
        if cur.rowcount == 0:
            await db.commit()
            return None
        await db.execute(
            """
            INSERT INTO rp_points (user_id, points, earned) VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                points = points + excluded.points,
                earned = earned + excluded.earned
            """,
            (user_id, delta, max(delta, 0))
        )
        await db.commit()
        async with db.execute("SELECT points FROM rp_points WHERE user_id = ?", (user_id,)) as c2:
            row = await c2.fetchone()
        return row[0] if row else delta

async def spend_points(user_id: int, cost: int, reason: str, ref: Optional[str] = None) -> Optional[int]:
    """Списывает баллы, если хватает. Возвращает новый баланс или None."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "UPDATE rp_points SET points = points - ?, spent = spent + ? WHERE user_id = ? AND points >= ?",
            (cost, cost, user_id, cost)
        )
        if cur.rowcount == 0:
            await db.commit()
            return None
        await db.execute(
            "INSERT INTO rp_log (user_id, delta, reason, ref) VALUES (?, ?, ?, ?)",
            (user_id, -cost, reason, ref)
        )
        await db.commit()
        async with db.execute("SELECT points FROM rp_points WHERE user_id = ?", (user_id,)) as c2:
            row = await c2.fetchone()
        return row[0] if row else 0

async def refund_points(user_id: int, amount: int, reason: str, ref: Optional[str] = None) -> Optional[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT OR IGNORE INTO rp_log (user_id, delta, reason, ref) VALUES (?, ?, ?, ?)",
            (user_id, amount, reason, ref)
        )
        if cur.rowcount == 0:
            await db.commit()
            return None
        await db.execute(
            """
            INSERT INTO rp_points (user_id, points, spent) VALUES (?, ?, 0)
            ON CONFLICT(user_id) DO UPDATE SET
                points = points + ?,
                spent = MAX(spent - ?, 0)
            """,
            (user_id, amount, amount, amount)
        )
        await db.commit()
        async with db.execute("SELECT points FROM rp_points WHERE user_id = ?", (user_id,)) as c2:
            row = await c2.fetchone()
        return row[0] if row else amount

async def points_log(user_id: int, limit: int = 5) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT delta, reason, created_at FROM rp_log WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit)
        ) as cursor:
            return [dict(r) for r in await cursor.fetchall()]

async def points_top(limit: int = 10) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT user_id, points, earned FROM rp_points WHERE earned > 0 ORDER BY earned DESC LIMIT ?",
            (limit,)
        ) as cursor:
            return [dict(r) for r in await cursor.fetchall()]


async def create_referral(inviter_id: int, inviter_nick: str, inviter_static: str,
                          invitee_nick: str, invitee_static: str, proof: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT INTO rp_referrals (inviter_id, inviter_nick, inviter_static, invitee_nick, invitee_static, proof)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (inviter_id, inviter_nick, inviter_static, invitee_nick, invitee_static, proof)
        )
        await db.commit()
        return cur.lastrowid

async def set_referral_message(ref_id: int, channel_id: int, message_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE rp_referrals SET channel_id = ?, message_id = ? WHERE id = ?", (channel_id, message_id, ref_id))
        await db.commit()

async def get_referral_by_message(message_id: int) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM rp_referrals WHERE message_id = ?", (message_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def get_referral(ref_id: int) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM rp_referrals WHERE id = ?", (ref_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def find_active_referral_by_static(static_id: str, statuses=('pending', 'verified', 'paid')) -> Optional[Dict[str, Any]]:
    marks = ",".join("?" for _ in statuses)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"SELECT * FROM rp_referrals WHERE invitee_static = ? AND status IN ({marks}) ORDER BY id DESC LIMIT 1",
            (static_id, *statuses)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def update_referral_status(ref_id: int, status: str, app_id: Optional[int] = None):
    async with aiosqlite.connect(DB_PATH) as db:
        closed = status in ('paid', 'rejected')
        await db.execute(
            "UPDATE rp_referrals SET status = ?, app_id = COALESCE(?, app_id), "
            "closed_at = CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE closed_at END WHERE id = ?",
            (status, app_id, 1 if closed else 0, ref_id)
        )
        await db.commit()


async def create_order(user_id: int, item_key: str, item_name: str, cost: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO rp_orders (user_id, item_key, item_name, cost) VALUES (?, ?, ?, ?)",
            (user_id, item_key, item_name, cost)
        )
        await db.commit()
        return cur.lastrowid

async def set_order_message(order_id: int, channel_id: int, message_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE rp_orders SET channel_id = ?, message_id = ? WHERE id = ?", (channel_id, message_id, order_id))
        await db.commit()

async def get_order_by_message(message_id: int) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM rp_orders WHERE message_id = ?", (message_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def update_order_status(order_id: int, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE rp_orders SET status = ?, closed_at = CURRENT_TIMESTAMP WHERE id = ?", (status, order_id))
        await db.commit()
