"""Small Windows window, screen, and keyboard interface for Soul Knight."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
from pathlib import Path
import time

import mss
from mss.tools import to_png
import win32api
import win32con
import win32gui
import win32process
import pywintypes


_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
_kernel32.OpenProcess.restype = ctypes.c_void_p
_kernel32.QueryFullProcessImageNameW.argtypes = (
    ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong)
)
_kernel32.QueryFullProcessImageNameW.restype = ctypes.c_int
_kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)


@dataclass(frozen=True)
class GameWindow:
    hwnd: int
    title: str
    process: str
    width: int
    height: int


def _process_name(hwnd: int) -> str:
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
    if not handle:
        return "unknown"
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        length = ctypes.c_ulong(len(buffer))
        if _kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
            return Path(buffer.value).name
        return "unknown"
    finally:
        _kernel32.CloseHandle(handle)


def list_windows() -> list[GameWindow]:
    """Return visible, non-minimized top-level windows with a usable client area."""
    windows: list[GameWindow] = []

    def add(hwnd: int, _: object) -> None:
        try:
            if not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
                return
            title = win32gui.GetWindowText(hwnd).strip()
            if not title:
                return
            left, top, right, bottom = win32gui.GetClientRect(hwnd)
            if right <= left or bottom <= top:
                return
            windows.append(GameWindow(hwnd, title, _process_name(hwnd), right - left, bottom - top))
        except pywintypes.error:
            pass  # A window may close while Windows is enumerating it.

    try:
        win32gui.EnumWindows(add, None)
    except pywintypes.error as exc:
        raise RuntimeError(
            "Windows could not enumerate the desktop (EnumWindows failed). "
            "Run this script in the same unlocked interactive Windows session as the game."
        ) from exc
    return windows


def _score(window: GameWindow) -> int:
    title = window.title.lower()
    process = window.process.lower()
    if "soul knight" in title or "soulknight" in title:
        return 2
    if any(name in title or name in process for name in (
        "bluestacks", "hd-player", "google play games", "googleplaygames"
    )):
        return 1
    return 0


def select_window(windows: list[GameWindow], index: int | None = None,
                  hwnd: int | None = None) -> GameWindow:
    if hwnd is not None:
        for window in windows:
            if window.hwnd == hwnd:
                return window
        raise ValueError(f"Window handle {hwnd:#x} is not visible or has no usable client area.")
    if index is not None:
        if not 0 <= index < len(windows):
            raise ValueError(f"Window index {index} is unavailable; choose one from the list.")
        return windows[index]
    candidates = [window for window in windows if _score(window) == 2]
    if not candidates:
        candidates = [window for window in windows if _score(window) == 1]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise ValueError("No Soul Knight, BlueStacks, or Google Play Games window found. Open the game and use --window INDEX or --hwnd HANDLE if its window has another name.")
    raise ValueError("Multiple possible game windows found. Select one with --window INDEX or --hwnd HANDLE.")


def client_area(hwnd: int) -> dict[str, int]:
    """Find the current on-screen client rectangle, excluding window borders."""
    if not win32gui.IsWindow(hwnd) or win32gui.IsIconic(hwnd):
        raise ValueError("The selected window closed or was minimized.")
    left, top = win32gui.ClientToScreen(hwnd, (0, 0))
    _, _, width, height = win32gui.GetClientRect(hwnd)
    if width <= 0 or height <= 0:
        raise ValueError("The selected window has no usable client area.")
    return {"left": left, "top": top, "width": width, "height": height}


def capture(hwnd: int, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with mss.mss() as screen:
        shot = screen.grab(client_area(hwnd))
        to_png(shot.rgb, shot.size, output=str(destination))
    return destination


_NAMED_KEYS = {
    "up": win32con.VK_UP, "down": win32con.VK_DOWN,
    "left": win32con.VK_LEFT, "right": win32con.VK_RIGHT,
    "space": win32con.VK_SPACE, "shift": win32con.VK_SHIFT,
    "ctrl": win32con.VK_CONTROL, "alt": win32con.VK_MENU,
}
_EXTENDED_KEYS = {win32con.VK_UP, win32con.VK_DOWN, win32con.VK_LEFT,
                  win32con.VK_RIGHT}


def _virtual_key(name: str) -> int:
    key = name.strip().lower()
    if key in _NAMED_KEYS:
        return _NAMED_KEYS[key]
    if len(key) == 1 and ("a" <= key <= "z" or "0" <= key <= "9"):
        return ord(key.upper())
    raise ValueError(f"Unsupported key {name!r}; use a letter, digit, arrow, space, shift, ctrl, or alt.")


class KeyboardControls:
    def __init__(self, hwnd: int, mapping: dict[str, str]):
        self.hwnd = hwnd
        self.mapping = {direction: _virtual_key(key) for direction, key in mapping.items()}
        self.pressed: set[str] = set()

    def _key_event(self, direction: str, release: bool) -> None:
        if direction not in self.mapping:
            raise ValueError(f"No key mapped for {direction}; specify it in --key-map.")
        key = self.mapping[direction]
        flags = (win32con.KEYEVENTF_KEYUP if release else 0)
        if key in _EXTENDED_KEYS:
            flags |= win32con.KEYEVENTF_EXTENDEDKEY
        win32api.keybd_event(key, win32api.MapVirtualKey(key, 0), flags, 0)

    def press(self, direction: str) -> None:
        if direction not in self.pressed:
            if win32gui.GetForegroundWindow() != self.hwnd:
                raise RuntimeError("The selected window is not foreground; no key was sent.")
            self._key_event(direction, release=False)
            self.pressed.add(direction)

    def release(self, direction: str) -> None:
        if direction in self.pressed:
            self._key_event(direction, release=True)
            self.pressed.remove(direction)

    def move(self, direction: str, seconds: float) -> None:
        if not 0 < seconds <= 2:
            raise ValueError("Movement duration must be greater than 0 and at most 2 seconds.")
        if direction not in self.mapping:
            raise ValueError(f"No key mapped for {direction}; specify it in --key-map.")
        try:
            win32gui.SetForegroundWindow(self.hwnd)
        except Exception as exc:
            raise RuntimeError("Could not focus the selected window; no key was sent.") from exc
        if win32gui.GetForegroundWindow() != self.hwnd:
            raise RuntimeError("The selected window is not foreground; no key was sent.")
        try:
            self.press(direction)
            time.sleep(seconds)
        finally:
            self.release(direction)
