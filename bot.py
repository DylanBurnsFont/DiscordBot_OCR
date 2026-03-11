import discord
from discord.ext import commands
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
BOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.database import init_db

EXTENSIONS = [
    "src.cogs.mi",
    "src.cogs.register",
    "src.cogs.ping",
]


class DiscordBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)
        self.ROOT_DIR = ROOT_DIR

    async def setup_hook(self):
        db_path = BOT_DIR / "data" / "bot.db"
        init_db(db_path)
        print(f"Database ready: {db_path}")
        for extension in EXTENSIONS:
            await self.load_extension(extension)

    async def on_ready(self):
        print(f"Logged in as {self.user} (app_id={self.application_id})")
        guild_id = os.getenv("DISCORD_GUILD_ID")
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild)
            guild_synced = await self.tree.sync(guild=guild)
            print(f"Guild sync: {len(guild_synced)} command(s) synced instantly to guild {guild_id}")

        synced = await self.tree.sync()
        print(f"Global sync: {len(synced)} command(s) (up to 1 hour to propagate)")


bot = DiscordBot()


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: commands.CommandError):
    print(f"App command error: {repr(error)}")
    if interaction.response.is_done():
        await interaction.followup.send(f"Command failed: {error}", ephemeral=True)
    else:
        await interaction.response.send_message(f"Command failed: {error}", ephemeral=True)


def load_env_file(env_path):
    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue

        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def get_token():
    token = os.getenv("DISCORD_BOT_TOKEN") or os.getenv("TOKEN")
    if not token:
        raise RuntimeError("Missing DISCORD_BOT_TOKEN (or TOKEN) environment variable.")
    return token


def get_google_credentials_from_env():
    creds = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not creds:
        raise RuntimeError(
            "Missing GOOGLE_APPLICATION_CREDENTIALS. Set it in system env or DiscordBot/env.env"
        )

    creds_path = Path(creds)
    if not creds_path.is_absolute():
        creds_path = (ROOT_DIR / creds).resolve()

    if not creds_path.exists():
        raise RuntimeError(f"Google credentials file not found: {creds_path}")

    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(creds_path)
    return str(creds_path)


if __name__ == "__main__":
    load_env_file(Path(__file__).resolve().parent / "env.env")
    load_env_file(ROOT_DIR / ".env")
    get_google_credentials_from_env()
    bot.run(get_token())

