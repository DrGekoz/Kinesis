<div align="center">

# KINESIS

### Your eyes pick the window. Your hands do the work.

Control Windows with your hands through one webcam. Look at a window and it becomes the target. Move your index finger and the cursor goes there — immediately, with no easing. Then pinch, fist, swipe or point your way through clicking, scrolling, dragging, minimising, Alt-Tabbing and push-to-talk without ever reaching for a mouse.

Built for desks with several monitors, not one.

**11 hand gestures · desk geometry · multi-monitor support · ~46 ms end-to-end · smooth cursor · gaze-targeted windows · push-to-talk dictation · chroma-key virtual camera · 93 tests**

<a href="https://github.com/DrGekoz/Kinesis/stargazers"><img src="https://img.shields.io/github/stars/DrGekoz/Kinesis?style=for-the-badge&color=f59e0b" alt="Stars"></a>
<a href="https://github.com/DrGekoz/Kinesis/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-22c55e?style=for-the-badge" alt="License"></a>
<img src="https://img.shields.io/badge/platform-Windows-06b6d4?style=for-the-badge" alt="Platform">
<img src="https://img.shields.io/badge/python-3.11-8b5cf6?style=for-the-badge" alt="Python">
<img src="https://img.shields.io/badge/latency-~46ms-f43f5e?style=for-the-badge" alt="Latency">
<img src="https://img.shields.io/badge/tests-93%20passing-22c55e?style=for-the-badge" alt="Tests">
<img src="https://img.shields.io/badge/virtual%20camera-OBS-8b5cf6?style=for-the-badge" alt="Virtual camera">

[Features](#features) · [Multi-monitor](#multi-monitor-support) · [Desk geometry](#desk-geometry) · [Gestures](#gesture-reference) · [Gaze](#gaze-your-eyes-pick-the-window) · [Dictation](#dictation-with-handy) · [Virtual camera](#virtual-camera-stream-while-kinesis-uses-the-camera) · [Latency](#latency) · [Install](#install) · [Config](#configuration) · [Architecture](#architecture) · [Credits](#credits)

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
| **Desk geometry** | Kinesis learns what your screens physically *are* — models, real sizes from EDID, how they sit in a row, and how far away you are — and uses it to aim the gaze calibration and the pointing maths. `--desk-report` shows what it found. |
| **Hands-free dictation** | Hold the thumb-and-pinky pose to hold your dictation hotkey. [Handy](https://github.com/cjpais/Handy) — free, open source, fully offline speech-to-text — defaults to `Ctrl+Space` on Windows, so you can write, prompt an LLM or take notes by talking, with almost no keyboard. |
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

**Your hands tell the PC what to do.** An index finger drives the cursor, thumb pinches click and drag, an open hand swiped sideways moves between tabs, a closed fist manages windows, a two-handed fist-plus-pinch drives Alt-Tab, and a thumb-and-pinky pose holds your dictation hotkey so you can write by talking.

If gaze is not calibrated, or you are looking elsewhere, the same gestures fall back to the monitor your hand is *pointing at* — hand orientation in 3D, calibrated per screen — and finally to the foreground window. Three ways to decide, in decreasing order of how much you meant it.

```
   eyes  ──────────────►  which window      (calibrated gaze → window under the point)
   hand  ──────────────►  which monitor     (pointing angle → nearest calibrated screen)
   fist  ──────────────►  what to do to it  (minimise / maximise / fullscreen / Alt-Tab)
```

## Desk geometry

A webcam sees you, not your desk — so Kinesis works out the desk for itself and feeds it into the gaze maths. Everything below runs automatically; `--desk-report` just shows you what it found.

```
$ run.bat --desk-report

desk geometry:
  camera 0: HD Pro Webcam C920  640x480  hFoV 65.9 deg (table:c920), focal 494 px
  4 monitors, 276 cm of active area (+10 mm bezel allowance between them)
   1. Digital TV                   93.0x53.0 cm   42.1"  52 ppi      1920x1080 (edid-timing)
   2. Lenovo L27i-30               60.0x34.0 cm   27.2"  81 ppi      1920x1080 (edid-basic)
   3. KAMN27F18WA                  60.0x33.0 cm   27.0"  82 ppi      1920x1080 (edid-basic)
   4. Lenovo L27i-30               60.0x34.0 cm   27.2"  81 ppi      1920x1080 (edid-basic)
  your eyes: 700 mm from the screen (70 cm), assumed
  angular span of the array from your seat: -69.3 to +52.4 deg
  angle between adjacent screen centres: 22.1, 41.1, 41.1 deg
  vertical alignment: aligned
```

That is a real desk: a 42" TV, two 27" Lenovo panels and a 27" Kogan, nearly 3 metres of active area, all on one GPU. Windows calls two of them "Generic PnP Monitor"; Kinesis reads their EDID and gets the actual models.

**Which camera, and how wide it sees.** Windows does not expose a webcam's field of view, so it is matched by device name against a table of known models (C920/C922 78° diagonal, Brio 90°, C270 60°, …), then read as horizontal/vertical for *your* capture aspect — 78° diagonal on a 4:3 frame is 66° across, not 78. Unknown camera? It assumes a documented 68° and says so. Set `camera_fov_deg` if you know better, or `camera_name` if the detection picked the wrong device (Oculus and OBS virtual cameras enumerate alongside real ones).

**Which monitors, and how big they are.** Each screen's EDID is read from the registry, keyed by the vendor+product code Windows reports per display. Physical size comes from the basic display parameters, falling back to the preferred timing descriptor, falling back to an estimate from the resolution — and the report always tells you which of those it used. From that: diagonal inches and true PPI per screen.

**How they sit.** Screens are laid out as a physical row in millimetres, with `bezel_mm` (default 10) between active areas because Windows snaps monitors edge-to-edge and knows nothing about bezels. Vertical offsets come from the pixel arrangement, so a screen sitting 12 cm lower is reported as misaligned rather than silently ignored.

**How far away you are.** Monocular, from the apparent width of your eyes: the outer eye corners span ~90 mm, the camera's focal length is known in pixels from its FoV, so distance = focal × 90 mm ÷ measured span. Accurate to roughly ±15%, which is plenty for the things it is used for. Measure your own eye span and set `eye_corner_mm` (or switch `scale_reference` to `ipd` and set your pupil distance) to tighten it. The measurement costs nothing: it is read from the face landmarks EyeTrax already computes, not a second model.

**What the geometry actually changes.**

- **Calibration is planned, not uniform.** Each screen gets sample points according to the angle it subtends from your seat — wide or off-axis screens get 9 points, narrow ones 5 — because those wide-angle screens are the ones a linear model gets wrong. The point count per screen is printed before you start.
- **Your seat distance is measured during calibration and recorded** next to the model. At runtime Kinesis keeps measuring it, and tells you when you have drifted far enough that the model is stale (`distance_warn_fraction`, default 25%): *"seat moved 34% since calibration (600 → 804 mm) — recalibrate gaze"*.
- **Per-screen hit rates, not just an overall score.** One bad screen is what actually ruins targeting, so each screen reports its own percentage.
- **It warns when your screens are too close in angle to tell apart** from where you sit — under 8° between adjacent screen centres, gaze discrimination gets unreliable and the report says so instead of letting you wonder why targeting keeps picking the wrong window.
- **It notices when the hardware changed.** If the screens are arranged differently to when gaze was calibrated, startup says so.
- **Pointing uses real triangulation.** The hand's pointing angle is matched against each screen's true angular span instead of being mapped linearly across a guessed ±70° range.

Honest limits, since this is inference rather than measurement: the FoV comes from a table (an unknown camera falls back to a documented default), the seat distance is monocular, bezel width is an assumption, and gaze accuracy falls off for screens more than ~45° off-axis — at which point the fix is to sit further back, and the report tells you the numbers.

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
| Thumb + pinky out, index/middle/ring curled | Hold `Ctrl+Space` for as long as the pose is held — push-to-talk **dictation** with [Handy](https://github.com/cjpais/Handy), Kinesis's headline combo gesture |
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
- Kinesis keeps measuring how far away your face is the whole time, and says so when your seat has drifted far enough that the model no longer applies. No other gesture system tells you that its calibration has gone stale.

Calibrate it once; re-run it only if you move your chair:

```
calibrate_gaze.bat                 all four monitors, 5 points each, ~40 seconds of looking at dots
calibrate_gaze.bat --points 9      more points, better accuracy
calibrate_gaze.bat --monitors 3    only the screen you actually work on
```

It prints the in-sample pixel error and the **monitor hit rate** — the number that decides whether targeting works. Below ~85% means your head moved during it. It also prints a per-screen hit rate, and the seat distance it measured while you were looking at the dots (see [Desk geometry](#desk-geometry)) — which is what lets Kinesis tell you later that you have moved far enough for the model to go stale.

```
$ calibrate_gaze.bat

in-sample error : mean   142 px   median   118 px   p90   290 px
monitor hit rate: 402/430 = 93.5%   <- what window targeting uses
  monitor 1: 84/90 = 93%
  monitor 2: 106/110 = 96%
  monitor 3: 122/130 = 94%
  monitor 4: 90/100 = 90%
seat distance   : 664 mm (66 cm) - recorded so Kinesis can tell you if you move
```

Kinesis ships its own calibration because EyeTrax's own only knows the primary monitor, and its screen size comes from `screeninfo`'s first monitor. Dots are therefore placed across the whole virtual desktop on a borderless topmost window positioned with `SetWindowPos` — Tk's geometry strings cannot express a negative origin (a leading `-` means "from the right edge"), which would have silently put calibration dots on the wrong screens.

### Gaze-driven scrolling

Hold your gaze in the top or bottom 12% band of the screen and it scrolls for as long as you keep looking. There is a dwell before it engages (a glance is not a scroll), a ramp to full speed (it does not jerk into motion), and a cooldown after release (it does not flap at the boundary). It stops the instant your gaze leaves the band.

## Dictation with Handy

The thumb-and-pinky pose holds `Ctrl+Space` for as long as you keep the pose, and `Ctrl+Space` is the Windows default **transcribe** binding in [Handy](https://github.com/cjpais/Handy) — a free, open-source, fully offline speech-to-text app (Whisper or Parakeet, running on your own machine, nothing sent to the cloud).

Hold the gesture, talk, release, and the text lands in whatever field has focus:

```
   thumb + pinky out  ──hold──►  Ctrl+Space down  ──►  Handy records
        (hand pose)                    (hotkey)              │
   open the hand      ──release─►  Ctrl+Space up    ──►  transcript pasted into the focused app
```

This is the point where hand gestures stop being a gimmick and start replacing the keyboard. Writing, prompting an LLM, replying to a message, taking notes in a meeting — all of it becomes one held pose and your voice. Combined with [gaze targeting](#gaze-your-eyes-pick-the-window), you look at the text field you mean and dictate into it without touching a key.

Both sides are configurable:

- In Handy: **Settings → Shortcuts** (its overlay works fine while Kinesis is running).
- In Kinesis: `ptt_keys`, if you remap Handy's shortcut — `run.bat --tune ptt_keys=ctrl,alt,d --save-config`. Release always releases exactly the keys it pressed, whatever they are, so a remap can never leave a key stuck down.

Handy's other default bindings also work well with Kinesis's window gestures: `Ctrl+Shift+Space` (post-process the transcript with an LLM) and `Ctrl+Shift+D` (Windows/Linux): both are one gesture away.

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
| `ptt_keys` | ctrl, space | The dictation hotkey the thumb-and-pinky gesture holds — matches Handy's Windows default |
| `scroll_gain` / `scroll_adaptive_k` | 1.0 / 1.4 | Scroll speed and how much it accelerates with hand speed |
| `gaze_target_enabled` | true | Whether gaze picks the target window |
| `gaze_scroll_mode` / `gaze_scroll_speed` | edge / 480 | Gaze scrolling on/off and how fast |
| `gaze_scroll_warp_cursor` | true | Park the cursor on the gaze point so the wheel lands correctly |
| `vcam_mode` / `vcam_width` | passthrough / 640x480 | Virtual camera mode and size |
| `aim_hysteresis_deg` / `aim_max_distance_deg` | 7 / 42 | Aim switching margin and the gate beyond which aim is unknown |
| `geometry_enabled` | true | Use camera/monitor physical data for gaze and pointing |
| `camera_name` / `camera_fov_deg` | (auto) / 0 | Override the detected camera, or its diagonal FoV (0 = use the model table) |
| `bezel_mm` | 10 | Physical gap between active areas — Windows snaps monitors edge-to-edge and knows nothing about bezels |
| `assumed_distance_mm` | 700 | Seat distance used until a gaze calibration measures it |
| `scale_reference` / `eye_corner_mm` / `ipd_mm` | eye_corners / 90 / 63 | What physical span the distance estimate uses, and how wide yours is |
| `distance_warn_fraction` | 0.25 | How much the seat can drift before the gaze model counts as stale |
| `distance_min_mm` / `distance_max_mm` | 300 / 1400 | Range outside which gaze is flagged as unreliable |
| `monitor_mm_overrides` | (none) | Physical size per screen if the EDID lies |

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
  aim.py        calibrated / heuristic monitor classification with hysteresis, triangulated
                against real screen geometry when it is known
  geometry.py   desk model: camera FoV, EDID monitor sizes, physical row layout, seat distance,
                angular spans - pure geometry, degrades to estimates when the hardware is shy
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
.venv\Scripts\python -m pytest tests -q      →  93 passed
.venv\Scripts\python tools\verify_actions.py →  16/16 live checks
```

**93 deterministic tests** cover all eleven gestures, the pose classifier including rotation invariance, the click/close-flick arbitration, hysteresis and cooldowns, the aim classifier against a real four-monitor layout, the One-Euro filter, deadband, wheel accumulation, gaze edge-scroll (dwell, direction, ramp, stop, cooldown, stale rejection, cursor warp), target and focus carrying, the chroma-green overlay, hand and gaze compositing, release-all safety, and the desk geometry: EDID parsing against a hand-built block laid out to the real spec, FoV-from-diagonal across aspect ratios, the distance round trip, physical row layout with bezels, vertical misalignment, PPI, angular spans, screen selection by angle, seat-drift gating, and the landmark tap turning eye corners into a distance. Landmark geometry is synthesised with exact joint angles (extended finger = collinear = 180°, curled = rotated at the PIP = 70°), so every classification is checked against a known-correct input rather than a recording.

**The desk model** is checked against this machine's real hardware: four screens identified by model (a 42" TV, two Lenovo L27i-30 and a Kogan KAMN27F18WA), physical sizes from EDID, 276 cm of active area, angular span and per-screen separation from a 70 cm seat, and the C920's focal length in pixels derived from its listed FoV.

**16 live checks** create a real window and drive it through the same ctypes path the gestures use, asserting that the OS actually changed state: `IsIconic`/`IsZoomed` transitions, `SendInput` keys, the YouTube-vs-`F11` fullscreen choice, dry-run logging, wheel-delta accumulation, and gaze-point → window resolution including the title blocklist and a point outside the window.

**The virtual camera** is verified against the real device: 45 frames sent synchronously at 28.4 fps plus 30 through the threaded publish path, 0 dropped, to "OBS Virtual Camera".

Not machine-verifiable, and honestly so: whether the pinch threshold suits *your* hand at *your* distance, whether the Alt-Tab hold feels right, and how accurate the gaze model is for your eyes — the distance estimate is monocular (±15%) and the FoV comes from a table. Those are `--tune` values and `eye_corner_mm` / `camera_fov_deg` overrides, and calibrations are the point of `calibrate.bat` and `calibrate_gaze.bat`.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `check.bat` says inference is fatal | The installed MediaPipe version rejects a model argument. Kinesis filters unsupported keywords, so this usually means MediaPipe itself failed to load — check the printed error. |
| Camera opens but no hands are ever detected | Lighting, or the camera is pointed past you. The preview shows `pose: no hand` when nothing is tracked. |
| Clicks fire when you close your hand | Raise `pinch_on` (a tighter pinch is required) with `--tune pinch_on=0.38 --save-config`. |
| The cursor jitters | Lower `filter_min_cutoff` to 1.2 and raise `deadband_px` to 2.5. |
| The cursor feels laggy | Raise `filter_beta`, or set `filter: false` for raw landmarks. |
| Gestures act on the wrong window | Run `calibrate_gaze.bat` and check the reported monitor hit rate; if gaze is uncalibrated, run `calibrate.bat` for aiming. |
| Gaze keeps picking the neighbouring screen | `run.bat --desk-report` and look at the angle between adjacent screen centres. Under ~8° the screens are too close together *from where you sit* — sit further back (a bigger angle per screen) or use `--monitors` to restrict gaze to the screens you actually work on. |
| Gaze accuracy was fine and now is not | Kinesis says so in the log when your seat has drifted more than `distance_warn_fraction`. Move the chair back or re-run `calibrate_gaze.bat`. |
| The desk report has the wrong screen sizes | That monitor's EDID did not carry a size (it would say `estimate`). Set `monitor_mm_overrides` or `--tune bezel_mm=` to match your desk. |
| The desk report names the wrong camera | Cameras enumerate in DirectShow order (Oculus and OBS virtual cameras appear too). Set `camera_name` and `camera_fov_deg` explicitly. |
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
| [awesome-hand-pose-estimation](https://github.com/xinghaochen/awesome-hand-pose-estimation) | Xinghao Chen | **The research index** used to choose the approach, and the upgrade path held open: HAPTIX (CVPR 2024) and HaMuCo (ICCV 2023) for better 3D pose → better aiming; Deformer (ICCV 2023) for temporal fusion against jitter; FastHand and MobRecon for speed; InterHand2.6M for two-handed work; RHD / FreiHand / OneHand10K if a custom classifier is ever trained. | — |
| [Handy](https://github.com/cjpais/Handy) | CJ Pais | **The dictation partner.** Not code Kinesis links against: the thumb-and-pinky gesture holds `Ctrl+Space`, which is Handy's default transcribe binding on Windows, so offline speech-to-text works with no setup on either side. | MIT |
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


