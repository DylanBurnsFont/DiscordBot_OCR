import cv2
import csv
import os

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

    # Fill in dictionary with the rest of the results
    for i in range(len(names)):
        MI_SCORES[names[i]] = values[i]

    # Get rid of whitespace and special characters
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
