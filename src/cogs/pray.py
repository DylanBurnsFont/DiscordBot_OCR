import discord
from discord import app_commands
from discord.ext import commands
from pathlib import Path
import random
import os

FORTUNE_GOD_PATH = Path(__file__).resolve().parents[2] / "PrayImages"
images = os.listdir(FORTUNE_GOD_PATH)


class PrayCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="pray", description="Offer a prayer to the Fortune God")
    async def pray_command(self, interaction: discord.Interaction):
        username = interaction.user.display_name
        randomIndex = random.randint(0, len(images) - 1)
        await interaction.response.send_message(
            f"🙏 Your prayer has been heard **{username}** 🙏",
            file=discord.File(FORTUNE_GOD_PATH / images[randomIndex]),
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(PrayCog(bot))
