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
    build_response_text,
    write_scores_csv,
    write_scores_chart,
)
from src.database import create_scan, save_scores


class MICog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _run_ocr_for_attachments(self, interaction, attachments, override=False, output_format="csv"):
        if override:
            await interaction.edit_original_response(content="Override enabled: Skipping OCR and returning dummy data.")
            dummy_scores = {"Player1": "12345", "Player2": "67890", "Player3": "54321"}
            response_text = build_response_text(dummy_scores)
            await interaction.edit_original_response(content=response_text)
            return

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
            scores = await loop.run_in_executor(
                None,
                lambda: extract_scores_from_files(vision_client, downloaded_files, max_height=1024),
            )

            response_text = build_response_text(scores)

            # Persist scores to the database (upserts by player_name + date)
            scan_id = create_scan(submitted_by=str(interaction.user.id))
            inserted, updated = save_scores(scan_id, scores)
            print(f"Scan {scan_id}: {inserted} inserted, {updated} updated in DB")

            file_to_send = None
            if output_format == "chart":
                chart_name = f"monster_invasion_scores_{interaction.id}.png"
                chart_path = temp_dir / chart_name
                write_scores_chart(scores, chart_path)
                file_to_send = chart_path
            else:
                csv_name = f"monster_invasion_scores_{interaction.id}.csv"
                csv_path = temp_dir / csv_name
                write_scores_csv(scores, csv_path)
                file_to_send = csv_path

            if len(response_text) > 1900:
                await interaction.edit_original_response(
                    content=f"Parsed scores are long, sending {output_format.upper()} file.",
                    attachments=[discord.File(file_to_send)],
                )
            else:
                await interaction.edit_original_response(
                    content=response_text,
                    attachments=[discord.File(file_to_send)],
                )
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
        output_format="Return `csv` (default) or `chart`",
    )
    async def mi_command(
        self,
        interaction: discord.Interaction,
        image1: discord.Attachment,
        image2: discord.Attachment | None = None,
        image3: discord.Attachment | None = None,
        image4: discord.Attachment | None = None,
        image5: discord.Attachment | None = None,
        override: bool = False,
        output_format: Literal["csv", "chart"] = "csv",
    ):
        print(f"/mi invoked by {interaction.user} ({interaction.user.id})")
        await interaction.response.defer(thinking=True)
        await interaction.edit_original_response(content="Received command. Processing OCR...")
        await self._run_ocr_for_attachments(
            interaction,
            [image1, image2, image3, image4, image5],
            override=override,
            output_format=output_format,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(MICog(bot))
