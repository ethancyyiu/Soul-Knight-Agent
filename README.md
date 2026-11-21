# Soul Knight game I/O

Minimal Windows Python interface for finding the game window, capturing its visible client area, and testing a known keyboard movement binding. It does not read or change game files or memory.

## Setup (PowerShell)

```powershell
cd C:\Users\unknown\Documents\Soul-Knight-Agent
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Open Soul Knight in its emulator or Google Play Games window. Keep the game visible and unobstructed; `mss` captures the pixels currently on screen. The game must be running in the same interactive Windows desktop as this script.

If the script reports `EnumWindows failed`, launch PowerShell in the unlocked desktop session where the game is running. A background or isolated execution session cannot inspect that desktop.

## Check the connection

```powershell
.\.venv\Scripts\python.exe test_connection.py --list
.\.venv\Scripts\python.exe test_connection.py
```

The first command lists available window indices, titles, processes, and handles. The second selects a unique Soul Knight, BlueStacks, or Google Play Games window and writes `captures/latest.png`. If detection is ambiguous or the window has an unexpected name, pass a listed index or handle:

```powershell
.\.venv\Scripts\python.exe test_connection.py --window 0
.\.venv\Scripts\python.exe test_connection.py --hwnd 0x123456
```

For repeated capture, use `--continuous` and optionally `--interval 0.2`. Press Ctrl+C to stop. The file `captures/latest.png` is replaced each time.

## Test a movement only after checking bindings

Check the emulator's or game's keyboard-control settings first. Substitute the **actual** mapped key for the direction you want to test. For example, if right movement is mapped to `d`:

```powershell
.\.venv\Scripts\python.exe test_connection.py --move right --key-map "right=d" --duration 0.15
```

The script captures `captures/before.png`, focuses the selected window, presses and releases the mapped key, then captures `captures/after.png` and `captures/latest.png`. Movement is never sent unless `--move` and `--key-map` are both supplied. Compare the before and after images to confirm that the player moved. If it did not, check window focus and the emulator's actual keyboard mapping; a successful key event alone does not prove that the game responded. Supported key names are letters, digits, `up`, `down`, `left`, `right`, `space`, `shift`, `ctrl`, and `alt`.

Use `--window INDEX` with `--move` if automatic selection finds multiple windows. The selected window must be focusable; if focus fails, the script does not send a key.

## Calibrate and track the player

Install the updated requirements in the same virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Open Soul Knight in Google Play Games, keep its window visible, then select the player's **full on-screen sprite** once:

```powershell
.\.venv\Scripts\python.exe test_vision.py --title "Soul Knight" --calibrate --display
```

Drag a tight box around the character in the selection window, then press Enter or Space. Avoid surrounding floor, walls, HUD, and movement joystick. The selection is saved as `captures/player_template.png`. Move the character manually while tracking runs; no movement keys are sent by this script. Press Q or Ctrl+C to stop.

Later runs can reuse the saved template:

```powershell
.\.venv\Scripts\python.exe test_vision.py --title "Soul Knight" --display --fps 5
```

If multiple titles match, get the correct handle from `test_connection.py --list` and use `--hwnd 0x123456` instead of `--title`. If the unique game window is detected automatically, both selection options may be omitted. Omit `--display` to log and save annotated frames without a preview window.

The live preview shows a green box, center coordinates relative to the captured game client area, and a match score from 0 to 1. A red `not detected` label means the best score is below `--threshold` (default `0.70`); coordinates are then blank in `captures/player_positions.csv`. `captures/player_detection.png` and `captures/latest.png` are refreshed each frame. Tracking searches near the last position and falls back to the full frame after a weak match, so camera movement can be recovered. It also tries a horizontally mirrored template for facing changes. If the character's animation, skin, scale, or appearance differs too much, detection may fail or match another object. Recalibrate with `--calibrate`, select a tighter sprite crop, or adjust `--threshold` while checking the preview. A high score alone does not prove the box is on the player.

The preview window hides briefly during each capture so it does not cover pixels in the screenshot. Keep the game unobstructed by other windows. You can change capture speed with `--fps 5` and local search margin with `--search-radius 120`.
