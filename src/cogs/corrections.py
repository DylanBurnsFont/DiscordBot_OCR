import os
import json
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from src.guild_context import get_appropriate_guild_for_interaction, format_guild_context_message
from src import mi_utils
from src.database import _connect, get_guild_by_name

from src.cogs.setscore import _recalculate_ranks_for_date


class CorrectionsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="add-correction", description="Add an OCR name correction for this guild/context")
    @app_commands.describe(incorrect="Name as detected (incorrect)", corrected="Correct in-game name")
    async def add_correction(self, interaction: discord.Interaction, incorrect: str, corrected: str):
        # Determine guild context (e.g., AboveAll, MoeCafe) from channel/category
        guild_name = get_appropriate_guild_for_interaction(interaction)
        if guild_name:
            display = format_guild_context_message(guild_name)
        else:
            display = "Global corrections"

        # Determine file path to save correction (repo-root / corrections)
        base = str(Path(__file__).resolve().parents[2] / "corrections")
        os.makedirs(base, exist_ok=True)
        if guild_name:
            filename = f"{guild_name}.json"
        else:
            filename = "corrections.json"

        path = os.path.join(base, filename)

        try:
            # Load existing corrections
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            else:
                data = {}

            # Add/overwrite the correction
            data[incorrect] = corrected

            # Write back
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=4)

            # Clear in-memory cache so changes take effect immediately
            try:
                mi_utils._GUILD_CORRECTIONS_CACHE.clear()
            except Exception:
                pass

            # Update database rows: replace player_name occurrences
            with _connect() as con:
                guild_row = None
                guild_id = None
                if guild_name:
                    guild_row = get_guild_by_name(guild_name)
                    if guild_row:
                        guild_id = guild_row['id']

                if guild_id is not None:
                    cur = con.execute(
                        "SELECT id, scan_date FROM mi_scores WHERE player_name = ? AND guild_id = ?",
                        (incorrect, guild_id),
                    )
                else:
                    cur = con.execute(
                        "SELECT id, scan_date FROM mi_scores WHERE player_name = ?",
                        (incorrect,),
                    )

                rows = cur.fetchall()
                updated_ids = [r['id'] for r in rows]
                scan_dates = sorted({r['scan_date'] for r in rows})

                if updated_ids:
                    if guild_id is not None:
                        con.execute(
                            "UPDATE mi_scores SET player_name = ? WHERE player_name = ? AND guild_id = ?",
                            (corrected, incorrect, guild_id),
                        )
                    else:
                        con.execute(
                            "UPDATE mi_scores SET player_name = ? WHERE player_name = ?",
                            (corrected, incorrect),
                        )

                    # Update player_id to match players table if possible
                    player_row = con.execute(
                        "SELECT id FROM players WHERE username = ?",
                        (corrected,),
                    ).fetchone()
                    if player_row:
                        player_id = player_row['id']
                        if guild_id is not None:
                            con.execute(
                                "UPDATE mi_scores SET player_id = ? WHERE player_name = ? AND guild_id = ?",
                                (player_id, corrected, guild_id),
                            )
                        else:
                            con.execute(
                                "UPDATE mi_scores SET player_id = ? WHERE player_name = ?",
                                (player_id, corrected),
                            )

                    # Recalculate ranks for every affected scan_date
                    for sd in scan_dates:
                        _recalculate_ranks_for_date(con, sd, guild_id)

                await interaction.response.send_message(
                    f"Saved correction for **{incorrect}** → **{corrected}** ({display}). Updated {len(updated_ids)} DB rows.",
                    ephemeral=True,
                )
        except Exception as e:
            await interaction.response.send_message(f"Error saving correction: {e}", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(CorrectionsCog(bot))
