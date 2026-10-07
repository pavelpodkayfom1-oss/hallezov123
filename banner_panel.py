import json
import os
import re

import aiohttp
import disnake
from disnake.ext import commands

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json")
KEY = "recruit_banner_url"
IMG_RE = re.compile(r"^https?://\S+$")


def _read() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _write(cfg: dict):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


BASE_DIR = os.path.dirname(os.path.dirname(__file__))
FILE_KEY = "recruit_banner_file"
ALLOWED_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp")


def get_banner_file():
    """(путь, имя_файла) загруженного баннера или None."""
    try:
        name = _read().get(FILE_KEY, "")
    except Exception:
        return None
    if name:
        path = os.path.join(BASE_DIR, name)
        if os.path.isfile(path):
            return path, name
    return None


def _remove_file():
    cfg = _read()
    name = cfg.get(FILE_KEY, "")
    if name:
        try:
            os.remove(os.path.join(BASE_DIR, name))
        except OSError:
            pass
    cfg[FILE_KEY] = ""
    _write(cfg)


def get_banner_url() -> str:
    try:
        return _read().get(KEY, "") or ""
    except Exception:
        return ""


def is_admin(user) -> bool:
    cfg = _read()
    if user.id in cfg.get("admin_user_ids", []):
        return True
    roles = {r.id for r in getattr(user, "roles", [])}
    return bool(roles & set(cfg.get("admin_role_ids", [])))


def panel_embed() -> disnake.Embed:
    cfg = _read()
    url = cfg.get(KEY, "")
    fname = cfg.get(FILE_KEY, "")
    color = int(cfg.get("embed_color", "0x990000"), 16)
    e = disnake.Embed(
        title="🖼️ Баннер заявок",
        description=(
            "Картинка отображается **внизу карточки анкеты** кандидата.\n\n"
            "**Сейчас:** "
            + (f"📎 загруженный файл `{fname}`" if fname else (f"[ссылка]({url})" if url else "_не задан_"))
            + "\n\nЧтобы загрузить файл, используй `/banner image:<файл>`."
        ),
        color=color,
    )
    if fname:
        e.set_image(url=f"attachment://{fname}")
    elif url:
        e.set_image(url=url)
    return e


OG_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\'][^>]+content=["\']([^"\']+)["\']'
    r'|<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\']',
    re.I,
)


async def resolve_image(url: str):
    """Возвращает (прямая_ссылка, ошибка). Если дали ссылку на страницу/альбом,
    достаёт из неё картинку (og:image)."""
    if not IMG_RE.match(url):
        return None, "Ссылка должна начинаться с http:// или https://"
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10),
            headers={"User-Agent": "Mozilla/5.0"},
        ) as s:
            async with s.get(url) as r:
                if r.status != 200:
                    return None, f"Ссылка не открывается (код {r.status})."
                ctype = r.headers.get("Content-Type", "")
                if ctype.startswith("image/"):
                    return url, None
                if "text/html" in ctype:
                    html = await r.text(errors="ignore")
                    m = OG_RE.search(html)
                    if m:
                        img = (m.group(1) or m.group(2)).replace("&amp;", "&")
                        async with s.get(img) as r2:
                            if r2.status == 200 and r2.headers.get("Content-Type", "").startswith("image/"):
                                return img, None
                return None, "По ссылке не нашёл картинку. Открой картинку, нажми ПКМ → «Копировать адрес изображения»."
    except Exception:
        return None, "Не удалось открыть ссылку."


class BannerModal(disnake.ui.Modal):
    def __init__(self):
        super().__init__(
            title="Баннер для заявок",
            components=[disnake.ui.TextInput(
                label="Ссылка на картинку или альбом", custom_id="url",
                placeholder="https://i.imgur.com/....png", style=disnake.TextInputStyle.short,
                max_length=500)],
        )

    async def callback(self, inter: disnake.ModalInteraction):
        url = inter.text_values["url"].strip()
        await inter.response.defer(ephemeral=True)
        direct, err = await resolve_image(url)
        if err:
            return await inter.edit_original_response(content=f"❌ {err}")
        _remove_file()
        cfg = _read()
        cfg[KEY] = direct
        _write(cfg)
        await inter.edit_original_response(content="✅ Баннер сохранён, он появится во всех новых заявках.", embed=panel_embed())


class BannerView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @disnake.ui.button(label="Задать ссылку", style=disnake.ButtonStyle.primary, emoji="🔗")
    async def set_url(self, button, inter: disnake.MessageInteraction):
        if not is_admin(inter.author):
            return await inter.response.send_message("⛔ Нет доступа.", ephemeral=True)
        await inter.response.send_modal(BannerModal())

    @disnake.ui.button(label="Убрать баннер", style=disnake.ButtonStyle.danger, emoji="🗑️")
    async def clear(self, button, inter: disnake.MessageInteraction):
        if not is_admin(inter.author):
            return await inter.response.send_message("⛔ Нет доступа.", ephemeral=True)
        _remove_file()
        cfg = _read()
        cfg[KEY] = ""
        _write(cfg)
        await inter.response.edit_message(embed=panel_embed(), view=self, attachments=[])


class BannerPanel(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.slash_command(name="banner", description="Панель баннера для заявок")
    async def banner(
        self,
        inter: disnake.ApplicationCommandInteraction,
        image: disnake.Attachment = commands.Param(default=None, description="Картинка-баннер (png/jpg/gif/webp)"),
    ):
        if not is_admin(inter.author):
            return await inter.response.send_message("⛔ Нет доступа.", ephemeral=True)

        if image is not None:
            ext = os.path.splitext(image.filename)[1].lower()
            if ext not in ALLOWED_EXT or not (image.content_type or "").startswith("image/"):
                return await inter.response.send_message("❌ Нужен файл-картинка: png, jpg, gif или webp.", ephemeral=True)
            await inter.response.defer(ephemeral=True)
            _remove_file()
            name = f"banner_image{ext}"
            await image.save(os.path.join(BASE_DIR, name))
            cfg = _read()
            cfg[FILE_KEY] = name
            cfg[KEY] = ""
            _write(cfg)
            f = disnake.File(os.path.join(BASE_DIR, name), filename=name)
            return await inter.edit_original_response(
                content="✅ Баннер загружен, он будет внизу всех новых заявок.",
                embed=panel_embed(), view=BannerView(), file=f)

        found = get_banner_file()
        kwargs = {}
        if found:
            kwargs["file"] = disnake.File(found[0], filename=found[1])
        await inter.response.send_message(embed=panel_embed(), view=BannerView(), ephemeral=True, **kwargs)


    @commands.slash_command(name="recruit_panel", description="Опубликовать панель набора с баннером внизу")
    async def recruit_panel(
        self,
        inter: disnake.ApplicationCommandInteraction,
        channel: disnake.TextChannel = commands.Param(default=None, description="Куда отправить (по умолчанию канал набора из config)"),
        message_id: str = commands.Param(default=None, description="ID старой панели, чтобы обновить её вместо новой"),
    ):
        if not is_admin(inter.author):
            return await inter.response.send_message("⛔ Нет доступа.", ephemeral=True)
        # импорт здесь, чтобы не было циклического импорта с recruitment.py
        from cogs.recruitment import RecruitLaunchView
        from utils.checks import load_config, get_text
        from utils.embeds import base_embed

        await inter.response.defer(ephemeral=True)
        cfg = load_config()
        if channel is None:
            cid = cfg.get("channels", {}).get("recruitment_channel_id", 0)
            channel = inter.guild.get_channel(cid) or inter.channel

        emb = base_embed(
            get_text("recruit_panel_title", "Заявление для вступления"),
            get_text("recruit_panel_desc", "Нажмите кнопку ниже, чтобы подать заявку."),
            guild=inter.guild,
        )
        kwargs = {}
        found = get_banner_file()
        if found:
            emb.set_image(url=f"attachment://{found[1]}")
            kwargs["file"] = disnake.File(found[0], filename=found[1])
        elif get_banner_url():
            emb.set_image(url=get_banner_url())

        if message_id and message_id.isdigit():
            try:
                msg = await channel.fetch_message(int(message_id))
                await msg.edit(embed=emb, view=RecruitLaunchView(), attachments=[], **kwargs)
                return await inter.edit_original_response(content=f"✅ Панель обновлена: {msg.jump_url}")
            except Exception as e:
                return await inter.edit_original_response(content=f"❌ Не удалось обновить сообщение: `{e}`")

        msg = await channel.send(embed=emb, view=RecruitLaunchView(), **kwargs)
        await inter.edit_original_response(content=f"✅ Панель опубликована: {msg.jump_url}\nСтарую панель можно удалить.")


    @commands.slash_command(name="emoji_list", description="Показать коды всех эмодзи сервера")
    async def emoji_list(self, inter: disnake.ApplicationCommandInteraction):
        if not is_admin(inter.author):
            return await inter.response.send_message("⛔ Нет доступа.", ephemeral=True)
        emojis = inter.guild.emojis
        if not emojis:
            return await inter.response.send_message(
                "На сервере пока нет своих эмодзи. Загрузи их: Настройки сервера → Эмодзи.", ephemeral=True)
        lines = [f"{e}  `{e.name}`  →  `{e}`" for e in emojis]
        chunks, cur = [], ""
        for ln in lines:
            if len(cur) + len(ln) + 1 > 1900:
                chunks.append(cur)
                cur = ""
            cur += ln + "\n"
        chunks.append(cur)
        await inter.response.send_message(chunks[0], ephemeral=True)
        for c in chunks[1:]:
            await inter.followup.send(c, ephemeral=True)


    @commands.slash_command(name="emoji_add", description="Загрузить эмодзи на сервер по ссылке (emoji.gg и др.)")
    async def emoji_add(
        self,
        inter: disnake.ApplicationCommandInteraction,
        url: str = commands.Param(description="Ссылка на картинку, напр. https://cdn3.emoji.gg/emojis/61702-skull.gif"),
        name: str = commands.Param(default=None, description="Название эмодзи (латиница, цифры, _)"),
    ):
        if not is_admin(inter.author):
            return await inter.response.send_message("⛔ Нет доступа.", ephemeral=True)
        await inter.response.defer(ephemeral=True)

        url = url.strip()
        # если вставили HTML-код с <img src="...">, достаём ссылку
        m = re.search(r'src=["\']([^"\']+)["\']', url)
        if m:
            url = m.group(1)
        if not IMG_RE.match(url):
            return await inter.edit_original_response(content="❌ Нужна прямая ссылка на картинку (https://...).")

        if not name:
            base = os.path.splitext(url.split("?")[0].rstrip("/").split("/")[-1])[0]
            name = re.sub(r"^\d+-", "", base)
        name = re.sub(r"[^A-Za-z0-9_]", "_", name)[:32]
        if len(name) < 2:
            name = "emoji_" + name

        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15),
                                             headers={"User-Agent": "Mozilla/5.0"}) as s:
                async with s.get(url) as r:
                    if r.status != 200:
                        return await inter.edit_original_response(content=f"❌ Ссылка не открывается (код {r.status}).")
                    data = await r.read()
        except Exception:
            return await inter.edit_original_response(content="❌ Не удалось скачать картинку.")

        if len(data) > 256 * 1024:
            return await inter.edit_original_response(
                content=f"❌ Файл {len(data)//1024} КБ, а лимит Discord для эмодзи 256 КБ. Нужна версия поменьше.")
        try:
            emoji = await inter.guild.create_custom_emoji(name=name, image=data, reason=f"/emoji_add от {inter.author}")
        except disnake.Forbidden:
            return await inter.edit_original_response(content="❌ У бота нет права **«Управление эмодзи и стикерами»**.")
        except disnake.HTTPException as e:
            return await inter.edit_original_response(content=f"❌ Discord отказал: `{e.text}` (возможно, закончились слоты эмодзи).")
        await inter.edit_original_response(content=f"✅ Добавлено: {emoji}\nКод для текстов: `{emoji}`")


def setup(bot):
    bot.add_cog(BannerPanel(bot))
