import asyncio
import os
from pathlib import Path
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands
from google.cloud import vision

from src.mi_utils import (
    _is_image_filename,
    extract_scores_from_files,
    write_scores_csv,
    write_scores_chart,
)
from src.database import create_scan, save_scores, get_player_by_discord_id, get_today_score, get_daily_scan_count, get_guild_by_name
from src.guild_context import get_guild_from_channel_category, format_guild_context_message

DAILY_SCAN_LIMIT = 1


def _is_unlimited_user(interaction: discord.Interaction) -> bool:
    """Returns True for the bot owner or anyone with a role listed in MI_UNLIMITED_ROLE_IDS."""
    owner_id = os.getenv("DISCORD_OWNER_ID", "")
    if owner_id and str(interaction.user.id) == owner_id:
        return True
        # return False
    unlimited_ids = {
        rid.strip()
        for rid in os.getenv("MI_UNLIMITED_ROLE_IDS", "").split(",")
        if rid.strip()
    }
    if unlimited_ids and isinstance(interaction.user, discord.Member):
        return any(str(role.id) in unlimited_ids for role in interaction.user.roles)
        # return False
    return False


class ConfirmUpdateView(discord.ui.View):
    """Shown when the user already has a score today. Lets them confirm a re-run."""

    def __init__(self, cog: "MICog", attachments: list):
        super().__init__(timeout=60)
        self.cog = cog
        self.attachments = attachments

    async def on_timeout(self):
        # Disable buttons if the user never responds
        for item in self.children:
            item.disabled = True

    @discord.ui.button(label="Yes, update", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(thinking=True)
        await interaction.edit_original_response(content="Re-running OCR...", view=None)
        await self.cog._run_ocr_for_attachments(
            interaction, self.attachments
        )
        self.stop()

    @discord.ui.button(label="No, keep existing", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Keeping your existing score.", view=None)
        self.stop()


class MICog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _run_ocr_for_attachments(self, interaction, attachments, note: str = ""):
        image_attachments = [att for att in attachments if att and _is_image_filename(att.filename)]
        if not image_attachments:
            await interaction.edit_original_response(content="Please provide at least one image attachment.")
            return

        root_dir = self.bot.ROOT_DIR
        temp_dir = root_dir / "output" / "discord_tmp"
        temp_dir.mkdir(parents=True, exist_ok=True)

        downloaded_files = []
        try:
            for attachment in image_attachments:
                out_path = temp_dir / f"{interaction.id}_{attachment.filename}"
                await attachment.save(out_path)
                downloaded_files.append(out_path)

            loop = asyncio.get_running_loop()
            vision_client = vision.ImageAnnotatorClient()
            
            # Determine guild based on channel category context first
            detected_guild_name = get_guild_from_channel_category(interaction)
            
            scores = await loop.run_in_executor(
                None,
                lambda: extract_scores_from_files(vision_client, downloaded_files, max_height=1024, guild_name=detected_guild_name),
            )

            # Persist scores to the database (upserts by player_name + date)
            submitter = get_player_by_discord_id(str(interaction.user.id))
            guild_id = None
            
            if detected_guild_name:
                guild_row = get_guild_by_name(detected_guild_name)
                guild_id = guild_row["id"] if guild_row else None
            
            # Fallback to submitter's registered guild if no category detected
            if guild_id is None:
                guild_id = submitter["game_guild_id"] if submitter else None
            
            scan_id = create_scan(submitted_by=str(interaction.user.id))
            inserted, updated = save_scores(scan_id, scores, guild_id=guild_id)
            print(f"Scan {scan_id}: {inserted} inserted, {updated} updated in DB")

            result_msg = f"✅ Done! Found **{len(scores)}** player(s) — {inserted} new, {updated} updated."
            
            # Add guild context information
            if detected_guild_name:
                guild_context_msg = format_guild_context_message(detected_guild_name)
                result_msg = f"{guild_context_msg}\n{result_msg}"
            
            if note:
                result_msg = f"{note}\n{result_msg}"
            await interaction.edit_original_response(content=result_msg)

        except Exception as exc:
            print(f"/mi failed: {exc}")
            await interaction.edit_original_response(content=f"Failed to process images: {exc}")
        finally:
            for file_path in downloaded_files:
                if file_path.exists():
                    file_path.unlink()

    @app_commands.command(name="mi", description="Extract Monster Invasion scores from attached screenshot(s)")
    @app_commands.describe(
        image1="Leaderboard screenshot (required)",
        image2="Leaderboard screenshot (optional)",
        image3="Leaderboard screenshot (optional)",
        image4="Leaderboard screenshot (optional)",
        image5="Leaderboard screenshot (optional)",
    )
    async def mi_command(
        self,
        interaction: discord.Interaction,
        image1: discord.Attachment,
        image2: discord.Attachment | None = None,
        image3: discord.Attachment | None = None,
        image4: discord.Attachment | None = None,
        image5: discord.Attachment | None = None,
    ):
        print(f"/mi invoked by {interaction.user} ({interaction.user.id})")
        await self._mi_checks_and_run(
            interaction,
            [image1, image2, image3, image4, image5],
        )

    async def _mi_checks_and_run(self, interaction: discord.Interaction, attachments: list, note: str = ""):
        """Shared pre-flight checks (registration, daily limit, duplicate) then runs OCR."""
        player = get_player_by_discord_id(str(interaction.user.id))
        if not player:
            await interaction.response.send_message(
                "You need to register before using this command. Use `/register` to get started.",
                ephemeral=True,
            )
            return

        if not _is_unlimited_user(interaction):
            scans_today = get_daily_scan_count(str(interaction.user.id))
            if scans_today >= DAILY_SCAN_LIMIT:
                await interaction.response.send_message(
                    f"You have reached the daily limit of **{DAILY_SCAN_LIMIT}** OCR scans. "
                    f"Try again tomorrow!",
                    ephemeral=True,
                )
                return

        existing = get_today_score(player["username"])
        if existing:
            view = ConfirmUpdateView(cog=self, attachments=attachments)
            await interaction.response.send_message(
                f"A score for **{player['username']}** was already recorded today: "
                f"**{existing['score']}** (rank #{existing['rank']}).\n"
                f"Do you want to re-run OCR and update it?",
                view=view,
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True)
        await interaction.edit_original_response(content="Processing OCR...")
        await self._run_ocr_for_attachments(interaction, attachments, note=note)


@app_commands.context_menu(name="Process MI")
async def mi_context_menu(interaction: discord.Interaction, message: discord.Message):
    cog: MICog = interaction.client.cogs.get("MICog")  # type: ignore
    if cog is None:
        await interaction.response.send_message("Bot is not ready yet.", ephemeral=True)
        return
    print(f"Process MI context menu invoked by {interaction.user} on message {message.id}")
    image_attachments = [a for a in message.attachments if _is_image_filename(a.filename)]
    if not image_attachments:
        await interaction.response.send_message(
            "That message has no image attachments to process.",
            ephemeral=True,
        )
        return

    note = ""
    if not _is_unlimited_user(interaction) and len(image_attachments) > DAILY_SCAN_LIMIT:
        image_attachments = image_attachments[:DAILY_SCAN_LIMIT]
        note = f"-# Only **{DAILY_SCAN_LIMIT}** image(s) can be processed per day — extra images were ignored."

    await cog._mi_checks_and_run(interaction, image_attachments, note=note)


async def setup(bot: commands.Bot):
    cog = MICog(bot)
    await bot.add_cog(cog)
    bot.tree.add_command(mi_context_menu)
