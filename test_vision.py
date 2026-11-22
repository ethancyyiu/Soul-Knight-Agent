"""Calibrate a player sprite and track it in Soul Knight screenshots."""

from __future__ import annotations

import argparse
import ctypes
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
LOST_PATH = CAPTURES / "player_lost.png"
LOG_PATH = CAPTURES / "player_positions.csv"
PREVIEW_TITLE = "Soul Knight player tracking"
WDA_EXCLUDEFROMCAPTURE = 0x11


def exclude_preview_from_capture(hwnd: int) -> None:
    """Keep the live OpenCV preview visible without feeding it back into mss."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetWindowDisplayAffinity.argtypes = (ctypes.c_void_p, ctypes.c_ulong)
    user32.SetWindowDisplayAffinity.restype = ctypes.c_int
    if not user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE):
        error = ctypes.get_last_error()
        raise RuntimeError(
            f"Windows could not exclude the preview from screen capture (error {error}). "
            "Run without --display or use Windows 10 version 2004 or newer."
        )


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


def read_frame(hwnd: int, preview_hwnd: int = 0):
    foreground = win32gui.GetForegroundWindow()
    if foreground != hwnd and (not preview_hwnd or foreground != preview_hwnd):
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception as exc:
            raise RuntimeError("Could not bring Soul Knight to the foreground; capture would include other windows.") from exc
        time.sleep(0.08)
    foreground = win32gui.GetForegroundWindow()
    if foreground != hwnd and (not preview_hwnd or foreground != preview_hwnd):
        raise RuntimeError("Neither Soul Knight nor its excluded preview is foreground; capture would include other windows.")
    capture(hwnd, LATEST_PATH)
    frame = cv2.imread(str(LATEST_PATH), cv2.IMREAD_COLOR)
    if frame is None:
        raise RuntimeError(f"Could not read screenshot {LATEST_PATH}.")
    return frame


def calibrate(frame, destination: Path, base_size: tuple[int, int] | None = None):
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
        raise ValueError("Selection cancelled or too small. Select the player's head and body.")
    if width > frame.shape[1] // 3 or height > frame.shape[0] // 3:
        raise ValueError("Selection is too large. Select only the player sprite.")
    if base_size is not None:
        base_width, base_height = base_size
        if base_width > frame.shape[1] or base_height > frame.shape[0]:
            raise ValueError("Game view is smaller than the base template; recalibrate.")
        if abs(width - base_width) > base_width * 0.25 or abs(height - base_height) > base_height * 0.25:
            raise ValueError("Additional selection must be close to the first template's size.")
        center_x, center_y = x + width // 2, y + height // 2
        x = max(0, min(frame.shape[1] - base_width, center_x - base_width // 2))
        y = max(0, min(frame.shape[0] - base_height, center_y - base_height // 2))
        width, height = base_width, base_height
    template = frame[y:y + height, x:x + width].copy()
    _, deviation = cv2.meanStdDev(template)
    if float(deviation.max()) < 10:
        raise ValueError("Selection has too little visual detail; select the player sprite.")
    CAPTURES.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(destination), template):
        raise RuntimeError(f"Could not save template to {destination}.")
    return template, Detection(x + width // 2, y + height // 2, 1.0)


def load_template_bank():
    base = cv2.imread(str(TEMPLATE_PATH), cv2.IMREAD_COLOR)
    if base is None:
        raise ValueError(f"No template at {TEMPLATE_PATH}; run once with --calibrate.")
    images = [base]
    base_time = TEMPLATE_PATH.stat().st_mtime
    for path in sorted(CAPTURES.glob("player_template_*.png")):
        if path.stat().st_mtime < base_time:
            continue  # Older poses belong to a previous calibration.
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None or image.shape != base.shape:
            raise ValueError(f"Invalid appearance template {path}; recalibrate it.")
        images.append(image)
    return images


def next_template_path() -> Path:
    number = 2
    while (CAPTURES / f"player_template_{number}.png").exists():
        number += 1
    return CAPTURES / f"player_template_{number}.png"


def template_variants(template):
    """Include the helmet, which stays steadier than the weapon and cape."""
    height, width = template.shape[:2]
    regions = [(0, 0, width, height, False)]
    if width >= 80 and height >= 80:
        regions.append((width // 4, 0, 3 * width // 4, int(0.45 * height), False))
        regions.append((int(0.06 * width), int(0.04 * height),
                        int(0.64 * width), int(0.27 * height), True))
    variants = []
    for left, top, right, bottom, helmet in regions:
        patch = template[top:bottom, left:right]
        _, deviation = cv2.meanStdDev(patch)
        if float(deviation.max()) < 10:
            continue
        variants.append((patch, left, top, template, helmet))
        variants.append((cv2.flip(patch, 1), width - right, top,
                         cv2.flip(template, 1), helmet))
    if not variants:
        raise ValueError("Template has too little visual detail; run with --calibrate again.")
    return variants


def _best_match(frame, templates, region):
    left, top, right, bottom = region
    area = frame[top:bottom, left:right]
    best_score = -1.0
    best_location = None
    for template, offset_x, offset_y, whole, helmet in templates:
        if area.shape[0] < template.shape[0] or area.shape[1] < template.shape[1]:
            continue
        result = cv2.matchTemplate(area, template, cv2.TM_CCOEFF_NORMED)
        _, score, _, location = cv2.minMaxLoc(result)
        candidate_x = left + location[0] - offset_x
        candidate_y = top + location[1] - offset_y
        if helmet:
            whole_height, whole_width = whole.shape[:2]
            if (candidate_x < 0 or candidate_y < 0 or
                    candidate_x + whole_width > frame.shape[1] or
                    candidate_y + whole_height > frame.shape[0]):
                continue
            crop = frame[candidate_y:candidate_y + whole_height,
                         candidate_x:candidate_x + whole_width]
            full_score = cv2.matchTemplate(crop, whole, cv2.TM_CCOEFF_NORMED)[0, 0]
            score = (score + float(full_score)) / 2
        if score > best_score:
            best_score = score
            best_location = (candidate_x, candidate_y)
    return best_score, best_location


def _global_match(frame, templates, template_width: int, template_height: int):
    """Find a few fast grayscale candidates, then score them in full color."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    candidates = []
    for patch, offset_x, offset_y, _, _ in templates:
        small_patch = cv2.resize(cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY), None,
                                 fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
        if small.shape[0] < small_patch.shape[0] or small.shape[1] < small_patch.shape[1]:
            continue
        result = cv2.matchTemplate(small, small_patch, cv2.TM_CCOEFF_NORMED)
        _, score, _, location = cv2.minMaxLoc(result)
        candidates.append((score, 2 * location[0] - offset_x,
                           2 * location[1] - offset_y))
    candidates.sort(reverse=True)
    best_score, best_location = -1.0, None
    checked = []
    for _, x, y in candidates:
        if any(abs(x - old_x) < template_width // 2 and
               abs(y - old_y) < template_height // 2 for old_x, old_y in checked):
            continue
        checked.append((x, y))
        region = (max(0, x - 40), max(0, y - 40),
                  min(frame.shape[1], x + template_width + 40),
                  min(frame.shape[0], y + template_height + 40))
        score, location = _best_match(frame, templates, region)
        if score > best_score:
            best_score, best_location = score, location
        if len(checked) == 3:
            break
    return best_score, best_location


def detect_player(frame, templates, previous: Detection | None,
                  threshold: float, radius: int) -> Detection:
    height, width = frame.shape[:2]
    template_height, template_width = templates[0][0].shape[:2]
    score, location = -1.0, None
    if previous is not None and previous.found:
        left = max(0, previous.x - template_width // 2 - radius)
        top = max(0, previous.y - template_height // 2 - radius)
        right = min(width, previous.x + template_width // 2 + radius)
        bottom = min(height, previous.y + template_height // 2 + radius)
        score, location = _best_match(frame, templates, (left, top, right, bottom))
    if score < threshold:
        global_score, global_location = _global_match(
            frame, templates, template_width, template_height
        )
        if global_score > score:
            score, location = global_score, global_location
    if location is None or score < threshold:
        return Detection(None, None, max(0.0, score))
    center_x = location[0] + template_width // 2
    center_y = location[1] + template_height // 2
    if not 0 <= center_x < width or not 0 <= center_y < height:
        return Detection(None, None, max(0.0, score))
    return Detection(center_x, center_y, score)


def annotate(frame, detection: Detection, template_width: int,
             template_height: int, processing_fps: float):
    view = frame.copy()
    if detection.found:
        left = detection.x - template_width // 2
        top = detection.y - template_height // 2
        cv2.rectangle(view, (left, top),
                      (left + template_width, top + template_height), (0, 255, 0), 2)
        label = f"TRACKING  ({detection.x}, {detection.y})  confidence {detection.confidence:.2f}"
        color = (0, 255, 0)
    else:
        label = f"LOST  best score {detection.confidence:.2f}"
        color = (0, 0, 255)
    cv2.rectangle(view, (5, 5), (min(view.shape[1] - 1, 820), 68), (0, 0, 0), -1)
    cv2.putText(view, label, (12, 29), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                color, 2, cv2.LINE_AA)
    cv2.putText(view, f"processing FPS {processing_fps:.1f}", (12, 57),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2, cv2.LINE_AA)
    return view


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument("--title", help="Unique substring of the visible game window title")
    choice.add_argument("--hwnd", type=lambda value: int(value, 0),
                        help="Window handle from test_connection.py --list (decimal or 0x hex)")
    calibration = parser.add_mutually_exclusive_group()
    calibration.add_argument("--calibrate", action="store_true",
                             help="Select a fresh base template; older appearance templates are ignored")
    calibration.add_argument("--add-template", action="store_true",
                             help="Select another player pose to add to the appearance templates")
    parser.add_argument("--fps", type=float, default=5.0,
                        help="Capture rate in frames per second (default: 5)")
    parser.add_argument("--threshold", type=float, default=0.70,
                        help="Minimum template match confidence, 0 to 1 (default: 0.70)")
    parser.add_argument("--search-radius", type=int, default=120,
                        help="Local search margin in pixels before full-frame fallback (default: 120)")
    parser.add_argument("--display", action="store_true",
                        help="Show annotated live preview; Q closes it")
    parser.add_argument("--seconds", type=float,
                        help="Stop tracking after this many seconds (default: run until Q or Ctrl+C)")
    args = parser.parse_args()
    if not 0 < args.fps <= 30:
        parser.error("--fps must be greater than 0 and at most 30.")
    if not 0 < args.threshold <= 1:
        parser.error("--threshold must be greater than 0 and at most 1.")
    if args.search_radius < 0:
        parser.error("--search-radius must be at least 0.")
    if args.seconds is not None and args.seconds <= 0:
        parser.error("--seconds must be greater than 0.")

    try:
        game = choose_window(args.title, args.hwnd)
        print(f"Selected: {game.title!r} (hwnd={game.hwnd:#x})", flush=True)
        frame = read_frame(game.hwnd)
        previous = None
        if args.calibrate:
            template, previous = calibrate(frame, TEMPLATE_PATH)
            print(f"Saved {TEMPLATE_PATH}; template size {template.shape[1]}x{template.shape[0]}",
                  flush=True)
        elif args.add_template:
            base = load_template_bank()[0]
            path = next_template_path()
            _, previous = calibrate(frame, path, (base.shape[1], base.shape[0]))
            print(f"Saved additional appearance: {path}", flush=True)
        bank = load_template_bank()
        template = bank[0]
        initial_size = frame.shape[:2]
        if template.shape[0] > initial_size[0] or template.shape[1] > initial_size[1]:
            raise ValueError("Saved template is larger than the game view; run with --calibrate again.")
        templates = [variant for image in bank for variant in template_variants(image)]
        print(f"Using {len(bank)} appearance template(s).", flush=True)
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
            exclude_preview_from_capture(preview_hwnd)
        CAPTURES.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("w", newline="", encoding="utf-8") as log_file:
            writer = csv.writer(log_file)
            writer.writerow(("elapsed_seconds", "x", "y", "confidence", "status", "processing_fps"))
            start = time.monotonic()
            tracked_frames = 0
            lost_frames = 0
            lost_snapshot_saved = False
            paused_for_focus = False
            q_was_down = bool(win32api.GetAsyncKeyState(ord("Q")) & 0x8000)
            t_was_down = bool(win32api.GetAsyncKeyState(ord("T")) & 0x8000)
            print("Tracking. Move the player manually; press Ctrl+C or Q to stop.", flush=True)
            while True:
                if args.seconds is not None and time.monotonic() - start >= args.seconds:
                    break
                if args.display and (cv2.waitKey(1) & 0xFF) in (ord("q"), ord("Q")):
                    break
                if win32api.GetAsyncKeyState(ord("Q")) & 0x8000:
                    break
                foreground = win32gui.GetForegroundWindow()
                if foreground != game.hwnd and (not preview_hwnd or foreground != preview_hwnd):
                    if not paused_for_focus:
                        print("Paused: another window has focus. Return to Soul Knight or its preview to resume.",
                              flush=True)
                        paused_for_focus = True
                    time.sleep(0.1)
                    continue
                if paused_for_focus:
                    print("Resumed tracking.", flush=True)
                    paused_for_focus = False
                frame_started = time.monotonic()
                frame = read_frame(game.hwnd, preview_hwnd)
                if frame.shape[:2] != initial_size:
                    raise RuntimeError("Game window size changed; rerun with --calibrate.")
                detection = detect_player(frame, templates, previous,
                                          args.threshold, args.search_radius)
                was_tracking = previous is not None and previous.found
                previous = detection if detection.found else None
                if detection.found:
                    tracked_frames += 1
                    lost_snapshot_saved = False
                else:
                    lost_frames += 1
                if not detection.found and (was_tracking or not lost_snapshot_saved):
                    cv2.imwrite(str(LOST_PATH), frame)
                    lost_snapshot_saved = True
                processing_fps = 1 / max(0.001, time.monotonic() - frame_started)
                view = annotate(frame, detection, template.shape[1], template.shape[0],
                                processing_fps)
                if not cv2.imwrite(str(ANNOTATED_PATH), view):
                    raise RuntimeError(f"Could not save {ANNOTATED_PATH}.")
                elapsed = time.monotonic() - start
                writer.writerow((f"{elapsed:.3f}", detection.x if detection.found else "",
                                 detection.y if detection.found else "",
                                 f"{detection.confidence:.3f}",
                                 "tracking" if detection.found else "lost",
                                 f"{processing_fps:.1f}"))
                log_file.flush()
                position = f"({detection.x}, {detection.y})" if detection.found else "not detected"
                print(f"{elapsed:7.2f}s  {position}  confidence={detection.confidence:.3f}  processing_fps={processing_fps:.1f}",
                      flush=True)
                key = -1
                if args.display:
                    cv2.imshow(PREVIEW_TITLE, view)
                    key = cv2.waitKey(1) & 0xFF
                    if key in (ord("q"), ord("Q")):
                        break
                q_down = bool(win32api.GetAsyncKeyState(ord("Q")) & 0x8000)
                t_down = bool(win32api.GetAsyncKeyState(ord("T")) & 0x8000)
                q_pressed = q_down and not q_was_down
                t_pressed = t_down and not t_was_down
                q_was_down, t_was_down = q_down, t_down
                if q_pressed:
                    break
                if (win32api.GetAsyncKeyState(win32con.VK_CONTROL) & 0x8000 and
                        win32api.GetAsyncKeyState(ord("C")) & 0x8000):
                    break
                if key in (ord("t"), ord("T")) or t_pressed:
                    path = next_template_path()
                    _, previous = calibrate(frame, path, (template.shape[1], template.shape[0]))
                    bank = load_template_bank()
                    templates = [variant for image in bank for variant in template_variants(image)]
                    print(f"Added pose {path}; using {len(bank)} templates.", flush=True)
                if args.seconds is not None and elapsed >= args.seconds:
                    break
                time.sleep(max(0, 1 / args.fps - (time.monotonic() - frame_started)))
        print(f"Tracked {tracked_frames}/{tracked_frames + lost_frames} frames; "
              f"lost {lost_frames}. Saved {ANNOTATED_PATH} and {LOG_PATH}", flush=True)
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
