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
<img src="https://img.shields.io/badge/tests-267%20passing-22c55e?style=for-the-badge" alt="Tests">
<img src="https://img.shields.io/badge/virtual%20camera-OBS-8b5cf6?style=for-the-badge" alt="Virtual camera">

[Gestures](#gestures) · [Quick start](#quick-start) · [Install](#install) · [Which screens](#which-screens) · [Calibration](#calibration) · [Eyes as the pointer](#the-pointer-is-your-eyes) · [Gaze](#gaze) · [Settings](#settings) · [Overlay](#overlay) · [Dictation](#dictation) · [Config](#config) · [Troubleshooting](#troubleshooting) · [Credits](#credits)

</div>

---

Built for desks with several monitors, not one. Your eyes track what you are focusing on; your hands tell the PC what to do.

| Feature | What it means |
| --- | --- |
| **13 hand gestures** | Cursor, click, right click, double click, drag, zoom in/out, Ctrl+Tab, Alt+Tab, claw-drag minimise/maximise, push-to-talk — plus gaze scrolling |
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
| Claw (all four fingertips on the thumb), dragged **down** | Minimise the window you are looking at (exiting fullscreen first). **Does nothing if your eyes are not on a window** — it never guesses at whatever has focus |
| Claw, then **spread** the fingers apart | Maximise; if maximised, fullscreen (`f` on YouTube, `F11` elsewhere) |
| Thumb + pinky out, other three curled | Hold `Ctrl+Space` — push-to-talk dictation |
| **Index + pinky** out, middle and ring curled, held | Move the hand **up** / **down** to raise or lower the system volume |
| **Both hands** open → both fists → open, fast | Screenshot (follows your machine's PrintScreen behaviour — snip-and-select, or straight to the clipboard) |
| **Left** fist held 2 s | Opens Alt-Tab and holds `Alt`; each right-hand pinch taps `Tab`; opening the left fist commits |
| `END` | Quit, releasing everything |

Window actions are aimed: the claw acts on the window you were **looking at**. There is no fallback — no gaze target means no minimise, ever, because guessing at the focused window is how a gesture you meant for the browser behind you closes the app you were typing in. Keys, clicks and the wheel still go to the focused window as normal.

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

python -m venv .venv
.venv\Scripts\pip install mediapipe==0.10.20 opencv-contrib-python==4.10.0.84 numpy==1.26.4
.venv\Scripts\pip install scikit-learn screeninfo pyvirtualcam comtypes Pillow
```

That is the whole list: **EyeTrax is vendored into `vendor/eyetrax` and nothing needs installing for
it.** Kinesis puts its source on the import path at startup, so a fresh clone tracks gaze with no
editable install, no second clone step and no version to pin. An already-installed copy still works
and wins if present - it is the same code. See `vendor/README.md` for the upstream revision.

`comtypes` drives UI Automation, which is what recognises a text box before the dictation gesture
clicks into it. Without it that check degrades to window classes and browsers stop being recognised.

## Which screens

On first run Kinesis lists your monitors and asks which ones to use:

```
 WHICH SCREENS SHOULD KINESIS USE?
  [1] 1920x1080  at -3840,0  Digital TV
  [2] 1920x1080  at -1920,0  Lenovo L27i-30
  [3] 1920x1080  at 0,0  primary  KAMN27F18WA
  [4] 1920x1080  at 1920,0  Lenovo L27i-30

 Use which screens? [1,3 / all / primary] (all):
```

The answer applies to **everything**: calibration only puts dots on those screens, and hand aiming,
gaze targeting, the overlays and the virtual camera all treat them as the whole desktop. A four
screen desk is not four usable screens - the outer ones sit at extreme angles from wherever anyone
actually sits, and gaze there is guesswork, so leaving them out makes the rest more reliable.

```bat
run.bat --ask-monitors              ask again
run.bat --enable-monitors 2,3       pick by number
run.bat --all-monitors              use every screen again
```

The choice is stored by device name, not position, so it survives a reboot or a dock change. A
screen that is unplugged later is simply ignored rather than breaking the selection.

## Calibration

Two one-off steps:

- `calibrate.bat` — **aiming.** Point at each screen, hold, Enter. Lets hand angle pick the monitor.
- `calibrate_gaze.bat` — **gaze.** Look at five dots per screen, ~40 s. This is the one that decides
  whether eye tracking works at all, so it is worth the minute.

The gaze wizard scores itself honestly. It refits the model leaving out **each dot in turn**, so the
figure it prints is accuracy on a position the model has never seen - the only number that predicts
how it behaves on the screen. It also sweeps the model's regularisation strength and keeps whichever
targets the most dots, drops samples taken while you were leaning in or out (a different seat is a
different geometry), and saves every sample plus the desk geometry each dot came from.

```bat
calibrate_gaze.bat                    full sweep, 5 dots per screen
calibrate_gaze.bat --quick            one dot per screen, ~15 s, to test the plumbing
calibrate_gaze.bat --dry-run          score it and report without saving
calibrate_gaze.bat --points 9         9 dots per screen for a hard desk
calibrate_gaze.bat --monitors 2,3     calibrate only those screens
```

Under ~85% on the held-out figure means your head moved; run it again. Skip all of this and
everything still works — gaze falls back to hand aim, then the focused window.

Not sure whether eye tracking is working at all? `tools\check_gaze.py` tests each stage in turn -
vendored source, camera, face, landmarks, features, model, prediction - and says which one is broken.

## The pointer is your eyes

The cursor follows your **gaze**, not your hand. Calibrate once (`calibrate_gaze.bat`) and the pointer
goes where you look, at the rate in `gaze_hz`, smoothed and deadbanded so a resting eye does not shake
it. Every gesture still works exactly as before - a pinch clicks, a ring pinch drags - but they act
**where you are looking** instead of where your hand is, and a drag selects with your eyes while the
hand holds the button.

There is no pointing gesture any more. Hand tracking still drives clicks, drags, scrolling, tabs,
window minimising and push-to-talk; it just does not move the pointer.

Before calibration the hand keeps driving the pointer (`cursor_fallback = hand`), because a mouse that
cannot move is worse than a prompt to calibrate. Set it to `hold` if you would rather the pointer
freeze until gaze is ready. Looking away holds the pointer too - it never snaps back to a hand.

```bat
run.bat --tune cursor_source=hand       go back to a pointing hand
run.bat --tune gaze_hz=30 --save-config a smoother pointer, if your machine keeps up
```

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

## Settings

Press **F2** while Kinesis is running (or start it with `run.bat --settings`) and a settings window
opens, drawn in the same palette and fonts as the on-screen overlay:

```
 KINESIS                                     screens and calibration
 Hand and eye tracking only work on screens you actually face. A screen
 Windows places somewhere else than it really is should be switched off.

  Lenovo L27i-30                     1920x1080  at -1920,0        [ o]
  KAMN27F18WA   PRIMARY              1920x1080  at 0,0            [o ]
  Lenovo L27i-30                     1920x1080  at 1920,0         [o ]

 3 of 4 screens tracked

 [ Save and apply ]  [ Recalibrate gaze ]  [ Recalibrate aiming ]        Close
```

- **Switches** turn tracking on or off per screen. What is switched off is left out of hand aiming,
  gaze targeting, the overlays and the virtual camera, and no calibration dot is ever shown on it.
- **Recalibrate gaze** and **Recalibrate aiming** run the wizards over the enabled screens only, so a
  screen you switched off is never asked for.
- **Save and apply** takes effect immediately - no restart.

This is the setting for a screen Windows has placed wrongly. A TV on the wall, a screen on a
different desk, anything whose EDID geometry does not match where it really is, will drag aiming and
gaze off with it; switch it off and the screens that are actually in front of you get better.

While the window is open Kinesis releases its keys and stops injecting, so a hand gesture cannot
press its own buttons.

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
| `claw_minimise` / `claw_travel_px` / `claw_window_s` | true / 140 / 1.2 | The claw drag: on, how far the palm must travel, and how fast |
| `claw_spread` / `claw_settle_s` / `claw_spread_frames` | true / 0.18 / 2 | Pinch-all then spread to maximise: on, how long the claw must be held first, and how many frames the spread must read across |
| `volume_enabled` / `volume_step_px` / `volume_max_steps` | true / 26 / 2 | The volume rocker: on, palm travel per step (~2%), and the per-frame ceiling |
| `volume_deadband_px` / `volume_smooth` | 4.0 / 0.35 | Movement below this is ignored; palm smoothing so noise is never a step |
| `volume_keys_up` / `volume_keys_down` | volumeup / volumedown | The Windows master-volume keys the rocker sends |
| `screenshot_enabled` / `screenshot_window_s` / `screenshot_fist_px` | true / 0.45 / 45 | The two-hand screenshot: on, how fast the whole flourish must be, and how far the fists must travel |
| `ctrl_tab_flick_guard` | two_hands | Legacy no-op, kept so old configs load — the flick it guarded is gone |
| `swipe_action` | none | The old lateral-swipe tab binding — replaced, off by default |
| `ptt_keys` | ctrl, space | The dictation hotkey the shaka holds |
| `gaze_target_enabled` | true | Gaze picks the target window |
| `gaze_focus_enabled` / `gaze_focus_dwell_s` | true / 0.15 | Looking at a window focuses it |
| `ptt_focus_mode` / `ptt_focus_settle_ms` | auto / 40 | Click into the field you are looking at before dictating, and the focus settle |
| `gaze_click_tabs` / `gaze_click_warp_delay_ms` | true / 1.0 | Clicking a tab you are looking at, and the pause after the pointer moves |
| `tab_strip_top_px` / `tab_strip_height_px` | 6 / 40 | Where the tab strip is, in logical pixels from the client top |
| `gaze_scroll_mode` / `gaze_scroll_speed` | edge / 480 | Gaze scrolling on/off and speed |
| `enabled_monitors` / `monitors_configured` | [] / false | Which screens Kinesis uses, by device name (empty = all). Set by the first-run question or the settings window |
| `gesture_map_path` | *(config folder)* | where your Gesture-Map (.json) is kept |
| `marketplace_api` | *(unset)* | the Gesture-Map marketplace endpoint |
| `cursor_source` | `gaze` | `gaze` or `hand` - what moves the pointer |
| `settings_hotkey` | f2 | Opens the settings window while running; `none` disables it |
| `cursor_source` | gaze | What moves the pointer: `gaze` or `hand` |
| `cursor_fallback` | hand | Before calibration: `hand` still moves it, or `hold` freezes the pointer |
| `gaze_cursor_deadband_px` / `gaze_cursor_smoothing` | 3.0 / 0.55 | Eye-cursor jitter floor and smoothing |
| `gaze_hz` | 20 | How often the eyes are read; the pointer is only as smooth as this |
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
| Ctrl+Tab is not switching | Right fist held `ctrl_tab_hold_s` (0.6 s), then pinch on the LEFT hand |
| Gestures act on the wrong window | `calibrate_gaze.bat`, check the hit rate; or `calibrate.bat` for aiming |
| Clicking a tab does nothing | Gaze has to be calibrated — an invalid gaze point cannot be warp-targeted. `tools/check_tabs.py` confirms the strip is being found |
| Dictation types into the wrong place | `tools/check_focus.py` shows what the hit test sees; `ptt_focus_mode=uia` only clicks fields it can confirm, `off` disables the click entirely |
| Scrolling does nothing | It is gaze-driven now — calibrate. `--tune pinch_ring_action=scroll` restores the pinch scroll as a fallback |
| Overlay does not appear | It needs gaze — an uncalibrated model draws nothing. INSERT toggles it |
| Gaze picks the neighbouring screen | `--desk-report`: under ~8° between screens, sit further back or restrict `--monitors` |
| Desk report has wrong sizes | That EDID had no size (says `estimate`) — set `monitor_mm_overrides` |

## Verification

```bat
.venv\Scripts\python -m pytest tests -q       360 passed
.venv\Scripts\python tools\verify_actions.py  21/21 live OS checks
.venv\Scripts\python tools\verify_volume.py    10/10 volume-key checks (mixer response skipped where it cannot be read)
node tools\marketplace_stub.mjs 8787        runs the real Worker locally against an in-memory D1
.venv\Scripts\python tools\verify_marketplace.py   18/18 submit round-trip, no Cloudflare needed
.venv\Scripts\python tools\deploy_check.py     is the marketplace ready to deploy, and what is missing
.venv\Scripts\python tools\verify_overlay.py  12/12 against the real compositor
.venv\Scripts\python tools\check_tabs.py      tab-strip detection against your open browsers
.venv\Scripts\python -u tools/check_gaze.py   live gaze pipeline: camera, face, features, model
.venv\Scripts\python -u tools/check_settings_window.py, tools/build_gesture_maps.py   renders the settings window
.venv\Scripts\python tools\bench_overlay.py   per-style overlay cost
.venv\Scripts\python tools\check_focus.py     text-box detection against your live desktop
check.bat                                     all of the above, in order
```

360 tests cover every gesture, the pose classifier, the click/fist arbitration, gesture locking, the
Alt-Tab session, gaze targeting and focus, desk geometry against a hand-built EDID block, overlay
rendering, and release-all safety — with landmark geometry synthesised at exact joint angles rather
than recorded, so each classification is checked against a known-correct input.

## Gesture-Maps

F2 -> **Gestures**. Every binding is one gesture -> one action, and the file is plain JSON so it can
be exported, handed to someone else, or published.

    + Add gesture        pick from the gestures the tracker can really see, then what it does
    Edit                 change either half; keys are captured by pressing them, with CTRL / SHIFT /
                         ALT / WIN as toggles
    Import / Export      a .json in, a .json out - a bad file is refused with the reason, not a stack
    Submit to Marketplace  publish it with a title, description, your name and GitHub link

Gestures marked **(built in)** are the ones Kinesis already uses - click, right click, drag, push to
talk, and the held fist / open hand that carry Alt-Tab and Ctrl-Tab. Binding one overrides the built
in, and the editor says so. Everything else in the vocabulary is free: peace, three, four, pinky, a
little-finger pinch, a point, and any two-hand pair. The claw drag is deliberately not bindable: it is
the one window gesture that must not be reassigned by an imported map.

Actions are keys (with modifiers), a held key, a mouse click or a real button-hold drag, or a window
command. There is deliberately **no** "run a program" and no "type text": a map imported from the
marketplace cannot execute anything, which is the only reason sharing one is safe.

Ten maps ship in `gesture_maps/`: Browser Power User, Media Player, Windows Navigation, Video
Editing, Presentation Remote, Coding / IDE, One-Handed Essentials, Meeting Controls, Reading
Comfort, and Gaming Hotbar.

## Marketplace

Gesture-Maps are shared through a Cloudflare Worker in front of a D1 database (`cloudflare/`), so the
desktop app holds no credentials - it only calls a public endpoint. Browse and download from
F2 -> **Marketplace**.

## Credits

| Project | Why it is here |
| --- | --- |
| [Virtual-Mouse](https://github.com/whitehatboy005/Virtual-Mouse) | The starting point. Its measured failures defined the problem |
| [EyeTrax](https://github.com/ck-zhang/eyetrax) | Gaze estimation, smoothing filters and the chroma-key overlay idea. **Vendored** into `vendor/eyetrax` (MIT) so users install nothing - revision recorded in `vendor/README.md` |
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