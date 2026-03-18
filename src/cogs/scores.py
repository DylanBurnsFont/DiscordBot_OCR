import os
import csv
import io
from datetime import datetime, timedelta
import unicodedata

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
    get_guild_weekly_attendance,
    _score_to_float,
    get_guild_by_name,
)

from src.guild_context import get_guild_from_channel_category

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import matplotlib.colors as mcolors
from io import BytesIO

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

def _display_width(text: str) -> int:
    """Calculate the display width of a string, accounting for wide Unicode characters."""
    width = 0
    for char in text:
        # Check if character is wide (typically CJK characters)
        if unicodedata.east_asian_width(char) in ('F', 'W'):
            width += 2  # Wide characters take 2 spaces
        else:
            width += 1  # Normal characters take 1 space (includes Cyrillic, Greek, etc.)
    return width


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
    if interaction.response.is_done():
        await interaction.followup.send(chunks[0], ephemeral=True)
    else:
        await interaction.response.send_message(chunks[0], ephemeral=True)
    for chunk in chunks[1:]:
        await interaction.followup.send(chunk, ephemeral=True)


def _create_attendance_heatmap(attendance_data: dict, guild_name: str, label: str) -> discord.File:
    """Create a heatmap visualization of guild attendance data."""
    
    # Use the proper font configuration from mi_utils
    from src.mi_utils import _configure_chart_font
    _configure_chart_font()
    
    dates = attendance_data['dates']
    
    # Get all unique players 
    all_players = set()
    
    # Add players who attacked this week
    for player_name in attendance_data['players_with_scores']:
        all_players.add(player_name)
    
    # Add players who didn't attack this week  
    for player in attendance_data['players_no_attacks']:
        all_players.add(player['player_name'])
    
    if not all_players:
        raise ValueError("No attendance data to display")
    
    # Sort players alphabetically (case-insensitive)
    sorted_players = sorted(list(all_players), key=lambda x: x.lower())
    
    # Create attendance matrix
    player_attendance = {}
    
    # Initialize all players with perfect attendance first
    for player_name in attendance_data['players_with_scores']:
        player_attendance[player_name] = [1] * 7  # Assume perfect attendance
    
    # Mark missing days for players who attacked but missed some days
    for player_data in attendance_data['players_missing_days']:
        player_name = player_data['player_name']
        if player_name in player_attendance:
            # Mark missed days as 0
            for missed_date in player_data['missed_days']:
                if missed_date in dates:
                    day_index = dates.index(missed_date)
                    player_attendance[player_name][day_index] = 0
    
    # Add players who didn't attack at all this week
    for player in attendance_data['players_no_attacks']:
        player_name = player['player_name']
        player_attendance[player_name] = [0] * 7  # No attacks all week
    
    # Build the final data matrix
    data_matrix = []
    for player in sorted_players:
        if player in player_attendance:
            data_matrix.append(player_attendance[player])
        else:
            # Default to no attacks if somehow missing
            data_matrix.append([0] * 7)
    
    # Convert to numpy array
    data = np.array(data_matrix)
    
    # Create the heatmap
    fig_width = max(8, min(15, len(sorted_players) * 0.5))
    fig, ax = plt.subplots(figsize=(8, fig_width))
    
    # Create heatmap with custom colors - red for missed, green for attacked
    colors = ['#ff6b6b', '#51cf66']  # Red for no attack, Green for attack
    im = ax.imshow(data, cmap=mcolors.ListedColormap(colors), aspect='auto')
    
    # Set ticks and labels
    day_labels = [datetime.strptime(date, '%d_%m_%Y').strftime('%a') for date in dates]
    ax.set_xticks(range(7))
    ax.set_xticklabels(day_labels, fontsize=10)
    ax.set_yticks(range(len(sorted_players)))
    ax.set_yticklabels(sorted_players, fontsize=9)
    
    # Move x-axis to the top
    ax.xaxis.tick_top()
    ax.xaxis.set_label_position('top')
    
    # Add grid
    ax.set_xticks(np.arange(-.5, 7, 1), minor=True)
    ax.set_yticks(np.arange(-.5, len(sorted_players), 1), minor=True)
    ax.grid(which='minor', color='gray', linestyle='-', linewidth=0.5)
    
    # Set title and labels
    ax.set_title(f'{guild_name} - Boss attack attendance {label}', fontsize=14, fontweight='bold', pad=20)
    ax.set_xlabel('Day of Week', fontsize=12)
    ax.set_ylabel('Players', fontsize=12)
    
    # Add legend
    legend_elements = [
        patches.Patch(color='#ff6b6b', label='No Attack'),
        patches.Patch(color='#51cf66', label='Attacked')
    ]
    ax.legend(handles=legend_elements, loc='upper left', bbox_to_anchor=(1.05, 1))
    
    # Adjust layout to prevent legend cutoff
    plt.tight_layout()
    
    # Save to BytesIO
    buffer = BytesIO()
    plt.savefig(buffer, format='png', dpi=150, bbox_inches='tight')
    buffer.seek(0)
    plt.close(fig)
    
    # Create discord file
    filename = f"attendance_heatmap_{guild_name.replace(' ', '_')}.png"
    return discord.File(buffer, filename=filename)


def _create_csv_file(data: list[dict], filename: str) -> discord.File:
    """Create a CSV file from data and return it as a discord.File."""
    output = io.StringIO()
    if data:
        writer = csv.DictWriter(output, fieldnames=data[0].keys())
        writer.writeheader()
        writer.writerows(data)
    
    output.seek(0)
    file_content = io.BytesIO(output.getvalue().encode('utf-8'))
    return discord.File(file_content, filename=filename)


class ScoresCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _get_guild_name_from_user_submissions(self, interaction: discord.Interaction) -> str | None:
        """Get the guild name from the user's submission history."""
        user_id = str(interaction.user.id)
        
        # Try to get guild from user's submissions
        from src.database import _connect
        with _connect() as con:
            # Get the most recent scan by this user and find the guild
            scan_row = con.execute("""
                SELECT guild_id FROM mi_scores ms
                JOIN mi_scans scan ON scan.id = ms.scan_id
                WHERE scan.submitted_by = ?
                ORDER BY scan.scanned_at DESC
                LIMIT 1
            """, (user_id,)).fetchone()
            
            if scan_row and scan_row["guild_id"]:
                guild_row = con.execute(
                    "SELECT name FROM game_guilds WHERE id = ?", (scan_row["guild_id"],)
                ).fetchone()
                if guild_row:
                    return guild_row["name"]
        
        # Fallback to registered player method
        return await self._get_guild_name(interaction)

    async def _get_guild_name(self, interaction: discord.Interaction) -> str | None:
        """Get guild name from channel context first, then fallback to registered player data."""
        
        # First try to detect guild from channel category
        detected_guild_name = get_guild_from_channel_category(interaction)
        if detected_guild_name:
            # Verify the guild exists in the database
            guild_row = get_guild_by_name(detected_guild_name)
            if guild_row:
                return detected_guild_name
        
        # Fallback to registered player method
        player = get_player_by_discord_id(str(interaction.user.id))
        if not player:
            await interaction.followup.send(
                "You are not registered. Use `/register` to get started.", ephemeral=True
            )
            return None
        if not player["game_guild_id"]:
            guild_context_msg = ""
            if detected_guild_name:
                guild_context_msg = f" Note: Detected {detected_guild_name} from channel context, but this guild is not in the database."
            await interaction.followup.send(
                f"You are not in a guild. Re-register with `/register` and select a guild.{guild_context_msg}",
                ephemeral=True,
            )
            return None
        from src.database import _connect
        with _connect() as con:
            guild_row = con.execute(
                "SELECT name FROM game_guilds WHERE id = ?", (player["game_guild_id"],)
            ).fetchone()
        if not guild_row:
            await interaction.followup.send("Could not find your guild.", ephemeral=True)
            return None
        return guild_row["name"]

    async def _get_player_name(self, interaction: discord.Interaction) -> str | None:
        player = get_player_by_discord_id(str(interaction.user.id))
        if not player:
            await interaction.followup.send(
                "You are not registered. Use `/register` to get started.", ephemeral=True
            )
            return None
        return player["username"]

    # ── /guild-scores ──────────────────────────────────────────────────
    guild_scores = app_commands.Group(name="guild-scores", description="Show guild scores")

    @guild_scores.command(name="today", description="Show today's scores for your guild")
    @app_commands.describe(view="Show raw scores or each player's % of total guild damage (default: scores)")
    @app_commands.choices(view=[
        app_commands.Choice(name="Scores", value="scores"),
        app_commands.Choice(name="Percent", value="percent"),
    ])
    async def gs_today(self, interaction: discord.Interaction, view: str = "scores"):
        await interaction.response.defer(ephemeral=True)
        guild_name = await self._get_guild_name(interaction)
        if guild_name is None:
            return
        now = datetime.now()
        rows = get_today_guild_scores(guild_name)
        if not rows:
            await interaction.followup.send(
                f"No scores found for **{guild_name}** today.", ephemeral=True
            )
            return
        SCORES = {row["player_name"]: row["score"] for row in rows}
        MI_SCORES = dict(sorted(SCORES.items(), key=lambda x: _score_to_float(x[1]), reverse=True))
        label = f"today ({now.strftime('%d %b %Y')})"
        if view == "percent":
            total = sum(_score_to_float(r['score']) for r in rows)
            lines = [
                f"`{i+1}.` **{key}**: {value} ({_score_to_float(value) / total * 100:.1f}%)"
                if total else f"`{i+1}.` **{key}**: {value} (0%)"
                for i, (key, value) in enumerate(MI_SCORES.items())
            ]
        else:
            lines = [f"`{i+1}.` **{key}**: {value}" for i, (key, value) in enumerate(MI_SCORES.items())]
        await _send_chunked(interaction, f"📊 **{guild_name}** — {label}", lines)

    @guild_scores.command(name="week", description="Show weekly total scores for your guild")
    @app_commands.describe(
        day="Day of the current month whose week to show (default: current week)",
        view="Show the weekly total or individual daily scores (default: total)",
        format="Output format: message or CSV file (default: message)",
    )
    @app_commands.choices(
        view=[
            app_commands.Choice(name="Total", value="total"),
            app_commands.Choice(name="Daily", value="daily"),
            app_commands.Choice(name="Percent", value="percent"),
        ],
        format=[
            app_commands.Choice(name="Message", value="message"),
            app_commands.Choice(name="CSV File", value="csv"),
        ]
    )
    async def gs_week(self, interaction: discord.Interaction, day: int | None = None, view: str = "total", format: str = "message"):
        await interaction.response.defer(ephemeral=True)
        guild_name = await self._get_guild_name(interaction)
        if guild_name is None:
            return
        isKnube = interaction.user.id == 1391487700242141347
        now = datetime.now()
        if day is not None:
            try:
                ref_date = now.replace(day=day)
            except ValueError:
                await interaction.followup.send(
                    f"Invalid day **{day}** for the current month.", ephemeral=True
                )
                return
        else:
            ref_date = now
        label = ref_date.strftime("week of %d %b %Y")
        scores_rows = get_total_weekly_leaderboard(guild_name, ref_date)
        if not scores_rows:
            await interaction.followup.send(
                f"No scores found for **{guild_name}** for the {label}.", ephemeral=True
            )
            return

        from src.database import _week_dates
        dates = _week_dates(ref_date)

        if format == "csv":
            # Create CSV data
            csv_data = []
            mon_abbr = ref_date.strftime("%b")
            
            for i, row in enumerate(scores_rows):
                csv_row = {
                    "Rank": i + 1,
                    "Player": row['player_name'],
                    "Total_Score": _fmt_score(row['total_score'])
                }
                
                if view == "daily":
                    for d in dates:
                        day_label = _DAY_NAMES[datetime.strptime(d, '%d_%m_%Y').weekday()]
                        csv_row[day_label] = row.get(d, "—")
                elif view == "percent":
                    grand_total = sum(r["total_score"] for r in scores_rows)
                    percentage = (row['total_score'] / grand_total * 100) if grand_total else 0
                    csv_row["Percentage"] = f"{percentage:.1f}%"
                
                csv_data.append(csv_row)
            
            filename = f"guild_weekly_scores_{ref_date.strftime('%Y_%m_%d')}.csv"
            csv_file = _create_csv_file(csv_data, filename)
            await interaction.followup.send(
                f"📊 **{guild_name}** weekly scores for {label}",
                file=csv_file,
                ephemeral=True
            )
        else:
            # Original message format
            if view == "daily":
                mon_abbr = ref_date.strftime("%b")
                lines = []
                for i, row in enumerate(scores_rows):
                    daily = " | ".join(
                        f"{int(d[:2])} {mon_abbr}: {row[d]}" if row.get(d) else f"{int(d[:2])} {mon_abbr}: —"
                        for d in dates
                    )
                    lines.append(f"`{i+1}.` **{row['player_name']}** [{daily}] — **Total: {_fmt_score(row['total_score'])}**")
            elif view == "percent":
                grand_total = sum(row["total_score"] for row in scores_rows)
                lines = [
                    f"`{i+1}.` **{row['player_name']}**: {_fmt_score(row['total_score'])} ({row['total_score'] / grand_total * 100:.1f}%)"
                    if grand_total else f"`{i+1}.` **{row['player_name']}**: {_fmt_score(row['total_score'])} (0%)"
                    for i, row in enumerate(scores_rows)
                ]
            else:
                SCORES = {row["player_name"]: row["total_score"] for row in scores_rows}
                if isKnube and "KNUBE" in SCORES:
                    knube_row = next((r for r in scores_rows if r["player_name"] == "KNUBE"), None)
                    first_row = scores_rows[0]
                    knube_scores = [v for k, v in (knube_row or {}).items() if isinstance(v, str) and v]
                    first_scores = [v for k, v in first_row.items() if isinstance(v, str) and v]
                    if knube_scores and first_scores:
                        SCORES["KNUBE"] = knubeScore(knube_scores[0], first_scores[0])
                MI_SCORES = dict(sorted(SCORES.items(), key=lambda x: x[1], reverse=True))
                lines = [f"`{i+1}.` **{name}**: {_fmt_score(val)}" for i, (name, val) in enumerate(MI_SCORES.items())]
            await _send_chunked(interaction, f"📊 **{guild_name}** — {label}", lines)

    @guild_scores.command(name="month", description="Show monthly scores for your guild")
    @app_commands.describe(
        week_day="Compare scores on each occurrence of this weekday across the month",
        month="Month to show (default: current month)",
        format="Output format: message or CSV file (default: message)",
    )
    @app_commands.choices(
        week_day=_WEEK_DAY_CHOICES,
        month=_MONTH_CHOICES,
        format=[
            app_commands.Choice(name="Message", value="message"),
            app_commands.Choice(name="CSV File", value="csv"),
        ]
    )
    async def gs_month(
        self,
        interaction: discord.Interaction,
        week_day: int | None = None,
        month: int | None = None,
        format: str = "message",
    ):
        await interaction.response.defer(ephemeral=True)
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
                await interaction.followup.send(
                    f"No scores found for **{guild_name}** on {label}.", ephemeral=True
                )
                return
            
            if format == "csv":
                csv_data = []
                for i, row in enumerate(scores_rows):
                    csv_row = {
                        "Rank": i + 1,
                        "Player": row['player_name'],
                        "Total_Score": _fmt_score(row['total_score'])
                    }
                    for d in dates:
                        day_label = _DAY_NAMES[datetime.strptime(d, '%d_%m_%Y').weekday()]
                        csv_row[day_label] = row.get(d, "—")
                    csv_data.append(csv_row)
                
                filename = f"guild_monthly_{_DAY_NAMES[week_day].lower()}s_{now.year}_{month_num:02d}.csv"
                csv_file = _create_csv_file(csv_data, filename)
                await interaction.followup.send(
                    f"📊 **{guild_name}** — {label}",
                    file=csv_file,
                    ephemeral=True
                )
            else:
                lines = []
                for i, row in enumerate(scores_rows):
                    daily = " | ".join(
                        f"{int(d[:2])} {mon_abbr}: {row[d]}" if row.get(d) else f"{int(d[:2])} {mon_abbr}: —"
                        for d in dates
                    )
                    lines.append(f"`{i+1}.` **{row['player_name']}** [{daily}] — **Total: {_fmt_score(row['total_score'])}**")
                await _send_chunked(interaction, f"📊 **{guild_name}** — {label}", lines)
        else:
            label = month_label
            scores_rows = get_total_monthly_leaderboard(guild_name, now.year, month_num)
            if not scores_rows:
                await interaction.followup.send(
                    f"No scores found for **{guild_name}** in {label}.", ephemeral=True
                )
                return
            
            if format == "csv":
                csv_data = [
                    {
                        "Rank": i + 1,
                        "Player": row['player_name'],
                        "Total_Score": _fmt_score(row['total_score'])
                    }
                    for i, row in enumerate(scores_rows)
                ]
                
                filename = f"guild_monthly_scores_{now.year}_{month_num:02d}.csv"
                csv_file = _create_csv_file(csv_data, filename)
                await interaction.followup.send(
                    f"📊 **{guild_name}** — {label}",
                    file=csv_file,
                    ephemeral=True
                )
            else:
                lines = [
                    f"`{i+1}.` **{row['player_name']}**: {_fmt_score(row['total_score'])}"
                    for i, row in enumerate(scores_rows)
                ]
                await _send_chunked(interaction, f"📊 **{guild_name}** — {label}", lines)

    # ── /user-score ────────────────────────────────────────────────────
    user_score = app_commands.Group(name="user-score", description="Show your scores")

    @user_score.command(name="today", description="Show your score for today")
    @app_commands.describe(view="Show your score or your % of total guild damage today (default: score)")
    @app_commands.choices(view=[
        app_commands.Choice(name="Score", value="score"),
        app_commands.Choice(name="Percent", value="percent"),
    ])
    async def us_today(self, interaction: discord.Interaction, view: str = "score"):
        await interaction.response.defer(ephemeral=True)
        player_name = await self._get_player_name(interaction)
        if player_name is None:
            return
        now = datetime.now()
        entry = get_today_score(player_name)
        if not entry:
            await interaction.followup.send("No score found for you today.", ephemeral=True)
            return
        label = f"today ({now.strftime('%d %b %Y')})"
        if view == "percent":
            player = get_player_by_discord_id(str(interaction.user.id))
            guild_name = None
            if player and player["game_guild_id"]:
                from src.database import _connect
                with _connect() as con:
                    g = con.execute("SELECT name FROM game_guilds WHERE id = ?", (player["game_guild_id"],)).fetchone()
                if g:
                    guild_name = g["name"]
            if not guild_name:
                await interaction.followup.send("Could not determine your guild for % calculation.", ephemeral=True)
                return
            guild_rows = get_today_guild_scores(guild_name)
            total = sum(_score_to_float(r['score']) for r in guild_rows)
            pct = _score_to_float(entry['score']) / total * 100 if total else 0
            msg = f"Rank **#{entry['rank']}** — **{entry['score']}** ({pct:.1f}% of guild total {_fmt_score(total)})"
        else:
            msg = f"Rank **#{entry['rank']}** — **{entry['score']}**"
        await interaction.followup.send(
            f"📊 **{player_name}** — {label}\n{msg}", ephemeral=True
        )

    @user_score.command(name="week", description="Show your scores for a week")
    @app_commands.describe(
        day="Day of the current month whose week to show (default: current week)",
        view="Show the weekly total or individual daily scores (default: total)",
        format="Output format: message or CSV file (default: message)",
    )
    @app_commands.choices(
        view=[
            app_commands.Choice(name="Total", value="total"),
            app_commands.Choice(name="Daily", value="daily"),
            app_commands.Choice(name="Percent", value="percent"),
        ],
        format=[
            app_commands.Choice(name="Message", value="message"),
            app_commands.Choice(name="CSV File", value="csv"),
        ]
    )
    async def us_week(self, interaction: discord.Interaction, day: int | None = None, view: str = "total", format: str = "message"):
        await interaction.response.defer(ephemeral=True)
        player_name = await self._get_player_name(interaction)
        if player_name is None:
            return
        now = datetime.now()
        if day is not None:
            try:
                ref_date = now.replace(day=day)
            except ValueError:
                await interaction.followup.send(
                    f"Invalid day **{day}** for the current month.", ephemeral=True
                )
                return
        else:
            ref_date = now
        label = ref_date.strftime("week of %d %b %Y")
        data = get_player_weekly_scores(player_name, ref_date)
        if not data["days_present"]:
            await interaction.followup.send(
                f"No scores found for the {label}.", ephemeral=True
            )
            return
        from src.database import _week_dates
        dates = _week_dates(ref_date)
        mon_abbr = ref_date.strftime("%b")
        
        if format == "csv":
            csv_data = []
            csv_row = {
                "Player": player_name,
                "Total_Score": _fmt_score(data['total_score']),
                "Days_Submitted": data['days_present']
            }
            
            if view == "daily":
                for d in dates:
                    day_label = _DAY_NAMES[datetime.strptime(d, '%d_%m_%Y').weekday()]
                    csv_row[day_label] = data.get(d, "—")
            elif view == "percent":
                player = get_player_by_discord_id(str(interaction.user.id))
                guild_name = None
                if player and player["game_guild_id"]:
                    from src.database import _connect
                    with _connect() as con:
                        g = con.execute("SELECT name FROM game_guilds WHERE id = ?", (player["game_guild_id"],)).fetchone()
                    if g:
                        guild_name = g["name"]
                if guild_name:
                    guild_rows = get_total_weekly_leaderboard(guild_name, ref_date)
                    guild_total = sum(r["total_score"] for r in guild_rows)
                    pct = data["total_score"] / guild_total * 100 if guild_total else 0
                    csv_row["Guild_Total"] = _fmt_score(guild_total)
                    csv_row["Percentage"] = f"{pct:.1f}%"
            
            csv_data.append(csv_row)
            
            filename = f"user_weekly_scores_{player_name}_{ref_date.strftime('%Y_%m_%d')}.csv"
            csv_file = _create_csv_file(csv_data, filename)
            await interaction.followup.send(
                f"📊 **{player_name}** weekly scores for {label}",
                file=csv_file,
                ephemeral=True
            )
        else:
            # Original message format
            if view == "daily":
                lines = [
                    f"{int(d[:2])} {mon_abbr} ({_DAY_NAMES[datetime.strptime(d, '%d_%m_%Y').weekday()][:3]}): {data[d]}"
                    if data.get(d) else
                    f"{int(d[:2])} {mon_abbr} ({_DAY_NAMES[datetime.strptime(d, '%d_%m_%Y').weekday()][:3]}): —"
                    for d in dates
                ] + [f"**Total: {_fmt_score(data['total_score'])}**"]
            elif view == "percent":
                player = get_player_by_discord_id(str(interaction.user.id))
                guild_name = None
                if player and player["game_guild_id"]:
                    from src.database import _connect
                    with _connect() as con:
                        g = con.execute("SELECT name FROM game_guilds WHERE id = ?", (player["game_guild_id"],)).fetchone()
                    if g:
                        guild_name = g["name"]
                if not guild_name:
                    await interaction.followup.send("Could not determine your guild for % calculation.", ephemeral=True)
                    return
                guild_rows = get_total_weekly_leaderboard(guild_name, ref_date)
                guild_total = sum(r["total_score"] for r in guild_rows)
                pct = data["total_score"] / guild_total * 100 if guild_total else 0
                lines = [f"**{_fmt_score(data['total_score'])}** ({pct:.1f}% of guild total {_fmt_score(guild_total)}, {data['days_present']} day(s) submitted)"]
            else:
                lines = [f"**Total: {_fmt_score(data['total_score'])}** ({data['days_present']} day(s) submitted)"]
            await interaction.followup.send(
                f"📊 **{player_name}** — {label}\n" + "\n".join(lines), ephemeral=True
            )

    @user_score.command(name="month", description="Show your scores for a month")
    @app_commands.describe(
        week_day="Compare your scores on each occurrence of this weekday across the month",
        month="Month to show (default: current month)",
        format="Output format: message or CSV file (default: message)",
    )
    @app_commands.choices(
        week_day=_WEEK_DAY_CHOICES,
        month=_MONTH_CHOICES,
        format=[
            app_commands.Choice(name="Message", value="message"),
            app_commands.Choice(name="CSV File", value="csv"),
        ]
    )
    async def us_month(
        self,
        interaction: discord.Interaction,
        week_day: int | None = None,
        month: int | None = None,
        format: str = "message",
    ):
        await interaction.response.defer(ephemeral=True)
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
                await interaction.followup.send(
                    f"No scores found for {label}.", ephemeral=True
                )
                return
            
            if format == "csv":
                csv_data = []
                csv_row = {
                    "Player": player_name,
                    "Total_Score": _fmt_score(data['total_score']),
                    "Days_Submitted": data['days_present'],
                    "Weekday": _DAY_NAMES[week_day],
                    "Month_Year": month_label
                }
                
                for d in dates:
                    day_label = _DAY_NAMES[datetime.strptime(d, '%d_%m_%Y').weekday()]
                    csv_row[day_label] = data.get(d, "—")
                
                csv_data.append(csv_row)
                
                filename = f"user_monthly_{_DAY_NAMES[week_day].lower()}s_{player_name}_{now.year}_{month_num:02d}.csv"
                csv_file = _create_csv_file(csv_data, filename)
                await interaction.followup.send(
                    f"📊 **{player_name}** — {label}",
                    file=csv_file,
                    ephemeral=True
                )
            else:
                lines = [
                    f"{int(d[:2])} {mon_abbr}: {data[d]}" if data.get(d) else f"{int(d[:2])} {mon_abbr}: —"
                    for d in dates
                ] + [f"**Total: {_fmt_score(data['total_score'])}**"]
                await interaction.followup.send(
                    f"📊 **{player_name}** — {label}\n" + "\n".join(lines), ephemeral=True
                )
        else:
            label = month_label
            data = get_player_monthly_scores(player_name, now.year, month_num)
            if not data["days_present"]:
                await interaction.followup.send(
                    f"No scores found for {label}.", ephemeral=True
                )
                return
            
            if format == "csv":
                csv_data = [{
                    "Player": player_name,
                    "Month_Year": month_label,
                    "Days_Submitted": data['days_present'],
                    "Total_Score": _fmt_score(data['total_score'])
                }]
                
                filename = f"user_monthly_scores_{player_name}_{now.year}_{month_num:02d}.csv"
                csv_file = _create_csv_file(csv_data, filename)
                await interaction.followup.send(
                    f"📊 **{player_name}** — {label}",
                    file=csv_file,
                    ephemeral=True
                )
            else:
                lines = [
                    f"Days submitted: **{data['days_present']}**",
                    f"Total: **{_fmt_score(data['total_score'])}**",
                ]
                await interaction.followup.send(
                    f"📊 **{player_name}** — {label}\n" + "\n".join(lines), ephemeral=True
                )

    # ── /guild-attendance ──────────────────────────────────────────────────────
    
    @app_commands.command(name="guild-attendance", description="Show who didn't attack this week and which days they missed")
    @app_commands.describe(
        day="Day of the current month whose week to show (default: current week)",
        format="Output format: message, CSV file, or heat chart (default: message)",
    )
    @app_commands.choices(
        format=[
            app_commands.Choice(name="Message", value="message"),
            app_commands.Choice(name="CSV File", value="csv"),
            app_commands.Choice(name="Heat Chart", value="heatmap"),
        ]
    )
    async def guild_attendance(self, interaction: discord.Interaction, day: int | None = None, format: str = "message"):
        """Standalone attendance command - same functionality as guild-scores attendance"""
        await interaction.response.defer(ephemeral=True)
        guild_name = await self._get_guild_name_from_user_submissions(interaction)
        if guild_name is None:
            return
        
        now = datetime.now()
        if day is not None:
            try:
                ref_date = now.replace(day=day)
            except ValueError:
                await interaction.followup.send(
                    f"Invalid day **{day}** for the current month.", ephemeral=True
                )
                return
        else:
            ref_date = now
        
        label = ref_date.strftime("week of %d %b %Y")
        attendance_data = get_guild_weekly_attendance(guild_name, ref_date)
        
        if format == "csv":
            # Create CSV data
            csv_data = []
            
            # Sort players alphabetically (case-insensitive)
            players_no_attacks = sorted(attendance_data['players_no_attacks'], key=lambda x: x['username'].lower())
            players_missing_days = sorted(attendance_data['players_missing_days'], key=lambda x: x['player_name'].lower())
            
            # Add players who didn't attack at all
            for player in players_no_attacks:
                csv_data.append({
                    "Player": player['username'],
                    "Status": "No attacks",
                    "Days_Missed": "All 7 days",
                    "Days_Attacked": 0
                })
            
            # Add players who attacked but missed some days
            for player in players_missing_days:
                missed_dates = [datetime.strptime(d, '%d_%m_%Y').strftime('%a %d') for d in player['missed_days']]
                csv_data.append({
                    "Player": player['player_name'],
                    "Status": "Partial attendance",
                    "Days_Missed": ", ".join(missed_dates) if missed_dates else "None",
                    "Days_Attacked": player['attacked_days']
                })
            
            if not csv_data:
                await interaction.followup.send(
                    f"🎉 Perfect attendance! All guild members attacked every day during the {label}.",
                    ephemeral=True
                )
                return
            
            filename = f"guild_attendance_{ref_date.strftime('%Y_%m_%d')}.csv"
            csv_file = _create_csv_file(csv_data, filename)
            await interaction.followup.send(
                f"📊 **{guild_name}** attendance for {label}",
                file=csv_file,
                ephemeral=True
            )
        elif format == "heatmap":
            # Heat chart format
            try:
                heatmap_file = _create_attendance_heatmap(attendance_data, guild_name, label)
                await interaction.followup.send(
                    f"📊 **{guild_name}** Boss hit heatmap for {label}",
                    file=heatmap_file,
                    ephemeral=True
                )
            except ValueError as e:
                await interaction.followup.send(
                    f"Unable to create heatmap: {str(e)}",
                    ephemeral=True
                )
        else:
            # Message format
            lines = []
            
            # Sort players alphabetically (case-insensitive)
            players_no_attacks = sorted(attendance_data['players_no_attacks'], key=lambda x: x['username'].lower())
            players_missing_days = sorted(attendance_data['players_missing_days'], key=lambda x: x['player_name'].lower())
            
            # Players who didn't attack at all this week
            if players_no_attacks:
                lines.append("❌ **No attacks this week:**")
                for player in players_no_attacks:
                    lines.append(f"  • {player['username']}")
                lines.append("")
            
            # Players who attacked but missed some days
            if players_missing_days:
                lines.append("⚠️ **Missed some days:**")
                for player in players_missing_days:
                    missed_days = [datetime.strptime(d, '%d_%m_%Y').strftime('%a %d') for d in player['missed_days']]
                    missed_str = ", ".join(missed_days)
                    lines.append(f"  • **{player['player_name']}** missed: {missed_str} ({player['attacked_days']}/7 days)")
                lines.append("")
            
            if not lines:
                await interaction.followup.send(
                    f"🎉 **{guild_name}** — Perfect attendance for {label}!\nAll registered guild members attacked every day.",
                    ephemeral=True
                )
                return
            
            header = f"📊 **{guild_name}** — Attendance for {label}"
            await _send_chunked(interaction, header, lines)

    @app_commands.command(name="guild-damage-report", description="Show weekly damage dealt by each player in table format")
    @app_commands.describe(
        day="Day of the current month whose week to show (default: current week)",
        format="Output format: message, CSV file, or heat chart (default: heatmap)",
    )
    @app_commands.choices(
        format=[
            app_commands.Choice(name="Message", value="message"),
            app_commands.Choice(name="CSV File", value="csv"),
            app_commands.Choice(name="Heat Chart", value="heatmap"),
        ]
    )
    async def guild_damage_report(self, interaction: discord.Interaction, day: int | None = None, format: str = "heatmap"):
        """Weekly damage report showing damage dealt by each player across weekdays"""
        await interaction.response.defer(ephemeral=True)
        # Use channel context first, then fallback to registered player
        guild_name = await self._get_guild_name(interaction)
        print("Guild name from channel context:", guild_name)
        if guild_name is None:
            return
        now = datetime.now()
        if day is not None:
            try:
                ref_date = now.replace(day=day)
            except ValueError:
                await interaction.followup.send(
                    f"Invalid day **{day}** for the current month.", ephemeral=True
                )
                return
        else:
            ref_date = now
        label = ref_date.strftime("week of %d %b %Y")
        damage_data = get_total_weekly_leaderboard(guild_name, ref_date)
        # Check if this is the special user and boost KNUBE's score
        isKnube = interaction.user.id == 1391487700242141347
        if isKnube and damage_data:
            # Find KNUBE in the data
            knube_player = None
            for player in damage_data:
                if player['player_name'] == 'KNUBE':
                    knube_player = player
                    break
            if knube_player:
                # Get week dates
                from src.database import _week_dates
                dates = _week_dates(ref_date)
                # For each day, find the highest damage and add it to KNUBE's score for that day
                for date in dates:
                    highest_daily_score = 0
                    for player in damage_data:
                        day_score = player.get(date)
                        if day_score:
                            score_float = _score_to_float(day_score)
                            if score_float > highest_daily_score:
                                highest_daily_score = score_float
                    # Add the highest daily score to KNUBE's score for this day
                    if highest_daily_score > 0:
                        current_knube_score = 0
                        if knube_player.get(date):
                            current_knube_score = _score_to_float(knube_player[date])
                        new_score = current_knube_score + highest_daily_score
                        knube_player[date] = _fmt_score(new_score)
                # Recalculate KNUBE's total_score
                new_total = 0
                for date in dates:
                    if knube_player.get(date):
                        new_total += _score_to_float(knube_player[date])
                knube_player['total_score'] = new_total
                # Re-sort the data by total score
                damage_data = sorted(damage_data, key=lambda x: x['total_score'], reverse=True)
        if not damage_data:
            await interaction.followup.send(
                f"No damage data found for **{guild_name}** for the {label}.",
                ephemeral=True
            )
            return
        if format == "csv":
            # Create CSV data
            csv_data = []
            # Get week dates for column headers
            from src.database import _week_dates
            dates = _week_dates(ref_date)
            day_labels = [datetime.strptime(date, '%d_%m_%Y').strftime('%a') for date in dates]
            for player in damage_data:
                row_data = {
                    "Player": player['player_name'],
                    "Total_Damage": _fmt_score(player['total_score']),
                    "Days_Present": player['days_present']
                }
                # Add daily scores
                for i, date in enumerate(dates):
                    day_score = player.get(date)
                    row_data[day_labels[i]] = day_score if day_score else "0"
                csv_data.append(row_data)
            filename = f"guild_damage_{ref_date.strftime('%Y_%m_%d')}.csv"
            csv_file = _create_csv_file(csv_data, filename)
            await interaction.followup.send(
                f"💥 **{guild_name}** damage report for {label}",
                file=csv_file,
                ephemeral=True
            )
        elif format == "heatmap":
            # Heat chart format
            try:
                heatmap_file = _create_damage_heatmap(damage_data, guild_name, label, ref_date)
                await interaction.followup.send(
                    f"💥 **{guild_name}** damage heatmap for {label}",
                    file=heatmap_file,
                    ephemeral=True
                )
            except ValueError as e:
                await interaction.followup.send(
                    f"Unable to create damage heatmap: {str(e)}",
                    ephemeral=True
                )
        else:
            # Message format
            lines = []
            if damage_data:
                lines.append("💥 **Top damage dealers this week:**")
                lines.append("")
                # Get week dates for day headers
                from src.database import _week_dates
                dates = _week_dates(ref_date)
                day_labels = [datetime.strptime(date, '%d_%m_%Y').strftime('%a') for date in dates]
                # First, collect all the formatted data to calculate proper column widths
                formatted_data = []
                for player in damage_data[:20]:
                    formatted_player = {
                        'name': player['player_name'],
                        'total': _fmt_score(player['total_score']),
                        'days': str(player['days_present']),
                        'daily_scores': []
                    }
                    for date in dates:
                        day_score = player.get(date)
                        if day_score:
                            formatted_score = _fmt_score(_score_to_float(day_score))
                            formatted_player['daily_scores'].append(formatted_score)
                        else:
                            formatted_player['daily_scores'].append('--')
                    formatted_data.append(formatted_player)
                # Calculate column widths based on actual formatted content and display width
                player_width = max(_display_width('Player'), max(_display_width(p['name']) for p in formatted_data)) + 3
                total_width = max(_display_width('Total'), max(_display_width(p['total']) for p in formatted_data)) + 2
                days_width = max(_display_width('Days'), max(_display_width(p['days']) for p in formatted_data)) + 2
                # Calculate daily column widths
                daily_widths = []
                for i, day_label in enumerate(day_labels):
                    day_scores = [p['daily_scores'][i] for p in formatted_data]
                    width = max(_display_width(day_label), max(_display_width(score) for score in day_scores)) + 2
                    daily_widths.append(width)
                # Helper function to pad text accounting for display width
                def _pad_text(text: str, target_width: int) -> str:
                    display_w = _display_width(text)
                    padding = target_width - display_w
                    return text + ' ' * padding
                # Create header row with proper alignment
                header_parts = [
                    _pad_text('Player', player_width),
                    _pad_text('Total', total_width),
                    _pad_text('Days', days_width)
                ]
                for i, day in enumerate(day_labels):
                    header_parts.append(_pad_text(day, daily_widths[i]))
                header = "".join(header_parts)
                lines.append(f"```{header}")
                lines.append("-" * len(header))
                # Add player data rows with proper alignment
                for player_data in formatted_data:
                    row_parts = [
                        _pad_text(player_data['name'], player_width),
                        _pad_text(player_data['total'], total_width),
                        _pad_text(player_data['days'], days_width)
                    ]
                    for i, score in enumerate(player_data['daily_scores']):
                        row_parts.append(_pad_text(score, daily_widths[i]))
                    row = "".join(row_parts)
                    lines.append(row)
                lines.append("```")
            if not lines:
                await interaction.followup.send(
                    f"No damage data found for **{guild_name}** for the {label}.",
                    ephemeral=True
                )
                return
            header = f"💥 **{guild_name}** — Damage Report for {label}"
            await _send_chunked(interaction, header, lines)


def _create_damage_heatmap(damage_data: list, guild_name: str, label: str, ref_date: datetime) -> discord.File:
    """Create a heatmap visualization of guild damage data with gradient colors."""
    
    # Use the proper font configuration from mi_utils
    from src.mi_utils import _configure_chart_font
    _configure_chart_font()
    
    if not damage_data:
        raise ValueError("No damage data to display")
    
    # Get week dates
    from src.database import _week_dates
    dates = _week_dates(ref_date)
    day_labels = [datetime.strptime(date, '%d_%m_%Y').strftime('%a') for date in dates]
    
    # Sort players by total damage (already sorted, but ensure it)
    sorted_players = sorted(damage_data, key=lambda x: x['total_score'], reverse=True)
    player_names = [player['player_name'] for player in sorted_players]
    
    # Calculate daily totals for column sums
    daily_totals = [0.0] * 7
    for player in sorted_players:
        for i, date in enumerate(dates):
            day_score = player.get(date)
            if day_score:
                daily_totals[i] += _score_to_float(day_score)
    
    # Calculate grand total
    grand_total = sum(daily_totals)
    
    # Create damage matrix - normalized scores for color scaling per day independently
    # Add one extra column for total damage
    data_matrix = []
    
    # First pass: collect scores for each day separately to determine per-day scaling
    daily_scores = [[] for _ in range(7)]
    total_scores = []
    
    for player in sorted_players:
        for i, date in enumerate(dates):
            day_score = player.get(date)
            if day_score:
                score_float = _score_to_float(day_score)
                if score_float > 0:
                    daily_scores[i].append(score_float)
        
        # Collect total scores for total column scaling
        if player['total_score'] > 0:
            total_scores.append(player['total_score'])
    
    # Calculate min/max for each day independently
    daily_min_max = []
    for day_scores in daily_scores:
        if day_scores:
            daily_min_max.append((min(day_scores), max(day_scores)))
        else:
            daily_min_max.append((0, 0))
    
    # Calculate min/max for total column
    total_min = min(total_scores) if total_scores else 0
    total_max = max(total_scores) if total_scores else 0
    
    # Second pass: create normalized matrix using percentile-based distribution (7 days + 1 total column)
    for player in sorted_players:
        row = []
        # Add daily scores with percentile-based normalization for binomial-like distribution
        for i, date in enumerate(dates):
            day_score = player.get(date)
            if day_score:
                score_float = _score_to_float(day_score)
                if score_float > 0 and daily_scores[i]:
                    # Find percentile rank of this score within the day
                    sorted_day_scores = sorted(daily_scores[i])
                    rank = sorted_day_scores.index(score_float)
                    percentile = rank / (len(sorted_day_scores) - 1) if len(sorted_day_scores) > 1 else 0.5
                    
                    # Map percentile to color range with binomial distribution
                    # 0-25% = red range (0.3-0.45)
                    # 25-75% = yellow range (0.45-0.75) 
                    # 75-100% = green range (0.75-1.0)
                    if percentile <= 0.25:
                        normalized = 0.3 + (percentile / 0.25) * 0.15  # 0.3 to 0.45
                    elif percentile <= 0.75:
                        normalized = 0.45 + ((percentile - 0.25) / 0.5) * 0.3  # 0.45 to 0.75
                    else:
                        normalized = 0.75 + ((percentile - 0.75) / 0.25) * 0.25  # 0.75 to 1.0
                else:
                    normalized = 0.1  # Grey for zero damage
            else:
                normalized = 0.1  # Grey for no damage
            row.append(normalized)
        
        # Add total damage column using same percentile approach
        total_score = player['total_score']
        if total_score > 0 and total_scores:
            sorted_totals = sorted(total_scores)
            rank = sorted_totals.index(total_score)
            percentile = rank / (len(sorted_totals) - 1) if len(sorted_totals) > 1 else 0.5
            
            # Same binomial mapping for totals
            if percentile <= 0.25:
                total_normalized = 0.3 + (percentile / 0.25) * 0.15
            elif percentile <= 0.75:
                total_normalized = 0.45 + ((percentile - 0.25) / 0.5) * 0.3
            else:
                total_normalized = 0.75 + ((percentile - 0.75) / 0.25) * 0.25
        else:
            total_normalized = 0.1  # Grey for zero total damage
        row.append(total_normalized)
        
        data_matrix.append(row)
    
    # Add column sum row - use static green color (1.0) for all sum cells
    sum_row = []
    for _ in daily_totals:
        sum_row.append(1.0)  # Static green color for daily sums
    
    # Add grand total cell - also green
    sum_row.append(1.0)  # Static green color for grand total
    
    data_matrix.append(sum_row)
    
    # Convert to numpy array
    data = np.array(data_matrix)
    
    # Create the heatmap with extra width for total column
    fig_width = max(10, min(18, len(player_names) * 0.4))
    fig, ax = plt.subplots(figsize=(10, fig_width))
    
    # Create custom colormap: extensive grey range (no damage), then red to muted green (damage range)
    colors = ['#888888', '#8a8a8a', '#8c8c8c', '#8e8e8e', '#909090', '#929292', '#949494', '#969696', '#989898', '#9a9a9a', '#9c9c9c', '#9e9e9e', '#a0a0a0', '#a2a2a2', '#a4a4a4', '#a6a6a6', '#a8a8a8', '#aaaaaa', '#cc4444', '#d14433', '#d64433', '#db4433', '#e04433', '#e54433', '#ea4433', '#ef4433', '#f44433', '#f94433', '#dd5533', '#e15533', '#e55533', '#e95533', '#ed5533', '#f15533', '#f55533', '#ee6622', '#f16622', '#f46622', '#f76622', '#fa6622', '#fd6622', '#ff7711', '#ff7a11', '#ff7d11', '#ff8011', '#ff8311', '#ff8611', '#ff8900', '#ff8b00', '#ff8d00', '#ff8f00', '#ff9100', '#ff9300', '#ff9500', '#ff9700', '#ff9900', '#ff9b00', '#ff9d00', '#ff9f00', '#ffa100', '#ffa300', '#ffa500', '#ffa700', '#ffa900', '#ffab00', '#ffad00', '#ffaf00', '#ffb100', '#ffcc00', '#ffce00', '#ffd000', '#ffd200', '#ffd400', '#ffd600', '#ffd800', '#ffda00', '#ffdc00', '#ffde00', '#ffe000', '#dddd00', '#dbdb00', '#d9d900', '#d7d700', '#d5d500', '#d3d300', '#d1d100', '#cfcf00', '#cdcd00', '#cbcb00', '#ccee00', '#caec00', '#c8ea00', '#c6e800', '#c4e600', '#c2e400', '#c0e200', '#bee000', '#bcde00', '#badc00', '#99cc33', '#97ca33', '#95c833', '#93c633', '#91c433', '#8fc233', '#8dc033', '#8bbe33', '#89bc33', '#87ba33', '#66aa66']
    n_bins = len(colors)
    cmap = mcolors.LinearSegmentedColormap.from_list('damage', colors, N=n_bins)
    
    # Create heatmap
    im = ax.imshow(data, cmap=cmap, aspect='auto', vmin=0, vmax=1)
    
    # Set ticks and labels (7 days + 1 total column)
    column_labels = day_labels + ['Total']
    ax.set_xticks(range(8))
    ax.set_xticklabels(column_labels, fontsize=10)
    
    row_labels = player_names + ['Daily Sum']
    ax.set_yticks(range(len(row_labels)))
    ax.set_yticklabels(row_labels, fontsize=9)
    
    # Move x-axis to the top
    ax.xaxis.tick_top()
    ax.xaxis.set_label_position('top')
    
    # Add grid
    ax.set_xticks(np.arange(-.5, 8, 1), minor=True)
    ax.set_yticks(np.arange(-.5, len(row_labels), 1), minor=True)
    ax.grid(which='minor', color='gray', linestyle='-', linewidth=0.5)
    
    # Add text annotations with damage values
    for i in range(len(sorted_players)):
        player = sorted_players[i]
        # Daily scores
        for j in range(7):
            day_score = player.get(dates[j])
            if day_score:
                score_float = _score_to_float(day_score)
                if score_float > 0:
                    formatted_score = _fmt_score(score_float)
                    # Always use black text, bold formatting
                    ax.text(j, i, formatted_score, ha='center', va='center', 
                           fontsize=7, fontweight='bold', color='black')
        
        # Total score column
        total_score = player['total_score']
        if total_score > 0:
            formatted_total = _fmt_score(total_score)
            # Always use black text, bold formatting
            ax.text(7, i, formatted_total, ha='center', va='center', 
                   fontsize=7, fontweight='bold', color='black')
    
    # Add column sum annotations (always black text, bold formatting)
    sum_row_index = len(sorted_players)
    for j in range(7):
        if daily_totals[j] > 0:
            formatted_sum = _fmt_score(daily_totals[j])
            ax.text(j, sum_row_index, formatted_sum, ha='center', va='center', 
                   fontsize=7, fontweight='bold', color='black')
    
    # Grand total (always black text, bold formatting)
    if grand_total > 0:
        formatted_grand_total = _fmt_score(grand_total)
        ax.text(7, sum_row_index, formatted_grand_total, ha='center', va='center', 
               fontsize=7, fontweight='bold', color='black')
    
    # Set title and labels
    ax.set_title(f'{guild_name} - Weekly Damage Report {label}', fontsize=14, fontweight='bold', pad=20)
    
    # Adjust layout to prevent legend cutoff
    plt.tight_layout()
    
    # Save to BytesIO
    buffer = BytesIO()
    plt.savefig(buffer, format='png', dpi=150, bbox_inches='tight')
    buffer.seek(0)
    plt.close(fig)
    
    # Create discord file
    filename = f"damage_heatmap_{guild_name.replace(' ', '_')}.png"
    return discord.File(buffer, filename=filename)


async def setup(bot: commands.Bot):
    await bot.add_cog(ScoresCog(bot))
