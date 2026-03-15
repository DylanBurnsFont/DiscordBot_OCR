import os
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands

from src.database import (
    _connect,
    create_scan,
    get_guild_by_name,
    _week_dates,
    _score_to_float,
)
from src.guild_context import get_guild_from_channel_category


def _is_unlimited_user(interaction: discord.Interaction) -> bool:
    """Returns True for the bot owner or anyone with a role listed in MI_UNLIMITED_ROLE_IDS."""
    owner_id = os.getenv("DISCORD_OWNER_ID", "")
    if owner_id and str(interaction.user.id) == owner_id:
        return True
    
    unlimited_ids = {
        rid.strip()
        for rid in os.getenv("MI_UNLIMITED_ROLE_IDS", "").split(",")
        if rid.strip()
    }
    if unlimited_ids and isinstance(interaction.user, discord.Member):
        return any(str(role.id) in unlimited_ids for role in interaction.user.roles)
    return False


def _get_weekdays_choices() -> list[app_commands.Choice[str]]:
    """Generate choices for the current week's days."""
    week_dates = _week_dates()
    choices = []
    
    for i, date_str in enumerate(week_dates):
        # Convert DD_MM_YYYY to readable format
        day, month, year = date_str.split("_")
        date_obj = datetime(int(year), int(month), int(day))
        weekday_name = date_obj.strftime("%A")  # Monday, Tuesday, etc.
        readable_date = date_obj.strftime("%m/%d")  # MM/DD format
        
        choices.append(app_commands.Choice(
            name=f"{weekday_name} ({readable_date})",
            value=date_str
        ))
    
    return choices


def set_player_score(player_name: str, score: str, scan_date: str, guild_id: int | None, submitted_by: str) -> bool:
    """
    Set a score for a specific player on a specific date.
    Always updates the score regardless of whether it's higher or lower than existing.
    Returns True if successful, False if failed.
    """
    with _connect() as con:
        # Check if a score already exists for this player on this date
        existing = con.execute(
            """
            SELECT id, scan_id, rank, score, guild_id
            FROM mi_scores
            WHERE player_name = ? AND scan_date = ?
            """,
            (player_name, scan_date),
        ).fetchone()
        
        if existing:
            # Update existing score
            scan_id = existing["scan_id"]
            # Preserve the original guild_id from the existing entry
            original_guild_id = existing["guild_id"]
            
            # Get or create player_id lookup
            player_rows = con.execute("SELECT id, username FROM players").fetchall()
            name_to_player_id = {r["username"]: r["id"] for r in player_rows}
            player_id = name_to_player_id.get(player_name)
            
            con.execute(
                """
                UPDATE mi_scores
                SET score = ?, player_id = ?
                WHERE id = ?
                """,
                (score, player_id, existing["id"]),
            )
            
            # Now we need to recalculate ranks for all scores on this date
            _recalculate_ranks_for_date(con, scan_date, original_guild_id)
            return True
        else:
            # Create new scan if needed, then insert new score
            scan_id = create_scan(submitted_by, scan_date)
            
            # Get player_id lookup
            player_rows = con.execute("SELECT id, username FROM players").fetchall()
            name_to_player_id = {r["username"]: r["id"] for r in player_rows}
            player_id = name_to_player_id.get(player_name)
            
            # Insert new score with temporary rank (we'll fix it below)
            con.execute(
                """
                INSERT INTO mi_scores (scan_id, scan_date, rank, player_name, score, player_id, guild_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (scan_id, scan_date, 1, player_name, score, player_id, guild_id),
            )
            
            # Recalculate ranks for all scores on this date
            _recalculate_ranks_for_date(con, scan_date, guild_id)
            return True


def _recalculate_ranks_for_date(con, scan_date: str, guild_id: int | None):
    """Recalculate ranks for all scores on a specific date, sorted by score descending."""
    # Get all scores for this date
    if guild_id:
        scores = con.execute(
            """
            SELECT id, player_name, score
            FROM mi_scores
            WHERE scan_date = ? AND guild_id = ?
            ORDER BY score DESC
            """,
            (scan_date, guild_id),
        ).fetchall()
    else:
        scores = con.execute(
            """
            SELECT id, player_name, score
            FROM mi_scores
            WHERE scan_date = ?
            ORDER BY score DESC
            """,
            (scan_date,),
        ).fetchall()
    
    # Sort by score value (highest first)
    sorted_scores = sorted(scores, key=lambda x: _score_to_float(x["score"]), reverse=True)
    
    # Update ranks
    for rank, score_row in enumerate(sorted_scores, start=1):
        con.execute(
            "UPDATE mi_scores SET rank = ? WHERE id = ?",
            (rank, score_row["id"]),
        )


class SetScoreCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="set-score",
        description="Set a score for a specific player on a specific day (owner/unlimited users only)"
    )
    @app_commands.describe(
        player_name="The exact in-game player name",
        score="The score (e.g., 1.5B, 500M, 750K)",
        day="Which day of the current week to set the score for"
    )
    @app_commands.choices(day=_get_weekdays_choices())
    async def set_score(
        self,
        interaction: discord.Interaction,
        player_name: str,
        score: str,
        day: str
    ):
        # Check permissions
        if not _is_unlimited_user(interaction):
            await interaction.response.send_message(
                "❌ You don't have permission to use this command.", 
                ephemeral=True
            )
            return
        
        # Validate score format
        if not score or _score_to_float(score) == 0.0:
            await interaction.response.send_message(
                "❌ Invalid score format. Use formats like: 1.5B, 500M, 750K, 50000", 
                ephemeral=True
            )
            return
        
        # Get guild context
        guild_name = get_guild_from_channel_category(interaction.channel)
        guild_id = None
        if guild_name:
            guild_row = get_guild_by_name(guild_name)
            if guild_row:
                guild_id = guild_row["id"]
        
        # Set the score
        try:
            success = set_player_score(
                player_name=player_name.strip(),
                score=score.strip().upper(),
                scan_date=day,
                guild_id=guild_id,
                submitted_by=str(interaction.user.id)
            )
            
            if success:
                # Convert date back to readable format for confirmation
                day_parts = day.split("_")
                date_obj = datetime(int(day_parts[2]), int(day_parts[1]), int(day_parts[0]))
                readable_date = date_obj.strftime("%A, %B %d")
                
                guild_info = f" in **{guild_name}**" if guild_name else ""
                await interaction.response.send_message(
                    f"✅ Set score for **{player_name}** to **{score}** on {readable_date}{guild_info}.",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "❌ Failed to set score. Please try again.", 
                    ephemeral=True
                )
        except Exception as e:
            await interaction.response.send_message(
                f"❌ Error setting score: {str(e)}", 
                ephemeral=True
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(SetScoreCog(bot))