"""Calibrate and inspect read-only Soul Knight environment observations."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import sys
import time

import cv2
import mss
import win32api
import win32gui

from environment_vision import (BAR_KINDS, OBJECT_KINDS, find_objects,
                                load_calibration, load_object_templates, read_bars,
                                save_bar, save_object_template)
from test_vision import (CAPTURES, TEMPLATE_PATH, choose_window, detect_player,
                         exclude_preview_from_capture, load_template_bank,
                         read_frame, template_variants)


PREVIEW = "Soul Knight environment perception"
ANNOTATED = CAPTURES / "environment_detection.png"
OBSERVATIONS = CAPTURES / "environment.json"
LOST_FRAME = CAPTURES / "environment_player_lost.png"
RECORDED = CAPTURES / "recorded"


def select_box(frame, kind: str) -> tuple[int, int, int, int]:
    title = f"Select {kind}"
    cv2.namedWindow(title, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(title, min(frame.shape[1], 1200), min(frame.shape[0], 800))
    try:
        return tuple(int(value) for value in cv2.selectROI(
            title, frame, showCrosshair=True, fromCenter=False
        ))
    finally:
        cv2.destroyWindow(title)


def annotate(frame, player, bars, objects, uncalibrated, fps: float):
    view = frame.copy()
    if player is not None and player.found:
        x = player.x - player.width // 2
        y = player.y - player.height // 2
        cv2.rectangle(view, (x, y), (x + player.width, y + player.height),
                      (0, 255, 0), 2)
        cv2.putText(view, f"player {player.confidence:.2f}", (x, max(20, y - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2)
    colors = {"enemy": (0, 0, 255), "projectile": (0, 165, 255),
              "wall": (170, 170, 170), "door": (0, 210, 255),
              "obstacle": (255, 150, 0), "weapon": (255, 0, 255)}
    for kind, matches in objects.items():
        for item in matches:
            x, y = item["x"], item["y"]
            cv2.rectangle(view, (x, y),
                          (x + item["width"], y + item["height"]), colors[kind], 2)
            cv2.putText(view, f"{kind} {item['confidence']:.2f}", (x, max(20, y - 5)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, colors[kind], 1)
    label = (f"health {bars['health'] if bars['health'] is not None else '?'}  "
             f"energy {bars['energy'] if bars['energy'] is not None else '?'}  "
             f"player {'found' if player is not None and player.found else 'lost'}  "
             f"FPS {fps:.1f}")
    cv2.rectangle(view, (5, view.shape[0] - 58),
                  (min(view.shape[1] - 1, 1100), view.shape[0] - 5), (0, 0, 0), -1)
    cv2.putText(view, label, (12, view.shape[0] - 32),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)
    cv2.putText(view, f"uncalibrated: {', '.join(uncalibrated) or 'none'}",
                (12, view.shape[0] - 11), cv2.FONT_HERSHEY_SIMPLEX,
                0.45, (180, 180, 180), 1)
    return view


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument("--title", help="Unique substring of the game window title")
    choice.add_argument("--hwnd", type=lambda value: int(value, 0),
                        help="Game window handle from test_connection.py --list")
    parser.add_argument("--calibrate", choices=BAR_KINDS + OBJECT_KINDS,
                        help="Select a bar interior or one object example, then exit")
    parser.add_argument("--threshold", type=float, default=0.80,
                        help="Minimum object template correlation (default: 0.80)")
    parser.add_argument("--display", action="store_true", help="Show annotated preview; Q stops")
    parser.add_argument("--profile", action="store_true",
                        help="Print capture, player, object matching, and save times")
    parser.add_argument("--record", action="store_true",
                        help="Save sampled raw game frames automatically while tracking")
    parser.add_argument("--record-interval", type=float, default=1.0,
                        help="Minimum seconds between recorded frames (default: 1)")
    parser.add_argument("--record-limit", type=int, default=120,
                        help="Maximum recorded frames in this run (default: 120)")
    parser.add_argument("--fps", type=float, default=5.0, help="Maximum capture rate (default: 5)")
    parser.add_argument("--seconds", type=float, help="Stop after this many seconds")
    args = parser.parse_args()
    if not 0 < args.threshold <= 1 or not 0 < args.fps <= 30:
        parser.error("--threshold must be in (0, 1] and --fps in (0, 30].")
    if args.seconds is not None and args.seconds <= 0:
        parser.error("--seconds must be positive.")
    if args.record_interval <= 0 or args.record_limit <= 0:
        parser.error("--record-interval and --record-limit must be positive.")

    screen = None
    try:
        game = choose_window(args.title, args.hwnd)
        print(f"Selected: {game.title!r} (hwnd={game.hwnd:#x})", flush=True)
        screen = getattr(mss, "MSS", mss.mss)()
        frame = read_frame(game.hwnd, screen=screen)
        if args.calibrate:
            box = select_box(frame, args.calibrate)
            if args.calibrate in BAR_KINDS:
                save_bar(args.calibrate, frame, box)
                print(f"Saved {args.calibrate} bar ROI to captures/environment_calibration.json")
            else:
                path = save_object_template(args.calibrate, frame, box)
                print(f"Saved {args.calibrate} example to {path}")
            return 0

        calibration = load_calibration()
        templates = load_object_templates()
        player_templates = ([variant for image in load_template_bank()
                             for variant in template_variants(image)]
                            if TEMPLATE_PATH.exists() else [])
        uncalibrated = [kind for kind in BAR_KINDS if kind not in calibration]
        uncalibrated += [kind for kind in OBJECT_KINDS if not templates[kind]]
        if calibration and calibration.get("frame_size") != [frame.shape[1], frame.shape[0]]:
            print("HUD calibration is for another window size; health and energy are unknown.",
                  flush=True)
            uncalibrated = list(BAR_KINDS) + [kind for kind in OBJECT_KINDS
                                               if not templates[kind]]
        print("Uncalibrated: " + (", ".join(uncalibrated) or "none"), flush=True)
        preview_hwnd = 0
        if args.display:
            cv2.namedWindow(PREVIEW, cv2.WINDOW_NORMAL)
            cv2.resizeWindow(PREVIEW, min(frame.shape[1], 1200),
                             min(frame.shape[0], 800))
            cv2.imshow(PREVIEW, frame)
            cv2.waitKey(1)
            preview_hwnd = win32gui.FindWindow(None, PREVIEW)
            if not preview_hwnd:
                raise RuntimeError("Could not find the preview window; run without --display.")
            exclude_preview_from_capture(preview_hwnd)
        previous = None
        lost_streak = 0
        initial_size = frame.shape[:2]
        start = time.monotonic()
        first = True
        paused = False
        lost_snapshot_saved = False
        recorded = 0
        record_dir = RECORDED / datetime.now().strftime("run_%Y%m%d_%H%M%S")
        next_record_at = 0.0
        while True:
            if args.seconds is not None and time.monotonic() - start >= args.seconds:
                break
            if win32api.GetAsyncKeyState(ord("Q")) & 0x8000:
                break
            if args.display and (cv2.waitKey(1) & 0xFF) in (ord("q"), ord("Q")):
                break
            tick = time.monotonic()
            if not first:
                if win32gui.GetForegroundWindow() not in (game.hwnd, preview_hwnd):
                    if not paused:
                        print("Paused: return to Soul Knight or its preview to resume.", flush=True)
                        paused = True
                    time.sleep(0.1)
                    continue
                if paused:
                    print("Resumed environment perception.", flush=True)
                    paused = False
                frame = read_frame(game.hwnd, preview_hwnd, screen=screen)
                if frame.shape[:2] != initial_size:
                    raise RuntimeError("Game window size changed; recalibrate the HUD and object examples.")
            first = False
            captured_at = time.monotonic()
            player = (detect_player(frame, player_templates, previous, 0.70, 120)
                      if player_templates else None)
            if player is not None and player.found:
                previous = player
                lost_streak = 0
                lost_snapshot_saved = False
            else:
                lost_streak += 1
                if not lost_snapshot_saved:
                    cv2.imwrite(str(LOST_FRAME), frame)
                    lost_snapshot_saved = True
                if lost_streak > 2:
                    previous = None
            player_at = time.monotonic()
            bars = read_bars(frame, calibration)
            objects = find_objects(frame, templates, args.threshold)
            objects_at = time.monotonic()
            fps = 1 / max(0.001, objects_at - tick)
            observation = {
                "elapsed_seconds": round(time.monotonic() - start, 3),
                "player": ({"x": player.x, "y": player.y,
                            "confidence": round(player.confidence, 3)}
                           if player is not None and player.found else None),
                "health_fill": bars["health"], "energy_fill": bars["energy"],
                "objects": objects, "uncalibrated": uncalibrated,
                "processing_fps": round(fps, 2),
            }
            view = annotate(frame, player, bars, objects, uncalibrated, fps)
            if not cv2.imwrite(str(ANNOTATED), view):
                raise RuntimeError(f"Could not save {ANNOTATED}.")
            if args.record and recorded < args.record_limit:
                elapsed = time.monotonic() - start
                if elapsed >= next_record_at:
                    record_dir.mkdir(parents=True, exist_ok=True)
                    path = record_dir / f"frame_{recorded + 1:04d}_{elapsed:.1f}s.jpg"
                    if not cv2.imwrite(str(path), frame,
                                       [cv2.IMWRITE_JPEG_QUALITY, 90]):
                        raise RuntimeError(f"Could not save {path}.")
                    recorded += 1
                    next_record_at = elapsed + args.record_interval
            saved_at = time.monotonic()
            observation["processing_fps"] = round(1 / max(0.001, saved_at - tick), 2)
            if args.profile:
                observation["timings_ms"] = {
                    "capture": round(1000 * (captured_at - tick)),
                    "player": round(1000 * (player_at - captured_at)),
                    "objects_and_hud": round(1000 * (objects_at - player_at)),
                    "annotate_and_png": round(1000 * (saved_at - objects_at)),
                }
            OBSERVATIONS.write_text(json.dumps(observation, indent=2) + "\n",
                                    encoding="utf-8")
            counts = " ".join(f"{kind}={len(items)}" for kind, items in objects.items()
                              if templates[kind])
            print(f"{observation['elapsed_seconds']:6.2f}s  "
                  f"player={observation['player']}  "
                  f"health={bars['health']} energy={bars['energy']} "
                  f"{counts} fps={observation['processing_fps']:.1f}" +
                  (f" timings_ms={observation['timings_ms']}" if args.profile else ""),
                  flush=True)
            if args.display:
                cv2.imshow(PREVIEW, view)
                if (cv2.waitKey(1) & 0xFF) in (ord("q"), ord("Q")):
                    break
            if win32api.GetAsyncKeyState(ord("Q")) & 0x8000:
                break
            time.sleep(max(0, 1 / args.fps - (time.monotonic() - tick)))
        print(f"Saved {ANNOTATED} and {OBSERVATIONS}; "
              f"recorded {recorded} raw frames in {record_dir if recorded else 'none'}")
        return 0
    except KeyboardInterrupt:
        print(f"Stopped. Latest outputs: {ANNOTATED}, {OBSERVATIONS}")
        return 0
    except (ValueError, RuntimeError, OSError, cv2.error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        if screen is not None:
            screen.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    raise SystemExit(main())
