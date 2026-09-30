# Kinesis — plan.md

**Webcam Human-Computer Use.** Drive the whole machine with hand gestures seen through one webcam:
cursor control, clicks, window management, scroll, drag, browser tabs and Alt-Tab, with the hand's
*angle* selecting which physical monitor you're operating on.

Project root: `F:\aaaaaVIBECODING\Kinesis`

```
Kinesis/
  plan.md                     this file
  kanban.json                 same plan as trackable cards
  kinesis.py                  entry point
  calibrate.py                point-at-each-monitor calibration wizard
  run.bat / calibrate.bat     launchers
  kinesis/                    the engine (this is the product)
    config.py                 config + calibration store
    winapi.py                 ctypes: monitors, windows, keyboard/mouse injection
    filters.py                One-Euro filter (jitter removal at low latency)
    pose.py                   landmark -> finger angles, pose, handedness, pointing angles
    tracking.py               camera + inference threads, landmark smoothing, hand state
    gestures.py               pose-transition state machine -> intent events
    actions.py                intent -> Windows action (window ops, keys, scroll, clicks)
    hud.py                    camera preview overlay
    app.py                    wiring + main loop
  reference/legacy/           the fixed build of the original Virtual-Mouse script (kept for diffing)
  vendor/Virtual-Mouse/       upstream whitehatboy005/Virtual-Mouse (unmodified, for reference)
  vendor/awesome-hand-pose-estimation/   xinghaochen's curated research list (reference material)
  tests/                      deterministic tests (no camera required)
```

---

## 1. Why this exists — what upstream taught us

`vendor/Virtual-Mouse/mouse.py` (21★, last touched 2024) is a 183-line MediaPipe demo. Measured
against the real webcam on this machine:

| Finding | Evidence |
|---|---|
| Detection itself works | 250 of 251 live frames detected a hand, 16.2 fps at 1280x720 |
| Gesture logic is broken | the same 15s capture would have fired **472** cursor actions — right-clicks nearly every frame |
| It stalls | `pyautogui.sleep(1)` after every right-click freezes the capture loop for a full second |
| It crashes | full-frame mapping makes screen corners reachable → `FailSafeException` kills the app |
| Clicks are accidental | `abs(fingertip_y - thumb_y) < 70` px is satisfied by an ordinary relaxed hand |
| Cursor lags | 5x gain applied on top of a 7x smoothing factor, then re-smoothed every frame |

The repaired standalone version is in `reference/legacy/mouse_fixed.py` — 12/12 checks pass, and the
same hand pose that fired 77 actions in upstream fires zero. Kinesis is a rewrite that keeps the
good idea (MediaPipe landmarks) and throws away the gesture logic, the smoothing and the latency.

**Machine layout discovered during setup:** four displays, not three.

```
\\.\DISPLAY13  primary   x=0     1920x1080
\\.\DISPLAY14            x=-1920  1920x1080
\\.\DISPLAY15            x=+1920  1920x1080
\\.\DISPLAY16            x=-3840  1920x1080
virtual desktop: -3840,0 -> 3840,1080  (7680x1080)
```

Aim calibration therefore reads the live monitor layout at runtime and supports any number of
displays; it does not hardcode three.

---

## 2. Latency and jitter

The brief: *the cursor should snap to the current hand position instead of animating its way
there*, and separately *the mouse feels jittery*. Those pull in opposite directions, so:

- **No positional animation at all.** No easing, no `plocx += (x - plocx)/7`. Cursor position =
  filtered hand position, written every frame.
- **Jitter is removed by filtering, not by lag.** MediaPipe landmark noise is high-frequency;
  a One-Euro filter is a low-pass whose cutoff rises with speed, so it kills tremor at rest and
  tracks fast motion with ~1 frame of lag. This is the standard fix (cf. Deformer, ICCV 2023, which
  attacks exactly this problem with temporal fusion).
- **A deadband** swallows sub-pixel residue so a stationary hand produces a literally still cursor.

Latency budget, measured stage by stage rather than assumed:

| Stage | Current (upstream) | Kinesis | How |
|---|---|---|---|
| Camera frame | MSMF backend, no buffer limit, may serve a stale frame | newest frame only | capture thread + `BUFFERSIZE=1`, DSHOW where available |
| Resolution | 1280x720 | 640x480 default | 2-4x less pixels through inference; configurable |
| Inference | `model_complexity=1` | `model_complexity=0` default | lite model, ~2x faster |
| Landmark noise | none removed | One-Euro (min_cutoff 1.6, beta 0.05) | `filters.py` |
| Cursor write | pyautogui (≈1-3 ms + FAILSAFE checks) | `SetCursorPos` via ctypes | `winapi.py` |
| Preview draw | `imshow` + `waitKey(1)` every frame | optional / throttled | `--no-preview` |

Measured on this machine after the build (`--latency-report`, 25 s live run, real camera):

```
camera capture    15.0-15.1 fps      (device ceiling at 640x480: it reports 15 fps for YUYV and
                                      MJPG alike when asked for 30, so this is the hardware floor)
mediapipe         17-39 ms/frame, 14-16 fps - keeps up with capture, drops nothing
end-to-end        45-48 ms average, 81-104 ms peak  (capture -> decision)
main loop         340-500 fps         (the loop itself is not the bottleneck)
```

So the ~65 ms camera interval dominates; a 30 fps webcam would roughly halve the end-to-end figure.
`--latency-report` prints exactly these numbers so the claim stays checkable rather than asserted.

---

## 3. Which screen are you pointing at

Single webcam, no depth sensor, so "which monitor" is a learned mapping from hand orientation:

- **Pointing vector.** From world landmarks (metric) and image landmarks: `palm -> index tip`
  when the index is extended, `wrist -> middle MCP` when the hand is closed.
- **Angles.** `yaw = atan2(dx, -dz)` (left/right), `pitch = atan2(dy, -dz)` (up/down), both in
  degrees and smoothed with their own One-Euro filter. With four displays on one horizontal row,
  yaw does the work; pitch is carried for future stacked layouts.
- **Calibration.** `calibrate.py` walks each monitor: point at it, hold, press its number. It
  samples ~40 frames and stores `(yaw, pitch)` centroids plus the monitor rect into
  `kinesis_calibration.json`. Calibration also captures the neutral palm-to-camera geometry, so it
  is per-desk, not per-machine, and can be redone in 30 seconds.
- **Classification.** Nearest calibrated centroid in angle space, with hysteresis (must be closer
  to the new monitor by a margin before switching) and a max-distance gate (beyond it, aim is
  "unknown" and aim-dependent actions fall back to the foreground window).
- **Heuristic before calibration.** Mapped monotonically across the virtual desktop, so aiming is
  roughly right on first run and gets accurate after calibration.

The HUD shows the live yaw, the matched monitor number and the aim confidence, so calibration
quality is visible rather than guessed.

---

## 4. Gesture table

Primitives are angle-based, not y-axis comparisons, so they survive an in-plane rotated hand
(a hand held sideways still reads correctly).

- **Extended / curled** — joint angle at the PIP joint of each finger, with hysteresis
  (>155° extended, <110° curled, sticky in between).
- **Pinch (thumb to X)** — 2D distance from thumb tip to fingertip, normalised by hand size
  (wrist → middle MCP), with separate on/off thresholds. Scale-relative, so it works at any
  distance from the camera.

| # | Gesture | Trigger | Action |
|---|---|---|---|
| 1 | Point | index extended | cursor follows index tip, 1:1, no easing |
| 2 | Left click | thumb+index pinch | left click at cursor — committed on release after a 120 ms grace, or by holding the pinch past 350 ms |
| 3 | Double click | two thumb+index pinches inside 450 ms | two clicks in the double-click window — the OS interprets them, exactly like a physical mouse |
| 4 | Right click | thumb+middle pinch | right click (held-pose guarded, 600 ms) |
| 5 | Scroll | thumb+ring pinch, held | *adaptive scroll*: vertical hand travel drives the wheel, gain rises with hand speed; cursor frozen while scrolling |
| 6 | Drag | thumb+pinky pinch, held | mouse down on pinch, up on release — text selection, file drags |
| 7 | Minimise | open hand **closing** into a fist | fullscreen-aware: if the aimed monitor's window is fullscreen, exit fullscreen first, then minimise |
| 8 | Maximise | fist **opening** into an open hand | if not maximised → maximise; if already maximised → fullscreen (`f` on YouTube, else `F11`) |
| 9 | Tab switch | open hand, fast lateral swipe | swipe right → next tab (`Ctrl+Tab`), swipe left → previous (`Ctrl+Shift+Tab`); cursor frozen during the swipe |
| 10 | Alt-Tab | **left** fist held 2 s, then **right** thumb+index pinches | 2 s: `Alt` down + one `Tab` (switcher opens), `Alt` stays down while the fist is held; each right pinch = one more `Tab` (hold to repeat); opening the left fist releases `Alt` and commits |
| 11 | Push-to-talk | thumb **and** pinky extended, index/middle/ring curled ("shaka") | `Ctrl+Space` held for as long as the pose is held, released the moment it breaks |

Requirements 7 and the originally-separate "close while aimed at a screen" are one gesture — the
aimed-monitor targeting is what the "aimed at any screen" case wanted, so it is folded into #7
rather than duplicated as a gesture nobody could distinguish.

### Conflict resolution (the part that decides whether it feels good)

- **Fist vs left click.** Closing a hand into a fist passes through a pinch, which would fire a
  stray left click. So a pinch *arms* a click rather than firing on the pinch itself: the click is
  committed 120 ms after the pinch is released, or immediately if the pinch is held past 350 ms
  (which a sweep through a pinch never reaches). If a fist forms inside that 120 ms grace window
  the pending click is cancelled and gesture #7 fires instead. Both cases are covered by tests.
- **Fist vs shaka.** Fist requires index, middle, ring **and** pinky curled. Shaka keeps the pinky
  out, so #11 and #7 cannot be confused.
- **Click vs Tab.** While `Alt` is held by gesture #10, a right-hand thumb+index pinch is a `Tab`
  and never a click.
- **Scroll/drag/swipe vs cursor.** Those three freeze the cursor for their duration so the pointer
  does not wander while the hand is busy.
- **One action at a time.** The engine holds a single active action; a new gesture cannot start
  while a held action (scroll, drag, alt-tab, push-to-talk) is in progress.

### Safety

- Any held key or mouse button is released when the hand leaves frame, on exit, on exception, and
  on `Esc`/`q`. A stuck `Alt` or `Ctrl+Space` is the worst failure this program could have, so
  release-all runs from a `finally` block and from the signal handler.
- `--dry` runs the entire pipeline with every action logged and nothing injected.
- `--exclusive` can ignore gesture actions entirely and only move the cursor, for trust-building.

---

## 5. Reference material (`vendor/awesome-hand-pose-estimation`)

The curated list behind the request, and what in it actually matters here:

**Used now** — *MediaPipe Hands: On-device Real-time Hand Tracking* (CVPRW 2020)
[PDF](https://arxiv.org/pdf/2006.10214.pdf) — the tracker Kinesis runs on. Fast, CPU-only,
21 landmarks plus a metric `world_landmarks` set, which is what makes the pointing-angle work
possible in the first place.

**Upgrade path for pointing accuracy** — better 3D pose means better yaw/pitch:
- *Reconstructing Hands in 3D with Transformers* (CVPR 2024) — current strong monocular 3D hand
  reconstruction.
- *HaMuCo: Hand Pose Estimation via Multiview Collaborative Self-Supervised Learning* (ICCV 2023)
  `github.com/zxz267/HaMuCo`
- *Deformer: Dynamic Fusion Transformer for Robust Hand Pose Estimation* (ICCV 2023) — temporal
  fusion, directly relevant to the jitter requirement.
- *FastHand* (arXiv 2102.07067) and *MobRecon* (CVPR 2022) `github.com/SeanChenxy/HandMesh` —
  the speed-first end of the literature if MediaPipe's lite model is not fast enough.
- *[2025 ICLR] SiMHand* `github.com/ut-vision/SiMHand` — pretraining for a fine-tuned personal
  model, the realistic route to "knows *my* hand gestures".

**Two-hand work** — Alt-Tab is a two-handed gesture, so *InterHand2.6M* (ECCV 2020)
`github.com/facebookresearch/InterHand2.6M` (2.6M frames) is the dataset of record, and
*Im2Hands* (CVPR 2023) / *ACR* (CVPR 2023) are the two-hand reconstruction methods.

**Datasets if we ever train our own classifier** — FreiHAND (130k, RGB), RHD (41k synthetic, free
pose labels), OneHand10K, HIU-DMTL (200 subjects), plus InterHand2.6M above. RHD is the cheapest
starting point for a custom gesture classifier.

**Evaluation** — the repo ships `evaluation/` (ICVL/MSRA/NYU error and yaw-pitch scripts) with
source. Relevant to us because *MSRA yaw-pitch* is precisely the metric family for the
pointing-angle feature; if a future model replaces MediaPipe, this is the harness to score it with.

---

## 6. Gaze (EyeTrax) and the virtual camera

Two additions, both required by Joe:

1. **Gaze selects the target window**, so hand gestures apply to the window you are looking at
   rather than to whatever merely has focus.
2. **The webcam feed goes back out through a virtual camera** with the hand-tracking overlaid, so
   the webcam can still be streamed while Kinesis is using it. Built on EyeTrax's existing virtual
   cam overlay, with the hand overlays composited on top.

### What EyeTrax (0.4.0) gives us, and what it does not

Gives: `GazeEstimator` (MediaPipe Tasks FaceLandmarker, rotation-normalised eye landmarks plus
yaw/pitch/roll as features, ridge/SVR/MLP models, save/load), four calibration routines, four
smoothers (Kalman, Kalman+EMA, KDE, none), a `draw_cursor` helper, and a virtual-cam path built on
**pyvirtualcam targeting the OBS virtual camera**.

Does not give, and why we add it:

| Gap | Consequence | What Kinesis does |
|---|---|---|
| `get_screen_size()` returns `get_monitors()[0]` | gaze coordinates only exist on the primary monitor; four displays here | our own calibration across the **whole virtual desktop** (dots placed on every monitor, trained against virtual-desktop coordinates) |
| every entry point opens its own `VideoCapture` | a second camera client, and frames that are not the ones the gestures saw | feed EyeTrax `extract_features()` from the frame Kinesis already captured |
| the virtual cam paints a full-screen green canvas with a gaze dot | no webcam in the feed, so it cannot be used *as* a webcam | two modes: `passthrough` (real frames + overlays) and `overlay` (EyeTrax's chroma-keyable look + hand overlays) |

Everything EyeTrax-side is imported from the vendored source (`pip install -e vendor/eyetrax`), so
the code being extended is the code that runs. `draw_cursor` and the green-canvas style are reused
verbatim in overlay mode rather than reimplemented.

### Which window a gesture applies to

Resolved per action, in order:

1. **Gaze target** — the topmost non-blocklisted window under the gaze point (z-order walk), when
   gaze is calibrated and the point is valid.
2. **Aim target** — the topmost window on the monitor the *hand* was pointing at (section 3).
3. **Foreground window** — the fallback when neither is available.

Mouse actions land at the cursor, so nothing has to move. **Keyboard actions need the window
actually focused** — a browser will not switch tabs in an unfocused window — so tab swipes and
Alt-Tab call `focus_window()` first and verify `GetForegroundWindow()` before sending keys.

### Gaze scrolling

Eye-driven, because a glance is the natural way to say "scroll this":

- **edge mode** — looking at the top or bottom band of the screen (default 12%) for a short dwell
  (0.25 s) scrolls continuously while you keep looking; leaving the band stops it. Bands are on the
  *screen*, so it works over any window.
- **adaptive hand scroll stays** (thumb+ring pinch) and now scrolls whatever window the gaze/cursor
  is over.
- Wheel events go to the window under the **cursor**, so on gaze-scroll activation the cursor is
  warped to the gaze point first (`gaze.scroll_warp_cursor`). Without that, "scroll what I'm
  looking at" would scroll whatever the pointer happened to be resting on.

### Virtual camera

The device is already on this machine: the physical camera is a **Logitech C920 ("HD Pro Webcam
C920")** and **"OBS Virtual Camera"** is registered as a DirectShow device, so `pyvirtualcam` can
target it. Verified the C920 also allows **multiple simultaneous clients** (two DSHOW opens, 13
frames each in 2 s), so the virtual camera is about adding overlays and freeing the device for other
apps, not about a workaround for exclusivity.

| Mode | Output | Use |
|---|---|---|
| `passthrough` | the real webcam frame + hand skeleton + gaze cursor + HUD status | select "OBS Virtual Camera" in Discord/OBS/Zoom and your face is there, with the tracking drawn on |
| `overlay` | EyeTrax's green canvas + gaze cursor + hand skeleton + labels, chroma-keyable | composite over a real camera feed in OBS |
| `off` | nothing | default until asked for |

### New pieces

```
kinesis/gaze.py            wraps GazeEstimator: our frames in, smoothed screen-space gaze out
kinesis/vcam.py            pyvirtualcam output + the two compositing modes
kinesis/windows.py         (winapi additions: topmost_window_at, focus verification)
calibrate_gaze.py          multi-monitor gaze calibration wizard (Tk, borderless, per monitor)
calibrate_gaze.bat         launcher
```

Config additions: `gaze_enabled`, `gaze_model_path`, `gaze_smoother`, `gaze_max_age_s`,
`gaze_scroll_mode`, `gaze_scroll_edge`, `gaze_scroll_dwell_s`, `gaze_scroll_speed`,
`gaze_scroll_warp_cursor`, `vcam_enabled`, `vcam_mode`, `vcam_fps`, `vcam_device`,
`vcam_show_landmarks`, `vcam_show_hud`.

### Verification, honestly split

Machine-checkable without his eyes: the target-resolution order (a synthetic gaze point over a real
window resolves to that window), focus-before-keyboard, the edge-scroll state machine (dwell,
activation, stop-on-leave, cooldown), palette/dimension correctness of the composited frames, that
frames really reach the OBS virtual camera device, and gaze model save/load.

Needs his eyes: the calibration itself, and whether gaze accuracy is good enough to pick windows in
practice. Webcam gaze tracking is roughly 2-5° — which is *fine* for "which window am I looking at"
(a window is orders of magnitude bigger than that error) and useless for pixel targeting. This is
why gaze is used for targeting and not for pointing.

## 7. Roadmap

Status is only ticked when the phase is implemented *and* its tests pass.

- [x] **P0 — Scaffold.** Project root, vendored repos, venv, config store, monitor enumeration,
      ctypes input layer, `reference/legacy` provenance.
- [x] **P1 — Latency and jitter.** Capture thread that drops stale frames, One-Euro landmark
      filter, deadband, snap cursor, direct `SetCursorPos`.
- [x] **P2 — Pose engine.** Joint-angle finger detection, scale-relative pinches, pose
      classification, handedness, yaw/pitch pointing vector.
- [x] **P3 — Aim.** Calibration wizard, angular nearest-centroid classifier with hysteresis,
      heuristic fallback, HUD readout.
- [x] **P4 — Gestures.** The eleven gestures above with the conflict rules, as an explicit state
      machine with a single active action.
- [x] **P5 — Actions.** Minimise/maximise/fullscreen (with YouTube detection), aimed-monitor
      window targeting, Alt-Tab held-key flow, tab swipes, adaptive scroll, drag, clicks,
      push-to-talk hold.
- [x] **P6 — HUD and safety.** Overlay (pose, aim, monitor, active action, fps, latency),
      release-all failsafe, dry-run mode.
- [x] **P7 — Tests.** Deterministic landmark tests for every pose, every transition, the conflict
      rules, the click/close arbitration, the monitor classifier and the filter. No camera needed.
- [x] **P8 — Docs.** README, this plan, launchers, kanban. Git init + commit. GitHub push
      deliberately left as an explicit request.
- [x] **P9 — Gaze engine and multi-monitor calibration.** EyeTrax fed from Kinesis's own frames,
  smoothed, persisted model; calibration wizard that places dots on every monitor and trains
  against virtual-desktop coordinates.
- [x] **P10 — Gaze-targeted actions.** Topmost window under the gaze point becomes the gesture
  target; keyboard gestures focus it and verify before sending keys.
- [x] **P11 — Virtual camera.** pyvirtualcam → "OBS Virtual Camera", passthrough and
  chroma-keyable overlay modes, hand overlays on top of EyeTrax's existing overlay.
- [x] **P12 — Gaze scrolling.** Edge-dwell scrolling with cursor warp, plus gaze-targeted
  hand scroll.

## 8. Verification

Machine-verified, with the evidence:

| Claim | Evidence |
|---|---|
| gesture logic | **53** deterministic gesture tests pass (`pytest tests -q`), driven by synthetic landmarks with exact joint angles — no camera involved |
| gaze scrolling | edge band dwell, direction (top = up), ramp to full speed, stop on leaving the band, cooldown, stale-gaze rejection, and the cursor warp that makes the wheel land on the looked-at window |
| gaze targeting | the target window is carried on the window intents and the focus window on keyboard intents; Alt-Tab focuses the gaze target before opening the switcher |
| virtual camera | chroma-green overlay canvas, hand skeleton and gaze cursor drawn, HUD optional, passthrough identical to the camera frame when nothing is drawn, gaze mapping monotonic left-to-right — **70 tests total** |
| the virtual camera is real | `--vcam-test`: 45 frames synchronous at 28.4 fps plus 30 frames through the threaded publish path, 0 dropped, device "OBS Virtual Camera" (backend obs) |
| gaze-point → window | live check against a window it creates: a point inside resolves to that window, a blocklisted title is skipped, a point outside returns nothing |
| rotation invariance | open hand still reads OPEN at 0/45/90/135/−90° of in-plane rotation (the specific thing upstream got wrong) |
| click/flick arbitration | tests assert the close-flick emits **no** click, and that a pinch alone emits exactly one |
| monitor aiming | the classifier is tested against this machine's real four-display layout (heuristic, calibrated, hysteresis, distance gate, reversed-axis handling) |
| the OS layer works | `kinesis.py --check` passes: 4 monitors enumerated, virtual desktop 7680x1080, 4 real windows located by monitor, fullscreen detection correct against a live fullscreen overlay window, virtual-key map, camera opened |
| it actually detects hands | live: 250/251 frames with a hand in view, a hand detected during the build (`Left(0.98)`), MediaPipe world landmarks feeding real yaw/pitch |
| the OS action layer really acts | `tools/verify_actions.py` 16/16: real `IsIconic`/`IsZoomed` transitions on a window it creates, `SendInput` keys, fullscreen-key choice, dry-run logging, wheel accumulation |
| latency | 45–48 ms average end-to-end, 81–104 ms peak, measured over a 25 s live run |
| no silent failure | the first build had the inference thread dying on a bad MediaPipe kwarg *silently*; that class of failure now raises loudly instead, which is how it was found |

Needs Joe's hands, and this plan does not pretend otherwise:

- how each gesture *feels* at his desk distance, and pinch tightness in particular;
- his own aim calibration (`calibrate.bat`) — accuracy depends on his camera position and chair;
- filter tuning: 1.6/0.05 is a starting point, `--tune` exists precisely so it becomes a number to
  change rather than something to reflavour by guess;
- whether the 2 s left-fist hold for Alt-Tab is the right duration in practice.

### One requirement added mid-build

The shaka pose (thumb **and** pinky out, index/middle/ring curled → hold `Ctrl+Space`, release on
release) arrived after the initial brief and is implemented as gesture #11. It is deliberately
disjoint from the fist (which requires the pinky curled), so push-to-talk can never minimise a
window.
