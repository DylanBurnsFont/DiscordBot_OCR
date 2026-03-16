import cv2
import csv
import json
import os
import re
import traceback

from src.database import _score_to_float

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

from google.cloud import vision
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

# Load name corrections
# Cache for guild-specific corrections
_GUILD_CORRECTIONS_CACHE = {}

def _load_guild_corrections(guild_name: str) -> dict[str, str]:
    """Load corrections for a specific guild."""
    if guild_name in _GUILD_CORRECTIONS_CACHE:
        return _GUILD_CORRECTIONS_CACHE[guild_name]
    
    corrections = {}
    try:
        # Try guild-specific corrections file first
        guild_corrections_path = os.path.join(os.path.dirname(__file__), '..', 'corrections', f'{guild_name}.json')
        with open(guild_corrections_path, 'r', encoding='utf-8') as f:
            corrections = json.load(f)
            print(f"✅ Loaded {len(corrections)} corrections for guild: {guild_name}")
    except FileNotFoundError:
        # Fallback to general corrections.json
        try:
            general_corrections_path = os.path.join(os.path.dirname(__file__), '..', 'corrections', 'corrections.json')
            with open(general_corrections_path, 'r', encoding='utf-8') as f:
                corrections = json.load(f)
                print(f"⚠️ No guild-specific corrections found for {guild_name}, using general corrections.json")
        except FileNotFoundError:
            print(f"❌ No corrections file found for guild {guild_name} - name corrections disabled")
        except Exception as e:
            print(f"❌ Error loading general corrections.json: {e}")
    except Exception as e:
        print(f"❌ Error loading guild corrections for {guild_name}: {e}")
    
    _GUILD_CORRECTIONS_CACHE[guild_name] = corrections
    return corrections

def _apply_name_corrections(name: str, guild_name: str = None) -> str:
    """Apply OCR name corrections based on guild-specific corrections."""
    if not guild_name:
        return name
    
    corrections = _load_guild_corrections(guild_name)
    corrected_name = corrections.get(name, name)
    return corrected_name


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
        values.append(round(parsed_score, 2))

    if not values:
        raise ValueError("No numeric scores available to build chart.")

    fig_width = max(8, min(20, len(labels) * 0.9))
    fig, axis = plt.subplots(figsize=(fig_width, 6))
    bars = axis.bar(labels, values)
    axis.set_title("Monster Invasion Scores")
    axis.set_xlabel("Players")
    axis.set_ylabel("Score (B)")
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


_SCORE_RE = re.compile(r'^\d+(\.\d+)?[KMBTkmbt]$')


def _correct_and_validate_score(s: str) -> tuple[bool, str]:
    """
    Correct common OCR errors in score strings and validate them.
    Returns (is_valid, corrected_string).
    """
    corrected = s
    if s and s[-1] == "3":
        corrected = s[:-1] + "B"
    elif s and s[-1] == "7":
        corrected = s[:-1] + "T"
    elif s and s[-1] == "1":
        corrected = s[:-1] + "T"
    
    is_valid = bool(_SCORE_RE.match(corrected))
    return is_valid, corrected


def _is_valid_score(s: str) -> bool:
    """Check if a score string is valid (with OCR error correction)."""
    is_valid, _ = _correct_and_validate_score(s)
    return is_valid


def _is_valid_name(s: str) -> bool:
    # Must be at least 2 characters and not itself a score token
    return len(s) >= 2 and not _is_valid_score(s)


def parseResults(results, guild_name=None):
    MI_SCORES = {}
    removeString = ["zzz", "Zzz", "ZZz", "ZZZ", "zzZ", "zZz", "Zzz", "zzZ", "ZZz", "ZZZ"]

    for item in removeString:
        try:
            results.remove(item)
        except ValueError:
            pass
    # Get rid of the header lines
    print(results)
    
    # Find "Member Ranking" and start processing from after it
    try:
        member_ranking_index = results.index("Member Ranking")
        dets = results[member_ranking_index + 1:]
        print(f"Found 'Member Ranking' at index {member_ranking_index}, starting from index {member_ranking_index + 1}")
    except ValueError:
        # Fallback to old behavior if "Member Ranking" not found
        print("'Member Ranking' not found, using fallback (skip first 5 elements)")
        dets = results[5:]
    dets = [item for item in dets if not item.isdigit()]
    top3 = dets[:6]
    
    # Clean whitespace and special characters from top3 scores before regex matching
    cleaned_top3 = []
    for item in top3:
        cleaned_item = item
        for char in ' <>_-':
            cleaned_item = cleaned_item.replace(char, '')
        cleaned_top3.append(cleaned_item)
    
    # Get rid of top3 from general results
    dets = dets[6:]
    # Process top-3 scores with OCR correction
    names = [x for x in cleaned_top3 if not _SCORE_RE.match(x)]
    scores = [x for x in cleaned_top3 if _SCORE_RE.match(x)]
    top3_pairs = zip(names, scores)
    for name, score in top3_pairs:
        # print(name, score)
        if not _SCORE_RE.match(name):
            corrected_name = _apply_name_corrections(name, guild_name=guild_name)
            is_valid, corrected_score = _correct_and_validate_score(score)
            if is_valid:
                MI_SCORES[corrected_name] = corrected_score

    # print(dets)
    # Get rid of the position of each player

    # Sliding window: find consecutive (valid_name, valid_score) pairs.
    # This is robust to garbled rows where OCR drops/adds tokens — unlike
    # strict dets[::2]/dets[1::2] which shifts all subsequent pairs on any misalignment.
    i = 0
    while i < len(dets) - 1:
        name = dets[i]
        score = dets[i + 1]
        if _is_valid_name(name):
            corrected_name = _apply_name_corrections(name, guild_name=guild_name)
            is_valid, corrected_score = _correct_and_validate_score(score)
            if is_valid:
                MI_SCORES[corrected_name] = corrected_score
                i += 2
            else:
                i += 1
        else:
            i += 1

    # Get rid of whitespace and special characters from final scores
    for key in MI_SCORES:
        for char in ' <>_-':
            MI_SCORES[key] = MI_SCORES[key].replace(char, '')

    # print(f"🎯 Final processed names: {list(MI_SCORES.keys())}")
    return MI_SCORES


def extract_scores_from_files(vision_client, image_paths, max_height=1024, guild_name=None):
    merged: dict[str, str] = {}
    for image_path in image_paths:
        image = cv2.imread(str(image_path))
        if image is None:
            continue

        image = downscaleImage(image, max_height=max_height)
        raw_text = detect_text_raw(vision_client, image)
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]

        try:
            scores = parseResults(lines, guild_name=guild_name)
        except Exception as exc:
            print(f"Could not parse {image_path}: {exc}")
            traceback.print_exc()
            continue

        for name, score in scores.items():
            if name not in merged or _score_to_float(score) > _score_to_float(merged[name]):
                merged[name] = score

    return merged


def build_response_text(scores):
    if not scores:
        return "No scores found. Make sure the attachment(s) are MI leaderboard screenshots."

    else:
        return "Scores processed!"
