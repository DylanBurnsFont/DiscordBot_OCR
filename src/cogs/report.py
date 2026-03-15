import os
from datetime import datetime, timedelta
import zoneinfo
import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

from src.database import get_today_guild_scores, _score_to_float, get_total_weekly_leaderboard, get_all_guilds

# Import the damage heatmap function from scores.py
from src.cogs.scores import _create_damage_heatmap

# Default configuration for backward compatibility
DEFAULT_GUILD_NAME = "AboveAll"
REPORT_HOUR = 3
REPORT_MINUTE = 00
REPORT_TZ = zoneinfo.ZoneInfo("Europe/Madrid")

# Guild configurations - can be moved to database later
GUILD_CONFIGS = {
    "AboveAll": {
        "channel_id_env": "MI_REPORT_CHANNEL_ID",
        "role_name": "AboveAll",  # Discord role name that identifies guild members
        "owner_id_env": "MI_REPORT_RECIPIENT_ID"
    }
    # Add variables to .env file 
    # Future guilds can be added here:
    # "OtherGuild": {
    #     "channel_id_env": "OTHER_GUILD_CHANNEL_ID", 
    #     "role_name": "OtherGuild",
    #     "owner_id_env": "OTHER_GUILD_RECIPIENT_ID"
    # }
}


class ReportCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._last_fired: str | None = None
        self.daily_report.start()

    def cog_unload(self):
        self.daily_report.cancel()

    def _log_command_usage(self, command_name: str, user: discord.User, **kwargs):
        """Log command usage to file."""
        try:
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            params = " ".join([f"{k}='{v}'" for k, v in kwargs.items() if v is not None])
            params_str = f" with {params}" if params else ""
            log_entry = f"[{timestamp}] {command_name} command called by {user.display_name} (ID: {user.id}){params_str}\n"
            
            # Use absolute path to ensure log file is created in bot directory
            log_path = os.path.abspath("command_usage_log.txt")
            with open(log_path, "a", encoding="utf-8") as log_file:
                log_file.write(log_entry)
            print(f"[report] Logged {command_name} command to {log_path}")
        except Exception as e:
            print(f"[report] Failed to log {command_name} usage: {e}")

    def get_guild_from_user_roles(self, member: discord.Member) -> str | None:
        """Determine which guild a Discord user belongs to based on their roles."""
        if not member.roles:
            return None
            
        user_role_names = {role.name for role in member.roles}
        
        # Check each configured guild for role matches
        for guild_name, config in GUILD_CONFIGS.items():
            if config["role_name"] in user_role_names:
                return guild_name
        
        return None

    async def get_role_members_to_mention(self, guild: discord.Guild, role_name: str) -> list[str]:
        """Get Discord user IDs of members with a specific role for mentioning."""
        role = discord.utils.get(guild.roles, name=role_name)
        if not role:
            return []
        
        return [str(member.id) for member in role.members if not member.bot]

    async def send_guild_report(self, guild_name: str, config: dict, yesterday: datetime):
        """Send damage report for a specific guild to its configured channel."""
        channel_id = os.getenv(config["channel_id_env"], "").strip()
        if not channel_id:
            print(f"[report] {config['channel_id_env']} not set — skipping {guild_name}")
            return

        channel = self.bot.get_channel(int(channel_id))
        if channel is None:
            print(f"[report] Channel {channel_id} not found — skipping {guild_name}")
            return

        # Get weekly damage data for the previous day's week
        damage_data = get_total_weekly_leaderboard(guild_name, yesterday)
        
        # Determine who to mention
        owner_id = os.getenv(config["owner_id_env"], "")
        mention_parts = []
        if owner_id:
            mention_parts.append(f"<@{owner_id}>")
        
        # Add role-based mentions if channel is in a guild
        if channel.guild:
            role_members = await self.get_role_members_to_mention(channel.guild, config["role_name"])
            if role_members:
                role_mentions = " ".join([f"<@{user_id}>" for user_id in role_members[:5]])  # Limit to 5 mentions
                mention_parts.append(role_mentions)
        
        mention = " ".join(mention_parts) if mention_parts else ""

        if not damage_data:
            await channel.send(f"{mention} No damage data found for **{guild_name}** for the week of {yesterday.strftime('%d %b %Y')}.")
            return

        try:
            # Generate damage heatmap for the previous day's week
            label = yesterday.strftime("week of %d %b %Y")
            heatmap_file = _create_damage_heatmap(damage_data, guild_name, label, yesterday)
            header = f"{mention} 💥 **{guild_name}** damage heatmap for {label}"
            await channel.send(header, file=heatmap_file)
            print(f"[report] Sent damage heatmap for {guild_name} to {channel.name}")
        except Exception as e:
            print(f"[report] Error generating damage heatmap for {guild_name}: {e}")
            # Fallback to simple text message
            date_key = yesterday.strftime("%d_%m_%Y")
            rows = get_today_guild_scores(guild_name, date=date_key)
            if rows:
                sorted_rows = sorted(rows, key=lambda r: _score_to_float(r["score"]), reverse=True)
                lines = [f"`{i+1}.` **{r['player_name']}**: {r['score']}" for i, r in enumerate(sorted_rows)]
                header = f"{mention} 📊 **{guild_name}** — scores for {yesterday.strftime('%d %b %Y')}\n"
                await channel.send(header + "\n".join(lines))
            else:
                await channel.send(f"{mention} No scores recorded for **{guild_name}** on {yesterday.strftime('%d %b %Y')}.")

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

        yesterday = now - timedelta(days=1)
        
        # Send reports for all configured guilds
        for guild_name, config in GUILD_CONFIGS.items():
            try:
                await self.send_guild_report(guild_name, config, yesterday)
            except Exception as e:
                print(f"[report] Error sending report for {guild_name}: {e}")

    @daily_report.before_loop
    async def before_daily_report(self):
        await self.bot.wait_until_ready()
        print(f"[report] Loop ready. Will fire daily at {REPORT_HOUR:02d}:{REPORT_MINUTE:02d} {REPORT_TZ}")
        print(f"[report] Configured guilds: {', '.join(GUILD_CONFIGS.keys())}")

    @app_commands.command(name="manual-report", description="Manually trigger a damage report")
    @app_commands.describe(guild_name="Guild name (optional - will auto-detect from your roles if not specified)")
    async def manual_report(self, interaction: discord.Interaction, guild_name: str = None):
        """Manually trigger a damage report for a specific guild or user's guild."""
        # Log the command usage
        self._log_command_usage("manual-report", interaction.user, guild_name=guild_name)
            
        if guild_name and guild_name not in GUILD_CONFIGS:
            await interaction.response.send_message(f"Guild '{guild_name}' not found. Available guilds: {', '.join(GUILD_CONFIGS.keys())}", ephemeral=True)
            return
        
        # If no guild specified, try to determine from user's roles
        if not guild_name:
            if interaction.guild and hasattr(interaction.user, 'roles'):
                guild_name = self.get_guild_from_user_roles(interaction.user)
                if not guild_name:
                    await interaction.response.send_message(f"Could not determine your guild from roles. Available guilds: {', '.join(GUILD_CONFIGS.keys())}", ephemeral=True)
                    return
            else:
                guild_name = DEFAULT_GUILD_NAME  # Fallback
        
        yesterday = datetime.now(tz=REPORT_TZ) - timedelta(days=1)
        config = GUILD_CONFIGS[guild_name]
        
        await interaction.response.send_message(f"Generating damage report for **{guild_name}**...")
        
        try:
            # Send the report to the current channel instead of the configured channel
            damage_data = get_total_weekly_leaderboard(guild_name, yesterday)
            
            if not damage_data:
                await interaction.followup.send(f"No damage data found for **{guild_name}** for the week of {yesterday.strftime('%d %b %Y')}.")
                return

            label = yesterday.strftime("week of %d %b %Y")
            heatmap_file = _create_damage_heatmap(damage_data, guild_name, label, yesterday)
            header = f"💥 **{guild_name}** damage heatmap for {label}"
            await interaction.followup.send(header, file=heatmap_file)
            
        except Exception as e:
            await interaction.followup.send(f"Error generating damage report: {str(e)}")
            print(f"[report] Manual report error for {guild_name}: {e}")

    @app_commands.command(name="guild-status", description="Show guild configuration status (owner only)")
    async def guild_status(self, interaction: discord.Interaction):
        """Show guild configuration status."""
        # Check if user is the bot owner
        discord_owner_id = os.getenv("DISCORD_OWNER_ID")
        if not discord_owner_id or str(interaction.user.id) != discord_owner_id:
            await interaction.response.send_message("❌ This command can only be used by the bot owner.", ephemeral=True)
            return
        
        # Log the command usage
        self._log_command_usage("guild-status", interaction.user)
            
        embed = discord.Embed(title="Guild Configuration Status", color=0x00ff00)
        
        for guild_name, config in GUILD_CONFIGS.items():
            channel_id = os.getenv(config["channel_id_env"])
            owner_id = os.getenv(config["owner_id_env"])
            
            status_lines = []
            status_lines.append(f"🔘 **Role:** {config['role_name']}")
            
            if channel_id:
                channel = self.bot.get_channel(int(channel_id))
                if channel:
                    status_lines.append(f"✅ **Channel:** {channel.mention}")
                else:
                    status_lines.append(f"⚠️ **Channel:** ID {channel_id} (not found)")
            else:
                status_lines.append(f"❌ **Channel:** {config['channel_id_env']} not set")
            
            if owner_id:
                try:
                    owner = await self.bot.fetch_user(int(owner_id))
                    status_lines.append(f"✅ **Report to:** {owner.display_name} ({owner.mention})")
                except:
                    status_lines.append(f"⚠️ **Report to:** ID {owner_id} (not found)")
            else:
                status_lines.append(f"❌ **Report to:** {config['owner_id_env']} not set")
            
            embed.add_field(
                name=f"{guild_name}",
                value='\n'.join(status_lines),
                inline=True
            )
        
        embed.add_field(
            name="Next Report",
            value=f"⏰ Daily at {REPORT_HOUR:02d}:{REPORT_MINUTE:02d} {REPORT_TZ}",
            inline=False
        )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def cog_load(self):
        """Called when cog is loaded."""
        print(f"[report] Loaded with {len(GUILD_CONFIGS)} guild configurations")
        
        # Check environment variables
        missing_vars = []
        for guild_name, config in GUILD_CONFIGS.items():
            if not os.getenv(config["channel_id_env"]):
                missing_vars.append(f"{config['channel_id_env']} (for {guild_name})")
            if not os.getenv(config["owner_id_env"]):
                missing_vars.append(f"{config['owner_id_env']} (for {guild_name})")
        
        if missing_vars:
            print(f"[report] WARNING: Missing environment variables: {', '.join(missing_vars)}")
            print(f"[report] Some guilds may not receive reports until these are configured")
        else:
            print(f"[report] All environment variables configured")

    def add_guild_config(self, guild_name: str, channel_id_env: str, role_name: str, owner_id_env: str = None):
        """Helper method to add new guild configurations dynamically."""
        if guild_name in GUILD_CONFIGS:
            print(f"[report] WARNING: Overwriting existing guild configuration for {guild_name}")
        
        GUILD_CONFIGS[guild_name] = {
            "channel_id_env": channel_id_env,
            "role_name": role_name,
            "owner_id_env": owner_id_env or "MI_REPORT_RECIPIENT_ID"
        }
        print(f"[report] Added guild configuration for {guild_name} with role '{role_name}'")


async def setup(bot: commands.Bot):
    await bot.add_cog(ReportCog(bot))
    print("[report] Multi-guild damage report system loaded")
