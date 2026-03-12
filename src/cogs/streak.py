import discord
from discord import app_commands
from discord.ext import commands

from src.database import get_player_by_discord_id, get_streak, get_guild_streaks, get_guild_by_name


class StreakCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="user-streak", description="Show your current consecutive-day submission streak")
    async def user_streak_command(self, interaction: discord.Interaction):
        player = get_player_by_discord_id(str(interaction.user.id))
        if not player:
            await interaction.response.send_message(
                "You are not registered. Use `/register` to get started.",
                ephemeral=True,
            )
            return

        streak = get_streak(str(interaction.user.id))
        flame = "🔥" if streak > 0 else "❄️"
        await interaction.response.send_message(
            f"{flame} **{player['username']}** has a streak of **{streak}** consecutive day(s).",
            ephemeral=True,
        )

    @app_commands.command(name="streak", description="Show the submission streaks for all players in your guild")
    async def streak_command(self, interaction: discord.Interaction):
        player = get_player_by_discord_id(str(interaction.user.id))
        if not player:
            await interaction.response.send_message(
                "You are not registered. Use `/register` to get started.",
                ephemeral=True,
            )
            return

        if not player["game_guild_id"]:
            await interaction.response.send_message(
                "You are not in a guild. Re-register with `/register` and select a guild.",
                ephemeral=True,
            )
            return

        # Resolve guild name from id
        guild_row = None
        from src.database import _connect
        with _connect() as con:
            guild_row = con.execute(
                "SELECT name FROM game_guilds WHERE id = ?", (player["game_guild_id"],)
            ).fetchone()

        if not guild_row:
            await interaction.response.send_message("Could not find your guild.", ephemeral=True)
            return

        guild_name = guild_row["name"]
        streaks = get_guild_streaks(guild_name)

        if not streaks:
            await interaction.response.send_message(
                f"No players found in guild **{guild_name}**.",
                ephemeral=True,
            )
            return

        lines = [f"🔥 **{guild_name}** — Submission Streaks\n"]
        for i, entry in enumerate(streaks, start=1):
            flame = "🔥" if entry["streak"] > 0 else "❄️"
            lines.append(f"`{i}.` {flame} **{entry['username']}** — {entry['streak']} day(s)")

        await interaction.response.send_message("\n".join(lines), ephemeral=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(StreakCog(bot))
