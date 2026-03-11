import os

import discord
from discord import app_commands
from discord.ext import commands

from src.database import (
    add_guild,
    get_guild_by_name,
    get_all_guilds,
    add_player,
    get_player_by_discord_id,
    update_player_username,
)


def _is_owner(interaction: discord.Interaction) -> bool:
    owner_id = os.getenv("DISCORD_OWNER_ID", "")
    return bool(owner_id and str(interaction.user.id) == owner_id)


class RegisterModal(discord.ui.Modal, title="Register"):
    game_name = discord.ui.TextInput(
        label="In-game name",
        placeholder="Your exact in-game player name",
        required=True,
        max_length=64,
    )

    def __init__(self, selected_guild: str | None):
        super().__init__()
        self.selected_guild = selected_guild

    async def on_submit(self, interaction: discord.Interaction):
        game_name = self.game_name.value.strip()
        guild_name = self.selected_guild

        existing = get_player_by_discord_id(str(interaction.user.id))
        if existing:
            await interaction.response.send_message(
                f"You are already registered as **{existing['username']}**.",
                ephemeral=True,
            )
            return

        guild_id: int | None = None
        if guild_name:
            row = get_guild_by_name(guild_name)
            if row is None:
                await interaction.response.send_message(
                    f"Guild **{guild_name}** not found. Ask an admin to register it first.",
                    ephemeral=True,
                )
                return
            guild_id = row["id"]

        add_player(str(interaction.user.id), game_name, guild_id)

        guild_info = f" in guild **{guild_name}**" if guild_name else " with no guild"
        await interaction.response.send_message(
            f"Registered **{game_name}**{guild_info}!",
            ephemeral=True,
        )


class GuildSelect(discord.ui.Select):
    def __init__(self):
        db_guilds = [row["name"] for row in get_all_guilds()]
        options = [discord.SelectOption(label="No guild", value="__none__")] + [
            discord.SelectOption(label=g, value=g) for g in db_guilds
        ]
        if not options:
            options = [discord.SelectOption(label="No guild", value="__none__")]
        super().__init__(placeholder="Select your in-game guild…", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        guild_name = None if self.values[0] == "__none__" else self.values[0]
        await interaction.response.send_modal(RegisterModal(guild_name))


class GuildSelectView(discord.ui.View):
    def __init__(self):
        super().__init__()
        self.add_item(GuildSelect())


class RegisterCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="register-guild", description="Register a new in-game guild (owner only)")
    @app_commands.describe(guild_name="Exact in-game guild name")
    async def register_guild_command(self, interaction: discord.Interaction, guild_name: str):
        if not _is_owner(interaction):
            await interaction.response.send_message("You are not authorised to use this command.", ephemeral=True)
            return

        existing = get_guild_by_name(guild_name)
        if existing:
            await interaction.response.send_message(
                f"Guild **{guild_name}** already exists (id={existing['id']}).",
                ephemeral=True,
            )
            return

        server_id = str(interaction.guild_id) if interaction.guild_id else None
        try:
            new_id = add_guild(guild_name, server_id)
        except Exception as exc:
            await interaction.response.send_message(f"Failed to add guild: {exc}", ephemeral=True)
            return

        await interaction.response.send_message(
            f"Guild **{guild_name}** registered (id={new_id}).",
            ephemeral=True,
        )

    @app_commands.command(name="register", description="Register yourself to use the bot")
    async def register_command(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "Select your in-game guild to continue registration:",
            view=GuildSelectView(),
            ephemeral=True,
        )

    @app_commands.command(name="update-ign", description="Update your in-game name")
    @app_commands.describe(new_name="Your new in-game player name")
    async def update_ign_command(self, interaction: discord.Interaction, new_name: str):
        existing = get_player_by_discord_id(str(interaction.user.id))
        if not existing:
            await interaction.response.send_message(
                "You are not registered yet. Use /register first.", ephemeral=True
            )
            return

        update_player_username(str(interaction.user.id), new_name.strip())
        await interaction.response.send_message(
            f"IGN updated to **{new_name.strip()}**.", ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(RegisterCog(bot))