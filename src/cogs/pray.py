import discord
from discord import app_commands
from discord.ext import commands
from pathlib import Path

FORTUNE_GOD_PATH = Path(__file__).resolve().parents[2] / "FortuneGod.png"


class PrayCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="pray", description="Offer a prayer to the Fortune God")
    async def pray_command(self, interaction: discord.Interaction):
        username = interaction.user.display_name
        await interaction.response.send_message(
            f"Your prayer has been heard **{username}** 🙏",
            file=discord.File(FORTUNE_GOD_PATH),
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(PrayCog(bot))
