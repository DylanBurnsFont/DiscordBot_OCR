import discord
from discord import app_commands
import cv2
import os
import csv
import asyncio
from pathlib import Path
from typing import Literal

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

from google.cloud import vision
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.utils import downscaleImage


def _configure_chart_font():
    candidates = [
        "Microsoft YaHei",
        "Microsoft JhengHei",
        "SimHei",
        "Noto Sans CJK SC",
        "Noto Sans CJK TC",
        "Arial Unicode MS",
    ]
    installed = {font.name for font in font_manager.fontManager.ttflist}
    selected = next((name for name in candidates if name in installed), "DejaVu Sans")
    plt.rcParams["font.family"] = selected
    plt.rcParams["axes.unicode_minus"] = False


_configure_chart_font()


def detect_text_raw(vision_client, image):
    success, encoded_image = cv2.imencode('.png', image)
    if not success:
        raise RuntimeError("Failed to encode image as PNG for Vision API request.")

    request_image = vision.Image(content=encoded_image.tobytes())
    response = vision_client.text_detection(image=request_image)

    if response.error.message:
        raise RuntimeError(f"Google Vision API error: {response.error.message}")

    if not response.text_annotations:
        return ""

    return response.text_annotations[0].description


def iter_images(path):
    if os.path.isfile(path):
        yield path
        return

    for file_name in sorted(os.listdir(path)):
        full_path = os.path.join(path, file_name)
        if os.path.isfile(full_path):
            yield full_path


def _is_image_filename(filename):
    return filename.lower().endswith((".png", ".jpg", ".jpeg", ".bmp", ".webp"))


def write_scores_csv(scores, out_file):
    with open(out_file, "w", newline="", encoding="utf-8") as file_obj:
        writer = csv.writer(file_obj)
        writer.writerow(["Name", "Score"])
        for name, score in scores.items():
            writer.writerow([name, score])


def _parse_score_value(score):
    digits_only = score[:-1]
    mult = score[-1]
    return float(digits_only), mult


def write_scores_chart(scores, out_file):
    labels = []
    values = []

    for name, score in scores.items():
        parsed_score, mult = _parse_score_value(score)
        if parsed_score is None:
            continue
        if mult == "K":
            parsed_score /= 1000000
        elif mult == "M":
            parsed_score /= 100 
        elif mult == "B":
            parsed_score *= 1
        elif mult == "T":
            parsed_score *= 1000
        labels.append(name)
        values.append(round(parsed_score,2))

    if not values:
        raise ValueError("No numeric scores available to build chart.")

    fig_width = max(8, min(20, len(labels) * 0.9))
    fig, axis = plt.subplots(figsize=(fig_width, 6))
    bars = axis.bar(labels, values)
    axis.set_title("Monster Invasion Scores")
    axis.set_xlabel("Player")
    axis.set_ylabel("Score")
    axis.tick_params(axis="x", rotation=45)

    for bar, value in zip(bars, values):
        axis.text(
            bar.get_x() + bar.get_width() / 2,
            value,
            str(value),
            ha="center",
            va="bottom",
            fontsize=8,
        )

    fig.tight_layout()
    fig.savefig(out_file, dpi=150)
    plt.close(fig)

def parseResults(results):
    MI_SCORES = {}
    removeString = ["zzz", "Zzz", "ZZz", "ZZZ", "zzZ", "zZz", "Zzz", "zzZ", "ZZz", "ZZZ"]

    for item in removeString:
        try:
            results.remove(item)
        except ValueError:
            pass
    # Get rid of the header lines
    dets = results[5:]
    top3 = dets[:6]
    # Get rid of top3 from general results
    dets = dets[6:]
    MI_SCORES[top3[0]] = top3[3]
    MI_SCORES[top3[1]] = top3[4]
    MI_SCORES[top3[2]] = top3[5]

    # Get rid of the position of each player
    dets = [item for item in dets if not item.isdigit()]
    names = dets[::2]
    values = dets[1::2]

    # Fill in dictionay with the rest of the results
    for i in range(len(names)):
        MI_SCORES[names[i]] = values[i]

    # Get rid of whitespace and scpecial characters
    for key in MI_SCORES:
        for char in ' <>_-':
            MI_SCORES[key] = MI_SCORES[key].replace(char, '')
    
    return MI_SCORES


def extract_scores_from_files(vision_client, image_paths, max_height=1024):
    results = []
    for image_path in image_paths:
        image = cv2.imread(str(image_path))
        if image is None:
            continue

        image = downscaleImage(image, max_height=max_height)
        raw_text = detect_text_raw(vision_client, image)
        for line in raw_text.splitlines():
            cleaned = line.strip()
            if cleaned:
                results.append(cleaned)

    return parseResults(results)


def build_response_text(scores):
    if not scores:
        return "No scores found. Make sure the attachment(s) are MI leaderboard screenshots."

    lines = ["Parsed Monster Invasion scores:"]
    for name, score in scores.items():
        lines.append(f"- {name}: {score}")
    return "\n".join(lines)


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


intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


@client.event
async def on_ready():
    print(f"Logged in as {client.user} (app_id={client.application_id})")
    guild_id = os.getenv("DISCORD_GUILD_ID")
    if guild_id:
        guild = discord.Object(id=int(guild_id))
        tree.copy_global_to(guild=guild)
        guild_synced = await tree.sync(guild=guild)
        print(f"Guild sync: {len(guild_synced)} command(s) synced instantly to guild {guild_id}")

    synced = await tree.sync()
    print(f"Global sync: {len(synced)} command(s) (up to 1 hour to propagate)")


async def run_ocr_for_attachments(interaction, attachments, override=False, output_format="csv"):
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

    temp_dir = ROOT_DIR / "output" / "discord_tmp"
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


@tree.command(name="mi", description="Extract Monster Invasion scores from attached screenshot(s)")
@app_commands.describe(
    image1="Leaderboard screenshot (required)",
    image2="Leaderboard screenshot (optional)",
    image3="Leaderboard screenshot (optional)",
    image4="Leaderboard screenshot (optional)",
    image5="Leaderboard screenshot (optional)",
    output_format="Return `csv` (default) or `chart`",
)
async def mi_command(
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
    await run_ocr_for_attachments(
        interaction,
        [image1, image2, image3, image4, image5],
        override=override,
        output_format=output_format,
    )


# ---------------------------------------------------------------------------
# /register-guild  (owner only)
# ---------------------------------------------------------------------------

def _is_owner(interaction: discord.Interaction) -> bool:
    owner_id = os.getenv("DISCORD_OWNER_ID", "")
    return owner_id and str(interaction.user.id) == owner_id


@tree.command(name="register-guild", description="Register a new in-game guild (owner only)")
@app_commands.describe(
    guild_name="Exact in-game guild name",
)
async def register_guild_command(
    interaction: discord.Interaction,
    guild_name: str,
):
    if not _is_owner(interaction):
        await interaction.response.send_message("You are not authorised to use this command.", ephemeral=True)
        return

    # TODO: check if guild_name already exists in DB
    # TODO: insert into game_guilds (name, discord_server_id, created_at)
    # TODO: reply with the new guild's ID so it can be referenced

    await interaction.response.send_message(
        f"Guild **{guild_name}** registered. (DB not wired up yet)",
        ephemeral=True,
    )


# ---------------------------------------------------------------------------
# /register  (any user) — guild select → name modal
# ---------------------------------------------------------------------------

# TODO: replace with a live DB query when database is wired up
REGISTERED_GUILDS: list[str] = [
    "AboveAll",
    "ArcheroMod",
    "MoeCafe",
]


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

        # TODO: check if player already registered (query players by discord_user_id)
        # TODO: look up game_guilds.id by guild_name
        # TODO: insert into players (discord_user_id, username, game_guild_id, joined_guild_at)

        guild_info = f" in guild **{guild_name}**" if guild_name else " with no guild"
        await interaction.response.send_message(
            f"Registered **{game_name}**{guild_info}. (DB not wired up yet)",
            ephemeral=True,
        )


class GuildSelect(discord.ui.Select):
    def __init__(self):
        options = [discord.SelectOption(label="No guild", value="__none__")] + [
            discord.SelectOption(label=g, value=g) for g in REGISTERED_GUILDS
        ]
        super().__init__(placeholder="Select your in-game guild…", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        guild_name = None if self.values[0] == "__none__" else self.values[0]
        await interaction.response.send_modal(RegisterModal(guild_name))


class GuildSelectView(discord.ui.View):
    def __init__(self):
        super().__init__()
        self.add_item(GuildSelect())


@tree.command(name="register", description="Register yourself to use the bot")
async def register_command(interaction: discord.Interaction):
    await interaction.response.send_message(
        "Select your in-game guild to continue registration:",
        view=GuildSelectView(),
        ephemeral=True,
    )


@tree.command(name="ping", description="Health check for the bot")
async def ping_command(interaction: discord.Interaction):
    await interaction.response.send_message("pong")


@tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    print(f"App command error: {repr(error)}")
    if interaction.response.is_done():
        await interaction.followup.send(f"Command failed: {error}", ephemeral=True)
    else:
        await interaction.response.send_message(f"Command failed: {error}", ephemeral=True)


if __name__ == "__main__":
    load_env_file(Path(__file__).resolve().parent / "env.env")
    load_env_file(ROOT_DIR / ".env")
    get_google_credentials_from_env()
    client.run(get_token())

