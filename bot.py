import discord
from discord import app_commands
import cv2
import os
import csv
import asyncio
from pathlib import Path
from google.cloud import vision
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.utils import downscaleImage


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
        tree.clear_commands(guild=guild)
        await tree.sync(guild=guild)
        print(f"Cleared guild slash command overrides for {guild_id}")

    synced = await tree.sync()
    print(f"Synced {len(synced)} global slash command(s)")


async def run_ocr_for_attachments(interaction, attachments, override=False):
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

        csv_name = f"monster_invasion_scores_{interaction.id}.csv"
        csv_path = temp_dir / csv_name
        write_scores_csv(scores, csv_path)

        if len(response_text) > 1900:
            await interaction.edit_original_response(
                content="Parsed scores are long, sending CSV file.",
                attachments=[discord.File(csv_path)],
            )
        else:
            await interaction.edit_original_response(
                content=response_text,
                attachments=[discord.File(csv_path)],
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
)
async def mi_command(
    interaction: discord.Interaction,
    image1: discord.Attachment,
    image2: discord.Attachment | None = None,
    image3: discord.Attachment | None = None,
    image4: discord.Attachment | None = None,
    image5: discord.Attachment | None = None,
    override: bool = False,
):
    print(f"/mi invoked by {interaction.user} ({interaction.user.id})")
    await interaction.response.defer(thinking=True)
    await interaction.edit_original_response(content="Received command. Processing OCR...")
    await run_ocr_for_attachments(interaction, [image1, image2, image3, image4, image5], override=override)


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

