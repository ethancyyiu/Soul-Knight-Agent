"""Calibrate a player sprite and track it in Soul Knight screenshots."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
import sys
import time

import cv2
import win32api
import win32con
import win32gui

from game_io import GameWindow, capture, list_windows, select_window


CAPTURES = Path("captures")
TEMPLATE_PATH = CAPTURES / "player_template.png"
LATEST_PATH = CAPTURES / "latest.png"
ANNOTATED_PATH = CAPTURES / "player_detection.png"
LOG_PATH = CAPTURES / "player_positions.csv"
PREVIEW_TITLE = "Soul Knight player tracking"


@dataclass(frozen=True)
class Detection:
    x: int | None
    y: int | None
    confidence: float

    @property
    def found(self) -> bool:
        return self.x is not None and self.y is not None


def choose_window(title: str | None, hwnd: int | None) -> GameWindow:
    windows = list_windows()
    if hwnd is not None:
        return select_window(windows, hwnd=hwnd)
    if title is not None:
        matches = [window for window in windows if title.casefold() in window.title.casefold()]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise ValueError(f"No visible window title contains {title!r}. Run test_connection.py --list.")
        raise ValueError(f"{len(matches)} window titles contain {title!r}; use --hwnd with a listed handle.")
    return select_window(windows)


def read_frame(hwnd: int):
    capture(hwnd, LATEST_PATH)
    frame = cv2.imread(str(LATEST_PATH), cv2.IMREAD_COLOR)
    if frame is None:
        raise RuntimeError(f"Could not read screenshot {LATEST_PATH}.")
    return frame


def calibrate(frame):
    cv2.namedWindow("Select player sprite", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("Select player sprite", min(frame.shape[1], 1200),
                     min(frame.shape[0], 800))
    try:
        x, y, width, height = (int(value) for value in cv2.selectROI(
            "Select player sprite", frame, showCrosshair=True, fromCenter=False
        ))
    finally:
        cv2.destroyWindow("Select player sprite")
    if width < 12 or height < 12:
        raise ValueError("Selection cancelled or too small. Select the full player sprite.")
    if width > frame.shape[1] // 3 or height > frame.shape[0] // 3:
        raise ValueError("Selection is too large. Select only the player sprite.")
    template = frame[y:y + height, x:x + width].copy()
    _, deviation = cv2.meanStdDev(template)
    if float(deviation.max()) < 10:
        raise ValueError("Selection has too little visual detail; select the player sprite.")
    CAPTURES.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(TEMPLATE_PATH), template):
        raise RuntimeError(f"Could not save template to {TEMPLATE_PATH}.")
    return template, Detection(x + width // 2, y + height // 2, 1.0)


def template_variants(template):
    """Match the whole sprite and a steadier upper part of the same selection."""
    height, width = template.shape[:2]
    regions = [(0, 0, width, height)]
    if width >= 40 and height >= 32:
        regions.append((width // 4, 0, 3 * width // 4, int(0.45 * height)))
    variants = []
    for left, top, right, bottom in regions:
        patch = template[top:bottom, left:right]
        _, deviation = cv2.meanStdDev(patch)
        if float(deviation.max()) < 10:
            continue
        variants.append((patch, left, top))
        variants.append((cv2.flip(patch, 1), width - right, top))
    if not variants:
        raise ValueError("Template has too little visual detail; run with --calibrate again.")
    return variants


def _best_match(frame, templates, region):
    left, top, right, bottom = region
    area = frame[top:bottom, left:right]
    best_score = -1.0
    best_location = None
    for template, offset_x, offset_y in templates:
        if area.shape[0] < template.shape[0] or area.shape[1] < template.shape[1]:
            continue
        result = cv2.matchTemplate(area, template, cv2.TM_CCOEFF_NORMED)
        _, score, _, location = cv2.minMaxLoc(result)
        if score > best_score:
            best_score = score
            best_location = (left + location[0] - offset_x,
                             top + location[1] - offset_y)
    return best_score, best_location


def detect_player(frame, templates, previous: Detection | None,
                  threshold: float, radius: int) -> Detection:
    height, width = frame.shape[:2]
    template_height, template_width = templates[0][0].shape[:2]
    full = (0, 0, width, height)
    if previous is not None and previous.found:
        left = max(0, previous.x - template_width // 2 - radius)
        top = max(0, previous.y - template_height // 2 - radius)
        right = min(width, previous.x + template_width // 2 + radius)
        bottom = min(height, previous.y + template_height // 2 + radius)
        score, location = _best_match(frame, templates, (left, top, right, bottom))
        if score < threshold:
            score, location = _best_match(frame, templates, full)
    else:
        score, location = _best_match(frame, templates, full)
    if location is None or score < threshold:
        return Detection(None, None, max(0.0, score))
    return Detection(location[0] + template_width // 2,
                     location[1] + template_height // 2, score)


def annotate(frame, detection: Detection, template_width: int,
             template_height: int):
    view = frame.copy()
    if detection.found:
        left = detection.x - template_width // 2
        top = detection.y - template_height // 2
        cv2.rectangle(view, (left, top),
                      (left + template_width, top + template_height), (0, 255, 0), 2)
        label = f"player ({detection.x}, {detection.y})  confidence {detection.confidence:.2f}"
        color = (0, 255, 0)
    else:
        label = f"player not detected  best score {detection.confidence:.2f}"
        color = (0, 0, 255)
    cv2.rectangle(view, (5, 5), (min(view.shape[1] - 1, 780), 40), (0, 0, 0), -1)
    cv2.putText(view, label, (12, 29), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                color, 2, cv2.LINE_AA)
    return view


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument("--title", help="Unique substring of the visible game window title")
    choice.add_argument("--hwnd", type=lambda value: int(value, 0),
                        help="Window handle from test_connection.py --list (decimal or 0x hex)")
    parser.add_argument("--calibrate", action="store_true",
                        help="Select the player sprite from a fresh screenshot and save a template")
    parser.add_argument("--fps", type=float, default=5.0,
                        help="Capture rate in frames per second (default: 5)")
    parser.add_argument("--threshold", type=float, default=0.70,
                        help="Minimum template match confidence, 0 to 1 (default: 0.70)")
    parser.add_argument("--search-radius", type=int, default=120,
                        help="Local search margin in pixels before full-frame fallback (default: 120)")
    parser.add_argument("--display", action="store_true",
                        help="Show annotated live preview; Q closes it")
    args = parser.parse_args()
    if not 0 < args.fps <= 30:
        parser.error("--fps must be greater than 0 and at most 30.")
    if not 0 < args.threshold <= 1:
        parser.error("--threshold must be greater than 0 and at most 1.")
    if args.search_radius < 0:
        parser.error("--search-radius must be at least 0.")

    try:
        game = choose_window(args.title, args.hwnd)
        print(f"Selected: {game.title!r} (hwnd={game.hwnd:#x})", flush=True)
        frame = read_frame(game.hwnd)
        if args.calibrate:
            template, previous = calibrate(frame)
            print(f"Saved {TEMPLATE_PATH}; template size {template.shape[1]}x{template.shape[0]}",
                  flush=True)
        else:
            template = cv2.imread(str(TEMPLATE_PATH), cv2.IMREAD_COLOR)
            if template is None:
                raise ValueError(f"No template at {TEMPLATE_PATH}; run once with --calibrate.")
            previous = None
        initial_size = frame.shape[:2]
        if template.shape[0] > initial_size[0] or template.shape[1] > initial_size[1]:
            raise ValueError("Saved template is larger than the game view; run with --calibrate again.")
        templates = template_variants(template)
        preview_hwnd = 0
        if args.display:
            cv2.namedWindow(PREVIEW_TITLE, cv2.WINDOW_NORMAL)
            cv2.resizeWindow(PREVIEW_TITLE, min(frame.shape[1], 1200),
                             min(frame.shape[0], 800))
            cv2.imshow(PREVIEW_TITLE, frame)
            cv2.waitKey(1)
            preview_hwnd = win32gui.FindWindow(None, PREVIEW_TITLE)
            if not preview_hwnd:
                raise RuntimeError("Could not find the OpenCV preview window; use without --display.")
        CAPTURES.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("w", newline="", encoding="utf-8") as log_file:
            writer = csv.writer(log_file)
            writer.writerow(("elapsed_seconds", "x", "y", "confidence", "status"))
            start = time.monotonic()
            print("Tracking. Move the player manually; press Ctrl+C or Q to stop.", flush=True)
            while True:
                frame_started = time.monotonic()
                # The OpenCV preview can cover the emulator on one monitor.
                # Hide it during mss capture so its own pixels are not tracked.
                if preview_hwnd:
                    win32gui.ShowWindow(preview_hwnd, win32con.SW_HIDE)
                    time.sleep(0.02)
                frame = read_frame(game.hwnd)
                if frame.shape[:2] != initial_size:
                    raise RuntimeError("Game window size changed; rerun with --calibrate.")
                detection = detect_player(frame, templates, previous,
                                          args.threshold, args.search_radius)
                previous = detection if detection.found else None
                view = annotate(frame, detection, template.shape[1], template.shape[0])
                if not cv2.imwrite(str(ANNOTATED_PATH), view):
                    raise RuntimeError(f"Could not save {ANNOTATED_PATH}.")
                elapsed = time.monotonic() - start
                writer.writerow((f"{elapsed:.3f}", detection.x if detection.found else "",
                                 detection.y if detection.found else "",
                                 f"{detection.confidence:.3f}",
                                 "detected" if detection.found else "not detected"))
                log_file.flush()
                position = f"({detection.x}, {detection.y})" if detection.found else "not detected"
                print(f"{elapsed:7.2f}s  {position}  confidence={detection.confidence:.3f}",
                      flush=True)
                if args.display:
                    cv2.imshow(PREVIEW_TITLE, view)
                    if preview_hwnd:
                        win32gui.ShowWindow(preview_hwnd, win32con.SW_SHOWNA)
                    if cv2.waitKey(1) & 0xFF in (ord("q"), ord("Q")):
                        break
                if win32api.GetAsyncKeyState(ord("Q")) & 1:
                    break
                time.sleep(max(0, 1 / args.fps - (time.monotonic() - frame_started)))
        print(f"Saved {ANNOTATED_PATH} and {LOG_PATH}", flush=True)
        return 0
    except KeyboardInterrupt:
        print(f"Stopped. Latest output: {ANNOTATED_PATH}; log: {LOG_PATH}", flush=True)
        return 0
    except (ValueError, RuntimeError, OSError, cv2.error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        cv2.destroyAllWindows()


if __name__ == "__main__":
    raise SystemExit(main())
