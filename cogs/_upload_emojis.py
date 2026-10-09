"""Массовая загрузка эмодзи на сервер из папки.
1) Создай рядом с этим файлом папку  emojis  и положи туда .gif/.png/.webp/.jpg
2) Запусти:  .venv\\Scripts\\python.exe upload_emojis.py
Берёт BOT_TOKEN и GUILD_ID из .env. У бота нужно право «Управление эмодзи и стикерами».
Результат (названия и коды) печатается и сохраняется в emoji_codes.txt
"""
import asyncio, base64, json, os, re, sys

import aiohttp
from dotenv import load_dotenv

BASE = os.path.dirname(os.path.abspath(__file__))
FOLDER = os.path.join(BASE, "emojis")
API = "https://discord.com/api/v10"
MIME = {".gif": "image/gif", ".png": "image/png", ".webp": "image/webp",
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}

load_dotenv(os.path.join(BASE, ".env"), override=True)
TOKEN = os.getenv("BOT_TOKEN", "").strip()
GUILD = (os.getenv("GUILD_ID", "").replace(" ", "").split(",") or [""])[0]


def make_name(filename: str) -> str:
    base = os.path.splitext(filename)[0]
    base = re.sub(r"^\d+-", "", base)            # 2751-number9 -> number9
    base = re.sub(r"[^A-Za-z0-9_]", "_", base)
    return (base if len(base) >= 2 else "e_" + base)[:32]


async def main():
    if not TOKEN or not GUILD.isdigit():
        print("❌ В .env нужны BOT_TOKEN и GUILD_ID"); return
    if not os.path.isdir(FOLDER):
        print(f"❌ Нет папки {FOLDER}\nСоздай папку «emojis» рядом со скриптом и положи туда файлы."); return
    files = sorted(f for f in os.listdir(FOLDER) if os.path.splitext(f)[1].lower() in MIME)
    if not files:
        print("❌ В папке emojis нет картинок."); return

    headers = {"Authorization": f"Bot {TOKEN}"}
    out = []
    async with aiohttp.ClientSession(headers=headers) as s:
        async with s.get(f"{API}/guilds/{GUILD}/emojis") as r:
            if r.status != 200:
                print(f"❌ Не получил список эмодзи (код {r.status}). Проверь токен и GUILD_ID."); return
            existing = {e["name"]: e for e in await r.json()}

        for fn in files:
            path = os.path.join(FOLDER, fn)
            name = make_name(fn)
            if name in existing:
                e = existing[name]
                code = f"<{'a' if e.get('animated') else ''}:{name}:{e['id']}>"
                print(f"⏭  {name}: уже есть  {code}"); out.append(f"{name}\t{code}"); continue
            data = open(path, "rb").read()
            if len(data) > 256 * 1024:
                print(f"⚠️  {fn}: {len(data)//1024} КБ — больше 256 КБ, пропускаю"); continue
            ext = os.path.splitext(fn)[1].lower()
            payload = {"name": name, "image": f"data:{MIME[ext]};base64,{base64.b64encode(data).decode()}"}
            while True:
                async with s.post(f"{API}/guilds/{GUILD}/emojis", json=payload) as r:
                    body = await r.json()
                    if r.status == 429:
                        await asyncio.sleep(float(body.get("retry_after", 5)) + 0.5); continue
                    if r.status in (200, 201):
                        code = f"<{'a' if body.get('animated') else ''}:{body['name']}:{body['id']}>"
                        print(f"✅ {name}  {code}"); out.append(f"{name}\t{code}")
                    else:
                        print(f"❌ {fn}: {body.get('message', body)}")
                    break
            await asyncio.sleep(1.2)

    with open(os.path.join(BASE, "emoji_codes.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    print("\nГотово. Коды сохранены в emoji_codes.txt")

if __name__ == "__main__":
    asyncio.run(main())
