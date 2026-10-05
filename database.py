import aiosqlite
import os
from typing import Optional, List, Dict, Any

DB_PATH = os.path.join(os.path.dirname(__file__), "hallez_famq.db")

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
