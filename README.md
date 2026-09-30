<div align="center">

# KINESIS

### Your eyes pick the window. Your hands do the work.

Control Windows with your hands through one webcam. Look at a window and it becomes the target. Move your index finger and the cursor goes there — immediately, with no easing. Then pinch, fist, swipe or point your way through clicking, scrolling, dragging, minimising, Alt-Tabbing and push-to-talk without ever reaching for a mouse.

Built for desks with several monitors, not one.

**11 hand gestures · multi-monitor support · ~46 ms end-to-end · smooth cursor · gaze-targeted windows · chroma-key virtual camera · 70 tests**

<a href="https://github.com/DrGekoz/Kinesis/stargazers"><img src="https://img.shields.io/github/stars/DrGekoz/Kinesis?style=for-the-badge&color=f59e0b" alt="Stars"></a>
<a href="https://github.com/DrGekoz/Kinesis/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-22c55e?style=for-the-badge" alt="License"></a>
<img src="https://img.shields.io/badge/platform-Windows-06b6d4?style=for-the-badge" alt="Platform">
<img src="https://img.shields.io/badge/python-3.11-8b5cf6?style=for-the-badge" alt="Python">
<img src="https://img.shields.io/badge/latency-~46ms-f43f5e?style=for-the-badge" alt="Latency">
<img src="https://img.shields.io/badge/tests-70%20passing-22c55e?style=for-the-badge" alt="Tests">
<img src="https://img.shields.io/badge/virtual%20camera-OBS-8b5cf6?style=for-the-badge" alt="Virtual camera">

[Features](#features) · [Multi-monitor](#multi-monitor-support) · [Gestures](#gesture-reference) · [Gaze](#gaze-your-eyes-pick-the-window) · [Virtual camera](#virtual-camera-stream-while-kinesis-uses-the-camera) · [Latency](#latency) · [Install](#install) · [Config](#configuration) · [Architecture](#architecture) · [Credits](#credits)

</div>

---

## Why this exists

Most webcam gesture demos are a toy. They work for thirty seconds, on one screen, with one hand, and fall apart the moment you actually try to use them: the cursor jitters, clicks fire on their own, the gesture you meant is not the gesture you got, and there is no way to tell the machine *which window* you are talking about.

Kinesis is built to be used. Every design decision here came from a concrete failure in front of a real camera — a click landing mid-gesture, a window that silently ignored the keys it was sent, an inference thread dying quietly while the app looked healthy. Those failures and the fix for each are in [Design notes](#design-notes).

## Features

| | |
| --- | --- |
| **Multi-monitor support** | Built with multiple monitors in mind from the first commit — four screens side by side on the development machine. Gestures carry the window *and* the monitor they apply to. |
| **Gaze-targeted windows** | Eye tracking picks the window your gesture lands on, so you never bring a window forward by hand before gesturing at it. |
| **Sub-second latency** | ~46 ms measured end-to-end. The cursor snaps to where your hand is rather than animating its way there. |
| **A genuinely smooth cursor** | A One-Euro filter removes webcam landmark jitter without paying for it in lag: hard smoothing when your hand is still, near-raw tracking when it moves. |
| **11 hand gestures** | Left click, right click, double click, drag, adaptive scroll, browser tabs, Alt-Tab, minimise, maximise, fullscreen and push-to-talk. |
| **A virtual camera** | Streams the webcam back out with the tracking overlays drawn on it, so OBS, Discord, Zoom or Teams can use the camera while Kinesis is using it. |
| **Chroma-key overlay mode** | A flat green canvas with the gaze cursor and hand skeleton, ready to key and composite over your real video. |
| **Gaze-driven scrolling** | Hold your gaze in the top or bottom band of the screen and it scrolls for as long as you keep looking. |
| **Safe by construction** | Dry-run mode, a cursor-only mode, and a release-everything path that runs on hand loss, exit, exception and panic (the `END` key). |
| **No mouse hooks** | Kinesis never installs a low-level mouse hook. Input is injected, never intercepted — nothing it does can swallow a click. |

## Multi-monitor support

This is the part most gesture tools get wrong, and the reason Kinesis looks the way it does.

A webcam sees you, not your desk. It has no idea there are four screens in front of you, or which one you mean. Kinesis answers that with two independent signals:

**Your eyes track what you're focusing on.** Eye tracking resolves where you are looking to the topmost window under that point, and that window becomes the target of the next hand gesture. No click to focus first, no Alt-Tab to bring it forward — you look at it, then you gesture at it. Keyboard gestures focus the resolved window first and verify the OS actually agreed before sending keys, because a browser will silently ignore `Ctrl+Tab` when it is not the foreground window.

**Your hands tell the PC what to do.** An index finger drives the cursor, thumb pinches click and drag, an open hand swiped sideways moves between tabs, a closed fist manages windows, and a two-handed fist-plus-pinch drives Alt-Tab.

If gaze is not calibrated, or you are looking elsewhere, the same gestures fall back to the monitor your hand is *pointing at* — hand orientation in 3D, calibrated per screen — and finally to the foreground window. Three ways to decide, in decreasing order of how much you meant it.

```
   eyes  ──────────────►  which window      (calibrated gaze → window under the point)
   hand  ──────────────►  which monitor     (pointing angle → nearest calibrated screen)
   fist  ──────────────►  what to do to it  (minimise / maximise / fullscreen / Alt-Tab)
```

## Gesture reference

| Gesture | Action |
| --- | --- |
| Point with the index finger | Move the cursor — 1:1, no easing, no animation |
| Thumb + index pinch | Left click (a short pinch clicks on release after a 120 ms grace; holding past 350 ms clicks while held) |
| Two quick pinches | Double click — two clicks inside the OS double-click window, exactly like a physical mouse |
| Thumb + middle pinch | Right click |
| Thumb + ring pinch, held | **Adaptive scroll** — hand travel drives the wheel and the gain rises with hand speed; the cursor freezes while scrolling |
| Thumb + pinky pinch, held | Drag — text selection, moving files; the cursor keeps following |
| Open hand, quick lateral swipe | Previous / next browser tab (`Ctrl+Shift+Tab` / `Ctrl+Tab`) |
| Open hand → closed fist | Minimise the target window, exiting fullscreen first if it is fullscreen |
| Closed fist → open hand | Maximise; if already maximised, fullscreen (`f` on YouTube, `F11` anywhere else) |
| Thumb + pinky out, index/middle/ring curled | Hold `Ctrl+Space` (push-to-talk) for as long as the pose is held |
| **Left** fist held 2 s | Opens Alt-Tab and holds `Alt`; each right-hand pinch taps `Tab` (hold to repeat); opening the left fist commits the switch |
| `END` key | Quit, releasing everything |

Every window action is aimed: "minimise" acts on the window you were looking at, or the topmost window of the monitor you were pointing at, not just whatever happened to have focus.

### Why the gestures don't fight each other

- Closing an open hand into a fist sweeps *through* a pinch, which would fire a stray click on the way to minimising. So a pinch *arms* a click instead of firing one: it commits 120 ms after release, a pinch held past 350 ms clicks while held, and a fist arriving inside that grace window cancels it. Tests assert the close-flick emits no click.
- A fist requires index, middle, ring **and** pinky curled, so the push-to-talk pose (pinky out) can never minimise a window.
- While `Alt` is held by the two-handed gesture, a right-hand pinch is a `Tab` and never a click.
- Scroll, drag and swipe take ownership of the cursor while active, and only one held action runs at a time — a swipe cannot drag a file and a scroll cannot click.

## Gaze: your eyes pick the window

Eye tracking (via [EyeTrax](#credits)) decides the **target window**. Look at a window, make a hand gesture, and the gesture applies to that window rather than to whatever happens to have focus. Across four screens this is the difference between a gesture system that works and one you give up on, because you no longer have to bring a window forward by hand first.

Two consequences worth knowing:

- Keyboard gestures (tab swipes, Alt-Tab) need a *focused* window, so Kinesis focuses the gaze target first and verifies the OS actually agreed before sending keys. A browser ignores `Ctrl+Tab` when it is not foreground, and without that verification the gesture would look like it worked while doing nothing.
- The mouse wheel goes to the window under the **cursor**, so gaze scrolling parks the cursor on the gaze point first. Turn that off with `gaze_scroll_warp_cursor: false`.

Calibrate it once; re-run it only if you move your chair:

```
calibrate_gaze.bat                 all four monitors, 5 points each, ~40 seconds of looking at dots
calibrate_gaze.bat --points 9      more points, better accuracy
calibrate_gaze.bat --monitors 3    only the screen you actually work on
```

It prints the in-sample pixel error and the **monitor hit rate** — the number that decides whether targeting works. Below ~85% means your head moved during it.

Kinesis ships its own calibration because EyeTrax's own only knows the primary monitor, and its screen size comes from `screeninfo`'s first monitor. Dots are therefore placed across the whole virtual desktop on a borderless topmost window positioned with `SetWindowPos` — Tk's geometry strings cannot express a negative origin (a leading `-` means "from the right edge"), which would have silently put calibration dots on the wrong screens.

### Gaze-driven scrolling

Hold your gaze in the top or bottom 12% band of the screen and it scrolls for as long as you keep looking. There is a dwell before it engages (a glance is not a scroll), a ramp to full speed (it does not jerk into motion), and a cooldown after release (it does not flap at the boundary). It stops the instant your gaze leaves the band.

## Virtual camera: stream while Kinesis uses the camera

Kinesis sends the camera feed back out as a webcam, with the tracking overlays drawn on it, so anything that consumes a camera — OBS, Discord, Zoom, Teams — can show your webcam while Kinesis is using it.

```
--vcam passthrough    the webcam frame with hand skeleton, gaze point and status drawn on it
--vcam overlay        EyeTrax's chroma-keyable look: flat green canvas, gaze cursor, hand overlays
                      composited on top — key the green out and lay it over your real video
--no-vcam             no virtual camera at all
kinesis.py --vcam-test  send a test pattern and report the device (a real self-check)
```

This needs **OBS Studio installed**, because its virtual-camera driver is what provides the "OBS Virtual Camera" device that `pyvirtualcam` writes to. If the device is missing, Kinesis says so and carries on without it.

Sending happens on its own thread with a newest-frame-only slot: `pyvirtualcam` paces with `sleep_until_next_frame()`, which blocks for a whole frame interval — up to 66 ms at this camera's 15 fps — and calling that from the tracking loop would add exactly the latency the rest of the project spent its effort removing.

```
        camera ──► hand tracking ──► gesture engine ──► Windows (SendInput)
            │                                              ▲
            └──► gaze ──► target window ────────────────────┘
            │
            └──► compositor ──► virtual camera ──► OBS / Discord / Zoom
```

## Latency

Measured on the development machine (`kinesis.py --latency-report`, 640x480 USB webcam):

| Metric | Measured |
| --- | --- |
| Camera capture | 15.0 fps (the device ceiling at 640x480 here — it reports 15 for YUYV and MJPG alike when asked for 30) |
| MediaPipe inference | 17-39 ms/frame at `model_complexity=0`, keeping up with capture (14-16 fps) and dropping no frames |
| **End-to-end (capture → decision)** | **45-48 ms average, 81-104 ms peak** |
| Main loop | 340-500 fps — the loop is not the bottleneck |

How the lag is kept down:

- **Snap, don't animate.** The cursor is written to the filtered hand position every frame. There is no easing, damping or interpolation between where your finger is and where the pointer goes.
- **A One-Euro filter** on every landmark axis and on the aiming angles. A plain low-pass kills jitter by adding lag and makes the cursor feel glued to treacle; One-Euro raises its cutoff as the signal speeds up, so a still hand is smooth and a fast hand is near-raw.
- **A deadband** so a still hand produces a literally still cursor rather than a one-pixel shimmer.
- **Newest-frame-only capture** on its own thread with `BUFFERSIZE=1`. Inference takes the latest frame and *drops* stale ones instead of queueing them, so the pipeline can never work on a frame the camera has already moved past.
- **No pyautogui.** Input goes out through `SendInput` / `SetCursorPos` via ctypes, with no FAILSAFE checks in the hot path and no screen-corner exceptions.

## Install

Requirements: **Windows 10/11**, **Python 3.11**, a webcam, and **OBS Studio** if you want the virtual camera.

```bat
git clone https://github.com/DrGekoz/Kinesis.git
cd Kinesis

python -m venv .venv
.venv\Scripts\pip install mediapipe==0.10.20 opencv-contrib-python==4.10.0.84 numpy==1.26.4
.venv\Scripts\pip install --no-deps -e vendor/eyetrax
.venv\Scripts\pip install scikit-learn screeninfo pyvirtualcam
```

EyeTrax has to be cloned first if it is not already there (it is a real dependency, not vendored code):

```bat
git clone https://github.com/ck-zhang/eyetrax vendor/eyetrax
```

Then:

```bat
check.bat            environment + camera + model self-test, then the test suite
calibrate.bat        aiming: point at each screen, hold, press Enter
calibrate_gaze.bat   gaze: look at the dots
run.bat              go
```

`END` quits from anywhere and releases every held key and button.

### Quick start

| Command | What it does |
| --- | --- |
| `run.bat` | Run it |
| `run.bat --dry` | Full pipeline, every action logged, **nothing injected** |
| `run.bat --exclusive` | Cursor movement only, gesture actions disabled |
| `run.bat --no-preview` | Skip the preview window (marginally lower latency) |
| `check.bat` | Environment self-test + the test suite |
| `calibrate.bat` | Point-at-screen calibration |
| `calibrate_gaze.bat` | Multi-monitor gaze calibration |
| `run.bat --vcam-test` | Prove the virtual camera device works |
| `run.bat --list-monitors` | Show the monitor layout and exit |
| `run.bat --tune pinch_on=0.30 --save-config` | Change and persist any setting |

## Configuration

Everything lives in `kinesis_config.json` (written by `--save-config`) and every key can be overridden live with `--tune key=value`. The ones worth touching:

| Key | Default | Meaning |
| --- | --- | --- |
| `pinch_on` / `pinch_off` | 0.34 / 0.55 | Pinch tightness, as a fraction of hand size — the main thing to tune for your hand |
| `filter_min_cutoff` | 1.6 | Smoothing at rest. Lower = smoother, slightly heavier |
| `filter_beta` | 0.05 | How fast the filter opens up during motion. Higher = more responsive |
| `deadband_px` | 1.5 | Movement below this is ignored, killing residual shimmer |
| `cursor_hand` | right | Which hand drives the cursor (`right`/`left`/`auto`) |
| `frame_width` / `frame_height` | 640 / 480 | Capture size. Lower = less latency, less detail |
| `model_complexity` | 0 | `0` = MediaPipe lite (faster), `1` = full (more accurate) |
| `active_margin` | 0.1 | Fraction of the frame edge treated as overflow, so the whole desktop is reachable |
| `click_arm_s` / `click_release_grace_s` | 0.35 / 0.12 | The click-vs-fist arbitration timings |
| `flick_window_s` | 0.4 | How fast a fist/open transition must be to count as a flick |
| `swipe_min_fraction` | 0.16 | Lateral travel needed to register a tab swipe |
| `alt_tab_hold_s` | 2.0 | How long the left fist must be held to open Alt-Tab |
| `scroll_gain` / `scroll_adaptive_k` | 1.0 / 1.4 | Scroll speed and how much it accelerates with hand speed |
| `gaze_target_enabled` | true | Whether gaze picks the target window |
| `gaze_scroll_mode` / `gaze_scroll_speed` | edge / 480 | Gaze scrolling on/off and how fast |
| `gaze_scroll_warp_cursor` | true | Park the cursor on the gaze point so the wheel lands correctly |
| `vcam_mode` / `vcam_width` | passthrough / 640x480 | Virtual camera mode and size |
| `aim_hysteresis_deg` / `aim_max_distance_deg` | 7 / 42 | Aim switching margin and the gate beyond which aim is unknown |

## Design notes

The decisions that make it feel right, and the failures they came from:

**Clicks are armed, not fired.** Closing an open hand into a fist sweeps through a pinch, and a naive pinch-to-click fires a stray click on the way to minimising a window. A pinch now arms a click that commits 120 ms after release (or while held past 350 ms — a duration a close sweep never reaches), and a fist inside the grace window cancels it outright.

**Finger states come from joint angles, not heights.** The original compared fingertip and knuckle `y`, which breaks the moment you rotate your hand — pointing at a screen to your left is exactly that case. Angles at the PIP joint are invariant to in-plane rotation, so an open hand reads open at any angle. This is checked in tests at 0/45/90/135/−90°.

**Pinch thresholds scale with the hand.** A fixed pixel threshold is satisfied by an ordinary relaxed hand, which is why the original fired right-clicks nearly every frame. Thresholds here are fractions of the distance from wrist to middle knuckle, so they hold regardless of how far you sit from the camera.

**The target travels on the intent.** Gesture logic emits `Intent` objects and never touches the OS, which is what makes the whole gesture layer testable without a camera. The window and monitor a gesture applies to ride along as `target_hwnd` / `focus_hwnd`, resolved by the caller from gaze, then aim, then the foreground window. Gaze-targeted behaviour is therefore covered by the same deterministic tests, with no eye tracker involved.

**Frame pacing cannot stall tracking.** `sleep_until_next_frame()` and every other blocking call lives on its own thread — capture, inference and the virtual camera sender all keep their own cadence while the decision loop runs at hundreds of frames per second.

**Failures are loud.** An early build had the inference thread die on a MediaPipe keyword argument that this version does not accept, while the app reported a healthy camera and an idle loop. That class of failure is now fatal, printed, and covered by a self-check that waits for a real inference result instead of sleeping a fixed time.

## Architecture

```
kinesis/
  winapi.py     ctypes: monitor enumeration, window inspection (z-order, fullscreen, maximised),
                SendInput / SetCursorPos / keyboard, DPI awareness, gaze-point window lookup
  tracking.py   capture thread (newest frame only) + inference thread + per-hand filtering
  filters.py    One-Euro filter (scalar and per-landmark 2D)
  pose.py       joint-angle finger states, pose classification, 3D pointing angles
  aim.py        calibrated / heuristic monitor classification with hysteresis
  gestures.py   pose-transition state machine -> intents, plus the gaze edge-scroller
                (no Windows calls: fully testable)
  actions.py    intents -> Windows actions, gaze/aim window targeting, focus-before-keyboard,
                deferred fullscreen-then-minimise
  gaze.py       EyeTrax wrapped around Kinesis's own frames: rate-limited, smoothed, persisted model
  vcam.py       virtual camera: passthrough and chroma-keyable overlay, on its own sender thread
  hud.py        preview overlay (pose, aim, gaze, target window, action, fps, latency)
  app.py        wiring + main loop
```

## Verification

```
.venv\Scripts\python -m pytest tests -q      →  70 passed
.venv\Scripts\python tools\verify_actions.py →  16/16 live checks
```

**70 deterministic tests** cover all eleven gestures, the pose classifier including rotation invariance, the click/close-flick arbitration, hysteresis and cooldowns, the aim classifier against a real four-monitor layout, the One-Euro filter, deadband, wheel accumulation, gaze edge-scroll (dwell, direction, ramp, stop, cooldown, stale rejection, cursor warp), target and focus carrying, the chroma-green overlay, hand and gaze compositing, and release-all safety. Landmark geometry is synthesised with exact joint angles (extended finger = collinear = 180°, curled = rotated at the PIP = 70°), so every classification is checked against a known-correct input rather than a recording.

**16 live checks** create a real window and drive it through the same ctypes path the gestures use, asserting that the OS actually changed state: `IsIconic`/`IsZoomed` transitions, `SendInput` keys, the YouTube-vs-`F11` fullscreen choice, dry-run logging, wheel-delta accumulation, and gaze-point → window resolution including the title blocklist and a point outside the window.

**The virtual camera** is verified against the real device: 45 frames sent synchronously at 28.4 fps plus 30 through the threaded publish path, 0 dropped, to "OBS Virtual Camera".

Not machine-verifiable, and honestly so: whether the pinch threshold suits *your* hand at *your* distance, and whether the Alt-Tab hold feels right. Those are `--tune` values, and calibrations are the point of `calibrate.bat` and `calibrate_gaze.bat`.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `check.bat` says inference is fatal | The installed MediaPipe version rejects a model argument. Kinesis filters unsupported keywords, so this usually means MediaPipe itself failed to load — check the printed error. |
| Camera opens but no hands are ever detected | Lighting, or the camera is pointed past you. The preview shows `pose: no hand` when nothing is tracked. |
| Clicks fire when you close your hand | Raise `pinch_on` (a tighter pinch is required) with `--tune pinch_on=0.38 --save-config`. |
| The cursor jitters | Lower `filter_min_cutoff` to 1.2 and raise `deadband_px` to 2.5. |
| The cursor feels laggy | Raise `filter_beta`, or set `filter: false` for raw landmarks. |
| Gestures act on the wrong window | Run `calibrate_gaze.bat` and check the reported monitor hit rate; if gaze is uncalibrated, run `calibrate.bat` for aiming. |
| Tab swipes do nothing | The target browser window was not focused. Kinesis focuses and verifies first, so check `focus FAILED` lines in the log. |
| No "OBS Virtual Camera" device | Install OBS Studio, run it once, and click Start Virtual Camera. |
| Handedness is inverted | Set `handedness_mirror: true` — MediaPipe reports handedness for a mirrored image, and the preview shows what it thinks each hand is. |

## Known limits

- One webcam cannot truly know which screen you are pointing at. It is inferred from hand orientation, and calibration is what makes it reliable; re-run it if you move the camera, your chair or a screen.
- Gaze is accurate enough to choose *which window*, not to choose a pixel. That is what it is used for.
- MediaPipe world landmarks are an estimate, so a hand pointed straight at the camera is foreshortened and finger angles get less reliable. Pinches are scale-relative and survive it; the flick gestures are the ones to watch.
- The development camera reports 15 fps at 640x480 regardless of format, which puts a ~65 ms floor under the end-to-end figure. A 30 fps webcam would roughly halve it.
- Aiming assumes screens in a row. Pitch is computed and stored, so stacked layouts are supported by the data model, but this machine's four displays are all on one horizontal line.
- Windows only. The gesture engine and the virtual camera are portable in principle; the entire `winapi` layer is not.

## Credits

Kinesis stands on other people's work, and this is what it owes:

| Project | Author | How it is used | Licence |
| --- | --- | --- | --- |
| [Virtual-Mouse](https://github.com/whitehatboy005/Virtual-Mouse) | whitehatboy005 | **The starting point.** Its approach seeded this project, and its measured failures in front of a real camera defined the problem Kinesis solves. It is vendored unmodified in `vendor/Virtual-Mouse` for the diff and the comparison. | MIT |
| [EyeTrax](https://github.com/ck-zhang/eyetrax) | ck-zhang | **Gaze estimation.** Feature extraction, ridge-regression gaze model, Kalman/EMA smoothers, and the chroma-keyable green virtual-camera look that the hand overlays are composited onto. Installed as a dependency, driven by Kinesis's own frames. | MIT |
| [awesome-hand-pose-estimation](https://github.com/xinghaochen/awesome-hand-pose-estimation) | Xinghao Chen | **The research index** used to choose the approach, and the upgrade path held open: HAPTIX (CVPR 2024) and HaMuCo (ICCV 2023) for better 3D pose → better aiming; Deformer (ICCV 2023) for temporal fusion against jitter; FastHand and MobRecon for speed; InterHand2.6M for two-handed work; RHD / FreiHAND / OneHand10K if a custom classifier is ever trained. | — |
| [MediaPipe](https://github.com/google-ai-edge/mediapipe) | Google | Hand landmarks (21 points) and metric world landmarks, plus the face landmarks under EyeTrax. The single biggest piece of the pipeline. | Apache-2.0 |
| [pyvirtualcam](https://github.com/letmaik/pyvirtualcam) | Maik Riechert | Virtual camera output. Used as an installed dependency, never bundled — it is GPL-2.0 because it links OBS's virtual-camera filter. | GPL-2.0 |
| [OBS Studio](https://github.com/obsproject/obs-studio) | OBS Project | Provides the "OBS Virtual Camera" DirectShow driver that `pyvirtualcam` writes to. | GPL-2.0 |
| [OpenCV](https://github.com/opencv/opencv) | OpenCV Foundation | Capture, drawing and the preview. | Apache-2.0 |
| [NumPy](https://github.com/numpy/numpy) · [scikit-learn](https://github.com/scikit-learn/scikit-learn) | respective authors | Numerics and the gaze model's regression. | BSD-3-Clause |
| [screeninfo](https://github.com/rr-/screeninfo) | Marwin Baumann | Monitor geometry (used by EyeTrax). | MIT |

Nothing from any of these applies input to your machine. Every action Kinesis takes is its own, through `SendInput` and `SetCursorPos`.

Per-dependency licence detail, including which ones are GPL-2.0 and why they are installed rather than bundled: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## License

MIT — see [LICENSE](LICENSE).


