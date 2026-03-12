import os
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands

from src.database import (
    get_today_guild_scores,
    get_today_score,
    get_player_by_discord_id,
    get_total_weekly_leaderboard,
    get_total_monthly_leaderboard,
    get_weekday_scores_for_month,
    weekday_dates_for_month,
    get_player_weekly_scores,
    get_player_monthly_scores,
    get_player_weekday_scores_for_month,
)

def knubeScore(knubeScore, offset):
    mult = {'K': 1e3, 'M':1e6, 'B':1e9, 'T':1e12}
    knubebase = float(knubeScore[:-1])
    knubeMult = knubeScore[-1]
    offsetbase = float(offset[:-1])
    offsetMult = offset[-1]
    if knubeMult in mult:
        knubebase *= mult[knubeMult]
    if offsetMult in mult:
        offsetbase *= mult[offsetMult]
    total = knubebase + offsetbase
    if total >= 1e12:
        return f"{round(total / 1e12, 2)}T"
    elif total >= 1e9:
        return f"{round(total / 1e9, 2)}B"
    elif total >= 1e6:
        return f"{round(total / 1e6, 2)}M"
    elif total >= 1e3:
        return f"{round(total / 1e3, 2)}K"


def _fmt_score(value) -> str:
    """Format a float total score into a human-readable string."""
    if isinstance(value, str):
        return value
    if value >= 1e12:
        return f"{round(value / 1e12, 2)}T"
    elif value >= 1e9:
        return f"{round(value / 1e9, 2)}B"
    elif value >= 1e6:
        return f"{round(value / 1e6, 2)}M"
    elif value >= 1e3:
        return f"{round(value / 1e3, 2)}K"
    return str(value)


_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

_WEEK_DAY_CHOICES = [
    app_commands.Choice(name="Monday", value=0),
    app_commands.Choice(name="Tuesday", value=1),
    app_commands.Choice(name="Wednesday", value=2),
    app_commands.Choice(name="Thursday", value=3),
    app_commands.Choice(name="Friday", value=4),
    app_commands.Choice(name="Saturday", value=5),
    app_commands.Choice(name="Sunday", value=6),
]

_MONTH_CHOICES = [
    app_commands.Choice(name="January", value=1),
    app_commands.Choice(name="February", value=2),
    app_commands.Choice(name="March", value=3),
    app_commands.Choice(name="April", value=4),
    app_commands.Choice(name="May", value=5),
    app_commands.Choice(name="June", value=6),
    app_commands.Choice(name="July", value=7),
    app_commands.Choice(name="August", value=8),
    app_commands.Choice(name="September", value=9),
    app_commands.Choice(name="October", value=10),
    app_commands.Choice(name="November", value=11),
    app_commands.Choice(name="December", value=12),
]


async def _send_chunked(
    interaction: discord.Interaction, header: str, score_lines: list[str]
) -> None:
    chunks: list[str] = []
    current = header
    for line in score_lines:
        if len(current) + 1 + len(line) > 1900:
            chunks.append(current)
            current = line
        else:
            current = current + "\n" + line
    chunks.append(current)
    await interaction.response.send_message(chunks[0], ephemeral=True)
    for chunk in chunks[1:]:
        await interaction.followup.send(chunk, ephemeral=True)


class ScoresCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _get_guild_name(self, interaction: discord.Interaction) -> str | None:
        player = get_player_by_discord_id(str(interaction.user.id))
        if not player:
            await interaction.response.send_message(
                "You are not registered. Use `/register` to get started.", ephemeral=True
            )
            return None
        if not player["game_guild_id"]:
            await interaction.response.send_message(
                "You are not in a guild. Re-register with `/register` and select a guild.",
                ephemeral=True,
            )
            return None
        from src.database import _connect
        with _connect() as con:
            guild_row = con.execute(
                "SELECT name FROM game_guilds WHERE id = ?", (player["game_guild_id"],)
            ).fetchone()
        if not guild_row:
            await interaction.response.send_message("Could not find your guild.", ephemeral=True)
            return None
        return guild_row["name"]

    async def _get_player_name(self, interaction: discord.Interaction) -> str | None:
        player = get_player_by_discord_id(str(interaction.user.id))
        if not player:
            await interaction.response.send_message(
                "You are not registered. Use `/register` to get started.", ephemeral=True
            )
            return None
        return player["username"]

    # ── /guild-scores ──────────────────────────────────────────────────
    guild_scores = app_commands.Group(name="guild-scores", description="Show guild scores")

    @guild_scores.command(name="today", description="Show today's scores for your guild")
    async def gs_today(self, interaction: discord.Interaction):
        guild_name = await self._get_guild_name(interaction)
        if guild_name is None:
            return
        now = datetime.now()
        rows = get_today_guild_scores(guild_name)
        if not rows:
            await interaction.response.send_message(
                f"No scores found for **{guild_name}** today.", ephemeral=True
            )
            return
        label = f"today ({now.strftime('%d %b %Y')})"
        lines = [f"`{r['rank']}.` **{r['player_name']}**: {r['score']}" for r in rows]
        await _send_chunked(interaction, f"📊 **{guild_name}** — {label}", lines)

    @guild_scores.command(name="week", description="Show weekly total scores for your guild")
    @app_commands.describe(
        day="Day of the current month whose week to show (default: current week)",
        view="Show the weekly total or individual daily scores (default: total)",
    )
    @app_commands.choices(view=[
        app_commands.Choice(name="Total", value="total"),
        app_commands.Choice(name="Daily", value="daily"),
    ])
    async def gs_week(self, interaction: discord.Interaction, day: int | None = None, view: str = "total"):
        guild_name = await self._get_guild_name(interaction)
        if guild_name is None:
            return
        isKnube = interaction.user.id == 1391487700242141347
        now = datetime.now()
        if day is not None:
            try:
                ref_date = now.replace(day=day)
            except ValueError:
                await interaction.response.send_message(
                    f"Invalid day **{day}** for the current month.", ephemeral=True
                )
                return
        else:
            ref_date = now
        label = ref_date.strftime("week of %d %b %Y")
        scores_rows = get_total_weekly_leaderboard(guild_name, ref_date)
        if not scores_rows:
            await interaction.response.send_message(
                f"No scores found for **{guild_name}** for the {label}.", ephemeral=True
            )
            return

        from src.database import _week_dates
        dates = _week_dates(ref_date)

        if view == "daily":
            mon_abbr = ref_date.strftime("%b")
            lines = []
            for i, row in enumerate(scores_rows):
                daily = " | ".join(
                    f"{int(d[:2])} {mon_abbr}: {row[d]}" if row.get(d) else f"{int(d[:2])} {mon_abbr}: —"
                    for d in dates
                )
                lines.append(f"`{i+1}.` **{row['player_name']}** [{daily}] — **Total: {_fmt_score(row['total_score'])}**")
        else:
            SCORES = {row["player_name"]: row["total_score"] for row in scores_rows}
            if isKnube and "KNUBE" in SCORES:
                knube_row = next((r for r in scores_rows if r["player_name"] == "KNUBE"), None)
                first_row = scores_rows[0]
                knube_scores = [v for k, v in (knube_row or {}).items() if isinstance(v, str) and v]
                first_scores = [v for k, v in first_row.items() if isinstance(v, str) and v]
                if knube_scores and first_scores:
                    SCORES["KNUBE"] = knubeScore(knube_scores[0], first_scores[0])
            lines = [f"`{i+1}.` **{name}**: {_fmt_score(val)}" for i, (name, val) in enumerate(SCORES.items())]
        await _send_chunked(interaction, f"📊 **{guild_name}** — {label}", lines)

    @guild_scores.command(name="month", description="Show monthly scores for your guild")
    @app_commands.describe(
        week_day="Compare scores on each occurrence of this weekday across the month",
        month="Month to show (default: current month)",
    )
    @app_commands.choices(week_day=_WEEK_DAY_CHOICES, month=_MONTH_CHOICES)
    async def gs_month(
        self,
        interaction: discord.Interaction,
        week_day: int | None = None,
        month: int | None = None,
    ):
        guild_name = await self._get_guild_name(interaction)
        if guild_name is None:
            return
        now = datetime.now()
        month_num = month if month is not None else now.month
        month_label = datetime(now.year, month_num, 1).strftime("%B %Y")
        mon_abbr = datetime(now.year, month_num, 1).strftime("%b")

        if week_day is not None:
            dates = weekday_dates_for_month(now.year, month_num, week_day)
            label = f"{_DAY_NAMES[week_day]}s in {month_label}"
            scores_rows = get_weekday_scores_for_month(guild_name, now.year, month_num, week_day)
            if not scores_rows:
                await interaction.response.send_message(
                    f"No scores found for **{guild_name}** on {label}.", ephemeral=True
                )
                return
            lines = []
            for i, row in enumerate(scores_rows):
                daily = " | ".join(
                    f"{int(d[:2])} {mon_abbr}: {row[d]}" if row.get(d) else f"{int(d[:2])} {mon_abbr}: —"
                    for d in dates
                )
                lines.append(f"`{i+1}.` **{row['player_name']}** [{daily}] — **Total: {_fmt_score(row['total_score'])}**")
        else:
            label = month_label
            scores_rows = get_total_monthly_leaderboard(guild_name, now.year, month_num)
            if not scores_rows:
                await interaction.response.send_message(
                    f"No scores found for **{guild_name}** in {label}.", ephemeral=True
                )
                return
            lines = [
                f"`{i+1}.` **{row['player_name']}**: {_fmt_score(row['total_score'])}"
                for i, row in enumerate(scores_rows)
            ]
        await _send_chunked(interaction, f"📊 **{guild_name}** — {label}", lines)

    # ── /user-score ────────────────────────────────────────────────────
    user_score = app_commands.Group(name="user-score", description="Show your scores")

    @user_score.command(name="today", description="Show your score for today")
    async def us_today(self, interaction: discord.Interaction):
        player_name = await self._get_player_name(interaction)
        if player_name is None:
            return
        now = datetime.now()
        entry = get_today_score(player_name)
        if not entry:
            await interaction.response.send_message("No score found for you today.", ephemeral=True)
            return
        label = f"today ({now.strftime('%d %b %Y')})"
        await interaction.response.send_message(
            f"📊 **{player_name}** — {label}\nRank **#{entry['rank']}** — **{entry['score']}**",
            ephemeral=True,
        )

    @user_score.command(name="week", description="Show your scores for a week")
    @app_commands.describe(
        day="Day of the current month whose week to show (default: current week)",
        view="Show the weekly total or individual daily scores (default: total)",
    )
    @app_commands.choices(view=[
        app_commands.Choice(name="Total", value="total"),
        app_commands.Choice(name="Daily", value="daily"),
    ])
    async def us_week(self, interaction: discord.Interaction, day: int | None = None, view: str = "total"):
        player_name = await self._get_player_name(interaction)
        if player_name is None:
            return
        now = datetime.now()
        if day is not None:
            try:
                ref_date = now.replace(day=day)
            except ValueError:
                await interaction.response.send_message(
                    f"Invalid day **{day}** for the current month.", ephemeral=True
                )
                return
        else:
            ref_date = now
        label = ref_date.strftime("week of %d %b %Y")
        data = get_player_weekly_scores(player_name, ref_date)
        if not data["days_present"]:
            await interaction.response.send_message(
                f"No scores found for the {label}.", ephemeral=True
            )
            return
        from src.database import _week_dates
        dates = _week_dates(ref_date)
        mon_abbr = ref_date.strftime("%b")
        if view == "daily":
            lines = [
                f"{int(d[:2])} {mon_abbr} ({_DAY_NAMES[datetime.strptime(d, '%d_%m_%Y').weekday()][:3]}): {data[d]}"
                if data.get(d) else
                f"{int(d[:2])} {mon_abbr} ({_DAY_NAMES[datetime.strptime(d, '%d_%m_%Y').weekday()][:3]}): —"
                for d in dates
            ] + [f"**Total: {_fmt_score(data['total_score'])}**"]
        else:
            lines = [f"**Total: {_fmt_score(data['total_score'])}** ({data['days_present']} day(s) submitted)"]
        await interaction.response.send_message(
            f"📊 **{player_name}** — {label}\n" + "\n".join(lines), ephemeral=True
        )

    @user_score.command(name="month", description="Show your scores for a month")
    @app_commands.describe(
        week_day="Compare your scores on each occurrence of this weekday across the month",
        month="Month to show (default: current month)",
    )
    @app_commands.choices(week_day=_WEEK_DAY_CHOICES, month=_MONTH_CHOICES)
    async def us_month(
        self,
        interaction: discord.Interaction,
        week_day: int | None = None,
        month: int | None = None,
    ):
        player_name = await self._get_player_name(interaction)
        if player_name is None:
            return
        now = datetime.now()
        month_num = month if month is not None else now.month
        month_label = datetime(now.year, month_num, 1).strftime("%B %Y")
        mon_abbr = datetime(now.year, month_num, 1).strftime("%b")

        if week_day is not None:
            dates = weekday_dates_for_month(now.year, month_num, week_day)
            label = f"{_DAY_NAMES[week_day]}s in {month_label}"
            data = get_player_weekday_scores_for_month(player_name, now.year, month_num, week_day)
            if not data["days_present"]:
                await interaction.response.send_message(
                    f"No scores found for {label}.", ephemeral=True
                )
                return
            lines = [
                f"{int(d[:2])} {mon_abbr}: {data[d]}" if data.get(d) else f"{int(d[:2])} {mon_abbr}: —"
                for d in dates
            ] + [f"**Total: {_fmt_score(data['total_score'])}**"]
        else:
            label = month_label
            data = get_player_monthly_scores(player_name, now.year, month_num)
            if not data["days_present"]:
                await interaction.response.send_message(
                    f"No scores found for {label}.", ephemeral=True
                )
                return
            lines = [
                f"Days submitted: **{data['days_present']}**",
                f"Total: **{_fmt_score(data['total_score'])}**",
            ]
        await interaction.response.send_message(
            f"📊 **{player_name}** — {label}\n" + "\n".join(lines), ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(ScoresCog(bot))
