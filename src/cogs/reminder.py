import os
import random
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord import app_commands
from discord.ext import commands, tasks

from src.database import (
    get_player_by_discord_id,
    set_player_reminder,
    clear_player_reminder,
    get_players_with_reminders,
)

MADRID_TZ = ZoneInfo("Europe/Madrid")

# Maps game guild names to the env var holding the reminder channel ID.
# Reuses the same channel env vars as the report cog.
GUILD_CHANNEL_MAP: dict[str, str] = {
    "AboveAll": "MI_REPORT_CHANNEL_ID_AA",
    "MoeCafe":  "MI_REPORT_CHANNEL_ID_MC",
}

# Fallback channel used for players with no guild or an unmapped guild.
FALLBACK_CHANNEL_ENV = "MI_REPORT_CHANNEL_ID"

REMINDER_IMAGES_DIR = Path(__file__).resolve().parents[2] / "ReminderImages"
_IMAGE_EXTENSIONS = {".gif", ".png", ".jpg", ".jpeg", ".webp"}


class ReminderCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._last_date: str = ""
        self._fired_today: set[str] = set()
        self.check_reminders.start()

    def cog_unload(self):
        self.check_reminders.cancel()

    # ------------------------------------------------------------------
    # Background loop — runs every minute, fires grouped reminder messages
    # ------------------------------------------------------------------

    @tasks.loop(minutes=1)
    async def check_reminders(self):
        now_madrid = datetime.now(tz=MADRID_TZ)

        # Reset fired set at the start of each new day
        today_key = now_madrid.strftime("%d_%m_%Y")
        if today_key != self._last_date:
            self._fired_today.clear()
            self._last_date = today_key

        fire_key = now_madrid.strftime("%H:%M")
        if fire_key in self._fired_today:
            return

        players = get_players_with_reminders()
        if not players:
            return

        current_hm = (now_madrid.hour, now_madrid.minute)

        # Group players whose reminder falls on the current Madrid HH:MM.
        # Key: guild_name (or "__none__") → list of discord_user_ids
        guild_groups: dict[str, list[str]] = defaultdict(list)

        for player in players:
            try:
                user_tz = ZoneInfo(player["reminder_timezone"])
            except ZoneInfoNotFoundError:
                continue

            h, m = map(int, player["reminder_time"].split(":"))
            # Build today's reminder datetime in the user's local timezone
            today_user = datetime.now(user_tz).date()
            reminder_local = datetime(
                today_user.year, today_user.month, today_user.day,
                h, m, tzinfo=user_tz
            )
            reminder_madrid = reminder_local.astimezone(MADRID_TZ)

            if (reminder_madrid.hour, reminder_madrid.minute) == current_hm:
                guild_name = player["guild_name"] or "__none__"
                guild_groups[guild_name].append(player["discord_user_id"])

        if not guild_groups:
            return

        self._fired_today.add(fire_key)

        # Send ONE message per guild group
        for guild_name, user_ids in guild_groups.items():
            channel_env = GUILD_CHANNEL_MAP.get(guild_name, FALLBACK_CHANNEL_ENV)
            channel_id = os.getenv(channel_env, "").strip()
            if not channel_id:
                channel_id = os.getenv(FALLBACK_CHANNEL_ENV, "").strip()
            if not channel_id:
                print(f"[reminder] No channel configured for guild '{guild_name}', skipping.")
                continue

            channel = self.bot.get_channel(int(channel_id))
            if channel is None:
                print(f"[reminder] Channel {channel_id} not found for guild '{guild_name}'.")
                continue

            mentions = " ".join(f"<@{uid}>" for uid in user_ids)
            content = f"⏰ {mentions} Don't forget to record your **Monster Invasion** scores!"

            # Pick a random image/gif from ReminderImages if any exist
            image_file = None
            if REMINDER_IMAGES_DIR.is_dir():
                candidates = [
                    p for p in REMINDER_IMAGES_DIR.iterdir()
                    if p.is_file() and p.suffix.lower() in _IMAGE_EXTENSIONS
                ]
                if candidates:
                    image_file = discord.File(str(random.choice(candidates)))

            await channel.send(content, file=image_file)
            print(f"[reminder] Sent reminder to {len(user_ids)} player(s) in '{guild_name}'.")

    @check_reminders.before_loop
    async def before_check_reminders(self):
        await self.bot.wait_until_ready()
        print("[reminder] Reminder loop ready.")

    # ------------------------------------------------------------------
    # /set-reminder
    # ------------------------------------------------------------------

    @app_commands.command(
        name="set-reminder",
        description="Set a daily reminder to record your Monster Invasion scores",
    )
    @app_commands.describe(
        time="Time in 24-hour HH:MM format (e.g. 09:00 or 21:30)",
        timezone="Your IANA timezone (e.g. Europe/London, America/New_York, Asia/Tokyo)",
    )
    async def set_reminder_command(
        self, interaction: discord.Interaction, time: str, timezone: str
    ):
        player = get_player_by_discord_id(str(interaction.user.id))
        if not player:
            await interaction.response.send_message(
                "You must be registered before setting a reminder. Use `/register` first.",
                ephemeral=True,
            )
            return

        time_clean = time.strip()
        if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", time_clean):
            await interaction.response.send_message(
                "Invalid time format. Please use `HH:MM` (24-hour clock), e.g. `09:00` or `21:30`.",
                ephemeral=True,
            )
            return

        tz_clean = timezone.strip()
        try:
            user_tz = ZoneInfo(tz_clean)
        except ZoneInfoNotFoundError:
            await interaction.response.send_message(
                f"Unknown timezone `{tz_clean}`. Use an IANA timezone name such as "
                "`Europe/London`, `America/New_York`, or `Asia/Tokyo`.\n"
                "See the full list at: https://en.wikipedia.org/wiki/List_of_tz_database_time_zones",
                ephemeral=True,
            )
            return

        set_player_reminder(str(interaction.user.id), time_clean, tz_clean)

        # Show the Madrid equivalent for confirmation
        now_user = datetime.now(user_tz)
        h, m = map(int, time_clean.split(":"))
        reminder_local = datetime(
            now_user.year, now_user.month, now_user.day, h, m, tzinfo=user_tz
        )
        madrid_str = reminder_local.astimezone(MADRID_TZ).strftime("%H:%M")

        await interaction.response.send_message(
            f"✅ Reminder set for **{time_clean} {tz_clean}** "
            f"(= **{madrid_str} Europe/Madrid**).\n"
            "You'll be pinged daily to record your scores!",
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # /clear-reminder
    # ------------------------------------------------------------------

    @app_commands.command(
        name="clear-reminder",
        description="Remove your daily score reminder",
    )
    async def clear_reminder_command(self, interaction: discord.Interaction):
        player = get_player_by_discord_id(str(interaction.user.id))
        if not player:
            await interaction.response.send_message("You are not registered.", ephemeral=True)
            return

        if not player["reminder_time"]:
            await interaction.response.send_message(
                "You don't have an active reminder.", ephemeral=True
            )
            return

        clear_player_reminder(str(interaction.user.id))
        await interaction.response.send_message("🗑️ Your reminder has been cleared.", ephemeral=True)

    # ------------------------------------------------------------------
    # /my-reminder
    # ------------------------------------------------------------------

    @app_commands.command(
        name="my-reminder",
        description="View your current reminder setting",
    )
    async def my_reminder_command(self, interaction: discord.Interaction):
        player = get_player_by_discord_id(str(interaction.user.id))
        if not player:
            await interaction.response.send_message("You are not registered.", ephemeral=True)
            return

        if not player["reminder_time"]:
            await interaction.response.send_message(
                "You have no active reminder. Use `/set-reminder` to set one.", ephemeral=True
            )
            return

        tz_str = player["reminder_timezone"]
        time_str = player["reminder_time"]

        try:
            user_tz = ZoneInfo(tz_str)
            now_user = datetime.now(user_tz)
            h, m = map(int, time_str.split(":"))
            reminder_local = datetime(
                now_user.year, now_user.month, now_user.day, h, m, tzinfo=user_tz
            )
            madrid_str = reminder_local.astimezone(MADRID_TZ).strftime("%H:%M")
            await interaction.response.send_message(
                f"⏰ Your reminder is set for **{time_str} {tz_str}** "
                f"(= **{madrid_str} Europe/Madrid**).",
                ephemeral=True,
            )
        except ZoneInfoNotFoundError:
            await interaction.response.send_message(
                f"⏰ Your reminder is set for **{time_str} {tz_str}**.",
                ephemeral=True,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(ReminderCog(bot))
    print("[reminder] Daily score reminder system loaded.")
