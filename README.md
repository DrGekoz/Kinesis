<div align="center">

# KINESIS

### Your eyes pick the window. Your hands do the work.

Control Windows with your hands through one webcam. Look at a window and it becomes the target; move your index finger and the cursor goes there. No keyboard, no mouse, no wearable.

**13 gestures · gesture locking · desk geometry · multi-monitor · ~46 ms · gaze targeting · gaze tab clicks · desktop overlay · dictation into the field you look at · virtual camera · 176 tests**

<a href="https://github.com/DrGekoz/Kinesis/stargazers"><img src="https://img.shields.io/github/stars/DrGekoz/Kinesis?style=for-the-badge&color=f59e0b" alt="Stars"></a>
<a href="https://github.com/DrGekoz/Kinesis/blob/master/LICENSE"><img src="https://img.shields.io/badge/license-MIT-22c55e?style=for-the-badge" alt="License"></a>
<img src="https://img.shields.io/badge/platform-Windows-06b6d4?style=for-the-badge" alt="Platform">
<img src="https://img.shields.io/badge/python-3.11-8b5cf6?style=for-the-badge" alt="Python">
<img src="https://img.shields.io/badge/latency-~46ms-f43f5e?style=for-the-badge" alt="Latency">
<img src="https://img.shields.io/badge/tests-176%20passing-22c55e?style=for-the-badge" alt="Tests">
<img src="https://img.shields.io/badge/virtual%20camera-OBS-8b5cf6?style=for-the-badge" alt="Virtual camera">

[Gestures](#gestures) · [Quick start](#quick-start) · [Install](#install) · [Calibration](#calibration) · [Gaze](#gaze) · [Overlay](#overlay) · [Dictation](#dictation) · [Config](#config) · [Troubleshooting](#troubleshooting) · [Credits](#credits)

</div>

---

Built for desks with several monitors, not one. Your eyes track what you are focusing on; your hands tell the PC what to do.

| Feature | What it means |
| --- | --- |
| **13 hand gestures** | Cursor, click, right click, double click, drag, zoom in/out, Ctrl+Tab, Alt+Tab, minimise, maximise/fullscreen, push-to-talk — plus gaze scrolling |
| **Gesture locking** | A gesture takes the hand and holds it until you open up, so gestures cannot be read out of each other's tails |
| **Gaze targeting** | Eye tracking picks the window a gesture acts on — and dwell brings it forward, so typing lands there too |
| **Desk geometry** | Learns your screens' real sizes from EDID, their physical row, and how far away you sit, then uses it to aim the gaze calibration and the pointing maths |
| **Multi-monitor first** | Written for a four-screen desk; supports N screens, mixed sizes, mixed PPIs |
| **~46 ms end-to-end** | Capture to decision, measured. The cursor snaps to your hand instead of animating there |
| **Smooth cursor** | A One-Euro filter removes landmark jitter without paying in lag: hard smoothing at rest, near-raw when moving |
| **Desktop overlay** | Transparent, click-through, always on top, over every screen — trails, heatmap and glow |
| **Dictation** | Thumb-and-pinky holds `Ctrl+Space` for [Handy](https://github.com/cjpais/Handy) offline speech-to-text |
| **Virtual camera** | The webcam feed goes back out with the overlays drawn on it, so OBS, Discord or Zoom can use the camera while Kinesis is using it, plus a chroma-keyable green mode |
| **Gaze scrolling** | Look at the top or bottom of the screen and it scrolls while you keep looking |

## Gestures

| Gesture | Action |
| --- | --- |
| Point with the index finger | Move the cursor — 1:1, no easing |
| Thumb + index pinch | Left click (short pinch clicks on release after a 120 ms grace; held past 350 ms clicks while held) |
| Two quick pinches | Double click |
| Thumb + middle pinch | Right click |
| Thumb + ring pinch, held | **Drag** — text selection, moving files. Cursor freezes, so the selection does not run away |
| Right fist held + left index pinch | Next browser tab (`Ctrl+Tab`) |
| Both hands index-thumb pinch, apart | Zoom in (`Ctrl+=`) — travel earns the steps, so a small move is a small zoom |
| Both hands index-thumb pinch, together | Zoom out (`Ctrl+-`) |
| Right fist held + left middle pinch | Previous browser tab (`Ctrl+Shift+Tab`) |
| Open hand → closed fist | Minimise the target window (exiting fullscreen first) |
| Closed fist → open hand | Maximise; if maximised, fullscreen (`f` on YouTube, `F11` elsewhere) |
| Thumb + pinky out, other three curled | Hold `Ctrl+Space` — push-to-talk dictation |
| **Left** fist held 2 s | Opens Alt-Tab and holds `Alt`; each right-hand pinch taps `Tab`; opening the left fist commits |
| `END` | Quit, releasing everything |

Window actions are aimed: "minimise" hits the window you were looking at, or the topmost window on the monitor you were pointing at.

## Quick start

```bat
check.bat            environment + camera + model self-test, then the test suite
calibrate.bat        aiming: point at each screen, hold, press Enter
calibrate_gaze.bat   gaze: look at the dots (40 s)
run.bat              go
```

| Command | What it does |
| --- | --- |
| `run.bat --dry` | Every action logged, nothing injected |
| `run.bat --exclusive` | Cursor movement only |
| `run.bat --no-preview` | Skip the preview window |
| `run.bat --desk-report` | What your screens, camera and seat actually are |
| `run.bat --overlay-desktop` | Draw the overlay on the desktop itself |
| `run.bat --vcam-demo` | Watch the overlay styles with no face in frame |
| `run.bat --tune deadband_px=2.2 --save-config` | Change any setting live and keep it |
| `run.bat --help` | Everything else |

## Install

Windows 10/11, Python 3.11, a webcam, and OBS Studio for the virtual camera.

```bat
git clone https://github.com/DrGekoz/Kinesis.git
cd Kinesis
git clone https://github.com/ck-zhang/eyetrax vendor/eyetrax

python -m venv .venv
.venv\Scripts\pip install mediapipe==0.10.20 opencv-contrib-python==4.10.0.84 numpy==1.26.4
.venv\Scripts\pip install --no-deps -e vendor/eyetrax
.venv\Scripts\pip install scikit-learn screeninfo pyvirtualcam comtypes Pillow
```

`comtypes` drives UI Automation, which is what recognises a text box before the dictation gesture
clicks into it. Without it that check degrades to window classes and browsers stop being recognised.

## Calibration

Two one-off steps, both about 40 seconds:

- `calibrate.bat` — **aiming.** Point at each screen, hold, Enter. Lets hand angle pick the monitor.
- `calibrate_gaze.bat` — **gaze.** Look at dots placed on every screen. Prints pixel error, overall
  monitor hit rate and per-screen hit rates; under ~85% means your head moved, so run it again. It
  also measures and records how far away you were sitting.

Skip them and everything still works — gaze falls back to hand aim, then the focused window.

## Gaze

Eye tracking (via [EyeTrax](#credits)) decides **which window a gesture applies to**. Look at a
window, gesture, and it lands there — no click-to-focus first.

- **Gaze focus.** Dwell on a window for 0.15 s and it comes to the front, so the keyboard and typing
  land there too. `gaze_focus_enabled: false` keeps gaze as a gesture target only.
- **Tab switching.** Click while looking at a browser tab and Kinesis parks the pointer on that tab
  1 ms before the click goes out, so the click lands on the tab you were looking at rather than
  wherever your hand happened to be pointing. Works in any Chromium browser and Firefox.
- **Gaze scrolling.** Looking at the top or bottom band scrolls, after a short dwell, ramping up the
  longer you look. **This is the only scroll binding** — the ring-pinch scroll was retired when this
  worked, so scrolling needs a calibrated model. `pinch_ring_action=scroll` brings the pinch back as
  a fallback, and Kinesis tells you at startup if you have neither.
- **Seat drift is monitored.** Kinesis measures how far away your face is the whole time and says so
  when the chair has moved far enough that the model is stale.
- **It is honest about failure.** An uncalibrated model is announced at startup, and screens closer
  than ~8° apart *from where you sit* are reported as too tight to tell apart reliably.
- Fallback order: gaze → hand angle (`calibrate.bat`) → focused window.

## Overlay

Five looks, from one accumulation buffer (faded each frame, stamped, blurred into itself for bloom):

| Style | Look |
| --- | --- |
| `pointer` | Crisp reticle, nothing persists |
| `comet` | Tapered tail behind the reticle (default) |
| `path` | Last ~48 samples as a tapering polyline — the gaze-plot look |
| `heatmap` | Accumulated dwell through a colormap |
| `heatmap_comet` | Both |
| `none` | Hands only |

Themes: `ember`, `cyan`, `violet`, `lime`, `ice` — reticle, tail and colormap together. Telemetry is
real typography (Bahnschrift via Pillow).

**On the desktop:** `run.bat --overlay-desktop` draws it over everything, one click-through window per
monitor, no focus stealing, real per-pixel transparency. **INSERT** hides and shows it. Only the
screen you are looking at gets the marker; the others fade out.

**Out as a webcam:** the virtual camera re-emits the feed with the overlays drawn on it — select
"OBS Virtual Camera" in OBS, Discord, Zoom or Teams. `vcam_mode: overlay` gives a plain green
background for chroma-keying, so the tracking can be keyed over your real camera in OBS.

## Dictation

Hold the thumb-and-pinky pose and talk. It holds `Ctrl+Space`, which is the Windows default
*transcribe* binding in [Handy](https://github.com/cjpais/Handy) — free, offline speech-to-text.
Remapped it? Mirror it: `--tune ptt_keys=ctrl,alt,d --save-config`.

**It clicks into the field you are looking at first.** Make the gesture while looking at a text box
and Kinesis parks the pointer on that point, waits 1 ms, clicks to put the caret there, gives the
field 40 ms to take focus, and only then sends `Ctrl+Space` — so the dictation lands where you were
looking instead of wherever the last click left the caret. Buttons, links, images, the desktop and
the taskbar are never clicked. `ptt_focus_mode`: `auto` (default) · `uia` (only when the field can be
confirmed) · `always` · `off`.

## Latency

| Metric | Measured |
| --- | --- |
| Camera capture | 15.0 fps (C920 ceiling at 640×480) |
| MediaPipe inference | 17–39 ms/frame, no dropped frames |
| **End-to-end** | **45–48 ms average, 81–104 ms peak** |
| Main loop | 340–500 fps |

Newest-frame-only capture on its own thread, no pyautogui, and every blocking call — including
virtual-camera frame pacing and overlay rendering — kept off the decision loop.

## Config

Everything lives in `kinesis_config.json` and every key can be overridden live with
`--tune key=value`. The ones worth touching:

| Key | Default | Meaning |
| --- | --- | --- |
| `pinch_on` / `pinch_off` | 0.34 / 0.55 | Pinch tightness as a fraction of hand size — tune for your hand |
| `filter_min_cutoff` / `filter_beta` | 1.6 / 0.05 | Cursor smoothing at rest, and how fast it opens up in motion |
| `deadband_px` | 1.5 | Movement below this is ignored |
| `cursor_hand` | right | Which hand drives the cursor |
| `frame_width` / `frame_height` | 640 / 480 | Capture size: lower = less latency |
| `model_complexity` | 0 | `0` = lite/faster, `1` = full/more accurate |
| `active_margin` | 0.1 | Fraction of frame edge treated as overflow |
| `gesture_lock` / `gesture_lock_open_fingers` | true / 4 | One gesture owns the hand until it opens |
| `gesture_lock_timeout_s` | 6.0 | Safety: a held pose can never wedge the engine |
| `pinch_ring_action` / `pinch_pinky_action` | drag / none | Thumb+ring drags; thumb+pinky is unbound (was the drag). `scroll` on the ring brings the pinch scroll back |
| `ring_confirm_frames` / `scroll_release_frames` | 2 / 3 | Hold to start a held action, flicker-tolerance before it releases |
| `scroll_gain` / `scroll_smooth` / `scroll_deadband_px` | 1.0 / 0.45 / 1.5 | Pinch-scroll tuning, used only when `pinch_ring_action=scroll` |
| `alt_tab_hold_s` / `alt_tab_session_timeout_s` | 2.0 / 30.0 | Left-fist hold, and the Alt release safety |
| `zoom_pinch_enabled` / `zoom_step_px` | true / 22 | Two-hand pinch zoom, and the hand travel that earns one zoom step |
| `zoom_keys_in` / `zoom_keys_out` | ctrl+= / ctrl+- | The keys the zoom sends |
| `ctrl_tab_hold_s` / `ctrl_tab_repeat_s` | 0.6 / 0.35 | Right-fist hold to open Ctrl-Tab, and the re-tap rate |
| `ctrl_tab_flick_guard` | two_hands | A right fist holds Ctrl instead of minimising while the left hand is in frame |
| `swipe_action` | none | The old lateral-swipe tab binding — replaced, off by default |
| `ptt_keys` | ctrl, space | The dictation hotkey the shaka holds |
| `gaze_target_enabled` | true | Gaze picks the target window |
| `gaze_focus_enabled` / `gaze_focus_dwell_s` | true / 0.15 | Looking at a window focuses it |
| `ptt_focus_mode` / `ptt_focus_settle_ms` | auto / 40 | Click into the field you are looking at before dictating, and the focus settle |
| `gaze_click_tabs` / `gaze_click_warp_delay_ms` | true / 1.0 | Clicking a tab you are looking at, and the pause after the pointer moves |
| `tab_strip_top_px` / `tab_strip_height_px` | 6 / 40 | Where the tab strip is, in logical pixels from the client top |
| `gaze_scroll_mode` / `gaze_scroll_speed` | edge / 480 | Gaze scrolling on/off and speed |
| `camera_name` / `camera_fov_deg` | auto / 0 | Override the camera, or its diagonal field of view |
| `bezel_mm` / `assumed_distance_mm` | 10 / 700 | Physical gap between screens; seat distance before calibration |
| `eye_corner_mm` / `ipd_mm` | 90 / 63 | Your own eye span, for the distance estimate |
| `distance_warn_fraction` | 0.25 | Seat drift before the gaze model counts as stale |
| `desktop_overlay` / `desktop_overlay_hotkey` | false / insert | The desktop overlay and its toggle |
| `vcam_mode` / `vcam_style` / `vcam_theme` | passthrough / comet / ember | Virtual camera mode and look |
| `vcam_trail_decay` / `vcam_glow` | 0.86 / 1.15 | Trail length and bloom |

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| Cursor jitters | `--tune filter_min_cutoff=1.2 deadband_px=2.5 --save-config` |
| Cursor feels laggy | Raise `filter_beta`, or `filter: false` for raw landmarks |
| Scrolling jumps or fights you | `scroll_smooth` up (0.6), `scroll_deadband_px` up, `scroll_gain` down |
| Alt-Tab is not switching | Hold the LEFT fist the full 2 s, then pinch index-to-thumb on the right hand |
| Ctrl+Tab is not switching | Right fist held `ctrl_tab_hold_s` (0.6 s), then pinch on the LEFT hand. If minimise fires instead, raise `ctrl_tab_flick_guard` back to `two_hands` |
| Gestures act on the wrong window | `calibrate_gaze.bat`, check the hit rate; or `calibrate.bat` for aiming |
| Clicking a tab does nothing | Gaze has to be calibrated — an invalid gaze point cannot be warp-targeted. `tools/check_tabs.py` confirms the strip is being found |
| Dictation types into the wrong place | `tools/check_focus.py` shows what the hit test sees; `ptt_focus_mode=uia` only clicks fields it can confirm, `off` disables the click entirely |
| Scrolling does nothing | It is gaze-driven now — calibrate. `--tune pinch_ring_action=scroll` restores the pinch scroll as a fallback |
| Overlay does not appear | It needs gaze — an uncalibrated model draws nothing. INSERT toggles it |
| Gaze picks the neighbouring screen | `--desk-report`: under ~8° between screens, sit further back or restrict `--monitors` |
| Desk report has wrong sizes | That EDID had no size (says `estimate`) — set `monitor_mm_overrides` |

## Verification

```bat
.venv\Scripts\python -m pytest tests -q       176 passed
.venv\Scripts\python tools\verify_actions.py  16/16 live OS checks
.venv\Scripts\python tools\verify_overlay.py  12/12 against the real compositor
.venv\Scripts\python tools\check_tabs.py      tab-strip detection against your open browsers
.venv\Scripts\python tools\bench_overlay.py   per-style overlay cost
.venv\Scripts\python tools\check_focus.py     text-box detection against your live desktop
check.bat                                     all of the above, in order
```

176 tests cover every gesture, the pose classifier, the click/flick arbitration, gesture locking, the
Alt-Tab session, gaze targeting and focus, desk geometry against a hand-built EDID block, overlay
rendering, and release-all safety — with landmark geometry synthesised at exact joint angles rather
than recorded, so each classification is checked against a known-correct input.

## Credits

| Project | Why it is here |
| --- | --- |
| [Virtual-Mouse](https://github.com/whitehatboy005/Virtual-Mouse) | The starting point. Its measured failures defined the problem |
| [EyeTrax](https://github.com/ck-zhang/eyetrax) | Gaze estimation, smoothing filters, and the chroma-key overlay idea |
| [awesome-hand-pose-estimation](https://github.com/xinghaochen/awesome-hand-pose-estimation) | The research index, and the held-open upgrade path |
| [Handy](https://github.com/cjpais/Handy) | Offline speech-to-text, driven by the thumb-and-pinky gesture |
| [MediaPipe](https://github.com/google-ai-edge/mediapipe) | Hand and face landmarks — the biggest single piece |
| [pyvirtualcam](https://github.com/letmaik/pyvirtualcam) | Virtual camera output (installed, not bundled — GPL-2.0) |
| [OBS Studio](https://github.com/obsproject/obs-studio) | Provides the virtual camera device |

Kinesis itself is MIT. Per-dependency licence detail: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
Nothing here injects input on your behalf — every action is Kinesis's own, via `SendInput` and
`SetCursorPos`.

---

Full version history and the reasoning behind each change: **[CHANGELOG.md](CHANGELOG.md)**.
Design notes and the research/upgrade path: **[plan.md](plan.md)**.

