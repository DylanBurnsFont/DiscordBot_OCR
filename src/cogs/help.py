import discord
from discord import app_commands
from discord.ext import commands
from pathlib import Path

HELP_PDF = Path(__file__).resolve().parents[2] / "docs" / "detailedHelp.pdf"

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
    name="📊 Score Tracking",
    value=(
        "`/guild-scores today` — See today's scores for your guild.\n"
        "`/guild-scores week` — See weekly scores for your guild.\n"
        "`/guild-scores month` — See monthly scores for your guild.\n"
        "`/guild-attendance` — Same as above, standalone command with heat chart visualization.\n"
        "`/guild-damage-report` — Generate weekly damage heatmap showing daily scores with color-coded visualization.\n"
        "`/manual-report [guild]` — Manually trigger a damage heatmap report.\n"
        "`/user-score today` — See your score for today.\n"
        "`/user-score week` — See your weekly scores.\n"
        "`/user-score month` — See your monthly scores.\n"
        "`/add-correction` — Submit a name correction. All instances of incorrect name will be substituted with the corrected version.\n"
    ),
    inline=False,
)
HELP_EMBED.add_field(
    name="🛠️ Other",
    value=(
        "`/ping` — Check that the bot is online.\n"
        "`/help` — Show this message.\n"
        "`/help-pdf` — Send the detailed PDF for usage and help.\n"
        "For questions, feedback or issues, contact **coomer314** on the Empire Discord server."
    ),
    inline=False,
)
HELP_EMBED.add_field(
    name="👑 Admin Commands",
    value=(
        "`/guild-status` — Show multi-guild configuration status (requires admin permissions).\n"
        "`/set-score` — Set a user's score (requires admin permissions)."
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

    @app_commands.command(name="help-pdf", description="Send the detailed PDF help")
    async def help_pdf(self, interaction: discord.Interaction):
        if not HELP_PDF.exists():
            await interaction.response.send_message("Help PDF not available.", ephemeral=True)
            return
        # acknowledge privately, then post the file to channel
        await interaction.response.send_message("Uploading detailed help", ephemeral=True)
        await interaction.followup.send(file=discord.File(str(HELP_PDF)), ephemeral=True)
async def setup(bot: commands.Bot):
    await bot.add_cog(HelpCog(bot))
