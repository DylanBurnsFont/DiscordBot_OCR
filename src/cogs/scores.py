import os
import csv
import io
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
    get_guild_weekly_attendance,
    _score_to_float,
)

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

CANDIDATES = [
        "Microsoft YaHei",     # Chinese (Simplified) - best for CJK
        "Microsoft JhengHei",  # Chinese (Traditional) 
        "Malgun Gothic",       # Korean
        "Yu Gothic",           # Japanese
        "Segoe UI",
        "Segoe UI Historic",   # Better Unicode support
        "Calibri", 
        "Tahoma",
        "Arial",
        "Microsoft Sans Serif", # Fallback with decent Unicode
        "DejaVu Sans",
        "sans-serif"
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


def _create_attendance_heatmap(attendance_data: dict, guild_name: str, label: str) -> discord.File:
    """Create a heatmap visualization of guild attendance data."""
    
    # Suppress font warnings for missing glyphs (CJK characters)
    import warnings
    warnings.filterwarnings("ignore", message=".*missing from font.*")
    
    # Set matplotlib to use fonts that support CJK characters
    plt.rcParams['font.family'] = CANDIDATES 
    plt.rcParams['axes.unicode_minus'] = False  # Fix minus sign display
    
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
        """Get guild name from registered player data."""
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
    @app_commands.describe(view="Show raw scores or each player's % of total guild damage (default: scores)")
    @app_commands.choices(view=[
        app_commands.Choice(name="Scores", value="scores"),
        app_commands.Choice(name="Percent", value="percent"),
    ])
    async def gs_today(self, interaction: discord.Interaction, view: str = "scores"):
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
            await interaction.response.send_message(
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
                await interaction.response.send_message(
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
                await interaction.response.send_message(
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
                await interaction.response.send_message(
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
        player_name = await self._get_player_name(interaction)
        if player_name is None:
            return
        now = datetime.now()
        entry = get_today_score(player_name)
        if not entry:
            await interaction.response.send_message("No score found for you today.", ephemeral=True)
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
                await interaction.response.send_message("Could not determine your guild for % calculation.", ephemeral=True)
                return
            guild_rows = get_today_guild_scores(guild_name)
            total = sum(_score_to_float(r['score']) for r in guild_rows)
            pct = _score_to_float(entry['score']) / total * 100 if total else 0
            msg = f"Rank **#{entry['rank']}** — **{entry['score']}** ({pct:.1f}% of guild total {_fmt_score(total)})"
        else:
            msg = f"Rank **#{entry['rank']}** — **{entry['score']}**"
        await interaction.response.send_message(
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
            await interaction.response.send_message(
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
                    await interaction.response.send_message("Could not determine your guild for % calculation.", ephemeral=True)
                    return
                guild_rows = get_total_weekly_leaderboard(guild_name, ref_date)
                guild_total = sum(r["total_score"] for r in guild_rows)
                pct = data["total_score"] / guild_total * 100 if guild_total else 0
                lines = [f"**{_fmt_score(data['total_score'])}** ({pct:.1f}% of guild total {_fmt_score(guild_total)}, {data['days_present']} day(s) submitted)"]
            else:
                lines = [f"**Total: {_fmt_score(data['total_score'])}** ({data['days_present']} day(s) submitted)"]
            await interaction.response.send_message(
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
                await interaction.response.send_message(
                    f"📊 **{player_name}** — {label}",
                    file=csv_file,
                    ephemeral=True
                )
            else:
                lines = [
                    f"{int(d[:2])} {mon_abbr}: {data[d]}" if data.get(d) else f"{int(d[:2])} {mon_abbr}: —"
                    for d in dates
                ] + [f"**Total: {_fmt_score(data['total_score'])}**"]
                await interaction.response.send_message(
                    f"📊 **{player_name}** — {label}\n" + "\n".join(lines), ephemeral=True
                )
        else:
            label = month_label
            data = get_player_monthly_scores(player_name, now.year, month_num)
            if not data["days_present"]:
                await interaction.response.send_message(
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
                await interaction.response.send_message(
                    f"📊 **{player_name}** — {label}",
                    file=csv_file,
                    ephemeral=True
                )
            else:
                lines = [
                    f"Days submitted: **{data['days_present']}**",
                    f"Total: **{_fmt_score(data['total_score'])}**",
                ]
                await interaction.response.send_message(
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
        guild_name = await self._get_guild_name_from_user_submissions(interaction)
        if guild_name is None:
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
                await interaction.response.send_message(
                    f"🎉 Perfect attendance! All guild members attacked every day during the {label}.",
                    ephemeral=True
                )
                return
            
            filename = f"guild_attendance_{ref_date.strftime('%Y_%m_%d')}.csv"
            csv_file = _create_csv_file(csv_data, filename)
            await interaction.response.send_message(
                f"📊 **{guild_name}** attendance for {label}",
                file=csv_file,
                ephemeral=True
            )
        elif format == "heatmap":
            # Heat chart format
            try:
                heatmap_file = _create_attendance_heatmap(attendance_data, guild_name, label)
                await interaction.response.send_message(
                    f"📊 **{guild_name}** Boss hit heatmap for {label}",
                    file=heatmap_file,
                    ephemeral=False
                )
            except ValueError as e:
                await interaction.response.send_message(
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
                await interaction.response.send_message(
                    f"🎉 **{guild_name}** — Perfect attendance for {label}!\\nAll registered guild members attacked every day.",
                    ephemeral=True
                )
                return
            
            header = f"📊 **{guild_name}** — Attendance for {label}"
            await _send_chunked(interaction, header, lines)


async def setup(bot: commands.Bot):
    await bot.add_cog(ScoresCog(bot))
