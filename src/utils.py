import cv2
import pandas as pd
import os
import json

def writeCSV(data, filename="monster_invasion_scores.csv"):
    """
    Writes a dictionary to a CSV file.
    
    :param data: Dictionary containing the data to write
    :param filename: Name of the output CSV file
    """
    if not filename.endswith(".csv"):
        filename += ".csv"

    df = pd.DataFrame(list(data.items()), columns=['Name', 'Score'])
    df.to_csv(os.path.join("output", filename), index=False)

def downscaleImage(image, max_width=None, max_height=None):
    """
    Downscale an image to fit within max_width and max_height while maintaining aspect ratio.
    
    :param image: Image to downscale
    :param max_width: Maximum width of the downscaled image
    :param max_height: Maximum height of the downscaled image

    :return: Downscaled image
    """
    h, w = image.shape[:2]

    if max_width is None and max_height is None:
        return image

    scale_w = max_width / w if max_width else float('inf')
    scale_h = max_height / h if max_height else float('inf')
    scale = min(scale_w, scale_h, 1.0)  # never upscale

    new_w = int(w * scale)
    new_h = int(h * scale)

    return cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_AREA)

def fuse_rects(rects, y_thresh=10, x_gap_thresh=5):
    """
    Fuses rectangles that are close to each other in the y-axis and overlapping or close in the x-axis.
    
    :param rects: List of rectangles to fuse, each represented as (x, y, w, h)
    :param y_thresh: Threshold for y-axis proximity to consider rectangles in the same row
    :param x_gap_thresh: Threshold for x-axis gap to consider rectangles overlapping or close
    
    :return: List of fused rectangles
    """
    fused = []

    for rect in rects:
        x, y, w, h = rect
        merged = False
        for i, (fx, fy, fw, fh) in enumerate(fused):
            # Check if y overlap or close
            if abs(y - fy) < y_thresh:
                # Check if x ranges overlap or are very close
                if (x <= fx + fw + x_gap_thresh) and (fx <= x + w + x_gap_thresh):
                    # Merge rectangles
                    nx = min(x, fx)
                    ny = min(y, fy)
                    nw = max(x + w, fx + fw) - nx
                    nh = max(y + h, fy + fh) - ny
                    fused[i] = (nx, ny, nw, nh)
                    merged = True
                    break
        if not merged:
            fused.append(rect)
    return fused

def group_by_rows(rects, y_thresh=10):
    """
    Groups the rectangles into rows based on their y-coordinate proximity.
    
    :param rects: List of rectangles to group, each represented as (x, y, w, h)
    :param y_thresh: Threshold for y-coordinate proximity to consider rectangles in the same row

    :return: List of rows, each row is a list of rectangles
    """
    rows = []

    for rect in rects:
        x, y, w, h = rect
        added = False

        for row in rows:
            # Compare to the first rect in the row
            rx, ry, rw, rh = row[0]
            if abs(y - ry) < y_thresh:
                row.append(rect)
                added = True
                break

        if not added:
            # Start a new row
            rows.append([rect])

    return rows

def xyxy_to_xywh(box):
    """
    Converts xyxy bounding box to xywh
    
    :param box: Bounding box in xyxy format

    :return: Bounding box in xywh format
    """
    x1, y1, x2, y2 = box
    x = x1
    y = y1
    w = x2 - x1
    h = y2 - y1
    return (x, y, w, h)

def processTop3(box):
    """
    Generates three individual boxes of the top 3 scores area
    
    :param box: Bounding box in xywh format
    :return: List of three bounding boxes in xywh format
    """
    x, y, w, h = box
    x1, y1, x2, y2 = x, y, x + w, y + h
    first3rdX = int(x1+(x2-x1)*0.3)
    second3rdX = int(x1+(x2-x1)*0.66)

    top1box = xyxy_to_xywh((first3rdX, y1, second3rdX, y2))
    top2box = xyxy_to_xywh((x1, y1, first3rdX, y2))
    top3box = xyxy_to_xywh((second3rdX, y1, x2, y2))

    return [top1box, top2box, top3box]

def loadJsonFile(filePath):
    """
    Loads a Json file and return the data
    
    :param filePath: Path to the JSON file

    :return: Data loaded from the JSON file
    """
    if filePath:
        with open(filePath, 'r') as f:
            data = json.load(f)
        return data