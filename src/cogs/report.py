import os
from datetime import datetime, timedelta
import zoneinfo

import discord
from discord.ext import commands, tasks

from src.database import get_today_guild_scores, _score_to_float

GUILD_NAME = "AboveAll"
REPORT_HOUR = 3
REPORT_MINUTE = 0
REPORT_TZ = zoneinfo.ZoneInfo("Europe/Madrid")


class ReportCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._last_fired: str | None = None
        self.daily_report.start()

    def cog_unload(self):
        self.daily_report.cancel()

    @tasks.loop(minutes=1)
    async def daily_report(self):
        now = datetime.now(tz=REPORT_TZ)
        if now.hour != REPORT_HOUR or now.minute != REPORT_MINUTE:
            return
        today_key = now.strftime("%d_%m_%Y")
        if self._last_fired == today_key:
            return
        self._last_fired = today_key
        print(f"[report] Firing daily report for {today_key}")

        owner_id = os.getenv("DISCORD_OWNER_ID", "")
        channel_id = os.getenv("MI_REPORT_CHANNEL_ID", "").strip()
        if not channel_id:
            print("[report] MI_REPORT_CHANNEL_ID not set — skipping")
            return

        channel = self.bot.get_channel(int(channel_id))
        if channel is None:
            print(f"[report] Channel {channel_id} not found — skipping")
            return

        yesterday = now - timedelta(days=1)
        date_key = yesterday.strftime("%d_%m_%Y")
        rows = get_today_guild_scores(GUILD_NAME, date=date_key)
        mention = f"<@{owner_id}>" if owner_id else ""

        if not rows:
            await channel.send(f"{mention} No scores recorded for **{GUILD_NAME}** on {yesterday.strftime('%d %b %Y')}.")
            return

        sorted_rows = sorted(rows, key=lambda r: _score_to_float(r["score"]), reverse=True)
        lines = [f"`{i+1}.` **{r['player_name']}**: {r['score']}" for i, r in enumerate(sorted_rows)]
        header = f"{mention} 📊 **{GUILD_NAME}** — scores for {yesterday.strftime('%d %b %Y')}\n"
        await channel.send(header + "\n".join(lines))

    @daily_report.before_loop
    async def before_daily_report(self):
        await self.bot.wait_until_ready()
        print(f"[report] Loop ready. Will fire daily at {REPORT_HOUR:02d}:{REPORT_MINUTE:02d} {REPORT_TZ}")


async def setup(bot: commands.Bot):
    await bot.add_cog(ReportCog(bot))
