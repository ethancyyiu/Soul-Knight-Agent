"""Image-only observations for Soul Knight. Uncalibrated classes stay unknown."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np


CAPTURES = Path("captures")
CALIBRATION_PATH = CAPTURES / "environment_calibration.json"
OBJECT_KINDS = ("enemy", "projectile", "wall", "door", "obstacle", "weapon")
BAR_KINDS = ("health", "energy")


def load_calibration() -> dict:
    if not CALIBRATION_PATH.exists():
        return {}
    return json.loads(CALIBRATION_PATH.read_text(encoding="utf-8"))


def save_bar(kind: str, frame, box: tuple[int, int, int, int]) -> None:
    if kind not in BAR_KINDS:
        raise ValueError(f"Unsupported bar: {kind}")
    x, y, width, height = box
    if width < 20 or height < 5:
        raise ValueError("Select the colored interior of the bar, including its full length.")
    data = load_calibration()
    if data.get("frame_size") != [frame.shape[1], frame.shape[0]]:
        data = {}
    data["frame_size"] = [frame.shape[1], frame.shape[0]]
    data[kind] = [x, y, width, height]
    CAPTURES.mkdir(parents=True, exist_ok=True)
    CALIBRATION_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def save_object_template(kind: str, frame, box: tuple[int, int, int, int]) -> Path:
    if kind not in OBJECT_KINDS:
        raise ValueError(f"Unsupported object: {kind}")
    x, y, width, height = box
    if width < 8 or height < 8:
        raise ValueError("Select a visible object at least 8 pixels wide and tall.")
    _, deviation = cv2.meanStdDev(frame[y:y + height, x:x + width])
    if float(deviation.max()) < 8:
        raise ValueError("Selection has too little visual detail; select the object itself.")
    CAPTURES.mkdir(parents=True, exist_ok=True)
    number = 1
    while (CAPTURES / f"environment_{kind}_{number}.png").exists():
        number += 1
    path = CAPTURES / f"environment_{kind}_{number}.png"
    if not cv2.imwrite(str(path), frame[y:y + height, x:x + width]):
        raise RuntimeError(f"Could not save {path}.")
    return path


def read_bars(frame, calibration: dict) -> dict[str, float | None]:
    if calibration.get("frame_size") != [frame.shape[1], frame.shape[0]]:
        return {kind: None for kind in BAR_KINDS}
    output = {}
    for kind in BAR_KINDS:
        box = calibration.get(kind)
        if box is None:
            output[kind] = None
            continue
        x, y, width, height = box
        if x < 0 or y < 0 or x + width > frame.shape[1] or y + height > frame.shape[0]:
            output[kind] = None
            continue
        hsv = cv2.cvtColor(frame[y:y + height, x:x + width], cv2.COLOR_BGR2HSV)
        hue, saturation, value = cv2.split(hsv)
        if kind == "health":
            colored = ((hue <= 10) | (hue >= 170)) & (saturation >= 85) & (value >= 55)
        else:
            colored = (hue >= 90) & (hue <= 130) & (saturation >= 75) & (value >= 55)
        columns = np.flatnonzero(np.mean(colored, axis=0) >= 0.2)
        # No colored pixels could mean an empty bar or an obscured HUD.
        output[kind] = round(float((columns[-1] + 1) / width), 3) if len(columns) else None
    return output


def load_object_templates() -> dict[str, list]:
    templates = {}
    for kind in OBJECT_KINDS:
        templates[kind] = []
        for path in sorted(CAPTURES.glob(f"environment_{kind}_*.png")):
            image = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if image is None or min(image.shape[:2]) < 8:
                raise ValueError(f"Invalid {kind} template: {path}")
            templates[kind].append(image)
    return templates


def find_objects(frame, templates: dict[str, list], threshold: float = 0.80,
                 max_per_kind: int = 30) -> dict[str, list[dict]]:
    """Coarse grayscale peaks, verified by full-color template correlation."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    found = {kind: [] for kind in OBJECT_KINDS}
    for kind in OBJECT_KINDS:
        for template in templates.get(kind, []):
            height, width = template.shape[:2]
            patch = cv2.resize(cv2.cvtColor(template, cv2.COLOR_BGR2GRAY), None,
                               fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
            if patch.shape[0] > small.shape[0] or patch.shape[1] > small.shape[1]:
                continue
            scores = cv2.matchTemplate(small, patch, cv2.TM_CCOEFF_NORMED)
            # Inspect multiple spatially distinct peaks for repeated objects.
            for _ in range(max_per_kind * 3):
                _, coarse_score, _, (sx, sy) = cv2.minMaxLoc(scores)
                if coarse_score < max(0.55, threshold - 0.15):
                    break
                rough_x, rough_y = 2 * sx, 2 * sy
                left, top = max(0, rough_x - 4), max(0, rough_y - 4)
                right = min(frame.shape[1], rough_x + width + 5)
                bottom = min(frame.shape[0], rough_y + height + 5)
                if right - left >= width and bottom - top >= height:
                    result = cv2.matchTemplate(frame[top:bottom, left:right],
                                               template, cv2.TM_CCOEFF_NORMED)
                    _, score, _, (dx, dy) = cv2.minMaxLoc(result)
                    x, y = left + dx, top + dy
                    if score >= threshold and not any(
                        abs(x - old["x"]) < width // 2 and
                        abs(y - old["y"]) < height // 2 for old in found[kind]
                    ):
                        found[kind].append({"x": x, "y": y, "width": width,
                                            "height": height, "confidence": round(float(score), 3)})
                cv2.rectangle(scores, (max(0, sx - patch.shape[1] // 2),
                                       max(0, sy - patch.shape[0] // 2)),
                              (min(scores.shape[1] - 1, sx + patch.shape[1] // 2),
                               min(scores.shape[0] - 1, sy + patch.shape[0] // 2)),
                              -1, thickness=-1)
                if len(found[kind]) >= max_per_kind:
                    break
    return found
