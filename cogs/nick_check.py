import disnake
from disnake.ext import commands
from utils.checks import load_config, save_config, is_admin

class NickCheck(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def should_check(self):
        cfg = load_config()
        return cfg.get("nick_check", {}).get("enabled", True)

    def get_tag(self):
        cfg = load_config()
        return cfg.get("nick_check", {}).get("tag", "Hallez")

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if not self.should_check():
            return
        
        if before.display_name == after.display_name:
            return

        tag = self.get_tag()
        if tag.lower() not in after.display_name.lower():
            try:
                await after.send(f"Слышь, {after.display_name}, ник говно! Смени на нормальный с тегом `{tag}`, а то вылетишь нахуй! 👊")
            except:
                pass

    @commands.slash_command(
        name="nick_check_toggle", 
        description="Включить/Выключить проверку ников (Для руководства)"
    )
    async def nick_check_toggle(self, inter: disnake.ApplicationCommandInteraction):
        if not is_admin(inter.author):
            return await inter.response.send_message("❌ Нет доступа, еблан.", ephemeral=True)

        cfg = load_config()
        if "nick_check" not in cfg:
            cfg["nick_check"] = {"enabled": True, "tag": "Hallez"}
        
        current = cfg["nick_check"].get("enabled", True)
        cfg["nick_check"]["enabled"] = not current
        save_config(cfg)

        status = "ВКЛЮЧЕНА" if not current else "ВЫКЛЮЧЕНА"
        await inter.response.send_message(f"✅ Проверка ников теперь {status}!", ephemeral=True)

    @commands.slash_command(
        name="set_family_tag", 
        description="Изменить тег семьи для проверки ников (Для руководства)"
    )
    async def set_family_tag(self, inter: disnake.ApplicationCommandInteraction, new_tag: str):
        if not is_admin(inter.author):
            return await inter.//_response.send_message("❌ Нет доступа.", ephemeral=True)

        cfg = load_config()
        if "nick_check" not in cfg:
            cfg["nick_check"] = {"enabled": True, "tag": "Hallez"}
        
        cfg["nick_check"]["tag"] = new_tag
        save_config(cfg)

        await inter.response.send_message(f"✅ Тег семьи изменен на: `{new_tag}`", ephemeral=True)

def setup(bot):
    bot.add_cog(NickCheck(bot))
