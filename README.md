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

Open Soul Knight in Google Play Games, keep its window visible, then select a tight crop around the player's **helmet and body**:

```powershell
.\.venv\Scripts\python.exe test_vision.py --title "Soul Knight" --calibrate --display
```

Drag a box around the character in the selection window, then press Enter or Space. Avoid surrounding floor, the sword or gun when possible, and the green health bar; they change as the player moves. The selection is saved as `captures/player_template.png`. Move the character manually while tracking runs; no movement keys are sent by this script. Press Q or Ctrl+C to stop.

To add another walking or facing pose, leave the character in that pose and run:

```powershell
.\.venv\Scripts\python.exe test_vision.py --title "Soul Knight" --add-template --display
```

Select roughly the same area and size as the first template. Additional poses are saved as `captures/player_template_2.png`, `_3.png`, and so on, and loaded automatically. Running `--calibrate` again starts a fresh active template set; older pose files remain on disk but are ignored. To test the saved poses, run without `--calibrate`.

You can also press **T** during live tracking to freeze the latest clean frame and select another walking pose. This is useful when a direction or animation first shows `LOST`; the game receives no key from the script.

Later runs can reuse the saved template:

```powershell
.\.venv\Scripts\python.exe test_vision.py --title "Soul Knight" --display --fps 5
```

If multiple titles match, get the correct handle from `test_connection.py --list` and use `--hwnd 0x123456` instead of `--title`. If the unique game window is detected automatically, both selection options may be omitted. Omit `--display` to log and save annotated frames without a preview window.

The live preview shows `TRACKING` or `LOST`, center coordinates relative to the captured game client area, a match score, and approximate processing FPS. A lost frame has no coordinates in `captures/player_positions.csv`. `captures/player_detection.png` and `captures/latest.png` are refreshed each frame; `captures/player_lost.png` retains the first raw frame from the latest loss. Tracking searches near the last position and uses a faster whole-screen search after a weak match, so camera movement can be recovered. It tries the whole selected crop, its upper middle region, a helmet patch, horizontal mirrors, and any additional pose templates. OpenCV `TM_CCOEFF_NORMED` measures how closely the pixels match after normalizing brightness; the helmet candidate's score is averaged with the full sprite score at the same position to reject unrelated objects. The reported score is **not** the probability that the player was found. A score below the default `0.70` threshold reports `LOST`, even if the best match was `0.58`. Animation, facing direction, weapons, background pixels, and UI overlays can all reduce the score or cause a wrong match. A high score alone does not prove the box is on the player.

The script brings the game to the foreground for the first capture. On Windows 10 version 2004 or newer, it asks Windows to exclude the OpenCV preview from screen capture so the preview can stay visible without appearing recursively in the game screenshots. The preview can have focus while tracking, which lets you click it and press Q to stop. If another window gets focus, tracking pauses until you return to the game or preview; Ctrl+C in the terminal still stops it. If capture exclusion fails, use the script without `--display` and inspect `captures/player_detection.png`. Check `captures/latest.png`: it should contain only the game. If it contains the tracking preview or another window, the reported scores and positions are invalid. You can change capture speed with `--fps 5` and local search margin with `--search-radius 120`.

For a 30-second manual check, run the command below and move in all four directions. Inspect the preview, saved annotated image, and CSV; the final frame count alone cannot verify that the box stayed on the player.

```powershell
.\.venv\Scripts\python.exe test_vision.py --title "Soul Knight" --display --fps 5 --seconds 30
```
