# Kinesis — webcam human-computer use

Control Windows with your hands through one webcam: cursor, clicks, scroll, drag, window
management, browser tabs, Alt-Tab and push-to-talk — with the *angle* of your hand selected as the
monitor you are operating on.

Built from the ground up after measuring what `whitehatboy005/Virtual-Mouse` actually does on this
machine (see [Provenance](#provenance)). MediaPipe supplies the landmarks; everything above that —
poses, gestures, aiming, latency — is this project.

```
pip-free install:   run.bat          (creates nothing, uses .venv)
first time setup:   python -m venv .venv  &&  .venv\Scripts\pip install mediapipe==0.10.20 opencv-contrib-python==4.10.0.84 numpy==1.26.4
check it:           check.bat        (environment + camera + model self-test)
calibrate aiming:   calibrate.bat    (point at each screen, press Enter)
run:                run.bat
```

## Gestures

| Gesture | Action |
|---|---|
| index finger | move the cursor — 1:1, no easing, no animation |
| thumb + index pinch | left click (short pinch clicks on release, a held pinch clicks at 0.35 s) |
| two quick pinches | double click (two clicks inside the double-click window, like a real mouse) |
| thumb + middle pinch | right click |
| thumb + ring pinch, held | **adaptive scroll** — hand travel drives the wheel, gain rises with hand speed; cursor freezes while scrolling |
| thumb + pinky pinch, held | drag — text selection, moving files; cursor still follows |
| open hand → closed fist | minimise (exits fullscreen first if the window is fullscreen) |
| closed fist → open hand | maximise; if already maximised, fullscreen (`f` on YouTube, else `F11`) |
| open hand swipe left / right | previous / next browser tab (`Ctrl+Shift+Tab` / `Ctrl+Tab`) |
| **left** fist held 2 s | opens Alt-Tab and holds `Alt`; each right-hand thumb+index pinch taps `Tab` (hold to repeat); opening the left fist commits |
| thumb + pinky out (shaka), index/middle/ring curled | holds `Ctrl+Space` (push to talk) for as long as the pose is held |
| END key | quit (releases everything) |

Every action can be aimed: with calibration, "minimise" acts on the topmost window of the monitor
you were pointing at, not just whatever happened to have focus.

### Why the gestures don't fight each other

- Closing an open hand into a fist sweeps through a pinch. A pinch therefore *arms* a click which
  is committed on release (after a 120 ms grace) or by holding past 350 ms — and a fist forming
  inside that grace window cancels it. You get the minimise, not a stray click.
- A fist needs index, middle, ring **and** pinky curled, so the shaka (pinky out) can never
  minimise.
- While `Alt` is held by the two-handed gesture, a right-hand pinch is a `Tab` and never a click.
- Scroll, drag and swipe take ownership of the cursor while active; only one held action runs at a
  time.

## Aiming at a monitor

Point your index finger at a screen; Kinesis reports and uses the monitor you are indicating.

```
calibrate.bat             # for each monitor: point at it, hold, press Enter
```

`calibrate.py` samples the hand's pointing angles (yaw/pitch derived from MediaPipe's metric world
landmarks) for two seconds per screen, stores the averages in `kinesis_calibration.json`, and tells
you whether the angles came out monotonic left-to-right (if they did not, it is only a mirrored
axis convention — matching uses the recorded angles, so it still works).

Without calibration, yaw is mapped across the virtual desktop heuristically, so aiming is roughly
right out of the box. With it, the classifier uses nearest-centroid matching with hysteresis (7°)
and a distance gate (42°), so aim does not flicker between screens at boundaries.

This machine: **four** displays side by side spanning 7680x1080 (`DISPLAY16/14/13/15`, primary is
the third). Kinesis reads the live layout at startup and indexes monitors left to right.

## Latency

Measured on this machine (`kinesis.py --latency-report`, RTX 3070 box, USB webcam at 640x480):

| Metric | Measured |
|---|---|
| camera capture | 15.0 fps (device ceiling at 640x480 here — requested 30, reports 15 in YUYV and MJPG alike) |
| MediaPipe inference | 17-39 ms/frame at `model_complexity=0`, and it keeps up with capture (14-16 fps) |
| **end-to-end (capture → decision)** | **45-48 ms average, 81-104 ms peak** |
| main loop | 340-500 fps (the loop itself is not the bottleneck) |

How the lag is kept down, versus the original script:

- **snap, don't animate** — the cursor is written to the filtered hand position every frame; the
  original applied a 5x gain on top of a 7x smoothing factor and then re-smoothed it.
- **One-Euro filter** on every landmark axis and on the aiming angles: hard smoothing at rest,
  near-raw tracking during fast motion, so jitter drops without buying lag.
- **a deadband** so a still hand produces a literally still cursor.
- **newest-frame-only capture** thread with `BUFFERSIZE=1`; inference takes the latest frame and
  *drops* stale ones instead of queueing them.
- **no pyautogui** anywhere — input goes out through `SendInput`/`SetCursorPos` via ctypes, with no
  FAILSAFE checks in the hot path.

## Configuration

Everything lives in `kinesis_config.json` (created by `--save-config`) or can be overridden live:

```
kinesis.py --tune pinch_on=0.30 --tune filter_min_cutoff=2.0 --save-config
kinesis.py --list-monitors
kinesis.py --dry                 # full pipeline, every action logged, nothing injected
kinesis.py --exclusive           # cursor movement only
kinesis.py --no-preview          # skip the preview window
kinesis.py --cursor-hand left
```

Useful knobs when tuning by feel: `pinch_on`/`pinch_off` (pinch tightness), `filter_min_cutoff`
(smoothness at rest), `filter_beta` (responsiveness), `deadband_px`, `flick_window_s`,
`swipe_min_fraction`, `scroll_gain`, `alt_tab_hold_s`.

## Safety

- Any held key or mouse button is released on hand loss, on exit, on exception and on END.
  A stuck `Alt` or `Ctrl+Space` is the worst failure this program could produce, so release-all
  also runs from the shutdown path.
- `--dry` runs the entire pipeline with nothing injected — useful for checking detection and for
  watching which gesture fires.
- `--exclusive` disables gesture actions entirely, leaving cursor movement only.
- Window gestures skip overlay/shell windows (NVIDIA overlay, Windows Input Experience, the
  desktop) so they cannot be targeted by accident.

## Architecture

```
kinesis/
  winapi.py     ctypes: monitor enumeration, window inspection (z-order, fullscreen, maximised),
                SendInput/SetCursorPos/keyboard, blocklisted-window filtering
  tracking.py   capture thread (newest frame only) + inference thread + per-hand filtering
  filters.py    One-Euro filter (scalar and per-landmark 2D)
  pose.py       joint-angle finger states, pose classification, pointing angles
  aim.py        calibrated / heuristic monitor classification with hysteresis
  gestures.py   pose-transition state machine -> intents (no Windows calls: fully testable)
  actions.py    intents -> Windows actions, aimed window targeting, deferred fullscreen->minimise
  hud.py        preview overlay (pose, aim, action, fps, latency)
  app.py        wiring + main loop
```

The gesture engine emits `Intent` objects and never touches the OS, which is why the whole gesture
layer is covered by deterministic tests with no camera involved.

## Tests

```
.venv\Scripts\python -m pytest tests -q
53 passed
```

Landmark geometry is synthesised with exact joint angles (extended finger = collinear = 180°,
curled = rotated at the PIP = 70°), so every classification is checked against a known-correct
input. Covered: all eleven gestures, the pose classifier including rotation invariance, the
click/close-flick arbitration, hysteresis and cooldowns, the aim classifier against the real
four-monitor layout, the One-Euro filter, deadband, wheel accumulation and release-all safety.

`tests/_diag_windows.py` is a manual diagnostic that dumps every visible window with its monitor,
z-order flags and fullscreen state.

## Provenance

`vendor/Virtual-Mouse` is the upstream repo, unmodified. `reference/legacy/mouse_fixed.py` is the
patched standalone version made before this rewrite; `reference/legacy/_gesture_test.py` is the
12-check suite that proved its gesture logic. What the measurement found in the original:

| Finding | Evidence |
|---|---|
| detection works | 250 of 251 live frames, 16.2 fps at 1280x720 |
| gesture logic does not | the same 15 s capture would have fired **472** cursor actions — right-clicks nearly every frame |
| it stalls | `pyautogui.sleep(1)` after every right-click freezes the capture loop for a second |
| it crashes | full-frame mapping makes screen corners reachable → `FailSafeException` |
| clicks are accidental | `abs(fingertip_y - thumb_y) < 70` px is satisfied by a relaxed hand |
| it lags | 5x gain composed with 7x smoothing, re-applied every frame |

`vendor/awesome-hand-pose-estimation` (xinghaochen's curated list, the reference that seeded this
project) holds the upgrade path: HAPTIX (CVPR 2024) and HaMuCo (ICCV 2023) for better 3D pose →
better aiming; Deformer (ICCV 2023) for temporal fusion against jitter; FastHand and MobRecon for
speed; InterHand2.6M for two-handed work; RHD/FreiHAND/OneHand10K if a custom gesture classifier is
ever trained. The repo's own `evaluation/` folder carries MSRA yaw-pitch scoring, which is the
metric family for the aiming feature.

## Reference material in `vendor/`

Not committed (`.gitignore`d) — restore with:

```
git clone https://github.com/whitehatboy005/Virtual-Mouse            vendor/Virtual-Mouse
git clone https://github.com/xinghaochen/awesome-hand-pose-estimation vendor/awesome-hand-pose-estimation
```

`Virtual-Mouse` is the original script this project replaced (kept for the diff and the measured
comparison below). `awesome-hand-pose-estimation` is the curated research list that seeded the
project; see [Provenance](#provenance) for the parts of it that matter here.

## Known limits

- **One webcam cannot truly know** which monitor you are pointing at — it infers it from hand
  orientation, and calibration is what makes that reliable. Redo it if you move the camera, your
  chair, or a screen.
- Finger angles are measured in the image plane, so a hand pointed *straight at* the camera
  (heavily foreshortened) reads less reliably. Pointing at a screen is naturally oblique, so this
  is mostly a non-issue in practice.
- The camera on this machine tops out at 15 fps at 640x480, which puts a ~65 ms floor under the
  end-to-end figure. A 30 fps webcam would roughly halve it.
- The aiming feature assumes screens in a row (pitch is computed and stored, but this machine's
  four displays are all on one horizontal line).
