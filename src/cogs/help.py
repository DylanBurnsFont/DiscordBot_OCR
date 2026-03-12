import discord
from discord import app_commands
from discord.ext import commands


HELP_EMBED = discord.Embed(
    title="📖 Bot Commands",
    color=discord.Color.blurple(),
)
HELP_EMBED.add_field(
    name="🔧 Setup",
    value=(
        "`/register` — Register your in-game name and guild so the bot can track your scores."
    ),
    inline=False,
)
HELP_EMBED.add_field(
    name="📸 Monster Invasion",
    value=(
        "`/mi` — Upload 1–5 leaderboard screenshots. The bot runs OCR and saves everyone's scores.\n"
        "**Right-click a message → Apps → Process MI** — Post a message with all your screenshots attached, "
        "then use this to process them all at once without filling in slots one by one.\n"
        "• Regular users are limited to **1 scan per day**.\n"
        "• Guild moderators have **unlimited** scans.\n"
        "• If your score was already recorded today, you'll be asked if you want to update it."
    ),
    inline=False,
)
HELP_EMBED.add_field(
    name="🔥 Streaks",
    value=(
        "`/user-streak` — See your own consecutive-day submission streak.\n"
        "`/streak` — See the streaks for every player in your guild, sorted by longest streak."
    ),
    inline=False,
)
HELP_EMBED.add_field(
    name="🛠️ Other",
    value=(
        "`/ping` — Check that the bot is online.\n"
        "`/help` — Show this message.\n"
        "For questions, feedback or issues, contact **coomer314** on the Empire Discord server."
    ),
    inline=False,
)
HELP_EMBED.set_footer(text="All responses are ephemeral (only visible to you) unless noted otherwise")


class HelpCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="help", description="Show a list of available commands")
    async def help_command(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=HELP_EMBED, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(HelpCog(bot))
