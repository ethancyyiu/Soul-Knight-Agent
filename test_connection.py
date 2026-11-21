"""List windows, capture Soul Knight, and optionally test one mapped movement."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys
import time

from game_io import KeyboardControls, capture, list_windows, select_window


def parse_key_map(value: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in value.split(","):
        if "=" not in item:
            raise argparse.ArgumentTypeError("Use direction=key pairs, separated by commas.")
        direction, key = (part.strip().lower() for part in item.split("=", 1))
        if direction not in {"up", "down", "left", "right"} or not key:
            raise argparse.ArgumentTypeError("Directions must be up, down, left, or right, with a key.")
        mapping[direction] = key
    return mapping


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="Only list visible windows")
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument("--window", type=int, metavar="INDEX", help="Select a listed window")
    choice.add_argument("--hwnd", type=lambda s: int(s, 0), metavar="HANDLE",
                        help="Select a listed handle (decimal or 0x hex)")
    parser.add_argument("--move", choices=("up", "down", "left", "right"),
                        help="Explicitly send one short movement")
    parser.add_argument("--key-map", type=parse_key_map, metavar="MAP",
                        help='Known game bindings, e.g. "up=w,down=s,left=a,right=d"')
    parser.add_argument("--duration", type=float, default=0.15,
                        help="Movement seconds, maximum 2 (default: 0.15)")
    parser.add_argument("--continuous", action="store_true",
                        help="Capture latest.png repeatedly until Ctrl+C; cannot be combined with --move")
    parser.add_argument("--interval", type=float, default=0.2,
                        help="Seconds between continuous captures (default: 0.2)")
    args = parser.parse_args()
    if args.move and not args.key_map:
        parser.error("--move requires --key-map with the verified game binding.")
    if args.move and args.continuous:
        parser.error("--move and --continuous cannot be combined.")
    if args.continuous and args.interval <= 0:
        parser.error("--interval must be greater than 0.")

    try:
        windows = list_windows()
        print("Visible windows:")
        for index, window in enumerate(windows):
            print(f"  [{index}] {window.title!r} ({window.process}, {window.width}x{window.height}, hwnd={window.hwnd:#x})")
        if args.list:
            return 0
        game = select_window(windows, args.window, args.hwnd)
        print(f"Selected: {game.title!r} (hwnd={game.hwnd:#x})")
        latest = Path("captures/latest.png")
        if args.move:
            first = capture(game.hwnd, Path("captures/before.png"))
            print(f"Captured {first}")
            controls = KeyboardControls(game.hwnd, args.key_map)
            controls.move(args.move, args.duration)
            print(f"Sent {args.move} for {args.duration:g} seconds using {args.key_map[args.move]!r}")
            time.sleep(0.2)
            second = capture(game.hwnd, Path("captures/after.png"))
            shutil.copyfile(second, latest)
            print(f"Captured {second} and {latest}")
        elif args.continuous:
            print("Capturing continuously; press Ctrl+C to stop.")
            try:
                while True:
                    capture(game.hwnd, latest)
                    time.sleep(args.interval)
            except KeyboardInterrupt:
                print(f"Stopped. Latest screenshot: {latest}")
        else:
            capture(game.hwnd, latest)
            print(f"Captured {latest}")
        return 0
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
